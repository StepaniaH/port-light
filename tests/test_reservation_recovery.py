"""Exercise durable CLI recovery through the real HTTP application."""
import io
import json

from fastapi.testclient import TestClient

from backend import main, port_store
from port_light_client.client import PortLightClient, PortLightError, _http_error
from port_light_client.cli import main as cli_main
from port_light_client.state import ReservationStore


class AppTransport:
    def __init__(self, app_client):
        self.client = app_client
        self.lose_response = True

    def request(self, method, path, *, headers=None, json=None):
        response = self.client.request(method, path, headers=headers, json=json)
        if response.status_code >= 400:
            raise _http_error(response.status_code, response.json()["detail"])
        if method == "POST" and self.lose_response:
            self.lose_response = False
            raise PortLightError("unreachable", "simulated lost response after commit")
        return response.json()


def test_cli_retries_lost_response_with_original_claim_and_token(empty_scan, tmp_path):
    with TestClient(main.app) as app_client:
        transport = AppTransport(app_client)
        argv = ["--json", "reserve", "--start", "45000", "--end", "45001", "--no-expiry"]

        def invoke():
            output = io.StringIO()
            code = cli_main(argv, client=PortLightClient(transport=transport),
                            store=ReservationStore(tmp_path / "cli-state"), environ={},
                            stdout=output, stderr=io.StringIO(), stdin=io.StringIO())
            return code, json.loads(output.getvalue())

        code, failed = invoke()
        assert code == 3
        assert failed["ok"] is False
        assert [entry["port"] for entry in port_store.get_manual_ports()] == [45000]
        code, recovered = invoke()
        assert code == 0
        assert recovered["ports"] == [45000]
        assert [entry["port"] for entry in port_store.get_manual_ports()] == [45000]
        store = ReservationStore(tmp_path / "cli-state")
        token = store.load("http://127.0.0.1:2100", 45000)
        assert token == recovered["reservations"][0]["token"]
        assert app_client.delete("/api/reservations/45000", headers={"X-Reservation-Token": token}).status_code == 200
        assert port_store.get_manual_ports() == []
        code, fresh = invoke()
        assert code == 0
        assert fresh["reservations"][0]["token"] != token


def test_pending_journal_is_private_durable_and_locked(tmp_path):
    import os
    import pytest

    store = ReservationStore(tmp_path)
    params = {"count": 1}
    with pytest.raises(PortLightError, match="lost"):
        with store.pending_request("http://localhost:2100", params) as original:
            journal = next((tmp_path / "requests").glob("*.json"))
            assert json.loads(journal.read_text())["key"] == original
            if os.name != "nt":
                assert journal.stat().st_mode & 0o077 == 0
            with pytest.raises(PortLightError, match="in progress"):
                with ReservationStore(tmp_path).pending_request("http://localhost:2100", params):
                    raise AssertionError("concurrent request entered")
            raise PortLightError("unreachable", "lost")
    with ReservationStore(tmp_path).pending_request("http://localhost:2100", params) as recovered:
        assert recovered == original
    assert not list((tmp_path / "requests").glob("*.json"))


def test_no_capacity_clears_pending_request(tmp_path):
    import pytest

    store = ReservationStore(tmp_path)
    with pytest.raises(PortLightError):
        with store.pending_request("http://localhost:2100", {}) as first:
            raise PortLightError("no_capacity", "full")
    with store.pending_request("http://localhost:2100", {}) as second:
        assert second != first


def test_stateless_cli_requires_retained_key_and_recovers(empty_scan, tmp_path):
    with TestClient(main.app) as app_client:
        transport = AppTransport(app_client)
        argv = ["--json", "reserve", "--start", "45000", "--end", "45001", "--no-save"]
        def invoke(env):
            output = io.StringIO()
            code = cli_main(argv, client=PortLightClient(transport=transport),
                            store=ReservationStore(tmp_path / "unused"), environ=env,
                            stdout=output, stderr=io.StringIO(), stdin=io.StringIO())
            return code, json.loads(output.getvalue())
        assert invoke({})[0] == 2
        assert not port_store.get_manual_ports()
        env = {"PORT_LIGHT_REQUEST_KEY": "k" * 43}
        assert invoke(env)[0] == 3
        code, result = invoke(env)
        assert code == 0
        assert result["ports"] == [45000]
        assert len(port_store.get_manual_ports()) == 1
        assert not (tmp_path / "unused").exists()


def test_concurrent_same_key_claims_once(empty_scan):
    from concurrent.futures import ThreadPoolExecutor

    with TestClient(main.app) as client:
        def reserve(_):
            response = client.post("/api/reservations", json={"start": 45000, "end": 45002},
                                   headers={"Idempotency-Key": "k" * 43})
            assert response.status_code == 200
            return response.json()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(reserve, range(4)))
        assert all(result == results[0] for result in results)
        assert len(port_store.get_manual_ports()) == 1


def test_expired_receipt_does_not_reallocate_or_extend_lease(empty_scan, monkeypatch):
    monkeypatch.setattr(port_store, "_now", lambda: 1000)
    with TestClient(main.app) as client:
        headers = {"Idempotency-Key": "k" * 43}
        body = {"start": 45000, "end": 45002, "ttl": 60}
        first = client.post("/api/reservations", headers=headers, json=body).json()
        monkeypatch.setattr(port_store, "_now", lambda: 1030)
        assert client.post("/api/reservations", headers=headers, json=body).json() == first
        monkeypatch.setattr(port_store, "_now", lambda: 1061)
        assert client.post("/api/reservations", headers=headers, json=body).status_code == 409
        assert client.get("/api/reservations/request", headers=headers).status_code == 409


