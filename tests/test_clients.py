"""Fase 2: organizaciones cliente, dominios, registro y correo según el contrato, no reconocidos y aviso de datos."""
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid

import pytest

from app import clients, config, domains, models, privacy, routing
from app.database import SessionLocal
from app.email_processing import process_message
from conftest import code_from
from org_helpers import link, organization, unique, unique_domain

MAILBOX = "soporte@empresa.test"
PASSWORD = "clave-segura-123"
TEMPLATE = "Categoría: Software\nPrioridad: Alta\nDescripción: No abre el sistema de planillas."


@pytest.fixture(autouse=True)
def mailbox(monkeypatch):
    monkeypatch.setattr(config, "SUPPORT_MAILBOX", MAILBOX)
    # Sin la IA: los correos con plantilla crean el ticket al momento
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", False)
    monkeypatch.setattr(config, "GROUPING_ENABLED", False)


def _db():
    return SessionLocal()


def _set_email(user_id, email):
    db = _db()
    try:
        db.get(models.User, user_id).email = email
        db.commit()
    finally:
        db.close()


def _process(sender, body=TEMPLATE, subject="[Soporte] Planillas"):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Message-ID"] = sender, MAILBOX, subject, make_msgid()
    msg.set_content(body)
    db = _db()
    try:
        return process_message(db, msg)
    finally:
        db.close()


def _tickets_of(user_id):
    db = _db()
    try:
        return db.query(models.Ticket).filter(models.Ticket.created_by == user_id).count()
    finally:
        db.close()


def _unrecognized(email):
    db = _db()
    try:
        return db.query(models.UnrecognizedContact).filter(models.UnrecognizedContact.email == email).first()
    finally:
        db.close()


def _profile(user_id):
    db = _db()
    try:
        return db.query(models.RequesterProfile).filter(models.RequesterProfile.user_id == user_id).first()
    finally:
        db.close()


# ----- Dominios -----

def test_generic_internal_and_invalid_domains_are_rejected(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_DOMAINS", ("adn-pruebas.example",))
    assert domains.problem("gmail.com") and "genérico" in domains.problem("gmail.com")
    assert "ADN" in domains.problem("adn-pruebas.example")
    assert "no es válido" in domains.problem("sin punto")
    assert domains.problem(domains.normalize(" @WWW.Cliente-Real.CR ")) is None


# ----- Administración -----

def test_admin_creates_organization_with_domains(client, make_user):
    admin = make_user("admin")
    domain = unique_domain()
    payload = {"name": unique("Cliente S.A."), "legal_id": "3-101-123456", "contract_number": "CT-1",
               "contract_start": str(date.today() - timedelta(days=1)), "contract_end": str(date.today() + timedelta(days=30)),
               "main_phone": "2222-3333", "data_clause": True, "domains": [domain.upper()]}
    response = client.post("/api/admin/organizations", headers=admin["headers"], json=payload)
    assert response.status_code == 201, response.text
    org = response.json()
    assert org["domains"] == [domain] and org["status"] == "current" and org["legal_id"] == "3101123456"

    # Mismo dominio en otra organización, genérico, nombre repetido
    other = {"name": unique("Otro"), "domains": [domain]}
    assert client.post("/api/admin/organizations", headers=admin["headers"], json=other).status_code == 409
    assert client.post("/api/admin/organizations", headers=admin["headers"],
                       json={"name": unique("Otro"), "domains": ["gmail.com"]}).status_code == 400
    assert client.post("/api/admin/organizations", headers=admin["headers"],
                       json={"name": payload["name"].lower()}).status_code == 409

    expired = client.patch(f"/api/admin/organizations/{org['id']}", headers=admin["headers"],
                           json={"contract_end": str(date.today() - timedelta(days=1))})
    assert expired.json()["status"] == "expired"


def test_organizations_permissions(client, make_user):
    admin = make_user("admin")
    org_id = organization(domains=[unique_domain()])
    client.patch(f"/api/admin/organizations/{org_id}", headers=admin["headers"],
                 json={"legal_id": "3101999999", "main_phone": "22224444"})
    for role in ("coordinator", "technician", "requester"):
        user = make_user(role)
        assert client.get("/api/admin/organizations", headers=user["headers"]).status_code == 403
        assert client.post("/api/admin/organizations", headers=user["headers"], json={"name": "X X"}).status_code == 403
        assert client.get("/api/admin/unrecognized", headers=user["headers"]).status_code == 403
    coordinator = client.get("/api/organizations", headers=make_user("coordinator")["headers"]).json()
    row = next(o for o in coordinator if o["id"] == org_id)
    assert row["legal_id"] == "3101999999"
    # El técnico solo busca: sin cédula jurídica ni teléfono
    technician = client.get("/api/organizations", headers=make_user("technician")["headers"]).json()
    row = next(o for o in technician if o["id"] == org_id)
    assert "legal_id" not in row and "main_phone" not in row
    assert client.get("/api/organizations", headers=make_user("requester")["headers"]).status_code == 403


def test_contract_pdf_only(client, make_user):
    admin = make_user("admin")
    org_id = organization()
    pdf = b"%PDF-1.4\n%prueba\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    response = client.post(f"/api/admin/organizations/{org_id}/contract", headers=admin["headers"],
                           files={"file": ("contrato.pdf", pdf, "application/pdf")})
    assert response.status_code == 200, response.text
    assert response.json()["has_contract_file"] is True
    assert client.get(f"/api/admin/organizations/{org_id}/contract", headers=admin["headers"]).content == pdf
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    assert client.post(f"/api/admin/organizations/{org_id}/contract", headers=admin["headers"],
                       files={"file": ("foto.png", png, "image/png")}).status_code == 400
    coordinator = make_user("coordinator")
    assert client.get(f"/api/admin/organizations/{org_id}/contract", headers=coordinator["headers"]).status_code == 403


