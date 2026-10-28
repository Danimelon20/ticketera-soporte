"""Las pruebas nunca usan la configuración real (.env) ni salen a la red: Graph, Ollama ni Hacienda."""
import socket

import httpx
import pytest

from app import config, main

from conftest import REAL_NETWORK_ATTEMPTS, TEST_ENV, UNREACHABLE_OLLAMA


def test_real_config_does_not_reach_tests():
    # Lo que leyó config.py al importarse, antes de cualquier monkeypatch
    assert config.EMAIL_ENABLED is False
    assert config.CHATBOT_ENABLED is False
    assert config.AI_ANALYSIS_ENABLED is False
    assert config.GROUPING_ENABLED is False
    assert config.AUTO_ASSIGN_ENABLED is False
    assert config.ALERTS_ENABLED is False
    assert config.DAILY_SUMMARY_ENABLED is False
    assert config.LLM_BASE_URL == UNREACHABLE_OLLAMA
    assert config.SUPPORT_MAILBOX == ""
    assert config.MS_TENANT_ID == "" and config.MS_CLIENT_ID == ""
    assert config.MS_TOKEN_CACHE == TEST_ENV["MS_TOKEN_CACHE"]


def test_no_warm_up_with_test_config():
    assert main.start_warm_up() is None


@pytest.mark.parametrize("url", [
    "https://graph.microsoft.com/v1.0/me",
    "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    f"{UNREACHABLE_OLLAMA}/api/chat",
    "http://ollama:11434/api/chat",
])
def test_real_connections_are_blocked(url):
    start = len(REAL_NETWORK_ATTEMPTS)
    with pytest.raises(httpx.ConnectError):
        httpx.get(url, timeout=2)
    assert len(REAL_NETWORK_ATTEMPTS) > start
    # Anotado a propósito: se quita para que no_real_network no falle esta prueba
    del REAL_NETWORK_ATTEMPTS[start:]


def test_raw_socket_is_blocked():
    start = len(REAL_NETWORK_ATTEMPTS)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, pytest.raises(ConnectionRefusedError):
        sock.connect(("127.0.0.1", 9))
    assert REAL_NETWORK_ATTEMPTS[start:] == ["127.0.0.1:9"]
    del REAL_NETWORK_ATTEMPTS[start:]
