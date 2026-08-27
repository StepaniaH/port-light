# Agent Visibility Suite Implementation Plan

> **Status: shipped (through v0.7.3). Historical record — do not execute.**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Port-Light's agent integrations discoverable and observable: a Settings → Automation tab with copy-paste MCP/skill/curl guidance, a usage event stream from the suggest API, and lease visibility on the grid + drawer + settings list.

**Architecture:** New `backend/agent_events.py` mirrors `history.py`'s SQLite pattern (same `history.db`, retention follows `HISTORY_RETENTION_DAYS`). `/api/meta`'s `automation` block gains an `agent_events` sub-object; suggest records one event per successful call. Frontend adds a fourth settings panel fed by pure render functions (`settings.js`) plus a tiny shared module `frontend/js/leases.js`; grid badge and drawer countdown derive purely from the existing `expires_at` payload field. Dockerfile ships `mcp/` + `skills/`.

**Tech Stack:** Python 3.11+ stdlib sqlite3 (no new deps), FastAPI; vanilla ES-module frontend, no build step; pytest + ruff backend, `node --test` frontend.

**Spec:** `docs/superpowers/specs/2026-08-25-agent-visibility-design.md`

## Global Constraints

- Python compatibility floor 3.11; backend additions stdlib-only.
- Frontend stays framework-free native ES modules served statically — no npm, no build.
- Every task ends with: `pytest -q` green, `ruff check backend tests` clean (backend tasks) or `node --test frontend/test/` green (frontend tasks), and a commit.
- Never push branches or tags to any remote.
- No comments in new code unless mirroring an adjacent documented pattern.
- i18n: every new user-visible string gets keys in all four locales (en, zh-CN, zh-TW, ja). `tests/test_i18n.py` enforces parity — run it after locale edits.
- `AGENT_TOKEN` value must never appear in any API response or rendered snippet (placeholders only).

---

### Task 1: `agent_events` storage module

**Files:**
- Create: `backend/agent_events.py`
- Test: `tests/test_agent_events.py`

**Interfaces:**
- Consumes: `backend.history.enabled()` and `backend.history.retention_days()`.
- Produces: `record(count: int, scope: str, label: str, leased: bool) -> None`, `recent(limit: int = 10) -> list[dict]` (`{ts, count, scope, label, leased}` newest first), `total_calls() -> int`, `last_used_at() -> int | None`, `summary() -> dict` (`{total, last_used_at, recent}`), `reset() -> None`. Task 2 consumes all of these.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agent_events.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.agent_events'`

- [ ] **Step 3: Write the module**

Create `backend/agent_events.py`:

```python
"""Usage events for the agent suggest API, stored in history.db.

One row per successful GET /api/ports/suggest call. Rows carry counts,
scope, caller-supplied label, and whether a reservation was requested —
never tokens, IPs, or host names. Retention follows HISTORY_RETENTION_DAYS
like the occupancy events table; ``0`` disables recording entirely.
"""

from __future__ import annotations

import os
import sqlite3
import threading

from .history import enabled, retention_days

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db_path() -> str:
    data_dir = os.environ.get("PORT_LIGHT_DATA_DIR", "/data")
    return os.path.join(data_dir, "history.db")


def _connect() -> sqlite3.Connection | None:
    global _conn
    if not enabled():
        return None
    if _conn is None:
        try:
            _conn = sqlite3.connect(_db_path(), check_same_thread=False)
            _conn.execute(
                "CREATE TABLE IF NOT EXISTS agent_events ("
                "ts INTEGER NOT NULL, count INTEGER NOT NULL,"
                "scope TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',"
                "leased INTEGER NOT NULL DEFAULT 0)"
            )
            _conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_events_ts"
                " ON agent_events(ts)"
            )
            _conn.commit()
        except sqlite3.Error:
            _conn = None
    return _conn


def reset() -> None:
    """Test hook: close the handle so env changes take effect."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


def record(count: int, scope: str, label: str, leased: bool) -> None:
    import time

    with _lock:
        conn = _connect()
        if conn is None:
            return
        ts = int(time.time())
        conn.execute(
            "INSERT INTO agent_events (ts, count, scope, label, leased)"
            " VALUES (?,?,?,?,?)",
            (ts, count, scope, label[:120], 1 if leased else 0),
        )
        conn.execute(
            "DELETE FROM agent_events WHERE ts < ?",
            (ts - retention_days() * 86400,),
        )
        conn.commit()


def _query(query: str, params: tuple = ()) -> list[tuple]:
    conn = _connect()
    if conn is None:
        return []
    return list(conn.execute(query, params))


def recent(limit: int = 10) -> list[dict]:
    rows = _query(
        "SELECT ts, count, scope, label, leased FROM agent_events"
        " ORDER BY ts DESC, rowid DESC LIMIT ?",
        (max(1, min(int(limit), 50)),),
    )
    return [
        {"ts": ts, "count": c, "scope": s, "label": l, "leased": bool(d)}
        for ts, c, s, l, d in rows
    ]


def total_calls() -> int:
    rows = _query("SELECT COUNT(*) FROM agent_events")
    return int(rows[0][0]) if rows else 0


def last_used_at() -> int | None:
    rows = _query("SELECT MAX(ts) FROM agent_events")
    if rows and rows[0][0]:
        return int(rows[0][0])
    return None


def summary(limit: int = 10) -> dict:
    return {
        "total": total_calls(),
        "last_used_at": last_used_at(),
        "recent": recent(limit),
    }
```

Note: `import time` sits inside `record` to match `history.py`'s local-import style there; if ruff complains, move it to module top with the other imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agent_events.py -v`
Expected: PASS (6 tests)

