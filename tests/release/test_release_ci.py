import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import check_release_ci as gate


SHA = "a" * 40
REPOSITORY = "example/port-light"
ROOT = Path(__file__).resolve().parents[2]


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
    return yaml.load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_only_ci_and_release_workflows_remain():
    assert {p.name for p in (ROOT / ".github/workflows").glob("*.y*ml")} == {"ci.yml", "release.yml"}


def test_ci_triggers_and_coverage():
    ci = workflow("ci.yml")
    assert ci["on"] == {"push": {"branches": ["main"]}, "pull_request": {"branches": ["main"]}}
    assert ci["concurrency"]["cancel-in-progress"] == "true"
    assert ci["permissions"] == {"contents": "read"}
    for name, job in ci["jobs"].items():
        assert int(job["timeout-minutes"]) > 0, f"ci job {name} missing timeout-minutes"
    backend = ci["jobs"]["test"]
    assert backend["strategy"]["matrix"]["python-version"] == ["3.11", "3.12", "3.13"]
    lint = next(step for step in backend["steps"] if "ruff check" in step.get("run", ""))
    assert lint["if"] == "matrix.python-version == '3.13'"
    for target in (
        "scripts/dev.py",
        "scripts/preview_fleet.py",
        "scripts/check_release_ci.py",
        "scripts/check_ai_install.py",
        "scripts/dockerhub_description.py",
        "scripts/locale-scaffold.py",
    ):
        assert target in lint["run"]
    wheel = next(step for step in backend["steps"] if "python -m build --wheel" in step.get("run", ""))
    assert "port-light-cli-smoke/bin/port-light" in wheel["run"]
    frontend_runs = "\n".join(step.get("run", "") for step in ci["jobs"]["frontend"]["steps"])
    for smoke in ("smoke:browser", "smoke:management", "smoke:fleet", "smoke:analysis"):
        assert f"npm run {smoke}" in frontend_runs
    container = ci["jobs"]["container"]
    assert any("docker build -t port-light:ci" in step.get("run", "") for step in container["steps"])
    assert any(
        "--entrypoint port-light" in step.get("run", "")
        and "mcp/server.py" in step.get("run", "")
        and "--entrypoint port-light-mcp" in step.get("run", "")
        for step in container["steps"]
    )
    assert any(
        "/api/health" in step.get("run", "")
        and "docker run -d" in step.get("run", "")
        for step in container["steps"]
    )


def test_release_requires_ci_before_publishing_and_pins_release_source():
    release = workflow("release.yml")
    assert release["on"] == {"push": {"tags": ["v*"]}}
    assert release["concurrency"]["cancel-in-progress"] == "false"
    for name, job in release["jobs"].items():
        assert int(job["timeout-minutes"]) > 0, f"release job {name} missing timeout-minutes"
    build = release["jobs"]["build-and-push"]
    steps = build["steps"]
    gate_index = next(i for i, step in enumerate(steps) if "check_release_ci.py" in step.get("run", ""))
    wheel_build_index = next(i for i, step in enumerate(steps) if "python -m build --wheel" in step.get("run", ""))
    wheel_smoke_index = next(
        i for i, step in enumerate(steps)
        if "port-light-cli-smoke/bin/port-light" in step.get("run", "")
        and "port-light-mcp" in step.get("run", "")
    )
    upload_index = next(i for i, step in enumerate(steps) if step.get("uses") == "actions/upload-artifact@v7")
    changelog_check_index = next(i for i, step in enumerate(steps) if step.get("name") == "Verify CHANGELOG section")
    login_index = next(i for i, step in enumerate(steps) if step.get("uses", "").startswith("docker/login-action@"))
    assert gate_index < wheel_build_index < wheel_smoke_index < upload_index < changelog_check_index < login_index
    source = next(step for step in steps if step.get("id") == "source")
    assert "git rev-parse HEAD" in source["run"]
    assert "git merge-base --is-ancestor" in source["run"]
    assert build["permissions"]["actions"] == "read"
    assert build["outputs"]["prerelease"] == "${{ steps.meta.outputs.prerelease }}"
    publish = release["jobs"]["github-release"]
    assert publish["needs"] == "build-and-push"
    assert publish["steps"][0]["with"]["ref"] == "${{ needs.build-and-push.outputs.sha }}"
    assert any(
        step.get("uses") == "actions/download-artifact@v8"
        for step in publish["steps"]
    )
    release_step = next(
        step for step in publish["steps"]
        if step.get("uses", "").startswith("softprops/action-gh-release@")
    )
    assert release_step["with"]["files"] == "dist/*.whl"
    assert release_step["with"]["prerelease"] == "${{ needs.build-and-push.outputs.prerelease == 'true' }}"
    dockerhub = release["jobs"]["dockerhub-description"]
    assert "needs.build-and-push.outputs.prerelease != 'true'" in dockerhub["if"]