def test_contacts_pending_and_authorized(client, make_user):
    admin = make_user("admin")
    domain = unique_domain()
    org_id = organization(domains=[domain])
    email = f"ana@{domain}"
    response = client.post(f"/api/admin/organizations/{org_id}/contacts", headers=admin["headers"],
                           json={"email": email, "name": "Ana Pérez", "authorized_for_changes": True})
    assert response.status_code == 201, response.text
    contact = response.json()
    assert contact["activation"] == "pending" and contact["authorized_for_changes"] is True
    assert contact["privacy_pending"] is True and email not in response.text
    updated = client.patch(f"/api/admin/organizations/{org_id}/contacts/{contact['id']}", headers=admin["headers"],
                           json={"authorized_for_changes": False})
    assert updated.json()["authorized_for_changes"] is False
    listed = client.get(f"/api/admin/organizations/{org_id}/contacts", headers=admin["headers"]).json()
    assert [c["id"] for c in listed] == [contact["id"]]
    # Nunca cédula ni teléfono
    assert "phone" not in listed[0] and "id_number" not in listed[0]
    staff = make_user("technician")
    assert client.post(f"/api/admin/organizations/{org_id}/contacts", headers=admin["headers"],
                       json={"email": staff["email"], "name": "Técnico"}).status_code == 400


# ----- Registro -----

def _register(client, email):
    return client.post("/api/auth/register", json={"email": email, "password": PASSWORD})


def _verify(client, email, sent):
    return client.post("/api/auth/register/verify", json={"email": email, "code": code_from(sent[-1][2])})


def test_switch_off_anyone_registers(client, sent):
    email = f"cualquiera-{uuid.uuid4().hex[:6]}@gmail.com"
    assert _register(client, email).status_code == 202
    assert _unrecognized(email) is None


def test_register_by_domain_with_switch_on(client, sent, clients_on):
    domain = unique_domain()
    org_id = organization(domains=[domain])
    email = f"luis-{uuid.uuid4().hex[:6]}@{domain}"
    assert _register(client, email).status_code == 202
    response = _verify(client, email, sent)
    assert response.status_code == 200, response.text
    db = _db()
    try:
        user = db.query(models.User).filter(models.User.email == email).one()
        assert clients.organization_of(db, user).id == org_id
    finally:
        db.close()

    for email in (f"x-{uuid.uuid4().hex[:6]}@gmail.com", f"y-{uuid.uuid4().hex[:6]}@{unique_domain('ajeno')}"):
        response = _register(client, email)
        assert response.status_code == 403
        assert response.json()["detail"] == clients.NOT_CLIENT
        # Correo completo, para que el administrador lo pueda ligar
        assert _unrecognized(email).channel == "register"


