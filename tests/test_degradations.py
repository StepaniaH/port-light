from __future__ import annotations

import pytest

from backend import degradations
from backend.degradations import _REPEAT_LOG_SECONDS


@pytest.fixture(autouse=True)
def _clean():
    degradations.reset()
    yield
    degradations.reset()


def test_recent_returns_newest_last():
    degradations.report("docker", "daemon", "unreachable")
    degradations.report("compose", "apps/wiki", "yaml parse error")
    events = degradations.recent()
    assert [e["source"] for e in events] == ["docker", "compose"]
    assert events[-1]["scope"] == "apps/wiki"
    assert isinstance(events[-1]["ts"], int)


def test_recent_caps_limit():
    for i in range(10):
        degradations.report("docker", f"scope-{i}", "down")
    assert len(degradations.recent(5)) == 5
    assert degradations.recent()[-1]["scope"] == "scope-9"


def test_repeat_events_collapse():
    for _ in range(50):
        degradations.report("docker", "daemon", "unreachable")
    events = degradations.recent()
    assert len(events) == 1
    assert events[0]["reason"] == "unreachable"


def test_log_line_is_single_record(caplog):
    with caplog.at_level("WARNING", logger="port-light"):
        degradations.report("listen", "ss", "scan failed")
    assert any(
        "source=listen scope=ss reason=scan failed" in record.getMessage()
        for record in caplog.records
    )


def test_first_report_logs_on_a_young_monotonic_clock(monkeypatch, caplog):
    """A monotonic clock younger than the repeat window (fresh container, CI
    VM) must not swallow the first report: "never logged" is not time 0.0."""
    monkeypatch.setattr(degradations.time, "monotonic", lambda: 10.0)
    with caplog.at_level("WARNING", logger="port-light"):
        degradations.report("listen", "ss", "scan failed")
    assert any(
        "source=listen scope=ss reason=scan failed" in record.getMessage()
        for record in caplog.records
    )


def test_repeat_report_within_window_logs_once(monkeypatch, caplog):
    clock = {"t": 500.0}
    monkeypatch.setattr(degradations.time, "monotonic", lambda: clock["t"])
    with caplog.at_level("WARNING", logger="port-light"):
        degradations.report("docker", "daemon", "unreachable")
        clock["t"] += 1.0
        degradations.report("docker", "daemon", "unreachable")
        clock["t"] += _REPEAT_LOG_SECONDS + 1.0
        degradations.report("docker", "daemon", "unreachable")
    hits = sum(
        "scope=daemon reason=unreachable" in record.getMessage()
        for record in caplog.records
    )
    assert hits == 2


def test_interleaved_reports_are_rate_limited_per_error(monkeypatch, caplog):
    clock = {"t": 500.0}
    monkeypatch.setattr(degradations.time, "monotonic", lambda: clock["t"])
    with caplog.at_level("WARNING", logger="port-light"):
        degradations.report("docker", "scan", "unavailable")
        clock["t"] += 1.0
        degradations.report("compose", "scan", "unavailable")
        clock["t"] += 1.0
        degradations.report("docker", "scan", "unavailable")

    docker_hits = sum(
        "source=docker scope=scan reason=unavailable" in record.getMessage()
        for record in caplog.records
    )
    assert docker_hits == 1
    assert [event["source"] for event in degradations.recent()] == ["compose", "docker"]