def test_release_image_tags_guard_prereleases(tmp_path):
    release = workflow("release.yml")
    meta_step = next(
        step for step in release["jobs"]["build-and-push"]["steps"]
        if step.get("id") == "meta"
    )
    assert 'if [[ "$TAG" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]; then' in meta_step["run"]
    bash = shutil.which("bash")
    if bash:
        for tag, expect_prerelease, expect_latest in [
            ("v0.9.0", "false", True),
            ("v0.9.0-rc1", "true", False),
            ("v0.9.0rc1", "true", False),
            ("v0.9.0a1", "true", False),
            ("v0.9.0.dev1", "true", False),
        ]:
            output_file = tmp_path / f"out-{tag}.txt"
            output_file.write_text("", encoding="utf-8")
            subprocess.run(
                [bash, "-euo", "pipefail", "-c", meta_step["run"]],
                check=True,
                env={
                    **os.environ,
                    "REPOSITORY_OWNER": "StepaniaH",
                    "TAG": tag,
                    "GITHUB_OUTPUT": str(output_file),
                },
            )
            outputs = dict(
                line.split("=", 1)
                for line in output_file.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
            assert outputs["prerelease"] == expect_prerelease
            tags = outputs["tags"].split(",")
            assert f"stepaniah/port-light:{tag}" in tags
            assert f"ghcr.io/stepaniah/port-light:{tag}" in tags
            assert ("stepaniah/port-light:latest" in tags) is expect_latest
            assert ("ghcr.io/stepaniah/port-light:latest" in tags) is expect_latest


def test_release_changelog_extraction_fails_fast_and_uses_utf8(tmp_path):
    release = workflow("release.yml")
    notes_step = next(
        step for step in release["jobs"]["github-release"]["steps"]
        if step.get("name") == "Notes from CHANGELOG"
    )
    run_script = notes_step["run"]
    assert "Unreleased" not in run_script
    assert 'encoding="utf-8"' in run_script
    _, _, rest = run_script.partition("<<'PY'\n")
    py_code, found, _ = rest.partition("\nPY")
    assert found

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## Unreleased\n\n- Pending\n\n"
        "## 0.9.0-rc1 — 2026-11-24\n\n- RC notes\n\n"
        "## 0.8.5 — 2026-10-03\n\n- Old\n",
        encoding="utf-8",
    )
    missing = subprocess.run(
        [sys.executable, "-c", py_code],
        cwd=tmp_path,
        env={**os.environ, "TAG": "v0.9.0"},
        capture_output=True,
        text=True,
    )
    assert missing.returncode != 0
    assert "0.9.0" in missing.stderr
    assert not (tmp_path / "release-notes.md").exists()

    rc = subprocess.run(
        [sys.executable, "-c", py_code],
        cwd=tmp_path,
        env={**os.environ, "TAG": "v0.9.0-rc1"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert rc.returncode == 0
    assert (tmp_path / "release-notes.md").read_text(encoding="utf-8") == (
        "## 0.9.0-rc1 — 2026-11-24\n\n- RC notes\n"
    )

    changelog.write_text(
        "# Changelog\n\n## Unreleased\n\n- Pending\n\n"
        "## 0.9.0 — 2026-11-24\n\n- Added UTF-8 notes — 中文\n\n"
        "## 0.8.5 — 2026-10-03\n\n- Old\n",
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, "-c", py_code],
        cwd=tmp_path,
        env={**os.environ, "TAG": "v0.9.0"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert (tmp_path / "release-notes.md").read_text(encoding="utf-8") == (
        "## 0.9.0 — 2026-11-24\n\n- Added UTF-8 notes — 中文\n"
    )
