"""Dominios de correo de las organizaciones cliente.

Una sola lista bloqueada de dominios genéricos: un cliente se reconoce por el dominio de su empresa, nunca por
un servicio de correo gratuito (cualquiera puede abrir una cuenta ahí).
"""
import re

from . import config

GENERIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.es", "outlook.com", "outlook.es", "live.com",
    "live.com.mx", "msn.com", "yahoo.com", "yahoo.es", "yahoo.com.mx", "ymail.com", "icloud.com", "me.com",
    "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me", "gmx.com", "gmx.net", "mail.com",
    "zoho.com", "yandex.com", "tutanota.com", "tuta.io", "hey.com", "fastmail.com", "racsa.co.cr",
})

_DOMAIN_RE = re.compile(r"^(?=.{4,150}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")

INVALID = "El dominio «{}» no es válido"
GENERIC = "«{}» es un correo genérico: un cliente se reconoce por el dominio de su empresa"
INTERNAL = "«{}» es un dominio de ADN: no puede ser de un cliente"


def normalize(value: str | None) -> str:
    """Sin espacios, en minúsculas y sin la arroba o el "www." del principio."""
    domain = (value or "").strip().lower()
    domain = domain.rpartition("@")[2]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain.rstrip(".")


def of_email(email: str | None) -> str:
    return (email or "").strip().lower().rpartition("@")[2]


def is_generic(domain: str) -> bool:
    return domain in GENERIC_DOMAINS


def problem(domain: str) -> str | None:
    """Mensaje de por qué no puede ser el dominio de un cliente, o None si sirve."""
    if not _DOMAIN_RE.match(domain):
        return INVALID.format(domain)
    if is_generic(domain):
        return GENERIC.format(domain)
    if domain in config.INTERNAL_DOMAINS:
        return INTERNAL.format(domain)
    return None
