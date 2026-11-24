from __future__ import annotations

import pytest

from backend import webhooks


@pytest.fixture(autouse=True)
def _reset():
    webhooks.reset()
    yield
    webhooks.reset()


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


def test_url_with_embedded_credentials_is_ignored(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "http://user:pass@127.0.0.1:9/hook")
    monkeypatch.setenv("WEBHOOK_EVENTS", "new_listener")
    sent = []
    webhooks.observe(_rows((22, "used")), deliver=lambda *a: sent.append(a))
    webhooks.observe(_rows((22, "used"), (25, "used")), deliver=lambda *a: sent.append(a))
    assert sent == []


def test_failed_delivery_reports_host_without_credentials(monkeypatch, ):
    from backend import degradations

    degradations.reset()

    def refuse(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(webhooks.urllib.request, "urlopen", refuse)
    webhooks.deliver("http://user:pass@hookhost.example:9/x", "", {"event": "new_listener", "port": 80})
    events = degradations.recent()
    assert any(
        e["source"] == "webhook" and "hookhost.example" in e["scope"]
        for e in events
    ), events
    assert all("user" not in e["scope"] and "pass" not in e["scope"] for e in events)
    degradations.reset()


def test_edge_triggered_repeat_alerts_and_cooldown(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "http://127.0.0.1:9/hook")
    monkeypatch.setenv("WEBHOOK_EVENTS", "new_listener,conflict")
    sent: list[tuple[str, int]] = []

    def sync_thread(*, target, args=(), daemon=None):
        class Runner:
            def start(self):
                target(*args)
        return Runner()

    monkeypatch.setattr(webhooks.threading, "Thread", sync_thread)

    def record_delivery(_url, _secret, body):
        sent.append((body["event"], body["port"]))

    webhooks.observe(_rows((22, "used")), deliver=record_delivery)
    webhooks.observe(
        [{"port": 8080, "status": "used", "conflict": True}],
        deliver=record_delivery,
    )
    assert sent == [("new_listener", 8080), ("conflict", 8080)]

    webhooks.observe([], deliver=record_delivery)
    webhooks.observe(
        [{"port": 8080, "status": "used", "conflict": True}],
        deliver=record_delivery,
    )
    assert sent == [
        ("new_listener", 8080),
        ("conflict", 8080),
        ("new_listener", 8080),
        ("conflict", 8080),
    ]

    webhooks.reset()
    sent.clear()
    clock = {"t": 100.0}
    monkeypatch.setenv("WEBHOOK_COOLDOWN_S", "300")
    monkeypatch.setattr(webhooks.time, "monotonic", lambda: clock["t"])

    webhooks.observe([], deliver=record_delivery)
    webhooks.observe(_rows((9090, "used")), deliver=record_delivery)
    assert sent == [("new_listener", 9090)]

    clock["t"] = 150.0
    webhooks.observe([], deliver=record_delivery)
    webhooks.observe(_rows((9090, "used")), deliver=record_delivery)
    assert sent == [("new_listener", 9090)]

    clock["t"] = 401.0
    webhooks.observe([], deliver=record_delivery)
    webhooks.observe(_rows((9090, "used")), deliver=record_delivery)
    assert sent == [("new_listener", 9090), ("new_listener", 9090)]


def test_deliver_ignores_proxy_and_refuses_redirects(monkeypatch):
    import http.client
    import socket
    import threading
    import urllib.request
    from backend import degradations

    degradations.reset()
    requests_seen: list[str] = []
    client_sock, server_sock = socket.socketpair()

    def serve():
        try:
            wire = bytearray()
            while b"\r\n\r\n" not in wire:
                chunk = server_sock.recv(4096)
                if not chunk:
                    break
                wire.extend(chunk)
            requests_seen.append(wire.splitlines()[0].decode("ascii"))
            server_sock.sendall(
                b"HTTP/1.1 302 Found\r\n"
                b"Location: http://hook.example.invalid/secret\r\n"
                b"Content-Length: 0\r\n"
                b"Connection: close\r\n\r\n"
            )
        finally:
            server_sock.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    class PairConn(http.client.HTTPConnection):
        def connect(self):
            self.sock = client_sock

    class PairHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(PairConn, req)

    real_build_opener = urllib.request.build_opener

    def build_with_pair(*handlers):
        assert any(isinstance(h, urllib.request.ProxyHandler) for h in handlers)
        return real_build_opener(PairHandler(), *handlers)

    try:
        monkeypatch.setenv("HTTP_PROXY", "http://198.51.100.1:8080")
        monkeypatch.setattr(webhooks.urllib.request, "build_opener", build_with_pair)
        webhooks.deliver("http://hook.example.invalid/hook", "sec", {"event": "new_listener", "port": 80})
    finally:
        client_sock.close()
        thread.join(timeout=1)

    assert requests_seen == ["POST /hook HTTP/1.1"]
    assert any(e["source"] == "webhook" and e["reason"] == "delivery failed" for e in degradations.recent())
    degradations.reset()