Run: `ruff check backend tests`
Expected: clean

- [ ] **Step 5: Commit**

```bash
git add backend/agent_events.py tests/test_agent_events.py
git commit -m "Add agent event store beside the occupancy history db"
```

---

### Task 2: Suggest instrumentation + `/api/meta` block

**Files:**
- Modify: `backend/main.py` (meta at ~116-137, suggest tail at ~548-562)
- Test: create `tests/test_agent_meta.py`, modify `tests/test_suggest.py` fixture

**Interfaces:**
- Consumes: everything Task 1 exported; `port_store.get_manual_ports() -> list[dict]` (entries may carry `expires_at`).
- Produces: `/api/meta` → `automation.agent_events = {total, last_used_at, recent, active_leases, lease_rows}` where `lease_rows` is `[{"port": int, "label": str, "expires_at": int}]` capped at 64, present only when history is enabled. Frontend Tasks 5-7 consume this shape.

- [ ] **Step 1: Write the failing tests**

Modify `tests/test_suggest.py`: extend the `_env` fixture so it also resets the agent store:

```python
@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTH_USER", raising=False)
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    from backend import agent_events

    agent_events.reset()
    yield
    agent_events.reset()
```

(Keep the existing `main._occ_snap` / scanner monkeypatching exactly as it is.)

Append to `tests/test_suggest.py`:

```python
def test_suggest_records_event(monkeypatch):
    from backend import agent_events

    client = TestClient(app)
    res = client.get("/api/ports/suggest",
                     params={"count": 2, "start": 7000, "end": 7010})
    assert res.status_code == 200
    rows = agent_events.recent()
    assert len(rows) == 1
    assert rows[0]["count"] == 2
    assert rows[0]["scope"] == "self"
    assert rows[0]["leased"] is False


def test_suggest_records_lease_and_label(monkeypatch):
    from backend import agent_events

    client = TestClient(app)
    client.get("/api/ports/suggest",
               params={"count": 1, "start": 7100, "end": 7109,
                       "reserve": True, "ttl": 3600, "label": "job"})
    row = agent_events.recent()[0]
    assert row["leased"] is True
    assert row["label"] == "job"


def test_suggest_token_failure_not_recorded(monkeypatch):
    from backend import agent_events

    monkeypatch.setenv("AGENT_TOKEN", "sekrit")
    client = TestClient(app)
    res = client.get("/api/ports/suggest",
                     params={"count": 1, "start": 7200, "end": 7209})
    assert res.status_code == 403
    assert agent_events.recent() == []
```

Create `tests/test_agent_meta.py`:

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "7")
    monkeypatch.delenv("AGENT_TOKEN", raising=False)
    from backend import agent_events, history

    history.reset()
    agent_events.reset()
    yield
    history.reset()
    agent_events.reset()


def test_meta_includes_agent_events_block():
    from backend import agent_events

    agent_events.record(3, "self", "preview", True)
    body = TestClient(app).get("/api/meta").json()
    ev = body["automation"]["agent_events"]
    assert ev["total"] == 1
    assert ev["active_leases"] == 0
    assert ev["recent"][0]["label"] == "preview"
    assert ev["lease_rows"] == []


def test_meta_omits_block_when_history_disabled(monkeypatch):
    monkeypatch.setenv("HISTORY_RETENTION_DAYS", "0")
    from backend import agent_events

    agent_events.reset()
    agent_events.record(1, "self", "", False)
    body = TestClient(app).get("/api/meta").json()
    assert "agent_events" not in body["automation"]


def test_active_leases_listed_with_rows():
    from backend import port_store

    port_store.add_manual_port(6000, "leased", "localhost", 3600)
    port_store.add_manual_port(6001, "manual", "localhost")
    body = TestClient(app).get("/api/meta").json()
    ev = body["automation"]["agent_events"]
    assert ev["active_leases"] == 1
    assert ev["lease_rows"] == [
        {"port": 6000, "label": "leased",
         "expires_at": ev["lease_rows"][0]["expires_at"]}]
    assert isinstance(ev["lease_rows"][0]["expires_at"], int)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_suggest.py tests/test_agent_meta.py -v`
Expected: FAIL — suggest tests on missing `agent_events.recent` data / meta tests KeyError `agent_events`

- [ ] **Step 3: Implement in `backend/main.py`**

Add import near the other `backend` imports:

```python
from backend import agent_events
```

(Adjust to the file's existing grouped-import style.)

Add two helpers directly above the `/api/meta` route:

```python
def _active_leases() -> list[dict]:
    now = int(time.time())
    rows = []
    for entry in port_store.get_manual_ports():
        exp = entry.get("expires_at")
        if exp and int(exp) > now:
            rows.append({"port": int(entry["port"]),
                         "label": entry.get("label") or "",
                         "expires_at": int(exp)})
    return rows[:64]
```

In `meta()`, build the automation dict into a variable and append conditionally before returning:

```python
@app.get("/api/meta")
def meta() -> dict:
    values, _ = app_settings.resolve()
    automation = {
        "agent_token": bool(os.environ.get("AGENT_TOKEN", "").strip()),
        "metrics": os.environ.get("METRICS_ENABLED", "").strip().lower()
                   in ("1", "true", "yes", "on"),
        "webhook": bool(os.environ.get("WEBHOOK_URL", "").strip()),
        "history_days": history.retention_days(),
        "events_stream": True,
        "suggest_peers": bool(hosts.list_public_peers()),
    }
    if agent_events.enabled():
        leases = _active_leases()
        automation["agent_events"] = {
            **agent_events.summary(),
            "active_leases": len(leases),
            "lease_rows": leases,
        }
    return {
        "version": VERSION,
        "auth_required": auth_configured(),
        "hidden_unlock_required": hidden_unlock_configured(),
        "hidden_ports_withheld": hidden_ports_withheld(),
        "settings_readonly": app_settings.settings_readonly(),
        "refresh_ms": values["refresh_ms"],
        "theme": values["theme"],
        "grid_density": values["grid_density"],
        "automation": automation,
    }
