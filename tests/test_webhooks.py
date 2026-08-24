from __future__ import annotations

import pytest

from backend import webhooks


@pytest.fixture(autouse=True)
def _reset():
    webhooks._primed = False
    webhooks._seen_used.clear()
    webhooks._seen_conflicts.clear()
    yield
    webhooks._primed = False
    webhooks._seen_used.clear()
    webhooks._seen_conflicts.clear()


def _rows(*pairs):
    rows = []
    for port, status in pairs:
        rows.append({"port": port, "status": status, "conflict": False})
    return rows


def test_cold_start_primes_without_events(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "http://127.0.0.1:9/hook")
    monkeypatch.setenv("WEBHOOK_EVENTS", "new_listener")
    sent = []
    webhooks.observe(_rows((22, "used"), (8080, "used")),
                     deliver=lambda *a: sent.append(a))
    assert sent == []


def test_new_listener_and_conflict_fire(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "http://127.0.0.1:9/hook")
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("WEBHOOK_EVENTS", "new_listener,conflict")
    sent = []

    def deliver(url, secret, body):
        sent.append((url, secret, body))

    webhooks.observe(_rows((22, "used")), deliver=deliver)  # prime
    rows = [
        {"port": 9091, "status": "used", "conflict": False},
        {"port": 7000, "status": "configured", "conflict": True},
        {"port": 22, "status": "used", "conflict": False},
    ]
    webhooks.observe(rows, deliver=deliver)

    events = sorted((body["event"], body["port"]) for _, _, body in sent)
    assert ("conflict", 7000) in events
    assert ("new_listener", 9091) in events
    assert all(secret == "s3cret" for _, secret, _ in sent)


def test_disabled_without_url(monkeypatch):
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    sent = []
    webhooks.observe(_rows((22, "used")), deliver=lambda *a: sent.append(a))
    webhooks.observe(_rows((23, "used")), deliver=lambda *a: sent.append(a))
    assert sent == []


def test_unknown_events_ignored(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "http://127.0.0.1:9/hook")
    monkeypatch.setenv("WEBHOOK_EVENTS", "banana")
    sent = []
    webhooks.observe(_rows((22, "used")), deliver=lambda *a: sent.append(a))
    webhooks.observe(_rows((22, "used"), (24, "used")), deliver=lambda *a: sent.append(a))
    assert sent == []
