import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "action" / "guard.sh"

BASE_ENV = {
    "EVENT_NAME": "pull_request",
    "PR_DRAFT": "false",
    "PR_HEAD_REPO": "owner/repo",
    "GITHUB_REPOSITORY": "owner/repo",
    "PR_HEAD_REF": "feature",
}


def run_guard(tmp_path, **overrides):
    """Run guard.sh with a clean env; an override of None removes that variable."""
    output = tmp_path / "github_output"
    output.write_text("")
    env = {"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(output), **BASE_ENV}
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    proc = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, check=False
    )
    outputs = {}
    for line in output.read_text().splitlines():
        key, _, value = line.partition("=")
        outputs[key] = value
    return proc, outputs


def assert_skipped(proc, outputs):
    assert proc.returncode == 0, proc.stderr
    assert outputs == {"skip": "true", "status": "skipped"}
    assert "::notice::readme-stack skipped:" in proc.stdout


def test_normal_pr_proceeds(tmp_path):
    proc, outputs = run_guard(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert outputs == {"skip": "false"}
    assert "::notice::" not in proc.stdout


def test_draft_skipped(tmp_path):
    assert_skipped(*run_guard(tmp_path, PR_DRAFT="true"))


def test_fork_skipped(tmp_path):
    assert_skipped(*run_guard(tmp_path, PR_HEAD_REPO="someone/repo"))


def test_missing_head_repo_skipped(tmp_path):
    assert_skipped(*run_guard(tmp_path, PR_HEAD_REPO=None))


def test_own_stacked_pr_skipped(tmp_path):
    assert_skipped(*run_guard(tmp_path, PR_HEAD_REF="readme-stack/pr-7"))


@pytest.mark.parametrize("event", ["push", "pull_request_target", ""])
def test_non_pull_request_event_skipped(tmp_path, event):
    assert_skipped(*run_guard(tmp_path, EVENT_NAME=event))


def test_custom_prefix_skips_matching_head(tmp_path):
    assert_skipped(*run_guard(tmp_path, BRANCH_PREFIX="docs-bot/", PR_HEAD_REF="docs-bot/pr-7"))


def test_custom_prefix_ignores_default_prefix(tmp_path):
    proc, outputs = run_guard(tmp_path, BRANCH_PREFIX="docs-bot/", PR_HEAD_REF="readme-stack/pr-7")
    assert proc.returncode == 0, proc.stderr
    assert outputs == {"skip": "false"}


def test_empty_prefix_falls_back_to_default(tmp_path):
    proc, outputs = run_guard(tmp_path, BRANCH_PREFIX="")
    assert proc.returncode == 0, proc.stderr
    assert outputs == {"skip": "false"}
    assert_skipped(*run_guard(tmp_path, BRANCH_PREFIX="", PR_HEAD_REF="readme-stack/pr-7"))


def test_missing_github_output_fails(tmp_path):
    proc, outputs = run_guard(tmp_path, GITHUB_OUTPUT=None)
    assert proc.returncode != 0
    assert "GITHUB_OUTPUT" in proc.stderr
    assert outputs == {}
