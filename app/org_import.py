"""Importación de organizaciones cliente desde un CSV: vista previa (no guarda) y confirmación.

Una fila por contacto: las filas con el mismo nombre de organización se juntan (los datos de la
organización van en la primera; en las demás pueden quedar vacíos). Errores (la organización no se
importa): contrato incompleto, dominio repetido, genérico o de ADN. Advertencia (sí se importa): un
contacto con un correo de otro dominio. Los contactos nuevos quedan "pendiente de activar".
"""
import csv
import io
import re
import unicodedata
from datetime import date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import clients, domains, models, routing
from .departments import ImportError_, _delimiter
from .schemas import _legal_id
from .validators import normalize_phone

MAX_ROWS = 2000
COLUMNS = ("nombre", "cedula_juridica", "contrato", "inicio", "fin", "telefono", "dominios", "clausula_datos",
           "contacto_nombre", "contacto_correo", "autorizado_cambios")
ORG_FIELDS = ("cedula_juridica", "contrato", "inicio", "fin", "telefono", "dominios", "clausula_datos")
TEMPLATE = (
    ";".join(COLUMNS) + "\n"
    + "Empresa Ejemplo S.A.;3101123456;CT-2026-001;2026-01-01;2026-12-31;22223333;"
      "empresa-ejemplo.invalid;si;Ana Pérez;ana@empresa-ejemplo.invalid;si\n"
    + "Empresa Ejemplo S.A.;;;;;;;;Luis Mora;luis@empresa-ejemplo.invalid;no\n"
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_YES = ("si", "sí", "s", "x", "1", "true", "yes")


def _key(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def _header_key(value: str) -> str:
    return _key(value).replace(" ", "_")


def read_rows(content: bytes) -> list[tuple[int, dict]]:
    """(número de fila del archivo, valores por columna). El encabezado es obligatorio."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=_delimiter(text))]
    if not rows or not any(c.strip() for c in rows[0]):
        raise ImportError_("El archivo está vacío")
    header = [_header_key(c) for c in rows[0]]
    if "nombre" not in header:
        raise ImportError_("Falta la columna «nombre». Descargue la plantilla")
    data = []
    for line, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        data.append((line, {h: (row[i].strip() if i < len(row) else "") for i, h in enumerate(header) if h}))
    if len(data) > MAX_ROWS:
        raise ImportError_(f"El archivo tiene más de {MAX_ROWS} filas")
    return data


def _date(value: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError


def _split_domains(value: str) -> list[str]:
    return [domains.normalize(d) for d in re.split(r"[,;\s]+", value or "") if d.strip()]


def _parse_org(lines: list[int], fields: dict, errors: list[str]) -> dict:
    """Valida los datos de la organización (de la primera fila que los trae)."""
    data = {"contract_number": fields.get("contrato") or None, "data_clause": _key(fields.get("clausula_datos", "")) in _YES}
    for column, name, label in (("inicio", "contract_start", "inicio"), ("fin", "contract_end", "fin")):
        raw = fields.get(column, "")
        if not raw:
            errors.append(f"Falta la fecha de {label} del contrato")
            data[name] = None
            continue
        try:
            data[name] = _date(raw)
        except ValueError:
            errors.append(f"La fecha de {label} «{raw}» no es válida (use AAAA-MM-DD o DD/MM/AAAA)")
            data[name] = None
    if not data["contract_number"]:
        errors.append("Falta el número de contrato")
    if data["contract_start"] and data["contract_end"] and data["contract_end"] < data["contract_start"]:
        errors.append("El contrato termina antes de empezar")
    try:
        data["legal_id"] = _legal_id(fields.get("cedula_juridica"))
    except ValueError as exc:
        errors.append(str(exc))
        data["legal_id"] = None
    phone = fields.get("telefono", "")
    try:
        data["main_phone"] = normalize_phone(phone) if phone else None
    except ValueError as exc:
        errors.append(str(exc))
        data["main_phone"] = None
    data["domains"] = _split_domains(fields.get("dominios", ""))
    if not data["domains"]:
        errors.append("Falta al menos un dominio de correo")
    return data


def preview(db: Session, content: bytes) -> dict:
    rows = read_rows(content)
    groups: dict[str, dict] = {}
    for line, fields in rows:
        name = " ".join(fields.get("nombre", "").split())
        key = _key(name)
        if not key:
            groups.setdefault("", {"name": "", "lines": [], "errors": ["Fila sin nombre de organización"],
                                   "fields": {}, "contacts": [], "warnings": []})["lines"].append(line)
            continue
        group = groups.setdefault(key, {"name": name, "lines": [], "errors": [], "warnings": [], "fields": {},
                                        "contacts": []})
        group["lines"].append(line)
        for column in ORG_FIELDS:
            value = fields.get(column, "")
            if not value:
                continue
            previous = group["fields"].get(column)
            if previous is None:
                group["fields"][column] = value
            elif previous != value:
                group["errors"].append(f"Fila {line}: «{column}» no coincide con la primera fila de la organización")
        email = fields.get("contacto_correo", "").lower()
        if email:
            group["contacts"].append({"line": line, "email": email, "name": fields.get("contacto_nombre", ""),
                                      "authorized": _key(fields.get("autorizado_cambios", "")) in _YES})

    existing_orgs = {_key(o.name): o for o in db.query(models.Organization)}
    taken = {d.domain: d.organization_id for d in db.query(models.OrganizationDomain)}
    org_names = {o.id: o.name for o in existing_orgs.values()}
    # Un dominio en dos organizaciones del archivo: error en las dos (el administrador decide de cuál es)
    owners: dict[str, list[str]] = {}
    for key, group in groups.items():
        for domain in dict.fromkeys(_split_domains(group["fields"].get("dominios", ""))):
            owners.setdefault(domain, []).append(group["name"])
    result = []
    for key, group in groups.items():
        errors, warnings = group["errors"], group["warnings"]
        existing = existing_orgs.get(key) if key else None
        if existing is not None and existing.kind != "client":
            errors.append("Ese nombre es de la organización interna de ADN")
        data = _parse_org(group["lines"], group["fields"], errors) if key else {"domains": []}
        for domain in data["domains"]:
            problem = domains.problem(domain)
            if problem:
                errors.append(problem)
            elif len(owners.get(domain, [])) > 1:
                others = ", ".join(f"«{n}»" for n in owners[domain] if n != group["name"])
                errors.append(f"El dominio «{domain}» está repetido (también en {others})")
            elif domain in taken and (existing is None or taken[domain] != existing.id):
                errors.append(f"El dominio «{domain}» ya es de «{org_names.get(taken[domain], 'otra organización')}»")
        contacts = []
        for contact in group["contacts"]:
            email = contact["email"]
            if not _EMAIL_RE.match(email):
                errors.append(f"Fila {contact['line']}: el correo del contacto no es válido")
                continue
            if routing.is_internal_domain(email):
                errors.append(f"Fila {contact['line']}: un correo de ADN no puede ser contacto de un cliente")
                continue
            account = db.query(models.User).filter(func.lower(models.User.email) == email).first()
            if account is not None and account.role != "requester":
                errors.append(f"Fila {contact['line']}: ese correo es de una cuenta del personal")
                continue
            if domains.is_generic(domains.of_email(email)):
                warnings.append(f"Fila {contact['line']}: el contacto usa un correo genérico")
            elif domains.of_email(email) not in data["domains"]:
                warnings.append(f"Fila {contact['line']}: el correo del contacto es de otro dominio")
            if len(" ".join(contact["name"].split())) < 2:
                errors.append(f"Fila {contact['line']}: falta el nombre del contacto")
                continue
            contacts.append(contact)
        result.append({
            "name": group["name"] or "(sin nombre)",
            "lines": group["lines"],
            "status": "error" if errors else ("update" if existing is not None else "new"),
            "errors": errors,
            "warnings": warnings,
            "domains": data["domains"],
            "contacts": len(contacts),
            "_data": data,
            "_contacts": contacts,
        })
    counts = {s: sum(1 for r in result if r["status"] == s) for s in ("new", "update", "error")}
    return {"organizations": result, "counts": counts}


def public(preview_data: dict) -> dict:
    """La vista previa sin los datos internos (los contactos no se devuelven: van con su correo completo)."""
    return {
        "organizations": [{k: v for k, v in r.items() if not k.startswith("_")} for r in preview_data["organizations"]],
        "counts": preview_data["counts"],
    }


def confirm(db: Session, content: bytes, actor: models.User) -> dict:
    """Importa las organizaciones sin errores (las que tienen errores se saltan). No hace commit."""
    from . import audit

    data = preview(db, content)
    totals = {"created": 0, "updated": 0, "contacts_created": 0, "contacts_linked": 0, "skipped": 0}
    for row in data["organizations"]:
        if row["status"] == "error":
            totals["skipped"] += 1
            continue
        values = row["_data"]
        # Mismo nombre sin importar tildes ni mayúsculas: se actualiza
        org = next((o for o in db.query(models.Organization).filter(models.Organization.kind == "client")
                    if _key(o.name) == _key(row["name"])), None)
        if org is None:
            org = models.Organization(name=row["name"], kind="client")
            db.add(org)
            totals["created"] += 1
        else:
            totals["updated"] += 1
        for field in ("legal_id", "contract_number", "contract_start", "contract_end", "main_phone", "data_clause"):
            setattr(org, field, values[field])
        db.flush()
        current = {d.domain for d in org.domains}
        for domain in values["domains"]:
            if domain not in current:
                db.add(models.OrganizationDomain(organization_id=org.id, domain=domain))
        db.flush()
        for contact in row["_contacts"]:
            user, outcome = clients.add_contact(db, org, contact["email"], contact["name"], contact["authorized"])
            if outcome == "created":
                totals["contacts_created"] += 1
            elif outcome == "linked":
                totals["contacts_linked"] += 1
        audit.record(db, actor.id, "organization_imported", target_label=org.name[:150],
                     new=f"{len(values['domains'])} dominio(s), {len(row['_contacts'])} contacto(s)")
    # Clientes que ya estaban registrados con esos dominios
    clients.link_existing_clients(db)
    return totals