```

In `suggest_ports`, insert one line immediately before the final `return {` (after the reserve loop, ~line 554):

```python
    agent_events.record(len(picks), scope_label, label, bool(reserved))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_suggest.py tests/test_agent_meta.py tests/test_agent_events.py -v`
Expected: PASS

Run: `pytest -q && ruff check backend tests`
Expected: full suite green, lint clean

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_suggest.py tests/test_agent_meta.py
git commit -m "Record suggest calls as agent events and expose them via meta"
```

---

### Task 3: Ship MCP + skill in the image, document them

**Files:**
- Modify: `Dockerfile` (after line 21 `COPY frontend/ ./frontend/`)
- Modify: `docs/integrations.md` (MCP server section, ~127-152)
- Modify: `CHANGELOG.md` (top)

**Interfaces:**
- Produces: image path `/app/mcp/server.py` and `/app/skills/port-light/SKILL.md`, referenced by Task 5's snippets.

- [ ] **Step 1: Dockerfile**

After `COPY frontend/ ./frontend/` add:

```dockerfile
COPY mcp/ ./mcp/
COPY skills/ ./skills/
```

Verify with: `grep -n "COPY" Dockerfile` — expect backend/frontend/mcp/skills lines present.

- [ ] **Step 2: docs/integrations.md**

In "### MCP server (experimental)", insert before the run command:

```markdown
The published image ships the server at `/app/mcp/server.py`, so Docker
deployments do not need a source checkout:

```json
{
  "mcpServers": {
    "port-light": {
      "command": "docker",
      "args": ["exec", "-i", "port-light", "python", "mcp/server.py"],
      "env": {"PORT_LIGHT_URL": "http://127.0.0.1:2100"}
    }
  }
}
```

The agent skill rides along at `/app/skills/port-light/SKILL.md`.
```

Also note under "### Agent skill" that the file is at `/app/skills/port-light/SKILL.md` inside the image.

- [ ] **Step 3: CHANGELOG.md**

Directly under the header/intro, above the `## 0.7.0` heading, add:

```markdown
## Unreleased

- Settings gains an **Automation** tab: copy-paste MCP registration (docker-exec or source forms), agent-skill install hint, curl examples, live usage activity, and an active-leases list with release buttons
- `GET /api/ports/suggest` calls are now recorded locally (time, count, scope, label, leased — never tokens or IPs) and summarized under `/api/meta` → `automation.agent_events`; retention follows `HISTORY_RETENTION_DAYS`
- Lease visibility: cards whose reservation carries a TTL show a badge on the grid and an expiry countdown in the detail drawer
- The published image now includes `mcp/server.py` and `skills/port-light/SKILL.md`, so Docker users can register the MCP server without cloning the repo
```

- [ ] **Step 4: Commit**

```bash
git add Dockerfile docs/integrations.md CHANGELOG.md
git commit -m "Ship mcp and skills in the image and document registration"
```

---

### Task 4: Shared lease/time helpers (`frontend/js/leases.js`)

**Files:**
- Create: `frontend/js/leases.js`
- Test: `frontend/test/leases.test.mjs`

**Interfaces:**
- Produces (Tasks 5-7 consume):
  - `isLease(row: {expires_at?: number}) -> boolean` — true iff `expires_at` is a finite number in the future.
  - `remainingSeconds(expiresAt: number, nowSec?: number) -> number` — clamped ≥ 0.
  - `fmtRemaining(secs: number) -> string` — `<1m` / `58m` / `3h` / `2d`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/test/leases.test.mjs`:

```javascript
/* Tests for frontend/js/leases.js — lease detection and duration formatting.
   All formatters are locale-neutral numeric strings; words come from i18n. */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { isLease, remainingSeconds, fmtRemaining } from '../js/leases.js';

const NOW = 1756100000;

test('isLease true only for future finite expires_at', () => {
  assert.equal(isLease({ expires_at: NOW + 60 }), true);
  assert.equal(isLease({ expires_at: NOW - 1 }), false);
  assert.equal(isLease({ expires_at: NaN }), false);
  assert.equal(isLease({}), false);
  assert.equal(isLease(null), false);
});

test('remainingSeconds clamps expired leases to zero', () => {
  assert.equal(remainingSeconds(NOW + 90, NOW), 90);
  assert.equal(remainingSeconds(NOW - 90, NOW), 0);
});

test('fmtRemaining picks the widest unit', () => {
  assert.equal(fmtRemaining(30), '<1m');
  assert.equal(fmtRemaining(59), '<1m');
  assert.equal(fmtRemaining(60), '1m');
  assert.equal(fmtRemaining(58 * 60), '58m');
  assert.equal(fmtRemaining(3 * 3600), '3h');
  assert.equal(fmtRemaining(47 * 3600), '2d');
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test frontend/test/leases.test.mjs`
Expected: FAIL — cannot find module `../js/leases.js`

- [ ] **Step 3: Write the module**

Create `frontend/js/leases.js`:

```javascript
/* Lease helpers shared by grid badges, drawer countdowns, and the
   Automation panel. Numeric output only — wording lives in locales. */

export function isLease(row) {
  const exp = row && row.expires_at;
  return typeof exp === 'number' && Number.isFinite(exp)
    && exp > Date.now() / 1000;
}

export function remainingSeconds(expiresAt, nowSec) {
  const now = nowSec != null ? nowSec : Date.now() / 1000;
  return Math.max(0, Math.round(expiresAt - now));
}

export function fmtRemaining(secs) {
  if (secs >= 172800) return Math.round(secs / 86400) + 'd';
  if (secs >= 7200) return Math.round(secs / 3600) + 'h';
  if (secs >= 60) return Math.round(secs / 60) + 'm';
  return '<1m';
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test frontend/test/leases.test.mjs`
Expected: PASS (3 tests)

Run: `node --test frontend/test/`
Expected: all pre-existing suites still green

- [ ] **Step 5: Commit**

```bash
git add frontend/js/leases.js frontend/test/leases.test.mjs
git commit -m "Add shared lease detection and duration helpers"
```

---

### Task 5: Automation tab shell, Connect card, i18n keys

**Files:**
- Modify: `frontend/index.html:106-109` (nav buttons)
- Modify: `frontend/js/state.js:4` (`SETTINGS_PANELS`)
- Modify: `frontend/js/settings.js` (panel composition ~387-399, plus new exports)
- Modify: all four `frontend/locales/*.json`
- Test: create `frontend/test/settings.test.mjs`

**Interfaces:**
- Consumes: Task 4 `isLease` not needed here; Task 2 meta shape `automation.agent_events`.
- Produces: `export function automationCardsHtml(a)` — pure function taking `/api/meta`'s `automation` object, returning the three-card HTML string (Connect / Status / Activity+Leases hosts). Task 6 replaces placeholder sections inside this function; Tasks 6-7 keep its signature stable. Copy buttons delegate through `[data-copy="<element-id>"]`.

- [ ] **Step 1: Add ALL i18n keys (all four locales, same tree)**

In each of `en.json`, `zh-CN.json`, `zh-TW.json`, `ja.json`:

1. Under `settings.nav` (keys `appearance`/`occupancy`/`advanced` exist) add `"automation"`.
2. Inside `settings.auto` add the sub-trees below (keep existing keys untouched).

en:

```json
"nav": { "automation": "Automation" },
"auto": {
  "connect": {
    "title": "Connect your agents",
    "blurb": "Copy-paste registration for coding agents. The MCP server wraps the suggest/check/release flow.",
    "mcpDocker": "MCP registration — Compose / image deployment",
    "mcpSource": "MCP registration — running from source",
    "skill": "Agent skill (Claude Code and compatible)",
    "skillHint": "Copy SKILL.md into your agent's skills directory.",
    "curl": "HTTP example",
    "curlToken": "This instance requires X-Agent-Token — replace <your-token>.",
    "copy": "Copy",
    "copied": "Copied!"
  },
  "status": { "title": "Exposed surfaces", "blurb": "What scripts and agents can reach right now." },
  "activity": {
    "title": "Suggest API activity",
    "blurb": "Local call log for GET /api/ports/suggest. Counts and labels only — never tokens or addresses.",
    "disabled": "History is disabled (HISTORY_RETENTION_DAYS=0), so usage is not recorded.",
    "total": "Calls",
    "activeLeases": "Active leases",
    "lastUsed": "Last used",
    "never": "never",
    "thTime": "When",
    "thCount": "Ports",
    "thScope": "Scope",
    "thLabel": "Label",
    "thLeased": "Lease"
  },
  "leases": {
    "title": "Active leases",
    "blurb": "Reserved by agents through the suggest API, freed automatically at expiry.",
    "none": "No active leases.",
    "release": "Release",
    "released": "Released"
  }
}
```

zh-CN（对应替换值，键树完全一致）：

```json
"nav": { "automation": "自动化" },
"auto": {
  "connect": {
    "title": "接入你的 agent",
    "blurb": "复制即用的编码助手注册配置。MCP 服务封装了建议/查询/释放流程。",
    "mcpDocker": "MCP 注册 —— Compose / 镜像部署",
    "mcpSource": "MCP 注册 —— 源码运行",
    "skill": "Agent skill（Claude Code 及兼容工具）",
    "skillHint": "把 SKILL.md 复制到你 agent 的 skills 目录。",
    "curl": "HTTP 示例",
    "curlToken": "本实例已开启 X-Agent-Token 校验——请把 <your-token> 替换为真实值。",
    "copy": "复制",
    "copied": "已复制！"
  },
  "status": { "title": "对外接口", "blurb": "脚本和 agent 当前能访问到的能力。" },
  "activity": {
    "title": "suggest API 使用活动",
    "blurb": "GET /api/ports/suggest 的本地调用记录。只有次数和标签——不含任何令牌或地址。",
    "disabled": "历史记录未启用（HISTORY_RETENTION_DAYS=0），不记录使用情况。",
    "total": "调用数",
    "activeLeases": "活跃租约",
    "lastUsed": "最近使用",
    "never": "从未",
    "thTime": "时间",
    "thCount": "端口数",
    "thScope": "范围",
    "thLabel": "标签",
    "thLeased": "租约"
  },
  "leases": {
    "title": "活跃租约",
    "blurb": "由 agent 通过 suggest API 预留，到期自动释放。",
    "none": "当前没有活跃租约。",
    "release": "释放",
    "released": "已释放"
  }
}
```

zh-TW：

```json
"nav": { "automation": "自動化" },
"auto": {
  "connect": {
    "title": "接入你的 agent",
    "blurb": "複製即用的編碼助手註冊設定。MCP 服務封裝了建議/查詢/釋放流程。",
    "mcpDocker": "MCP 註冊 —— Compose / 映像部署",
    "mcpSource": "MCP 註冊 —— 原始碼執行",
    "skill": "Agent skill（Claude Code 及相容工具）",
    "skillHint": "把 SKILL.md 複製到你 agent 的 skills 目錄。",
    "curl": "HTTP 範例",
    "curlToken": "此實例已啟用 X-Agent-Token 驗證——請將 <your-token> 換成實際值。",
    "copy": "複製",
    "copied": "已複製！"
  },
  "status": { "title": "對外介面", "blurb": "腳本和 agent 目前能存取的能力。" },
  "activity": {
    "title": "suggest API 使用活動",
    "blurb": "GET /api/ports/suggest 的本機呼叫紀錄。只有次數與標籤——不含任何權杖或位址。",
    "disabled": "歷史紀錄未啟用（HISTORY_RETENTION_DAYS=0），不會記錄使用情況。",
    "total": "呼叫數",
    "activeLeases": "活躍租約",
    "lastUsed": "最近使用",
    "never": "從未",
    "thTime": "時間",
    "thCount": "連接埠數",
    "thScope": "範圍",
    "thLabel": "標籤",
    "thLeased": "租約"
  },
  "leases": {
    "title": "活躍租約",
    "blurb": "由 agent 透過 suggest API 保留，到期自動釋放。",
    "none": "目前沒有活躍租約。",
    "release": "釋放",
    "released": "已釋放"
  }
}
```

ja：

```json
"nav": { "automation": "オートメーション" },
"auto": {
  "connect": {
    "title": "エージェントを接続",
    "blurb": "コーディングエージェント向けのコピペ登録セット。MCP サーバーは suggest/check/release をラップします。",
    "mcpDocker": "MCP 登録 —— Compose / イメージデプロイ",
    "mcpSource": "MCP 登録 —— ソースから実行",
    "skill": "Agent skill（Claude Code 互換）",
    "skillHint": "SKILL.md をエージェントの skills ディレクトリにコピーしてください。",
    "curl": "HTTP 例",
    "curlToken": "このインスタンスは X-Agent-Token を要求します。<your-token> を実際の値に置き換えてください。",
    "copy": "コピー",
    "copied": "コピーしました！"
  },
  "status": { "title": "公開インターフェース", "blurb": "スクリプトやエージェントが現在利用できる機能。" },
  "activity": {
    "title": "suggest API の利用状況",
    "blurb": "GET /api/ports/suggest のローカル呼び出し記録。回数とラベルのみ——トークンやアドレスは記録しません。",
    "disabled": "履歴が無効（HISTORY_RETENTION_DAYS=0）のため、利用は記録されません。",
    "total": "呼び出し数",
    "activeLeases": "有効なリース",
    "lastUsed": "最終利用",
    "never": "なし",
    "thTime": "時刻",
    "thCount": "ポート数",
    "thScope": "スコープ",
    "thLabel": "ラベル",
    "thLeased": "リース"
  },
  "leases": {
    "title": "有効なリース",
    "blurb": "エージェントが suggest API で確保したもので、期限切れになると自動解放されます。",
    "none": "有効なリースはありません。",
    "release": "解放",
    "released": "解放済み"
  }
}
```

3. Also add top-level (under `grid`) `"leaseBadge"` and (under `detail`) `"expiresIn"`:

| key | en | zh-CN | zh-TW | ja |
|-----|----|-------|-------|----|
| `grid.leaseBadge` | Agent lease | Agent 租约 | Agent 租約 | Agentリース |
| `detail.expiresIn` | Expires in {time} | {time} 后到期 | {time} 後到期 | {time}後に失効 |

Check how `t()` interpolation works by finding an existing placeholder usage (e.g. `detail.deleteConfirm` uses `{port}`); mirror it exactly for `{time}`.

- [ ] **Step 2: Write the failing test**

Create `frontend/test/settings.test.mjs`:

```javascript
/* Tests for frontend/js/settings.js — pure HTML builders for the Automation
   panel. String assertions only; no DOM. */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { automationCardsHtml } from '../js/settings.js?v=test5';

const base = {
  agent_token: false,
  metrics: true,
  webhook: false,
  history_days: 7,
  events_stream: true,
  suggest_peers: false,
};

test('connect card renders both MCP variants and curl without token', () => {
  const html = automationCardsHtml(base);
  assert.match(html, /docker","args":\["exec","-i","port-light","python","mcp\/server\.py"/);
  assert.match(html, /mcp\/server\.py/);
  assert.doesNotMatch(html, /X-Agent-Token/);
});

test('token gate adds placeholder header line only', () => {
  const html = automationCardsHtml(Object.assign({}, base, { agent_token: true }));
  assert.match(html, /X-Agent-Token: &lt;your-token&gt;/);
  assert.doesNotMatch(html, /sekrit/);
});

test('activity disabled note when history off', () => {
  const html = automationCardsHtml(Object.assign({}, base, { history_days: 0 }));
  assert.ok(html.includes('data-auto="activity-disabled"'));
});

test('lease rows render ports with release hooks', () => {
  const a = Object.assign({}, base, {
    agent_events: {
      total: 2,
      last_used_at: Math.floor(Date.now() / 1000) - 300,
      active_leases: 1,
      recent: [{ ts: Math.floor(Date.now() / 1000) - 300, count: 1,
                 scope: 'all:1/1', label: 'preview', leased: true }],
      lease_rows: [{ port: 8081, label: 'preview',
                     expires_at: Math.floor(Date.now() / 1000) + 600 }],
    },
  });
  const html = automationCardsHtml(a);
  assert.match(html, /data-release-port="8081"/);
  assert.ok(html.includes('>preview<'));
  assert.match(html, /data-auto-summary/);
});
```

Note the `?v=test5` query — follow whatever cache-busting query pattern existing tests use (check `router.test.mjs`); adjust the import specifier to match convention.

- [ ] **Step 3: Run test to verify it fails**

Run: `node --test frontend/test/settings.test.mjs`
Expected: FAIL — `automationCardsHtml` not exported

- [ ] **Step 4: Wire tab + panel + builder**

1. `frontend/js/state.js:4`:

```javascript
export const SETTINGS_PANELS = ['appearance', 'occupancy', 'automation', 'advanced'];
```

2. `frontend/index.html` between the occupancy and advanced nav buttons:

```html
<button type="button" role="tab" id="settings-tab-automation" data-settings-panel="automation" aria-controls="settings-panel-automation" aria-selected="false" tabindex="-1" data-i18n="settings.nav.automation">Automation</button>
```

3. In `frontend/index.html` locate the static settings panels container (search `data-settings-panel="advanced"`); add the empty automation panel div alongside:

```html
<div class="settings-panel" id="settings-panel-automation" role="tabpanel" data-settings-panel="automation" aria-labelledby="settings-tab-automation"></div>
```

If panels are generated wholly by JS instead, mirror exactly how `appearance` etc. get their containers — match the existing mechanism.

4. In `frontend/js/settings.js` add the pure builder (place above `renderSettingsForm`). Use the file's existing helpers (`escapeHtml`, `t`, `kvRow`, `settingsCard`). Snippet ids must be unique and stable: `al-mcp-docker`, `al-mcp-src`, `al-curl`.

```javascript
function snippetBlock(captionKey, id, code) {
  return '<div class="snippet"><p class="snippet-cap">' + escapeHtml(t(captionKey)) + '</p>' +
    '<pre id="' + id + '">' + escapeHtml(code) + '</pre>' +
    '<button type="button" class="btn-secondary" data-copy="' + id + '" data-label="' +
    escapeHtml(t('settings.auto.connect.copy')) + '">' +
    escapeHtml(t('settings.auto.connect.copy')) + '</button></div>';
}

export function automationCardsHtml(a) {
  const origin = location.origin;
  const mcpDocker = JSON.stringify({
    mcpServers: {
      'port-light': {
        command: 'docker',
        args: ['exec', '-i', 'port-light', 'python', 'mcp/server.py'],
        env: { PORT_LIGHT_URL: 'http://127.0.0.1:2100' },
      },
    },
  }, null, 2);
  const mcpSource = JSON.stringify({
    mcpServers: {
      'port-light': {
        command: 'python',
        args: ['/path/to/port-light/mcp/server.py'],
        env: { PORT_LIGHT_URL: origin },
      },
    },
  }, null, 2);
  let curl = 'curl -s "' + origin + '/api/ports/suggest?count=2&reserve=true&ttl=3600&label=preview"';
  if (a.agent_token) curl += ' \\\n  -H "X-Agent-Token: <your-token>"';

  const connect =
    snippetBlock('settings.auto.connect.mcpDocker', 'al-mcp-docker', mcpDocker) +
    snippetBlock('settings.auto.connect.mcpSource', 'al-mcp-src', mcpSource) +
    snippetBlock('settings.auto.connect.skill', 'al-skill',
      'docker exec port-light cat /app/skills/port-light/SKILL.md' +
      ' > ~/.claude/skills/port-light/SKILL.md') +
    '<p class="muted">' + escapeHtml(t('settings.auto.connect.skillHint')) + '</p>' +
    snippetBlock('settings.auto.connect.curl', 'al-curl', curl) +
    (a.agent_token ? '<p class="muted">' + escapeHtml(t('settings.auto.connect.curlToken')) + '</p>' : '');

  const statusRows = [
    kvRow('settings.auto.agentToken',
      t(a.agent_token ? 'settings.on' : 'settings.off'),
      a.agent_token ? 'settings.on' : 'settings.off'),
    kvRow('settings.auto.suggest', t('settings.auto.suggestValue'), 'settings.auto.suggestValue'),
    kvRow('settings.auto.metrics', t(a.metrics ? 'settings.on' : 'settings.off'), ''),
    kvRow('settings.auto.webhook', t(a.webhook ? 'settings.on' : 'settings.off'), ''),
    kvRow('settings.auto.history', a.history_days > 0 ? String(a.history_days) : t('settings.off'), ''),
    kvRow('settings.auto.events', t(a.events_stream ? 'settings.on' : 'settings.off'), ''),
  ].join('');

  const ev = a.agent_events || null;
  const activity = ev
    ? '<p class="auto-summary" data-auto-summary>' +
      escapeHtml(t('settings.auto.activity.total')) + ': ' + ev.total + ' · ' +
      escapeHtml(t('settings.auto.activeLeases')) + ': ' + (ev.active_leases || 0) + '</p>' +
      '<table class="auto-table"><thead><tr>' +
      ['thTime', 'thCount', 'thScope', 'thLabel', 'thLeased']
        .map(k => '<th>' + escapeHtml(t('settings.auto.activity.' + k)) + '</th>').join('') +
      '</tr></thead><tbody>' +
      (ev.recent || []).map(r =>
        '<tr><td>' + new Date(r.ts * 1000).toLocaleString() + '</td><td>' + r.count +
        '</td><td>' + escapeHtml(r.scope) + '</td><td>' + escapeHtml(r.label || '—') +
        '</td><td>' + (r.leased ? '✓' : '—') + '</td></tr>').join('') +
      '</tbody></table>'
    : '<p class="muted" data-auto="activity-disabled">' +
      escapeHtml(t('settings.auto.activity.disabled')) + '</p>';

  const leases = ev && (ev.lease_rows || []).length
    ? (ev.lease_rows).map(l =>
      '<div class="lease-row"><span class="lease-port">' + l.port + '</span>' +
      '<span class="lease-label">' + escapeHtml(l.label || '—') + '</span>' +
      '<button type="button" class="btn-delete" data-release-port="' + l.port + '">' +
      escapeHtml(t('settings.auto.leases.release')) + '</button></div>').join('')
    : '<p class="muted">' + escapeHtml(t('settings.auto.leases.none')) + '</p>';

  return settingsCard('settings.auto.connect.title', 'settings.auto.connect.blurb', connect) +
    settingsCard('settings.auto.status.title', 'settings.auto.status.blurb', statusRows) +
    settingsCard('settings.auto.activity.title', 'settings.auto.activity.blurb', activity) +
    settingsCard('settings.auto.leases.title', 'settings.auto.leases.blurb', leases);
}
```

5. In `renderSettingsForm`, replace the old composition: remove the trailing third argument automation card from the advanced panel call, and insert the automation panel between occupancy and advanced:

```javascript
      settingsPanelHtml('occupancy',
        settingsCard('settings.groups.grid.title', 'settings.groups.grid.blurb', rowsFor(byGroup.grid || [])) +
        settingsCard('hosts.title', 'hosts.blurb', '<div id="settings-peers"></div>')) +
      settingsPanelHtml('automation', automationCardsHtml(S.meta && S.meta.automation ? S.meta.automation : {})) +
      settingsPanelHtml('advanced',
        settingsCard('settings.groups.scanning.title', 'settings.groups.scanning.blurb', rowsFor(byGroup.scanning || [])) +
        settingsCard('settings.groups.links.title', 'settings.groups.links.blurb', rowsFor(byGroup.links || [])) +
        extraAdvanced +
        settingsCard('settings.host.title', 'settings.host.blurb', '<div id="settings-env-only"></div>'));
```

Delete the old `#settings-automation` kvRow block (the `autoHost` section, lines ~417-439) — its content moved into the Status card.

6. Add the delegated click handler once per page load (module-level guard), covering copy and release:

```javascript
let _delegated = false;
function ensureAutomationDelegates() {
  if (_delegated) return;
  _delegated = true;
  document.addEventListener('click', function (e) {
    const copyBtn = e.target.closest('[data-copy]');
    if (copyBtn) {
      const src = document.getElementById(copyBtn.getAttribute('data-copy'));
      if (!src) return;
      navigator.clipboard.writeText(src.textContent.trim()).then(function () {
        copyBtn.textContent = t('settings.auto.connect.copied');
        setTimeout(function () {
          copyBtn.textContent = copyBtn.getAttribute('data-label') ||
            t('settings.auto.connect.copy');
        }, 1200);
      });
      return;
    }
    const relBtn = e.target.closest('[data-release-port]');
    if (relBtn) releaseLease(Number(relBtn.getAttribute('data-release-port')), relBtn);
  });
}
```

Call `ensureAutomationDelegates()` from `renderSettingsForm`. Define `releaseLease` in Task 6 — for this task stub it as:

```javascript
async function releaseLease(port, btn) {
  btn.textContent = t('settings.auto.leases.released');
  btn.disabled = true;
}
```

(Task 6 replaces the body.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `node --test frontend/test/`
Expected: PASS including new settings suite

Run: `pytest tests/test_i18n.py -q`
Expected: PASS (locale parity)

Manual smoke: `python -m uvicorn backend.main:app --port 2100`, open `#/settings/automation`, confirm tab switches, snippets show, copy button flashes 已复制/Copied!.

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/js/state.js frontend/js/settings.js frontend/locales frontend/test/settings.test.mjs
git commit -m "Add Automation settings tab with connect guidance and status"
```

---

### Task 6: Live activity + lease release

**Files:**
- Modify: `frontend/js/settings.js` (`releaseLease` body, meta refresh)

**Interfaces:**
- Consumes: `api(path, opts)` from `./api.js` (same helper `saveSettingsPage` uses); Task 2's `DELETE /api/manual-ports/{port}` and refreshed `/api/meta`.

- [ ] **Step 1: Replace the `releaseLease` stub body**

```javascript
async function releaseLease(port, btn) {
  btn.disabled = true;
  const res = await api('/api/manual-ports/' + port, { method: 'DELETE' });
  if (!res.ok) {
    btn.disabled = false;
    return;
  }
  const metaRes = await api('/api/meta');
  if (metaRes.ok) {
    const { S } = await import('./state.js?v=' + CACHE_V);
    S.meta = await metaRes.json();
  }
  rerenderAutomationCards();
}

function rerenderAutomationCards() {
  const panel = document.getElementById('settings-panel-automation');
  if (!panel || !S.meta) return;
  panel.innerHTML = automationCardsHtml(S.meta.automation || {});
}
```

Match how the rest of the codebase imports state (static import at top vs dynamic) — prefer the existing static `S` already imported by settings.js and skip the dynamic import entirely; the snippet above shows the fallback only if `S` isn't in scope. `CACHE_V` refers to whatever cache-busting version constant the modules use (`?v=60` today); reuse the established pattern.

- [ ] **Step 2: Verify**

Run: `node --test frontend/test/`
Expected: PASS

Manual smoke: reserve a short lease via
`curl "http://127.0.0.1:2100/api/ports/suggest?count=1&ttl=120&label=smoke"`,
open Automation → Active leases, click Release — row disappears, summary count drops. Then re-run a suggest and see the activity table grow.

- [ ] **Step 3: Commit**

```bash
git add frontend/js/settings.js
git commit -m "Release leases from the automation panel and refresh activity"
```

---

### Task 7: Grid badge + drawer countdown + styles

**Files:**
- Modify: `frontend/js/grid.js` (~line 213 area, card template)
- Modify: `frontend/js/detail.js` (after manual info-box ~215)
- Modify: `frontend/style.css` (near `.settings-*` rules ~853+, and card chip styles)
- Test: extend `frontend/test/leases.test.mjs`

**Interfaces:**
- Consumes: Task 4 `isLease`, `remainingSeconds`, `fmtRemaining`; i18n keys `grid.leaseBadge`, `detail.expiresIn` from Task 5.

- [ ] **Step 1: Extend the failing tests**

Append to `frontend/test/leases.test.mjs`:

```javascript
test('badge text derivation matches drawer countdown', () => {
  // Same inputs both surfaces use — pins the contract between them.
  const exp = NOW + 58 * 60;
  assert.equal(fmtRemaining(remainingSeconds(exp, NOW)), '58m');
  const expOld = NOW + 25 * 3600;
  assert.equal(fmtRemaining(remainingSeconds(expOld, NOW)), '25h');
});
```

Run: `node --test frontend/test/leases.test.mjs` — expected PASS already (pure math); this pins the contract the UI must use.

- [ ] **Step 2: Grid badge**

In `frontend/js/grid.js`, find the card-template function that renders the title/name area (search `manual_label`, ~line 213, and the card html builder around line ~516). Import the helpers following the file's existing import style (note the `?v=` suffix convention used by sibling imports):

```javascript
import { isLease } from './leases.js?v=61';
```

(Bump the version segment consistently with however other cross-module imports are versioned in this changeset.)

Inside the card markup where the name/label renders, append:

```javascript
(isLease(p)
  ? '<span class="lease-badge" role="img" aria-label="' + escapeHtml(t('grid.leaseBadge')) +
    '" title="' + escapeHtml(t('grid.leaseBadge')) + '"></span>'
  : '')
```

Match the surrounding string-concatenation style exactly.

- [ ] **Step 3: Drawer countdown**

In `frontend/js/detail.js`, import:

```javascript
import { isLease, remainingSeconds, fmtRemaining } from './leases.js?v=61';
```

Immediately after the manual info-box block (~lines 214-217), add:

```javascript
if (isLease(p)) {
  const left = fmtRemaining(remainingSeconds(p.expires_at));
  html += '<div class="info-box"><span class="info-name">' +
    escapeHtml(t('detail.expiresIn', { time: left })) + '</span></div>';
}
```

Mirror exactly how `detail.deleteConfirm` performs `{port}` interpolation when calling `t()`.

- [ ] **Step 4: Styles**

Append to `frontend/style.css` near the settings styles:

```css
.lease-badge {
  display: inline-block;
  width: .55em; height: .55em;
  margin-left: .35em;
  border: 1px solid var(--amber, #d79921);
  border-radius: 50%;
  vertical-align: middle;
}
.lease-badge::before {
  content: '';
  position: absolute;
}
.auto-table { width: 100%; border-collapse: collapse; font-size: .85em; }
.auto-table th, .auto-table td {
  text-align: left; padding: .25rem .45rem;
  border-bottom: 1px solid var(--border, rgba(128,128,128,.3));
}
.snippet { margin-bottom: .6rem; }
.snippet-cap { margin: .35rem 0 .2rem; font-weight: 600; }
.snippet pre {
  background: var(--surface, rgba(128,128,128,.08));
  padding: .5rem; overflow-x: auto; font-size: .8em; margin: 0 0 .25rem;
}
.auto-summary { font-weight: 600; }
.lease-row {
  display: flex; align-items: center; gap: .6rem;
  padding: .3rem 0; border-bottom: 1px solid var(--border, rgba(128,128,128,.3));
}
.lease-port { font-weight: 600; min-width: 3.5em; }
.lease-row .btn-delete { margin-left: auto; }
.muted { opacity: .72; }
```

First check which CSS custom properties actually exist (`grep -n "\-\-amber\|--border\|--surface" frontend/style.css | head`) and substitute the real names — the fallback values shown are safety nets, not the primary choice.

- [ ] **Step 5: Verify everything**

Run: `node --test frontend/test/`
Expected: PASS

Run: `pytest -q && ruff check backend tests`
Expected: PASS, clean

Manual smoke: `curl "http://127.0.0.1:2100/api/ports/suggest?count=1&ttl=1800&label=preview"`, reload grid — amber card for the reserved port shows the small round badge; open the drawer — countdown reads e.g. "Expires in 30m"; wait past expiry (or lower ttl) — card disappears on next refresh.

- [ ] **Step 6: Commit**

```bash
git add frontend/js/grid.js frontend/js/detail.js frontend/style.css frontend/test/leases.test.mjs
git commit -m "Surface leases on grid cards and in the detail drawer"
```

---

## Self-Review Notes

- Spec coverage: guidance panel (Task 5), event stream + monitoring (Tasks 1-2, 6), lease visibility grid/drawer/list (Tasks 4, 6, 7), packaging + docs (Task 3). Non-goals untouched.
- Type consistency: `automationCardsHtml(a)` signature fixed in Task 5, reused in Task 6's `rerenderAutomationCards`; `lease_rows` shape `{port, label, expires_at}` identical between Task 2 producer and Task 5 consumer.
- Known risk flagged inline: the old `renderSettingsForm` passed a third argument to `settingsPanelHtml` (which takes two) — Task 5 removes that call site entirely; if the automation card was silently invisible before, this fixes it. Executor should verify the panel appears in manual smoke regardless.
