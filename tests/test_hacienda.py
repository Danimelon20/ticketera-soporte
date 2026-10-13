import io
import json
import socket
import urllib.error

import pytest

from app import hacienda


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# Se guarda al importar el módulo, antes de que el fixture de conftest la reemplace
_real_lookup = hacienda.lookup_id


@pytest.fixture
def lookup(monkeypatch):
    """La lookup_id real, con la caché vacía."""
    monkeypatch.setattr(hacienda, "_cache", {})
    return _real_lookup


def test_lookup_200_returns_tipo_without_name(lookup, monkeypatch):
    calls = []

    def _urlopen(request, timeout):
        calls.append(timeout)
        body = {"nombre": "PERSONA DE PRUEBA", "tipoIdentificacion": "01", "situacion": {"estado": "Inscrito"}}
        return _FakeResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(hacienda.urllib.request, "urlopen", _urlopen)
    assert lookup("102340567") == {"tipo": "01"}
    assert calls == [5]
    assert "PERSONA DE PRUEBA" not in repr(hacienda._cache)


def test_lookup_uses_cache(lookup, monkeypatch):
    calls = []

    def _urlopen(request, timeout):
        calls.append(request.full_url)
        return _FakeResponse(b'{"tipoIdentificacion": "03"}')

    monkeypatch.setattr(hacienda.urllib.request, "urlopen", _urlopen)
    assert lookup("123456789012") == {"tipo": "03"}
    assert lookup("123456789012") == {"tipo": "03"}
    assert len(calls) == 1


def test_lookup_404_returns_none(lookup, monkeypatch):
    def _urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(hacienda.urllib.request, "urlopen", _urlopen)
    assert lookup("102340567") is None


def test_lookup_timeout_raises_unavailable(lookup, monkeypatch):
    def _urlopen(request, timeout):
        raise socket.timeout("timed out")

    monkeypatch.setattr(hacienda.urllib.request, "urlopen", _urlopen)
    with pytest.raises(hacienda.HaciendaUnavailable):
        lookup("102340567")
    assert "102340567" not in hacienda._cache


def test_lookup_5xx_raises_unavailable(lookup, monkeypatch):
    def _urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(hacienda.urllib.request, "urlopen", _urlopen)
    with pytest.raises(hacienda.HaciendaUnavailable):
        lookup("102340567")