def test_expired_organization_cannot_register(client, sent, clients_on):
    domain = unique_domain()
    organization(domains=[domain], start=date.today() - timedelta(days=400), end=date.today() - timedelta(days=1))
    assert _register(client, f"z@{domain}").status_code == 403


def test_pending_contact_activates_by_registering(client, make_user, sent, clients_on):
    admin = make_user("admin")
    domain = unique_domain()
    org_id = organization(domains=[domain])
    email = f"maria-{uuid.uuid4().hex[:6]}@{domain}"
    contact = client.post(f"/api/admin/organizations/{org_id}/contacts", headers=admin["headers"],
                          json={"email": email, "name": "María Mora", "authorized_for_changes": True}).json()
    assert _register(client, email).status_code == 202
    response = _verify(client, email, sent)
    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == contact["id"]
    row = _profile(contact["id"])
    assert row.activation == "active" and row.organization_id == org_id and row.authorized_for_changes is True


# ----- Correo entrante -----

def test_unrecognized_email_creates_nothing(client, make_user, sent, clients_on):
    sender = f"desconocido-{uuid.uuid4().hex[:6]}@{unique_domain('ajeno')}"
    assert _process(sender) == "not_client"
    assert sent[-1][0] == sender and clients.NOT_CLIENT in sent[-1][2]
    assert _unrecognized(sender).channel == "email"
    # Cliente registrado sin organización: tampoco
    requester = make_user("requester")
    assert _process(requester["email"]) == "not_client"
    assert _tickets_of(requester["id"]) == 0


def test_expired_contract_blocks_new_tickets_but_not_existing(client, make_user, sent, clients_on):
    requester = make_user("requester")
    org_id = organization(start=date.today() - timedelta(days=400), end=date.today() - timedelta(days=1))
    link(requester["id"], org_id)
    assert _process(requester["email"]) == "not_client"
    assert clients.CONTRACT_NOT_CURRENT in sent[-1][2]
    payload = {"title": "Sin internet", "description": "No hay conexión", "category_id": 1, "priority_id": 1}
    privacy_ok(client, requester)
    response = client.post("/api/tickets", headers=requester["headers"], json=payload)
    assert response.status_code == 403 and response.json()["detail"] == clients.CONTRACT_NOT_CURRENT

    # Un ticket que ya tenía sigue en proceso: puede comentar
    db = _db()
    try:
        ticket = models.Ticket(title="Anterior", description="Desde antes", category_id=1, priority_id=1,
                               created_by=requester["id"], source="web")
        db.add(ticket)
        db.commit()
        ticket_id = ticket.id
    finally:
        db.close()
    assert client.post(f"/api/tickets/{ticket_id}/comments", headers=requester["headers"],
                       json={"content": "Sigue igual"}).status_code == 200


def privacy_ok(client, user):
    notice = client.get("/api/privacy/notice", headers=user["headers"]).json()
    if notice:
        client.post("/api/privacy/accept", headers=user["headers"], json={"version": notice["version"], "accepted": True})


def test_contact_email_ticket_carries_one_time_privacy_link(client, make_user, sent, clients_on):
    domain = unique_domain()
    org_id = organization(domains=[domain])
    requester = make_user("requester")
    email = f"contacto-{uuid.uuid4().hex[:6]}@{domain}"
    _set_email(requester["id"], email)
    link(requester["id"], org_id)
    assert _process(email) == "created"
    body = next(b for to, subject, b in sent if to == email and "Recibimos su solicitud" in subject)
    assert "Ley 8968" in body
    token = re.search(r"#aviso/([\w\-]+)", body).group(1)
    # El token solo con hash
    db = _db()
    try:
        assert db.query(models.PrivacyEmailToken).filter(models.PrivacyEmailToken.token_hash == token).first() is None
    finally:
        db.close()

    notice = client.post("/api/privacy/email-notice", json={"token": token})
    assert notice.status_code == 200 and notice.json()["kind"] == "external"
    assert "## Fines" in notice.json()["text"]
    version = notice.json()["version"]
    assert client.post("/api/privacy/email-accept",
                       json={"token": token, "version": version, "accepted": True}).status_code == 204
    # Un solo uso
    assert client.post("/api/privacy/email-accept",
                       json={"token": token, "version": version, "accepted": True}).status_code == 400
    assert client.post("/api/privacy/email-notice", json={"token": token}).status_code == 400
    db = _db()
    try:
        row = db.query(models.PrivacyAcceptance).filter(models.PrivacyAcceptance.user_id == requester["id"]).one()
        assert row.via == "email_link" and row.notice == "external"
    finally:
        db.close()
    # Ya aceptado: el siguiente correo no lleva el aviso
    assert _process(email) == "created"
    assert "Ley 8968" not in [b for to, s, b in sent if to == email and "Recibimos" in s][-1]


