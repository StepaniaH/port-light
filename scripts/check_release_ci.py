"""Require a successful main-branch CI run for the commit being released."""

import argparse
import json
import subprocess
import time


def latest_main_run(runs: list[dict], sha: str) -> dict | None:
    matches = [
        run for run in runs
        if run.get("head_sha") == sha
        and run.get("head_branch") == "main"
        and run.get("event") == "push"
    ]
    return max(matches, key=lambda run: run["run_number"], default=None)


def fetch_runs(repository: str, sha: str) -> list[dict]:
    result = subprocess.run(
        [
            "gh", "api", "--method", "GET",
            f"repos/{repository}/actions/workflows/ci.yml/runs",
            "-f", "branch=main", "-f", "event=push", "-f", f"head_sha={sha}",
            "-f", "per_page=100",
        ],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return json.loads(result.stdout)["workflow_runs"]


def wait_for_ci(repository: str, sha: str, *, timeout: float = 900, interval: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while True:
        run = latest_main_run(fetch_runs(repository, sha), sha)
        if run and run["status"] == "completed":
            if run["conclusion"] != "success":
                raise RuntimeError(f"Main CI for {sha} ended with {run['conclusion']}; release blocked.")
            print(f"Main CI passed: {run['html_url']}", flush=True)
            return

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"No successful main CI run for {sha} within {timeout:g} seconds.")
        status = run["status"] if run else "not yet registered"
        print(f"Waiting for main CI for {sha[:12]} ({status})...", flush=True)
        time.sleep(min(interval, remaining))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", help="GitHub owner/repository")
    parser.add_argument("sha", help="The tagged commit, not the annotated tag object")
    args = parser.parse_args()
    try:
        wait_for_ci(args.repository, args.sha)
    except (RuntimeError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        parser.exit(1, f"Release CI check failed: {exc}\n")


if __name__ == "__main__":
    main()
