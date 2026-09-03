import json
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import check_release_ci as gate


SHA = "a" * 40
REPOSITORY = "example/port-light"
ROOT = Path(__file__).resolve().parents[1]


def ci_run(**overrides):
    return {
        "head_sha": SHA,
        "head_branch": "main",
        "event": "push",
        "run_number": 1,
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/example/port-light/actions/runs/1",
        **overrides,
    }


def test_selects_only_main_push_for_exact_commit():
    runs = [
        ci_run(head_sha="b" * 40),
        ci_run(head_branch="dev"),
        ci_run(head_branch="v1.0.0"),
        ci_run(event="pull_request"),
    ]
    assert gate.latest_main_run(runs, SHA) is None
    assert gate.latest_main_run([*runs, ci_run()], SHA) == ci_run()


def test_newer_run_takes_precedence_over_older_success():
    newer = ci_run(run_number=2, conclusion="failure")
    assert gate.latest_main_run([newer, ci_run()], SHA) == newer


def test_passed_ci_does_not_wait(monkeypatch, capsys):
    monkeypatch.setattr(gate, "fetch_runs", lambda *_: [ci_run()])
    monkeypatch.setattr(gate.time, "sleep", lambda _: pytest.fail("Unexpected wait"))
    gate.wait_for_ci(REPOSITORY, SHA)
    assert "Main CI passed:" in capsys.readouterr().out


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "skipped", "neutral", None])
def test_unsuccessful_ci_blocks_release(monkeypatch, conclusion):
    monkeypatch.setattr(gate, "fetch_runs", lambda *_: [ci_run(conclusion=conclusion)])
    with pytest.raises(RuntimeError, match="release blocked"):
        gate.wait_for_ci(REPOSITORY, SHA)


def test_waits_for_registration_and_completion(monkeypatch):
    responses = iter([
        [], [ci_run(status="queued", conclusion=None)],
        [ci_run(status="in_progress", conclusion=None)], [ci_run()],
    ])
    sleeps = []
    monkeypatch.setattr(gate, "fetch_runs", lambda *_: next(responses))
    monkeypatch.setattr(gate.time, "sleep", sleeps.append)
    gate.wait_for_ci(REPOSITORY, SHA)
    assert sleeps == [15, 15, 15]


@pytest.mark.parametrize("runs", [[], [ci_run(status="in_progress", conclusion=None)]])
def test_wait_has_a_deadline(monkeypatch, runs):
    ticks = iter([0, 1, 2])
    sleeps = []
    monkeypatch.setattr(gate, "fetch_runs", lambda *_: runs)
    monkeypatch.setattr(gate.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(gate.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="within 2 seconds"):
        gate.wait_for_ci(REPOSITORY, SHA, timeout=2)
    assert sleeps == [1]


def test_api_query_is_scoped_to_ci_main_push_and_commit(monkeypatch):
    def fake_run(command, **kwargs):
        assert command == [
            "gh", "api", "--method", "GET",
            f"repos/{REPOSITORY}/actions/workflows/ci.yml/runs",
            "-f", "branch=main", "-f", "event=push", "-f", f"head_sha={SHA}",
            "-f", "per_page=100",
        ]
        assert kwargs == {"check": True, "capture_output": True, "text": True, "timeout": 30}
        return subprocess.CompletedProcess(command, 0, json.dumps({"workflow_runs": [ci_run()]}))

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    assert gate.fetch_runs(REPOSITORY, SHA) == [ci_run()]


def test_api_failure_does_not_allow_release(monkeypatch):
    def fail(*_):
        raise subprocess.CalledProcessError(1, ["gh", "api"])

    monkeypatch.setattr(gate, "fetch_runs", fail)
    with pytest.raises(subprocess.CalledProcessError):
        gate.wait_for_ci(REPOSITORY, SHA)


def workflow(name):
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def test_only_ci_and_release_workflows_remain():
    assert {p.name for p in (ROOT / ".github/workflows").glob("*.y*ml")} == {"ci.yml", "release.yml"}


def test_ci_triggers_and_coverage():
    ci = workflow("ci.yml")
    assert ci["on"] == {"push": {"branches": ["main"]}, "pull_request": {"branches": ["main"]}}
    assert ci["concurrency"]["cancel-in-progress"] == "true"
    assert ci["permissions"] == {"contents": "read"}
    backend = ci["jobs"]["test"]
    assert backend["strategy"]["matrix"]["python-version"] == ["3.11", "3.12", "3.13"]
    lint = next(step for step in backend["steps"] if "ruff check" in step.get("run", ""))
    assert lint["if"] == "matrix.python-version == '3.13'"
    assert any("npm run smoke:browser" in step.get("run", "") for step in ci["jobs"]["frontend"]["steps"])


def test_release_requires_ci_before_publishing_and_pins_release_source():
    release = workflow("release.yml")
    assert release["on"] == {"push": {"tags": ["v*"]}}
    assert release["concurrency"]["cancel-in-progress"] == "false"
    build = release["jobs"]["build-and-push"]
    steps = build["steps"]
    gate_index = next(i for i, step in enumerate(steps) if "check_release_ci.py" in step.get("run", ""))
    login_index = next(i for i, step in enumerate(steps) if step.get("uses", "").startswith("docker/login-action@"))
    assert gate_index < login_index
    source = next(step for step in steps if step.get("id") == "source")
    assert "git rev-parse HEAD" in source["run"]
    assert "git merge-base --is-ancestor" in source["run"]
    assert build["permissions"]["actions"] == "read"
    publish = release["jobs"]["github-release"]
    assert publish["needs"] == "build-and-push"
    assert publish["steps"][0]["with"]["ref"] == "${{ needs.build-and-push.outputs.sha }}"
