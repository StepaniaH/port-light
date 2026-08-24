from __future__ import annotations

import pytest

from backend import degradations


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
