"""Correos de dominios internos (INTERNAL_DOMAINS): el personal puede usarlos en su cuenta. (El registro de
clientes, que los rechazaba, ya no existe.)"""
import uuid

import pytest

from app import config

PASSWORD = "clave-segura-123"


@pytest.fixture
def internal(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_DOMAINS", ["data.cr"])


def _local():
    return f"persona-{uuid.uuid4().hex[:10]}"


def _request_email(client, user, new_email):
    return client.post(
        "/api/account/email", json={"current_password": PASSWORD, "new_email": new_email}, headers=user["headers"]
    )


def test_staff_can_change_email_to_internal_domain(client, make_user, sent, internal):
    user = make_user("technician")
    assert _request_email(client, user, f"{_local()}@data.cr").status_code == 202
