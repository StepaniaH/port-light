from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "7")
    from backend import agent_events

    agent_events.reset()
    yield
    agent_events.reset()


def test_record_and_recent():
    from backend import agent_events

    agent_events.record(2, "self", "preview", True)
    agent_events.record(1, "all:1/2", "", False)
    rows = agent_events.recent()
    assert len(rows) == 2
    assert rows[0]["scope"] == "all:1/2"
    assert rows[0]["leased"] is False
    assert rows[1]["label"] == "preview"
    assert rows[1]["leased"] is True
    s = agent_events.summary()
    assert s["total"] == 2
    assert s["last_used_at"] == rows[0]["ts"]
    assert [r["ts"] for r in s["recent"]] == sorted(
        (r["ts"] for r in s["recent"]), reverse=True)


def test_recent_caps_limit():
    from backend import agent_events

    for _ in range(15):
        agent_events.record(1, "self", "", False)
    assert len(agent_events.recent()) == 10
    assert len(agent_events.recent(limit=3)) == 3


def test_disabled_when_retention_zero(monkeypatch):
    from backend import agent_events

    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "0")
    agent_events.reset()
    agent_events.record(1, "self", "", False)
    assert agent_events.recent() == []
    assert agent_events.total_calls() == 0
    assert agent_events.last_used_at() is None


def test_retention_sweep_deletes_old_rows():
    from backend import agent_events

    agent_events.record(1, "self", "old", False)
    conn = sqlite3.connect(agent_events._db_path())
    conn.execute("UPDATE agent_events SET ts=0")
    conn.commit()
    conn.close()
    agent_events.record(1, "self", "new", False)
    assert [r["label"] for r in agent_events.recent()] == ["new"]
    assert agent_events.total_calls() == 1


def test_label_truncated_to_120_chars():
    from backend import agent_events

    agent_events.record(1, "self", "x" * 500, False)
    assert len(agent_events.recent()[0]["label"]) == 120
