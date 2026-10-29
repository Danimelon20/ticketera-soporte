"""Organizaciones cliente, sus dominios, contactos y contrato; correos no reconocidos (Fase 2).

El administrador lo gestiona todo. El coordinador consulta las organizaciones (solo lectura) y el técnico
solo las busca (sin cédula jurídica, teléfono ni contrato). Los contactos nunca muestran cédula ni teléfono.
"""
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from .. import attachments, audit, clients, domains, models, org_import, privacy, routing
from ..departments import ImportError_
from ..schemas import (
    ClientOrganizationUpdate, ClientWithoutOrganization, ContactCreate, ContactRead, ContactUpdate,
    OrganizationCreate, OrganizationRead, OrganizationSummary, OrganizationWrite, UnrecognizedLink,
    UnrecognizedList, UnrecognizedRead,
)
from ..security import get_current_user, mask_email
from .admin import require_admin

router = APIRouter(prefix="/api", tags=["organizations"])

MAX_IMPORT_BYTES = 1_000_000
NOT_FOUND = "La organización no existe"
NAME_TAKEN = "Ya existe una organización con ese nombre"
DOMAIN_TAKEN = "El dominio «{domain}» ya es de «{org}»"
PDF_ONLY = "El contrato debe ser un PDF"
READ_ROLES = ("coordinator", "technician", "admin")


def _domains(org: models.Organization) -> list[str]:
    return sorted(d.domain for d in org.domains)


def _summary(org: models.Organization) -> OrganizationSummary:
    status = clients.contract_status(org)
    return OrganizationSummary(id=org.id, name=org.name, kind=org.kind, status=status,
                               status_label=clients.STATUS_LABELS[status], domains=_domains(org))


def _read(db: Session, org: models.Organization) -> OrganizationRead:
    profiles = db.query(models.RequesterProfile).filter(models.RequesterProfile.organization_id == org.id)
    contract = db.query(models.OrganizationContract.id).filter(
        models.OrganizationContract.organization_id == org.id).first()
    return OrganizationRead(
        **_summary(org).model_dump(), legal_id=org.legal_id, contract_number=org.contract_number,
        contract_start=org.contract_start, contract_end=org.contract_end, main_phone=org.main_phone,
        data_clause=org.data_clause, has_contract_file=contract is not None, contacts=profiles.count(),
        authorized_contacts=profiles.filter(models.RequesterProfile.authorized_for_changes.is_(True)).count(),
    )


