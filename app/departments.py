"""Nombres de departamentos: normalización para comparar y alias.

Tomado de la rama areas-y-clientes. Dos nombres que difieren solo en tildes, mayúsculas, signos o
abreviaturas comunes ("Nivel 2" y "N2", "Departamento" y "Dpto.") se consideran el mismo.
"""
import re
import unicodedata

from sqlalchemy.orm import Session

from . import models, routing

SEPARATOR = routing.SEPARATOR
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
