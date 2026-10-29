"""Inicio de sesión del personal con Microsoft (Entra ID): flujo de código de autorización con PKCE.

No tiene nada que ver con el buzón (graph.py y ms_login.py): usa su propia instancia de MSAL con caché
solo en memoria, nunca lee ni escribe MS_TOKEN_CACHE y solo pide openid, profile, email y User.Read.
Nunca registrar en logs tokens, códigos, el state, el oid ni correos completos.
"""
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import msal
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import audit, config, mailer, models, routing
from .security import hash_token, mask_email, unusable_password_hash

# MSAL agrega openid y profile por su cuenta; offline_access se excluye. Nunca permisos de correo
SCOPES = ["User.Read", "email"]
EXCLUDED_SCOPES = ["offline_access"]
FLOW_MINUTES = 10
HANDOFF_SECONDS = 60
COOKIE_NAME = "ms_login_browser"
CALLBACK_PATH = "/auth/microsoft/callback"
PURPOSES = ("login", "activate")

# Clave del rechazo (viaja en la URL de vuelta a la página) y texto para el Registro de cambios
REJECT_REASONS = {
    "state": "Solicitud vencida o inválida",
    "failed": "Microsoft no confirmó la identidad",
    "tenant": "Otro inquilino",
    "not_staff": "Correo que no es del personal",
    "requester": "Cuenta de cliente",
    "inactive": "Cuenta desactivada",
    "wrong_portal": "Opción de inicio equivocada",
    "already_linked": "Ya enlazada a otra cuenta Microsoft",
    "invitation": "Invitación no válida",
    "invitation_email": "Correo distinto al de la invitación",
    "not_internal": "Correo que no es de un dominio interno",
    "guest": "Cuenta invitada de otro inquilino",
}


class LoginRejected(Exception):
    def __init__(self, reason: str, portal: str | None = None, email: str | None = None,
                 user: models.User | None = None):
        super().__init__(reason)
        self.reason = reason
        self.portal = portal
        self.email = email
        self.user = user


@dataclass
class Identity:
    oid: str
    email: str
    purpose: str
    portal: str | None
    invitation_id: int | None
    # Cuenta propia del inquilino (no invitada desde otro): solo estas pueden volverse solicitantes internos
    member: bool = True


def _now() -> datetime:
    # La base guarda fechas UTC sin zona horaria
    return datetime.now(timezone.utc).replace(tzinfo=None)


def build_app() -> msal.ConfidentialClientApplication:
    """Instancia nueva en cada llamada: no comparte objetos ni caché con el buzón."""
    return msal.ConfidentialClientApplication(
        config.MS_LOGIN_CLIENT_ID,
        client_credential=config.MS_LOGIN_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{config.MS_LOGIN_TENANT_ID}",
        token_cache=msal.TokenCache(),
        exclude_scopes=EXCLUDED_SCOPES,
    )


def _same(value: str | None, stored_hash: str) -> bool:
    return bool(value) and hmac.compare_digest(hash_token(value), stored_hash)


def _cleanup(db: Session) -> None:
    now = _now()
    db.query(models.MicrosoftLoginFlow).filter(models.MicrosoftLoginFlow.expires_at <= now).delete()
    db.query(models.MicrosoftLoginHandoff).filter(models.MicrosoftLoginHandoff.expires_at <= now).delete()


def start(db: Session, purpose: str, portal: str | None = None, invitation_id: int | None = None) -> tuple[str, str]:
    """Prepara la ida a Microsoft. Devuelve (dirección de Microsoft, valor de la cookie del navegador)."""
    if purpose not in PURPOSES:
        raise ValueError(f"Propósito desconocido: {purpose}")
    flow = build_app().initiate_auth_code_flow(
        SCOPES, redirect_uri=config.MS_LOGIN_REDIRECT_URI, prompt="select_account",
    )
    # La cookie amarra el state a este navegador: nadie puede hacer entrar a otra persona con su cuenta
    browser = secrets.token_urlsafe(32)
    _cleanup(db)
    db.add(models.MicrosoftLoginFlow(
        state_hash=hash_token(flow["state"]),
        browser_hash=hash_token(browser),
        flow_json=json.dumps(flow),
        purpose=purpose,
        portal=portal,
        invitation_id=invitation_id,
        expires_at=_now() + timedelta(minutes=FLOW_MINUTES),
    ))
    db.commit()
    return flow["auth_uri"], browser


