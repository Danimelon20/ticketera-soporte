"""Importación del organigrama: una columna con la ruta completa separada por " / ".

Vista previa sin guardar: quita segmentos repetidos consecutivos, marca rutas duplicadas y sugiere
equivalencias cuando dos nombres difieren solo en tildes, mayúsculas o abreviaturas. El administrador
confirma qué fusionar; los nombres fusionados quedan como alias del departamento que se conserva.
El archivo real nunca se guarda en el servidor ni en el repositorio: solo se lee en memoria.
"""
import csv
import io
import re
import unicodedata

from sqlalchemy.orm import Session

from . import models, routing

SEPARATOR = " / "
PATH_COLUMNS = ("ruta", "departamento", "path")
MAX_ROWS = 2000
MAX_NAME = 120
# Abreviaturas comunes: se comparan ya sin tildes y en minúsculas
ABBREVIATIONS = {
    "nivel": "n",
    "niv": "n",
    "departamento": "depto",
    "dpto": "depto",
    "dept": "depto",
    "gerencia": "ger",
    "gcia": "ger",
    "administracion": "admin",
    "adm": "admin",
    "operaciones": "op",
    "oper": "op",
    "tecnologia": "tec",
    "tecnologias": "tec",
    "informacion": "info",
    "telecomunicaciones": "telecom",
    "y": "",
    "de": "",
    "del": "",
    "la": "",
}


class ImportError_(ValueError):
    pass


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def normalize_name(name: str) -> str:
    """Clave para comparar nombres: sin tildes, minúsculas, sin signos y con las abreviaturas unificadas."""
    words = re.findall(r"[a-z0-9]+", _plain(name))
    # "Nivel 2" y "N2" quedan iguales
    joined = []
    for word in words:
        word = ABBREVIATIONS.get(word, word)
        if not word:
            continue
        if joined and joined[-1] == "n" and word.isdigit():
            joined[-1] = f"n{word}"
        else:
            joined.append(word)
    return " ".join(joined)


def normalize_path(segments: list[str]) -> str:
    return SEPARATOR.join(normalize_name(s) for s in segments)


def clean_segments(raw: str) -> tuple[list[str], list[str]]:
    """Segmentos limpios de una ruta y avisos. Quita los repetidos consecutivos (iguales salvo tildes o
    mayúsculas): "Gerencias / Gerencias / General" → "Gerencias / General"."""
    parts = [" ".join(p.split()) for p in raw.split("/")]
    parts = [p for p in parts if p]
    segments, notes = [], []
    for part in parts:
        if segments and _plain(segments[-1]) == _plain(part):
            notes.append(f"Se quitó el segmento repetido «{part}»")
            continue
        segments.append(part[:MAX_NAME])
    return segments, notes