def _client_org(db: Session, org_id: int) -> models.Organization:
    org = db.get(models.Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if org.kind != "client":
        raise HTTPException(status_code=400, detail="La organización interna de ADN se configura con INTERNAL_DOMAINS")
    return org


def _check_name(db: Session, name: str, org_id: int | None = None) -> None:
    clash = db.query(models.Organization).filter(func.lower(models.Organization.name) == name.lower())
    if org_id is not None:
        clash = clash.filter(models.Organization.id != org_id)
    if clash.first() is not None:
        raise HTTPException(status_code=409, detail=NAME_TAKEN)


def _set_domains(db: Session, org: models.Organization, values: list[str]) -> None:
    wanted = []
    for value in values:
        domain = domains.normalize(value)
        problem = domains.problem(domain)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        row = db.query(models.OrganizationDomain).filter(models.OrganizationDomain.domain == domain).first()
        if row is not None and row.organization_id != org.id:
            other = db.get(models.Organization, row.organization_id)
            raise HTTPException(status_code=409, detail=DOMAIN_TAKEN.format(domain=domain, org=other.name))
        if domain not in wanted:
            wanted.append(domain)
    for row in list(org.domains):
        if row.domain not in wanted:
            org.domains.remove(row)
    current = {d.domain for d in org.domains}
    for domain in wanted:
        if domain not in current:
            org.domains.append(models.OrganizationDomain(domain=domain))


def _apply(db: Session, org: models.Organization, data: OrganizationWrite) -> list[str]:
    """Aplica lo que viene en la petición y devuelve qué cambió (para el registro)."""
    values = data.model_dump(exclude_unset=True)
    changed = []
    if "name" in values and values["name"] and values["name"] != org.name:
        _check_name(db, values["name"], org.id)
        org.name = values["name"]
        changed.append("nombre")
    for field, label in (("legal_id", "cédula jurídica"), ("contract_number", "contrato"),
                         ("contract_start", "inicio"), ("contract_end", "fin"), ("main_phone", "teléfono"),
                         ("data_clause", "cláusula de datos")):
        if field in values and values[field] != getattr(org, field):
            setattr(org, field, values[field] if field != "data_clause" else bool(values[field]))
            changed.append(label)
    if org.contract_start and org.contract_end and org.contract_end < org.contract_start:
        raise HTTPException(status_code=400, detail="El contrato termina antes de empezar")
    if "domains" in values and values["domains"] is not None:
        before = _domains(org)
        _set_domains(db, org, values["domains"])
        db.flush()
        if _domains(org) != before:
            changed.append("dominios")
    return changed


# ----- Consulta (personal) -----

# Sin response_model: el técnico recibe el resumen y los demás la ficha (cada lista ya viene del esquema)
@router.get("/organizations", response_model=None)
def list_organizations(q: Optional[str] = None, db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    """Coordinador y administrador: ficha completa (solo lectura). Técnico: búsqueda con lo mínimo."""
    if current_user.role not in READ_ROLES:
        raise HTTPException(status_code=403, detail="No tiene permiso para ver las organizaciones")
    query = db.query(models.Organization).filter(models.Organization.kind == "client").order_by(models.Organization.name)
    orgs = query.all()
    if q and q.strip():
        needle = org_import._key(q)
        orgs = [o for o in orgs if needle in org_import._key(o.name) or any(needle in d.domain for d in o.domains)]
    if current_user.role == "technician":
        return [_summary(o) for o in orgs]
    return [_read(db, o) for o in orgs]


# ----- Administración -----

@router.get("/admin/organizations", response_model=list[OrganizationRead])
def admin_list(db: Session = Depends(get_db), admin=Depends(require_admin)):
    orgs = db.query(models.Organization).filter(models.Organization.kind == "client").order_by(models.Organization.name)
    return [_read(db, o) for o in orgs]


@router.post("/admin/organizations", response_model=OrganizationRead, status_code=201)
def admin_create(data: OrganizationCreate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    _check_name(db, data.name)
    org = models.Organization(name=data.name, kind="client")
    db.add(org)
    db.flush()
    _apply(db, org, data)
    audit.record(db, admin.id, "organization_changed", target_label=org.name[:150], new="Creada")
    # Clientes ya registrados con esos dominios
    clients.link_existing_clients(db)
    db.commit()
    return _read(db, org)


@router.patch("/admin/organizations/{org_id}", response_model=OrganizationRead)
def admin_update(org_id: int, data: OrganizationWrite, db: Session = Depends(get_db), admin=Depends(require_admin)):
    org = _client_org(db, org_id)
    changed = _apply(db, org, data)
    if changed:
        audit.record(db, admin.id, "organization_changed", target_label=org.name[:150], new=", ".join(changed)[:150])
    clients.link_existing_clients(db)
    db.commit()
    return _read(db, org)


@router.post("/admin/organizations/{org_id}/contract", response_model=OrganizationRead)
async def upload_contract(org_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
                          admin=Depends(require_admin)):
    org = _client_org(db, org_id)
    data = await file.read(attachments.MAX_BYTES + 1)
    try:
        content_type = attachments.validate(file.filename or "contrato.pdf", data)
    except attachments.Rejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if content_type != "application/pdf":
        raise HTTPException(status_code=400, detail=PDF_ONLY)
    stored_name, sha256 = attachments.store_file(data)
    row = db.query(models.OrganizationContract).filter(models.OrganizationContract.organization_id == org.id).first()
    old_file = row.stored_name if row is not None else None
    if row is None:
        row = models.OrganizationContract(organization_id=org.id)
        db.add(row)
    row.original_name = attachments.clean_name(file.filename or "contrato.pdf")
    row.stored_name, row.sha256 = stored_name, sha256
    row.content_type, row.size, row.uploaded_by = content_type, len(data), admin.id
    db.flush()
    org.contract_attachment_id = row.id
    audit.record(db, admin.id, "organization_changed", target_label=org.name[:150], new="Contrato (PDF)")
    db.commit()
    if old_file:
        attachments.remove_files([old_file])
    return _read(db, org)


@router.get("/admin/organizations/{org_id}/contract")
def download_contract(org_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    org = _client_org(db, org_id)
    row = db.query(models.OrganizationContract).filter(models.OrganizationContract.organization_id == org.id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Esta organización no tiene el contrato en PDF")
    return attachments.file_response(row)


# ----- Contactos -----

def _contact_read(db: Session, user: models.User, row: models.RequesterProfile) -> ContactRead:
    return ContactRead(id=user.id, name=user.name or "Sin nombre", email_hint=mask_email(user.email),
                       authorized_for_changes=row.authorized_for_changes, activation=row.activation,
                       privacy_pending=_privacy_pending(db, user))


def _privacy_pending(db: Session, user: models.User) -> bool:
    """Aviso de clientes sin aceptar (aunque el interruptor esté apagado: la ficha lo muestra igual)."""
    notice = privacy.read_notice("external")
    if notice is None:
        return False
    version = notice[0]
    return db.query(models.PrivacyAcceptance.id).filter(
        models.PrivacyAcceptance.user_id == user.id, models.PrivacyAcceptance.notice == "external",
        models.PrivacyAcceptance.version == version,
    ).first() is None


@router.get("/admin/organizations/{org_id}/contacts", response_model=list[ContactRead])
def list_contacts(org_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    org = _client_org(db, org_id)
    rows = (
        db.query(models.User, models.RequesterProfile)
        .join(models.RequesterProfile, models.RequesterProfile.user_id == models.User.id)
        .filter(models.RequesterProfile.organization_id == org.id)
        .order_by(models.User.name, models.User.id)
    )
    return [_contact_read(db, user, row) for user, row in rows]


@router.post("/admin/organizations/{org_id}/contacts", response_model=ContactRead, status_code=201)
def add_contact(org_id: int, data: ContactCreate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    org = _client_org(db, org_id)
    email = data.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=422, detail="Escriba un correo válido")
    user, outcome = clients.add_contact(db, org, email, data.name, data.authorized_for_changes)
    if user is None:
        raise HTTPException(status_code=400, detail=outcome)
    audit.record(db, admin.id, "client_linked", target=user, target_label=org.name[:150], new=mask_email(email))
    db.commit()
    return _contact_read(db, user, clients.profile(db, user.id))


@router.patch("/admin/organizations/{org_id}/contacts/{user_id}", response_model=ContactRead)
def update_contact(org_id: int, user_id: int, data: ContactUpdate, db: Session = Depends(get_db),
                   admin=Depends(require_admin)):
    org = _client_org(db, org_id)
    row = clients.profile(db, user_id)
    user = db.get(models.User, user_id)
    if row is None or user is None or row.organization_id != org.id:
        raise HTTPException(status_code=404, detail="Ese contacto no es de esta organización")
    if row.authorized_for_changes != data.authorized_for_changes:
        row.authorized_for_changes = data.authorized_for_changes
        audit.record(db, admin.id, "organization_changed", target=user, target_label=org.name[:150],
                     new="Autorizado para cambios" if data.authorized_for_changes else "Sin autorización para cambios")
    db.commit()
    return _contact_read(db, user, row)


# ----- Importación -----

@router.get("/admin/organizations/template.csv")
def import_template(admin=Depends(require_admin)):
    return Response(content=org_import.TEMPLATE.encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="plantilla-organizaciones.csv"'})


async def _read_upload(file: UploadFile) -> bytes:
    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=400, detail="El archivo pesa más de 1 MB")
    return content


@router.post("/admin/organizations/import/preview")
async def import_preview(file: UploadFile = File(...), db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Solo en memoria: no guarda nada."""
    try:
        return org_import.public(org_import.preview(db, await _read_upload(file)))
    except ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/admin/organizations/import/confirm")
async def import_confirm(file: UploadFile = File(...), db: Session = Depends(get_db), admin=Depends(require_admin)):
    """El mismo archivo de la vista previa: importa las organizaciones sin errores."""
    try:
        totals = org_import.confirm(db, await _read_upload(file), admin)
    except ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return totals


# ----- No reconocidos y clientes sin organización -----

@router.get("/admin/unrecognized", response_model=UnrecognizedList)
def list_unrecognized(db: Session = Depends(get_db), admin=Depends(require_admin)):
    rows = db.query(models.UnrecognizedContact).order_by(models.UnrecognizedContact.last_seen.desc())
    return UnrecognizedList(
        contacts=[UnrecognizedRead(id=r.id, email=r.email, channel=r.channel, first_seen=r.first_seen,
                                   last_seen=r.last_seen, attempts=r.attempts,
                                   expires_on=(r.first_seen + timedelta(days=clients.UNRECOGNIZED_DAYS)).date())
                  for r in rows],
        clients_without_organization=[ClientWithoutOrganization(id=u.id, name=u.name or "Sin nombre", email=u.email)
                                      for u in clients.clients_without_organization(db)],
    )


@router.post("/admin/unrecognized/{contact_id}/link", response_model=ContactRead)
def link_unrecognized(contact_id: int, data: UnrecognizedLink, db: Session = Depends(get_db),
                      admin=Depends(require_admin)):
    row = db.get(models.UnrecognizedContact, contact_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Ese correo ya no está en la lista")
    org = _client_org(db, data.organization_id)
    email = row.email
    if data.add_domain:
        _set_domains(db, org, _domains(org) + [domains.of_email(email)])
        db.flush()
    user, outcome = clients.add_contact(db, org, email, data.name, data.authorized_for_changes)
    if user is None:
        raise HTTPException(status_code=400, detail=outcome)
    # El registro de cambios guarda el correo enmascarado; la fila con el correo completo se borra
    audit.record(db, admin.id, "unrecognized_linked", target=user, target_label=org.name[:150], new=mask_email(email))
    db.commit()
    return _contact_read(db, user, clients.profile(db, user.id))


@router.delete("/admin/unrecognized/{contact_id}", status_code=204)
def delete_unrecognized(contact_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    db.query(models.UnrecognizedContact).filter(models.UnrecognizedContact.id == contact_id).delete()
    db.commit()


@router.put("/admin/clients/{user_id}/organization", response_model=ContactRead)
def link_client(user_id: int, data: ClientOrganizationUpdate, db: Session = Depends(get_db),
                admin=Depends(require_admin)):
    """Cliente ya registrado sin organización: se liga a una."""
    user = db.get(models.User, user_id)
    if user is None or user.role != "requester" or routing.is_internal_domain(user.email):
        raise HTTPException(status_code=404, detail="Ese cliente no existe")
    org = _client_org(db, data.organization_id)
    row = clients.link_user(db, user, org)
    clients.forget_unrecognized(db, user.email)
    audit.record(db, admin.id, "client_linked", target=user, target_label=org.name[:150], new=mask_email(user.email))
    db.commit()
    return _contact_read(db, user, row)
