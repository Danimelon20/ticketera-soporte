"""Cuentas: filtro por departamento (además de los filtros rápidos y del nombre o correo). Solo ese departamento,
sin subdepartamentos, o «Sin departamento». Lo resuelve el servidor, también para las invitaciones pendientes."""
import hashlib
import uuid
from datetime import datetime, timedelta

from app import models
from app.database import SessionLocal
from org_helpers import coordinate, department, place, unique


def _accounts(client, user, **params):
    return client.get("/api/accounts", params=params, headers=user["headers"])


def _ids(response, kind="user"):
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()["items"] if row["kind"] == kind}


def _invitation(admin, role, department_id=None):
    with SessionLocal() as db:
        now = datetime.now()
        invitation = models.StaffInvitation(
            email=f"{uuid.uuid4().hex[:8]}@invitado.test", name=unique("Invitado"), role=role,
            code_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), expires_at=now + timedelta(days=2),
            sent_at=now, created_by=admin["id"],
        )
        db.add(invitation)
        db.flush()
        if department_id is not None:
            db.add(models.StaffInvitationPlacement(invitation_id=invitation.id, department_id=department_id))
        db.commit()
        return invitation.id


def test_filter_by_department_shows_only_its_staff(client, make_user):
    admin = make_user("admin")
    parent = department(unique("Gerencia"), handles=False)
    redes = department(unique("Redes"), parent_id=parent)
    soporte = department(unique("Soporte"))
    tech_redes, tech_soporte = make_user("technician"), make_user("technician")
    coordinator = make_user("coordinator")
    place(tech_redes["id"], redes)
    place(tech_soporte["id"], soporte)
    coordinate(coordinator["id"], redes)
    loose = make_user("technician")
    client_user = make_user("requester")
    invited_redes = _invitation(admin, "technician", redes)
    invited_soporte = _invitation(admin, "technician", soporte)

    response = _accounts(client, admin, department=str(redes))
    ids = _ids(response)
    assert tech_redes["id"] in ids and coordinator["id"] in ids
    assert tech_soporte["id"] not in ids and loose["id"] not in ids
    # Clientes y administradores no tienen departamento: no aparecen con el filtro puesto
    assert client_user["id"] not in ids and admin["id"] not in ids
    invited = _ids(response, "invitation")
    assert invited_redes in invited and invited_soporte not in invited
    # Los contadores respetan el filtro
    counts = response.json()["counts"]
    assert counts["clients"] == 0 and counts["staff"] == len(ids)
    # Solo ese departamento: el padre no incluye a sus subdepartamentos
    assert tech_redes["id"] not in _ids(_accounts(client, admin, department=str(parent)))
    # Con búsqueda y filtro rápido a la vez
    assert _ids(_accounts(client, admin, department=str(redes), filter="staff", q=tech_redes["name"])) >= {tech_redes["id"]}
    # Las opciones del selector traen la ruta completa
    options = {o["id"]: o["path"] for o in response.json()["departments"]}
    assert redes in options and soporte in options and " / " in options[redes] and parent not in options


def test_without_department(client, make_user):
    admin = make_user("admin")
    dept = department(unique("Con gente"))
    placed, loose = make_user("technician"), make_user("technician")
    place(placed["id"], dept)
    invited_loose = _invitation(admin, "technician")
    invited_placed = _invitation(admin, "technician", dept)
    response = _accounts(client, admin, department="none")
    ids = _ids(response)
    assert loose["id"] in ids and placed["id"] not in ids
    invited = _ids(response, "invitation")
    assert invited_loose in invited and invited_placed not in invited


def test_invalid_department_is_422(client, make_user):
    admin = make_user("admin")
    for value in ("999999", "abc", "-1"):
        response = _accounts(client, admin, department=value)
        assert response.status_code == 422 and response.json()["detail"] == "El departamento no existe"


def test_coordinator_filter_keeps_their_scope(client, make_user):
    """El coordinador ve técnicos y clientes: con el filtro, solo los técnicos de ese departamento."""
    viewer = make_user("coordinator")
    dept = department(unique("Equipo"))
    tech, other_coordinator = make_user("technician"), make_user("coordinator")
    place(tech["id"], dept)
    coordinate(other_coordinator["id"], dept)
    ids = _ids(_accounts(client, viewer, department=str(dept)))
    assert tech["id"] in ids and other_coordinator["id"] not in ids