def read_paths(content: bytes) -> list[str]:
    """Rutas del CSV (con encabezado ruta/departamento o, si no, la primera columna)."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=_delimiter(text)) if any(c.strip() for c in r)]
    if not rows:
        raise ImportError_("El archivo está vacío")
    header = [c.strip().lower() for c in rows[0]]
    column = next((header.index(name) for name in PATH_COLUMNS if name in header), None)
    if column is None:
        column = 0
    else:
        rows = rows[1:]
    if len(rows) > MAX_ROWS:
        raise ImportError_(f"El archivo tiene más de {MAX_ROWS} filas")
    return [r[column] if column < len(r) else "" for r in rows]


def _delimiter(text: str) -> str:
    first = text.splitlines()[0] if text else ""
    return max((";", ",", "\t"), key=first.count) if any(d in first for d in ";,\t") else ","


def _existing(db: Session) -> tuple[dict[str, int], dict[str, str]]:
    """Ruta normalizada → departamento (también sus alias) y ruta normalizada → texto de la ruta."""
    by_id = routing.departments_by_id(db)
    keys, texts = {}, {}
    for d in by_id.values():
        names = routing.path_names(d, by_id)
        keys[normalize_path(names)] = d.id
        texts[normalize_path(names)] = SEPARATOR.join(names)
    for alias in db.query(models.DepartmentAlias):
        keys.setdefault(alias.normalized, alias.department_id)
    return keys, texts


def _collapsed(path: str) -> list[str]:
    """Segmentos normalizados, sin repetir los equivalentes que van seguidos."""
    result = []
    for segment in path.split(SEPARATOR):
        key = normalize_name(segment)
        if not result or result[-1] != key:
            result.append(key)
    return result


def preview(db: Session, paths: list[str]) -> dict:
    """Filas normalizadas, duplicados y sugerencias de fusión. No guarda nada."""
    existing, existing_texts = _existing(db)
    rows, seen = [], {}
    for number, raw in enumerate(paths, start=1):
        segments, notes = clean_segments(raw or "")
        if not segments:
            rows.append({"line": number, "original": raw, "path": None, "status": "error", "notes": ["Ruta vacía"]})
            continue
        path = SEPARATOR.join(segments)
        exact = path.casefold()
        status = "new"
        if exact in seen:
            status = "duplicate"
            notes.append(f"Repite la fila {seen[exact]}")
        else:
            seen[exact] = number
            if existing_texts.get(normalize_path(segments)) == path:
                status = "exists"
        rows.append({"line": number, "original": raw, "path": path, "status": status, "notes": notes})

    existing_paths = set(existing_texts.values())
    known = {r["path"] for r in rows if r["status"] in ("new", "exists")} | existing_paths
    suggestions = _suggestions(known, existing_paths)
    merged = {s["merge"] for s in suggestions} | {s["into"] for s in suggestions}
    for row in rows:
        if row["status"] == "new" and row["path"] in merged:
            row["notes"].append("Parece el mismo departamento que otra ruta: revise las equivalencias")
    return {"rows": rows, "suggestions": suggestions,
            "counts": {s: sum(1 for r in rows if r["status"] == s) for s in ("new", "exists", "duplicate", "error")}}


def _accents(path: str) -> int:
    """Letras con tilde o diéresis: ante dos nombres equivalentes, se propone el que las tiene."""
    return sum(1 for c in path if _plain(c) != c.lower())


def _parent(path: str) -> str:
    return path.rpartition(SEPARATOR)[0]


def renamable(existing: str, new: str) -> bool:
    """Un departamento que ya existe solo puede quedar con el nombre del otro si tienen el mismo padre."""
    return _parent(existing) == _parent(new) and normalize_name(existing.split(SEPARATOR)[-1]) == normalize_name(
        new.split(SEPARATOR)[-1])


def _pair(a: str, b: str, existing_paths: set[str], prefer_deeper: bool) -> dict | None:
    """Sugerencia {merge, into, reason, choosable} con el nombre oficial propuesto en "into", o None."""
    a_exists, b_exists = a in existing_paths, b in existing_paths
    if a_exists and b_exists:
        return None
    depth = (lambda p: p.count(SEPARATOR)) if prefer_deeper else (lambda p: -p.count(SEPARATOR))
    keep, other = max((a, b), key=lambda p: (_accents(p), depth(p))), None
    other = b if keep == a else a
    choosable = True
    old, new = (a, b) if a_exists else (b, a) if b_exists else (None, None)
    if old is not None and not renamable(old, new):
        # El que ya existe se conserva: el otro queda como alias
        keep, other, choosable = old, new, False
    return {"merge": other, "into": keep, "reason": _reason(other, keep), "choosable": choosable}


def _suggestions(known: set[str], existing_paths: set[str]) -> list[dict]:
    """Equivalencias: iguales al normalizar (tildes, mayúsculas, abreviaturas) o al juntar segmentos
    equivalentes seguidos ("Soporte Nivel 2 / Soporte N2" ≈ "Soporte N2"); además, mismo nombre final
    con padres distintos (se muestran las dos rutas completas: decide el administrador)."""
    ordered = sorted(known, key=lambda p: (p.count(SEPARATOR), p))
    result, paired = [], set()

    def add(group: list[str], prefer_deeper: bool) -> None:
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if frozenset((a, b)) in paired:
                    continue
                suggestion = _pair(a, b, existing_paths, prefer_deeper)
                if suggestion is not None:
                    paired.add(frozenset((a, b)))
                    result.append(suggestion)

    same: dict[tuple, list[str]] = {}
    leaves: dict[str, list[str]] = {}
    for path in ordered:
        collapsed = _collapsed(path)
        same.setdefault(tuple(collapsed), []).append(path)
        leaves.setdefault(collapsed[-1], []).append(path)
    for group in same.values():
        add(group, prefer_deeper=False)
    for group in leaves.values():
        add(group, prefer_deeper=True)
    return result


def _reason(a: str, b: str) -> str:
    if _collapsed(a) != _collapsed(b):
        return "Mismo nombre final con otros departamentos padre: revise las dos rutas"
    leaf_a, leaf_b = a.split(SEPARATOR)[-1], b.split(SEPARATOR)[-1]
    if a.count(SEPARATOR) != b.count(SEPARATOR):
        return "Mismo nombre en otra ruta"
    if _plain(leaf_a) == _plain(leaf_b):
        return "Solo cambian tildes o mayúsculas"
    return "Mismo nombre con abreviatura"


def _find_or_create(db: Session, segments: list[str], existing: dict[str, int]) -> models.Department:
    parent_id = None
    dept = None
    for i in range(len(segments)):
        key = normalize_path(segments[: i + 1])
        found = existing.get(key)
        dept = db.get(models.Department, found) if found else None
        if dept is None:
            dept = models.Department(name=segments[i], parent_id=parent_id, is_active=True)
            db.add(dept)
            db.flush()
            existing[key] = dept.id
        parent_id = dept.id
    return dept


def add_alias(db: Session, dept: models.Department, alias_path: str) -> bool:
    """Alias con la ruta completa. False si ese texto ya es alias de otro departamento. No hace commit."""
    segments, _ = clean_segments(alias_path)
    text = SEPARATOR.join(segments)[:300]
    if not text or text == routing.path_label(dept, routing.departments_by_id(db)):
        return False
    row = db.query(models.DepartmentAlias).filter(models.DepartmentAlias.alias == text).first()
    if row is not None:
        return False
    db.add(models.DepartmentAlias(department_id=dept.id, alias=text, normalized=normalize_path(segments)))
    db.flush()
    return True


def confirm(db: Session, paths: list[str], merges: list[dict]) -> dict:
    """Crea las rutas nuevas. merges: [{"merge": ruta, "into": ruta}] que eligió el administrador; la ruta
    fusionada no se crea y queda como alias. No hace commit."""
    merge_map = {}
    for item in merges:
        source, target = item.get("merge"), item.get("into")
        if source and target and source != target:
            merge_map[SEPARATOR.join(clean_segments(source)[0])] = target
    existing, existing_texts = _existing(db)
    before = set(existing.values())
    aliases = 0
    # Un departamento que ya existe y se fusiona en otro nombre: se renombra (mismo padre) y su nombre
    # anterior queda como alias. Con otro padre no se puede (lo revisa el servidor, no solo la página)
    current = set(existing_texts.values())
    for source in [m for m in merge_map if m in current]:
        target = SEPARATOR.join(clean_segments(merge_map.pop(source))[0])
        if target in current:
            raise ImportError_(f"«{source}» y «{target}» ya existen: renombre o desactive uno desde la lista")
        if not renamable(source, target):
            raise ImportError_(f"«{source}» ya existe: solo puede quedar con un nombre del mismo departamento padre")
        dept = db.get(models.Department, existing[normalize_path(source.split(SEPARATOR))])
        dept.name = target.split(SEPARATOR)[-1]
        db.query(models.DepartmentAlias).filter(models.DepartmentAlias.alias == target).delete()
        db.flush()
        if add_alias(db, dept, source):
            aliases += 1
        existing, existing_texts = _existing(db)
        current = set(existing_texts.values())
    for raw in paths:
        segments, _ = clean_segments(raw or "")
        if not segments:
            continue
        text = SEPARATOR.join(segments)
        if text in merge_map:
            target = _find_or_create(db, clean_segments(merge_map[text])[0], existing)
            if add_alias(db, target, text):
                aliases += 1
            continue
        _find_or_create(db, segments, existing)
    return {"created": len(set(existing.values()) - before), "aliases": aliases}