def finish(db: Session, params: dict, browser: str | None) -> Identity:
    """Valida la vuelta de Microsoft (state, inquilino y audiencia). El flujo se gasta aunque falle."""
    state = params.get("state") or ""
    row = None
    if state:
        row = db.query(models.MicrosoftLoginFlow).filter(
            models.MicrosoftLoginFlow.state_hash == hash_token(state)
        ).first()
    if row is None or row.expires_at <= _now() or not _same(browser, row.browser_hash):
        raise LoginRejected("state", portal=row.portal if row is not None else None)
    flow = json.loads(row.flow_json)
    purpose, portal, invitation_id = row.purpose, row.portal, row.invitation_id
    db.delete(row)
    db.commit()

    try:
        result = build_app().acquire_token_by_auth_code_flow(flow, params)
    except ValueError:
        # MSAL revisa el state contra el flujo guardado
        raise LoginRejected("state", portal=portal)
    if not isinstance(result, dict) or "error" in result:
        # Solo el código de error de Microsoft (por ejemplo access_denied), nunca la respuesta completa
        logging.warning("Microsoft no confirmó el inicio de sesión: %s", (result or {}).get("error"))
        raise LoginRejected("failed", portal=portal)

    claims = result.get("id_token_claims") or {}
    email = (claims.get("preferred_username") or claims.get("email") or "").strip().lower()
    if claims.get("tid") != config.MS_LOGIN_TENANT_ID:
        raise LoginRejected("tenant", portal=portal, email=email)
    if claims.get("aud") != config.MS_LOGIN_CLIENT_ID or not claims.get("oid"):
        raise LoginRejected("failed", portal=portal, email=email)
    return Identity(oid=claims["oid"], email=email, purpose=purpose, portal=portal, invitation_id=invitation_id,
                    member=is_member(claims))


def is_member(claims: dict) -> bool:
    """Cuenta del propio inquilino. Un invitado (B2B) trae idp con su inquilino de origen, o acct = 1."""
    if claims.get("acct") == 1:
        return False
    idp = claims.get("idp")
    return not idp or config.MS_LOGIN_TENANT_ID in idp


def _find_by_email(db: Session, email: str) -> models.User | None:
    if not email:
        return None
    return db.query(models.User).filter(func.lower(models.User.email) == email).first()


def _link(db: Session, user: models.User, oid: str) -> None:
    db.add(models.UserMicrosoftLink(user_id=user.id, oid=oid, linked_at=_now()))
    audit.record(db, user.id, "microsoft_linked", target=user)


def resolve_user(db: Session, identity: Identity, allowed_roles: tuple) -> models.User:
    """Cuenta del personal que ya existe y está activa. Primer ingreso: se enlaza por correo. No hace commit.

    Opción Solicitante: solo empleados de ADN (solicitantes internos); sin cuenta, se crea al entrar.
    """
    portal, email = identity.portal, identity.email
    link = db.query(models.UserMicrosoftLink).filter(models.UserMicrosoftLink.oid == identity.oid).first()
    user = db.get(models.User, link.user_id) if link is not None else _find_by_email(db, email)
    if portal == "requester":
        return _resolve_internal_requester(db, identity, user, link)
    if user is None or user.role == "agent":
        raise LoginRejected("not_staff", portal=portal, email=email)
    if user.role == "requester":
        raise LoginRejected("requester", portal=portal, user=user)
    if not user.is_active:
        raise LoginRejected("inactive", portal=portal, user=user)
    if user.role not in allowed_roles:
        raise LoginRejected("wrong_portal", portal=portal, user=user)
    if link is None:
        if db.query(models.UserMicrosoftLink).filter(models.UserMicrosoftLink.user_id == user.id).first():
            raise LoginRejected("already_linked", portal=portal, user=user)
        _link(db, user, identity.oid)
    return user


def _resolve_internal_requester(db: Session, identity: Identity, user: models.User | None,
                                link: models.UserMicrosoftLink | None) -> models.User:
    """Mismo inquilino (ya revisado en finish), cuenta miembro y dominio de INTERNAL_DOMAINS. El rol y el
    departamento del personal nunca salen de aquí: solo los da el administrador."""
    portal, email = identity.portal, identity.email
    if not identity.member:
        raise LoginRejected("guest", portal=portal, email=email, user=user)
    if user is None:
        if not routing.is_internal_domain(email):
            raise LoginRejected("not_internal", portal=portal, email=email)
        user = models.User(name="", email=email, role="requester", password_hash=unusable_password_hash())
        db.add(user)
        db.flush()
        routing.link_internal(db, user)
        audit.record(db, user.id, "internal_requester_created", target=user, new=mask_email(email))
        _link(db, user, identity.oid)
        return user
    if user.role == "agent":
        raise LoginRejected("not_staff", portal=portal, email=email)
    if user.role != "requester":
        raise LoginRejected("wrong_portal", portal=portal, user=user)
    if not user.is_active:
        raise LoginRejected("inactive", portal=portal, user=user)
    if routing.requester_kind(db, user) != "internal":
        raise LoginRejected("not_internal", portal=portal, user=user)
    if link is None:
        if db.query(models.UserMicrosoftLink).filter(models.UserMicrosoftLink.user_id == user.id).first():
            raise LoginRejected("already_linked", portal=portal, user=user)
        _link(db, user, identity.oid)
    return user


