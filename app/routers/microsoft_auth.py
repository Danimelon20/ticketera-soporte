"""Inicio de sesión del personal con Microsoft. La sesión nunca viaja en la URL: la vuelta trae un código
de un solo uso (60 s) que la página canjea con un POST por el mismo token de siempre."""
import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..database import get_db
from .. import audit, config, entra_login
from ..schemas import AuthResponse, EmailCodeRequest, MicrosoftExchange, MicrosoftLoginConfig, MicrosoftStart
from .auth import PORTAL_ROLES, _create_session, normalize_email, valid_invitation

router = APIRouter(tags=["microsoft-login"])

# Pestañas de la pantalla de inicio que entran con Microsoft
# requester: solo empleados de ADN (solicitantes internos)
MICROSOFT_PORTALS = ("staff", "admin", "requester")
RETURN_PATH = "/#microsoft"
EXCHANGE_FAILED = "El inicio de sesión con Microsoft venció. Intente de nuevo."
UNAVAILABLE = "No se pudo conectar con Microsoft. Intente más tarde."


def _require_enabled() -> None:
    if not config.MS_LOGIN_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")


def _set_browser_cookie(response: Response, browser: str) -> None:
    response.set_cookie(
        entra_login.COOKIE_NAME, browser,
        max_age=entra_login.FLOW_MINUTES * 60, path="/", httponly=True, samesite="lax",
        secure=config.MS_LOGIN_REDIRECT_URI.startswith("https://"),
    )


def _back_to_app(params: dict) -> RedirectResponse:
    # En el fragmento: el navegador no lo envía al servidor, así que no queda en ningún log
    response = RedirectResponse(f"{RETURN_PATH}?{urlencode(params)}", status_code=302)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/api/auth/microsoft/config", response_model=MicrosoftLoginConfig)
def microsoft_config():
    return MicrosoftLoginConfig(enabled=config.MS_LOGIN_ENABLED)


@router.get("/auth/microsoft/login")
def microsoft_login(portal: str = "staff", db: Session = Depends(get_db)):
    _require_enabled()
    if portal not in MICROSOFT_PORTALS:
        portal = "staff"
    try:
        auth_uri, browser = entra_login.start(db, "login", portal=portal)
    except Exception as exc:
        db.rollback()
        logging.error("No se pudo iniciar el ingreso con Microsoft (%s)", type(exc).__name__)
        return _back_to_app({"error": "unavailable", "portal": portal})
    response = RedirectResponse(auth_uri, status_code=302)
    response.headers["Cache-Control"] = "no-store"
    _set_browser_cookie(response, browser)
    return response


@router.get(entra_login.CALLBACK_PATH)
def microsoft_callback(request: Request, db: Session = Depends(get_db)):
    _require_enabled()
    browser = request.cookies.get(entra_login.COOKIE_NAME)
    try:
        identity = entra_login.finish(db, dict(request.query_params), browser)
        if identity.purpose == "activate":
            user = entra_login.activate(db, identity)
            portal = "staff"
        else:
            portal = identity.portal
            user = entra_login.resolve_user(db, identity, PORTAL_ROLES.get(portal or "", ()))
        code = entra_login.new_handoff(db, user, browser)
    except entra_login.LoginRejected as exc:
        entra_login.record_rejection(db, exc)
        return _back_to_app({"error": exc.reason, "portal": exc.portal or ""})
    except Exception as exc:
        db.rollback()
        logging.error("Falló la vuelta del ingreso con Microsoft (%s)", type(exc).__name__)
        return _back_to_app({"error": "failed", "portal": ""})
    return _back_to_app({"code": code, "portal": portal or ""})


@router.post("/api/auth/microsoft/exchange", response_model=AuthResponse)
def microsoft_exchange(data: MicrosoftExchange, request: Request, response: Response, db: Session = Depends(get_db)):
    _require_enabled()
    user = entra_login.redeem(db, data.code, request.cookies.get(entra_login.COOKIE_NAME))
    if user is None:
        db.commit()
        raise HTTPException(status_code=400, detail=EXCHANGE_FAILED)
    audit.record(db, user.id, "microsoft_login", target=user)
    # _create_session hace commit (también del código gastado y del registro)
    result = _create_session(db, user)
    response.delete_cookie(entra_login.COOKIE_NAME, path="/")
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/api/auth/activate/microsoft", response_model=MicrosoftStart)
def activate_with_microsoft(data: EmailCodeRequest, response: Response, db: Session = Depends(get_db)):
    """Revisa el código de la invitación (sin gastarla) y prepara la ida a Microsoft para enlazar la cuenta."""
    _require_enabled()
    invitation = valid_invitation(db, normalize_email(data.email), data.code)
    try:
        auth_uri, browser = entra_login.start(db, "activate", portal="staff", invitation_id=invitation.id)
    except Exception as exc:
        db.rollback()
        logging.error("No se pudo iniciar la activación con Microsoft (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail=UNAVAILABLE)
    _set_browser_cookie(response, browser)
    response.headers["Cache-Control"] = "no-store"
    return MicrosoftStart(auth_url=auth_uri)
