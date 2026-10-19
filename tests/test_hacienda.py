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
    result = lookup("102340567")
    assert result["tipo"] == "01"
    assert result["name_tokens"] == hacienda.name_tokens("Persona de Prueba")
    assert calls == [5]
    assert "PERSONA" not in repr(hacienda._cache).upper()


def test_lookup_uses_cache(lookup, monkeypatch):
    calls = []

    def _urlopen(request, timeout):
        calls.append(request.full_url)
        return _FakeResponse(b'{"tipoIdentificacion": "03"}')

    monkeypatch.setattr(hacienda.urllib.request, "urlopen", _urlopen)
    assert lookup("123456789012")["tipo"] == "03"
    assert lookup("123456789012")["tipo"] == "03"
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


def _hacienda_name(monkeypatch, name):
    monkeypatch.setattr(hacienda, "lookup_id", lambda id_number: {"tipo": "01", "name_tokens": hacienda.name_tokens(name)})


@pytest.mark.parametrize("ours, theirs", [
    ("Daniel Alejandro Sáenz Maroto", "SAENZ MAROTO DANIEL"),
    ("Daniel Sáenz Maroto", "SAENZ MAROTO DANIEL ALEJANDRO"),
    ("Daniel Alejandro Sáenz Maroto", "Daniel Sáenz Maroto"),
    ("daniel   SÁENZ maroto", "SAENZ MAROTO DANIEL"),
])
def test_identity_match_in_both_directions(monkeypatch, ours, theirs):
    _hacienda_name(monkeypatch, theirs)
    assert hacienda.identity_result("102340567", ours) == "match"


@pytest.mark.parametrize("ours, theirs", [
    ("Daniel Pérez", "SAENZ MAROTO DANIEL"),
    ("Daniel Sáenz Rojas", "SAENZ MAROTO DANIEL"),
    ("Daniel", "DANIEL"),
])
def test_identity_mismatch(monkeypatch, ours, theirs):
    _hacienda_name(monkeypatch, theirs)
    assert hacienda.identity_result("102340567", ours) == "mismatch"


def test_identity_not_found(monkeypatch):
    monkeypatch.setattr(hacienda, "lookup_id", lambda id_number: None)
    assert hacienda.identity_result("102340567", "Daniel Sáenz Maroto") == "not_found"


def test_identity_unverified_when_hacienda_is_down(monkeypatch):
    def _unavailable(id_number):
        raise hacienda.HaciendaUnavailable("caído")

    monkeypatch.setattr(hacienda, "lookup_id", _unavailable)
    assert hacienda.identity_result("102340567", "Daniel Sáenz Maroto") == "unverified"


def test_identity_unverified_when_check_is_off(monkeypatch):
    monkeypatch.setenv("HACIENDA_CHECK", "false")
    assert hacienda.identity_result("102340567", "Daniel Sáenz Maroto") == "unverified"


def test_real_lookup_compares_names_without_storing_them(lookup, monkeypatch):
    def _urlopen(request, timeout):
        body = {"nombre": "SAENZ MAROTO DANIEL", "tipoIdentificacion": "01"}
        return _FakeResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(hacienda.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(hacienda, "lookup_id", lookup)
    assert hacienda.identity_result("102340567", "Daniel Alejandro Sáenz Maroto") == "match"
    cache = repr(hacienda._cache).upper()
    assert "SAENZ" not in cache and "MAROTO" not in cache and "DANIEL" not in cache