def activate(db: Session, identity: Identity) -> models.User:
    """Invitación del personal activada con Microsoft: cuenta sin contraseña utilizable y oid enlazado."""
    invitation = db.get(models.StaffInvitation, identity.invitation_id) if identity.invitation_id else None
    if invitation is None or invitation.used_by is not None or invitation.expires_at <= _now():
        raise LoginRejected("invitation", portal="staff", email=identity.email)
    if identity.email != invitation.email.strip().lower():
        raise LoginRejected("invitation_email", portal="staff", email=identity.email)
    if _find_by_email(db, identity.email) is not None:
        raise LoginRejected("invitation", portal="staff", email=identity.email)
    if db.query(models.UserMicrosoftLink).filter(models.UserMicrosoftLink.oid == identity.oid).first():
        raise LoginRejected("already_linked", portal="staff", email=identity.email)

    user = models.User(
        name=invitation.name, email=invitation.email, role=invitation.role, password_hash=unusable_password_hash(),
    )
    db.add(user)
    db.flush()
    invitation.used_by = user.id
    invitation.used_at = _now()
    audit.record(db, user.id, "invitation_used", target=user, new=audit.role_name(user.role))
    routing.apply_invitation_placement(db, invitation, user)
    _link(db, user, identity.oid)
    return user


def record_rejection(db: Session, exc: LoginRejected) -> None:
    """Registro de cambios sin datos sensibles: la cuenta, o el correo enmascarado, y el motivo. Hace commit."""
    db.rollback()
    reason = REJECT_REASONS.get(exc.reason, exc.reason)
    audit.record(
        db, exc.user.id if exc.user is not None else None, "microsoft_login_rejected",
        target=exc.user,
        target_label=None if exc.user is not None else audit.masked_email(exc.email),
        new=reason,
    )
    db.commit()
    logging.info("Ingreso con Microsoft rechazado: %s", reason)


def new_handoff(db: Session, user: models.User, browser: str | None) -> str:
    """Código de un solo uso (60 s) para que la página lo canjee por la sesión. Solo se guarda el hash. Hace commit."""
    code = secrets.token_urlsafe(32)
    db.add(models.MicrosoftLoginHandoff(
        code_hash=hash_token(code),
        user_id=user.id,
        browser_hash=hash_token(browser or ""),
        expires_at=_now() + timedelta(seconds=HANDOFF_SECONDS),
    ))
    db.commit()
    return code


def redeem(db: Session, code: str, browser: str | None) -> models.User | None:
    """Gasta el código. None si no existe, ya se usó, venció, es de otro navegador o la cuenta ya no está activa."""
    row = db.query(models.MicrosoftLoginHandoff).filter(
        models.MicrosoftLoginHandoff.code_hash == hash_token(code)
    ).first()
    if row is None or row.used_at is not None or row.expires_at <= _now() or not _same(browser, row.browser_hash):
        return None
    row.used_at = _now()
    user = db.get(models.User, row.user_id)
    if user is None or not user.is_active:
        return None
    # Solicitantes: solo los internos (empleados de ADN)
    if user.role not in ("technician", "coordinator", "admin") and not (
        user.role == "requester" and routing.requester_kind(db, user) == "internal"
    ):
        return None
    return user


def record_emergency_access(db: Session, admin: models.User) -> None:
    """El administrador entró con contraseña y código: queda en el registro y avisa a los demás. No hace commit."""
    audit.record(db, admin.id, "emergency_access", target=admin)
    others = db.query(models.User).filter(
        models.User.role == "admin", models.User.is_active == True, models.User.id != admin.id,  # noqa: E712
    ).all()
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = "\n".join([
        "Se usó el acceso de emergencia de la Ticketera (contraseña y código por correo).",
        "",
        f"Administrador: {admin.name} ({mask_email(admin.email)})",
        f"Fecha: {when}",
        "",
        "Si no estaba previsto, revise el Registro de cambios.",
    ])
    for other in others:
        mailer.send_email(other.email, "Acceso de emergencia a la Ticketera", body)


class RedactCallbackQuery(logging.Filter):
    """El registro de accesos de uvicorn no anota el code ni el state de la vuelta de Microsoft."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) \
                and args[2].startswith(CALLBACK_PATH + "?"):
            record.args = (*args[:2], CALLBACK_PATH, *args[3:])
        return True


def install_log_filter() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactCallbackQuery) for f in access.filters):
        access.addFilter(RedactCallbackQuery())