def test_external_notice_required_on_portal_only_with_switch(client, make_user, clients_on):
    requester = make_user("requester")
    link(requester["id"], organization())
    assert client.get("/api/tickets", headers=requester["headers"]).status_code == 403
    notice = client.get("/api/privacy/notice", headers=requester["headers"]).json()
    assert notice["kind"] == "external"
    privacy_ok(client, requester)
    assert client.get("/api/tickets", headers=requester["headers"]).status_code == 200


def test_external_notice_has_article_5_structure():
    version, text = privacy.read_notice("external")
    for heading in ("Base de datos", "Fines", "Destinatarios", "Datos obligatorios", "Consecuencia de negarse",
                    "Sus derechos", "Responsable y contacto"):
        assert f"## {heading}" in text
    assert "[PENDIENTE: texto oficial de American Data Networks" in text and version


# ----- No reconocidos -----

def test_unrecognized_list_link_and_purge(client, make_user, sent, clients_on):
    admin = make_user("admin")
    domain = unique_domain("nuevo")
    email = f"pedro-{uuid.uuid4().hex[:6]}@{domain}"
    _register(client, email)
    data = client.get("/api/admin/unrecognized", headers=admin["headers"]).json()
    row = next(c for c in data["contacts"] if c["email"] == email)
    assert row["expires_on"] == str((datetime.now(timezone.utc) + timedelta(days=30)).date())

    org_id = organization()
    response = client.post(f"/api/admin/unrecognized/{row['id']}/link", headers=admin["headers"],
                           json={"organization_id": org_id, "name": "Pedro Solís", "add_domain": True})
    assert response.status_code == 200, response.text
    assert response.json()["activation"] == "pending"
    assert _unrecognized(email) is None
    orgs = {o["id"]: o for o in client.get("/api/admin/organizations", headers=admin["headers"]).json()}
    assert domain in orgs[org_id]["domains"]
    # Ahora sí se puede registrar
    assert _register(client, email).status_code == 202

    old = f"viejo-{uuid.uuid4().hex[:6]}@{unique_domain('ajeno')}"
    db = _db()
    try:
        clients.record_unrecognized(db, old, "email")
        db.commit()
        row = db.query(models.UnrecognizedContact).filter(models.UnrecognizedContact.email == old).one()
        row.first_seen = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=31)
        db.commit()
        assert clients.purge_unrecognized(db) >= 1
    finally:
        db.close()
    assert _unrecognized(old) is None


def test_link_registered_client_without_organization(client, make_user):
    admin = make_user("admin")
    requester = make_user("requester", "Cliente suelto")
    listed = client.get("/api/admin/unrecognized", headers=admin["headers"]).json()["clients_without_organization"]
    assert requester["id"] in {c["id"] for c in listed}
    org_id = organization()
    assert client.put(f"/api/admin/clients/{requester['id']}/organization", headers=admin["headers"],
                      json={"organization_id": org_id}).status_code == 200
    assert _profile(requester["id"]).organization_id == org_id


def test_migration_links_existing_clients_by_domain(make_user):
    domain = unique_domain()
    requester = make_user("requester")
    _set_email(requester["id"], f"viejo@{domain}")
    org_id = organization(domains=[domain])
    db = _db()
    try:
        assert clients.link_existing_clients(db) >= 1
        db.commit()
    finally:
        db.close()
    assert _profile(requester["id"]).organization_id == org_id


# ----- Importación -----

def _csv(*rows):
    header = "nombre;cedula_juridica;contrato;inicio;fin;telefono;dominios;clausula_datos;contacto_nombre;contacto_correo;autorizado_cambios"
    return ("\n".join([header, *rows]) + "\n").encode("utf-8")