def test_recovery_enforces_agent_token(empty_scan, monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN", "agent-secret")
    with TestClient(main.app) as client:
        headers = {"Idempotency-Key": "k" * 43}
        assert client.post("/api/reservations", headers=headers, json={}).status_code == 403
        assert client.get("/api/reservations/request", headers=headers).status_code == 403
        headers["X-Agent-Token"] = "agent-secret"
        assert client.post("/api/reservations", headers=headers, json={}).status_code == 200
        assert client.get("/api/reservations/request", headers=headers).status_code == 200


def test_cli_lists_and_recovers_old_pending_request_without_post(empty_scan, tmp_path):
    import time
    with TestClient(main.app) as app_client:
        transport = AppTransport(app_client)
        store = ReservationStore(tmp_path / 'cli')
        def invoke(argv):
            output = io.StringIO()
            code = cli_main(['--json', *argv], client=PortLightClient(transport=transport),
                            store=store, environ={}, stdout=output, stderr=io.StringIO())
            return code, json.loads(output.getvalue())
        assert invoke(['reserve', '--start', '45000', '--end', '45001', '--no-expiry'])[0] == 3
        journal = next((store.root / 'requests').glob('*.json'))
        payload = json.loads(journal.read_text())
        payload['created_at'] = int(time.time()) - 8 * 86400
        journal.write_text(json.dumps(payload))
        code, listing = invoke(['requests'])
        assert code == 0
        assert len(listing['requests']) == 1
        assert payload['key'] not in json.dumps(listing)
        original_request = transport.request
        def read_only(method, *args, **kwargs):
            assert method == 'GET', 'recovery must never submit a new claim'
            return original_request(method, *args, **kwargs)
        transport.request = read_only
        code, result = invoke(['recover', listing['requests'][0]['id']])
        assert code == 0
        assert result['ports'] == [45000]
        assert store.load('http://127.0.0.1:2100', 45000)
        assert not journal.exists()


def test_partial_release_still_allows_read_only_recovery(empty_scan):
    with TestClient(main.app) as client:
        headers = {'Idempotency-Key': 'k' * 43}
        body = {'start': 45000, 'end': 45001, 'count': 2}
        first = client.post('/api/reservations', headers=headers, json=body).json()
        token = first['reservations'][0]['token']
        assert client.delete('/api/reservations/45000', headers={'X-Reservation-Token': token}).status_code == 200
        assert client.post('/api/reservations', headers=headers, json=body).status_code == 409
        recovered = client.get('/api/reservations/request', headers=headers)
        assert recovered.status_code == 200
        assert recovered.json()['ports'] == [45001]
        assert recovered.json()['inactive_ports'] == [45000]
        assert recovered.json()['reservations'] == first['reservations'][1:]


def test_corrupt_receipt_fails_closed(empty_scan, tmp_path):
    with TestClient(main.app) as client:
        headers = {'Idempotency-Key': 'k' * 43}
        assert client.post('/api/reservations', headers=headers, json={}).status_code == 200
        target = tmp_path / 'port_light.json'
        data = json.loads(target.read_text())
        next(iter(data['reservation_requests'].values()))['result'].pop('expires_at')
        target.write_text(json.dumps(data))
        before = target.read_bytes()
        response = client.get('/api/reservations/request', headers=headers)
        assert response.status_code == 503
        assert target.read_bytes() == before


def test_failed_recovery_token_save_keeps_journal(empty_scan, tmp_path, monkeypatch):
    import pytest
    store = ReservationStore(tmp_path / 'cli')
    with TestClient(main.app) as client:
        parameters = dict(count=1, start=45000, end=45000, label='', ttl=None, scope='self', require_count=True)
        with pytest.raises(PortLightError):
            with store.pending_request('http://127.0.0.1:2100', parameters) as key:
                assert client.post('/api/reservations', json=parameters, headers={'Idempotency-Key': key}).status_code == 200
                raise PortLightError('unreachable', 'lost')
        row = store.pending_requests('http://127.0.0.1:2100')[0]
        def fail(*_):
            raise PortLightError('state_write_failed', 'disk full')
        monkeypatch.setattr(store, 'save', fail)
        output = io.StringIO()
        code = cli_main(['--json', 'recover', row['id']], store=store,
                        client=PortLightClient(transport=AppTransport(client)), environ={},
                        stdout=output, stderr=io.StringIO())
        assert code == 2
        assert json.loads(output.getvalue())['error']['code'] == 'state_write_failed'
        assert store.pending_requests('http://127.0.0.1:2100') == [row]
        assert store.pending_requests('http://other.example') == []


def test_failed_journal_write_never_sends_request(empty_scan, tmp_path, monkeypatch):
    store = ReservationStore(tmp_path / 'cli')
    def fail(*_):
        raise PortLightError('state_write_failed', 'disk full')
    monkeypatch.setattr(store, '_write_request', fail)
    class NoTransport:
        def request(self, *_args, **_kwargs):
            raise AssertionError('network must not be contacted before journal persistence')
    output = io.StringIO()
    assert cli_main(['--json', 'reserve'], store=store,
                    client=PortLightClient(transport=NoTransport()), environ={},
                    stdout=output, stderr=io.StringIO()) == 2
    assert json.loads(output.getvalue())['error']['code'] == 'state_write_failed'
