from __future__ import annotations

import base64

from backend.auth import (
    auth_configured,
    hidden_ports_withheld,
    request_may_see_hidden,
    valid_basic_header,
    valid_hidden_unlock,
)


class _Req:
    def __init__(self, headers: dict[str, str]):
        self.headers = headers


def test_auth_off_by_default(monkeypatch):
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("HIDDEN_UNLOCK_PASSWORD", raising=False)
    assert auth_configured() is False
    assert hidden_ports_withheld() is False
    assert request_may_see_hidden(_Req({})) is True


def test_basic_header(monkeypatch):
    monkeypatch.setenv("AUTH_USER", "admin")
    monkeypatch.setenv("AUTH_PASSWORD", "s3cret")
    token = base64.b64encode(b"admin:s3cret").decode()
    assert valid_basic_header(f"Basic {token}") is True
    assert valid_basic_header("Basic " + base64.b64encode(b"admin:wrong").decode()) is False
    assert valid_basic_header("Bearer nope") is False


def test_hidden_unlock(monkeypatch):
    monkeypatch.setenv("HIDDEN_UNLOCK_PASSWORD", "unlock-me")
    assert valid_hidden_unlock("unlock-me") is True
    assert valid_hidden_unlock("nope") is False
    assert hidden_ports_withheld() is True
    assert request_may_see_hidden(_Req({})) is False
    assert request_may_see_hidden(_Req({"x-hidden-unlock": "unlock-me"})) is True