def test_import_preview_flags_errors_and_confirm_skips_them(client, make_user, monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_DOMAINS", ("adn-pruebas.example",))
    admin = make_user("admin")
    good, other, repeated = unique("Buena"), unique("Otra"), unique_domain("repetido")
    good_domain = unique_domain("buena")
    content = _csv(
        f"{good};3101123456;CT-9;2026-01-01;2030-12-31;22223333;{good_domain};si;Ana Pérez;ana@{good_domain};si",
        f"{good};;;;;;;;Luis Mora;luis@{unique_domain('ajeno')};no",
        f"{other};;CT-10;01/01/2026;31/12/2030;;{repeated};no;;;",
        f"{unique('Repite')};;CT-11;2026-01-01;2030-12-31;;{repeated};no;;;",
        f"{unique('Generica')};;CT-12;2026-01-01;2030-12-31;;gmail.com;no;;;",
        f"{unique('Interna')};;CT-13;2026-01-01;2030-12-31;;adn-pruebas.example;no;;;",
        f"{unique('SinContrato')};;;;;;{unique_domain('sin')};no;;;",
    )
    files = {"file": ("organizaciones.csv", content, "text/csv")}
    preview = client.post("/api/admin/organizations/import/preview", headers=admin["headers"], files=files)
    assert preview.status_code == 200, preview.text
    rows = {r["name"]: r for r in preview.json()["organizations"]}
    assert rows[good]["status"] == "new" and rows[good]["contacts"] == 2
    assert any("otro dominio" in w for w in rows[good]["warnings"])
    # Dominio repetido: las dos organizaciones quedan con error
    assert rows[other]["status"] == "error"
    by_problem = [r for name, r in rows.items() if name != good]
    assert all(r["status"] == "error" for r in by_problem)
    text = " ".join(e for r in by_problem for e in r["errors"])
    assert "repetido" in text and "genérico" in text and "ADN" in text and "número de contrato" in text
    # Nada guardado todavía
    names = {o["name"] for o in client.get("/api/admin/organizations", headers=admin["headers"]).json()}
    assert good not in names

    result = client.post("/api/admin/organizations/import/confirm", headers=admin["headers"], files=files).json()
    assert result["created"] == 1 and result["skipped"] == 5 and result["contacts_created"] == 2
    orgs = {o["name"]: o for o in client.get("/api/admin/organizations", headers=admin["headers"]).json()}
    assert orgs[good]["status"] == "current" and orgs[good]["authorized_contacts"] == 1
    contacts = client.get(f"/api/admin/organizations/{orgs[good]['id']}/contacts", headers=admin["headers"]).json()
    assert {c["activation"] for c in contacts} == {"pending"}


def test_import_template_and_admin_only(client, make_user):
    admin = make_user("admin")
    template = client.get("/api/admin/organizations/template.csv", headers=admin["headers"])
    assert template.status_code == 200 and "contacto_correo" in template.text
    coordinator = make_user("coordinator")
    assert client.get("/api/admin/organizations/template.csv", headers=coordinator["headers"]).status_code == 403
    assert client.post("/api/admin/organizations/import/preview", headers=coordinator["headers"],
                       files={"file": ("x.csv", b"nombre\nX", "text/csv")}).status_code == 403


# ----- Activación -----

def test_client_switch_needs_checklist(client, make_user, monkeypatch):
    admin = make_user("admin")
    make_user("requester")  # cliente sin organización
    switches = {s["key"]: s for s in client.get("/api/admin/activation", headers=admin["headers"]).json()}
    switch = switches[routing.CLIENT_DOMAIN_ENFORCED]
    assert switch["available"] is True and switch["enabled"] is False
    item = next(i for i in switch["checklist"] if i["key"] == "clients_without_organization")
    assert item["ok"] is False and item["link"] == "admin/no-reconocidos"
    assert client.put(f"/api/admin/activation/{routing.CLIENT_DOMAIN_ENFORCED}", headers=admin["headers"],
                      json={"enabled": True}).status_code == 409
    monkeypatch.setattr(clients, "checklist", lambda db: [{"key": "x", "label": "Listo", "ok": True, "detail": None}])
    response = client.put(f"/api/admin/activation/{routing.CLIENT_DOMAIN_ENFORCED}", headers=admin["headers"],
                          json={"enabled": True})
    assert response.status_code == 200 and response.json()["enabled"] is True
    client.put(f"/api/admin/activation/{routing.CLIENT_DOMAIN_ENFORCED}", headers=admin["headers"],
               json={"enabled": False})
