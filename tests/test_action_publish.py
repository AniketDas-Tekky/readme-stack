"""Tests for scripts/action/publish.sh against a temp bare origin and a fake `gh`."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "action" / "publish.sh"

PR_NUMBER = "7"
BRANCH = "readme-stack/pr-7"
NEW_PR = "8"
EXISTING_PR = "12"
EXISTING_URL = "https://github.com/o/r/pull/12"

FAKE_GH = """\
#!{python}
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")

def fail():
    sys.stderr.write("fake gh: unexpected call: %r\\n" % (args,))
    sys.exit(1)

if args == ["--version"]:
    version = os.environ.get("FAKE_GH_VERSION", "2.101.0")
    print("gh version %s (2026-09-15)" % version)
    print("https://github.com/cli/cli/releases/tag/v%s" % version)
elif args[:2] == ["extension", "install"]:
    pass
elif args[:2] == ["pr", "list"]:
    existing = os.environ.get("FAKE_GH_EXISTING_PR")
    if existing:
        print("%s\\t%s" % (existing, os.environ["FAKE_GH_EXISTING_URL"]))
elif args[:2] == ["pr", "create"]:
    print("https://github.com/o/r/pull/%s" % os.environ.get("FAKE_GH_NEW_PR", "8"))
elif args[:2] in (["pr", "edit"], ["pr", "close"], ["pr", "comment"]):
    pass
elif args[:2] == ["stack", "link"]:
    pass
elif args[:1] == ["api"]:
    print("{{}}")
else:
    fail()
"""


def git(cwd, *args, env=None):
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated git config plus a fake `gh` first on PATH."""
    gitconfig = tmp_path / "gitconfig"
    # Contributor identity comes from the "global" config so the script's repo-local
    # bot identity takes precedence (GIT_AUTHOR_* env vars would override it).
    gitconfig.write_text("[user]\n\tname = Contributor\n\temail = contrib@example.com\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "gh"
    fake.write_text(FAKE_GH.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    e = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GH_", "GITHUB_"))}
    e.update(
        PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
        GIT_CONFIG_GLOBAL=str(gitconfig),
        GIT_CONFIG_NOSYSTEM="1",
        FAKE_GH_LOG=str(tmp_path / "gh.log"),
    )
    return e


@pytest.fixture
def repo(tmp_path, env):
    """A bare origin with `main` and `feature`, plus a clone checked out on `feature`."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin), env=env)
    clone = tmp_path / "clone"
    git(tmp_path, "clone", str(origin), str(clone), env=env)
    git(clone, "switch", "-c", "main", env=env)
    (clone / "README.md").write_text("# Project\n")
    git(clone, "add", "README.md", env=env)
    git(clone, "commit", "-m", "init", env=env)
    git(clone, "push", "origin", "main", env=env)
    git(clone, "switch", "-c", "feature", env=env)
    (clone / "app.py").write_text("print('hi')\n")
    git(clone, "add", "app.py", env=env)
    git(clone, "commit", "-m", "feature", env=env)
    git(clone, "push", "-u", "origin", "feature", env=env)
    return clone, origin


def run_publish(tmp_path, env, clone, **extra):
    output = tmp_path / "github_output"
    summary = tmp_path / "step_summary"
    e = dict(env)
    e.update(
        GH_TOKEN="dummy-token",
        PR_NUMBER=PR_NUMBER,
        PR_BASE_REF="main",
        PR_HEAD_REF="feature",
        PR_HEAD_SHA=git(clone, "rev-parse", "HEAD", env=env),
        GITHUB_WORKSPACE=str(clone),
        GITHUB_OUTPUT=str(output),
        GITHUB_STEP_SUMMARY=str(summary),
    )
    e.update(extra)
    result = subprocess.run([str(SCRIPT)], cwd=clone, env=e, capture_output=True, text=True)
    outputs = {}
    if output.exists():
        for line in output.read_text().splitlines():
            key, _, value = line.partition("=")
            outputs[key] = value
    return result, outputs, summary


def gh_calls(env):
    log = Path(env["FAKE_GH_LOG"])
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def assert_no_forbidden(calls):
    for call in calls:
        assert call[:2] != ["stack", "submit"]
        assert "--open" not in call
        assert call[:2] != ["stack", "init"]


def remote_sha(origin, ref, env):
    return git(origin, "rev-parse", f"refs/heads/{ref}", env=env)


def remote_has(origin, ref, env):
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{ref}"], cwd=origin, env=env
    )
    return result.returncode == 0


def test_changed_without_pr_creates_and_links(tmp_path, env, repo):
    clone, origin = repo
    feature_before = remote_sha(origin, "feature", env)
    (clone / "README.md").write_text("# Project\n\nNow with app.py.\n")

    result, outputs, summary = run_publish(tmp_path, env, clone, MODEL="claude-x")

    assert result.returncode == 0, result.stderr
    assert outputs == {
        "status": "created",
        "pr-number": NEW_PR,
        "pr-url": f"https://github.com/o/r/pull/{NEW_PR}",
    }
    assert "status=created" in summary.read_text()
    calls = gh_calls(env)
    assert calls[0] == ["--version"]
    assert ["extension", "install", "github/gh-stack", "--pin", "v0.1.1"] in calls
    create = next(c for c in calls if c[:2] == ["pr", "create"])
    assert create[create.index("--base") + 1] == "feature"
    assert create[create.index("--head") + 1] == BRANCH
    assert create[create.index("--title") + 1] == f"docs: update README for #{PR_NUMBER}"
    body = create[create.index("--body") + 1]
    assert f"#{PR_NUMBER}" in body and feature_before in body and "claude-x" in body
    assert calls[-1] == ["stack", "link", "--base", "main", PR_NUMBER, NEW_PR]
    assert not any(c[:2] == ["pr", "edit"] for c in calls)
    assert_no_forbidden(calls)

    # Our branch is on origin, one README commit on top of the feature head.
    assert remote_has(origin, BRANCH, env)
    assert git(origin, "rev-parse", f"{BRANCH}^", env=env) == feature_before
    assert git(origin, "show", f"{BRANCH}:README.md", env=env).endswith("Now with app.py.")
    assert git(origin, "log", "-1", "--format=%an <%ae>|%s", BRANCH, env=env) == (
        "readme-stack[bot] <readme-stack[bot]@users.noreply.github.com>"
        f"|docs: update README for #{PR_NUMBER}"
    )
    assert remote_sha(origin, "feature", env) == feature_before


def test_changed_with_existing_pr_resets_and_updates(tmp_path, env, repo):
    clone, origin = repo
    head = git(clone, "rev-parse", "HEAD", env=env)
    # A stale README branch from an earlier run, on an unrelated older commit.
    git(clone, "push", "origin", f"main:refs/heads/{BRANCH}", env=env)
    stale = remote_sha(origin, BRANCH, env)
    (clone / "README.md").write_text("# Project\n\nUpdated again.\n")

    result, outputs, _ = run_publish(
        tmp_path, env, clone, FAKE_GH_EXISTING_PR=EXISTING_PR, FAKE_GH_EXISTING_URL=EXISTING_URL
    )

    assert result.returncode == 0, result.stderr
    assert outputs == {"status": "updated", "pr-number": EXISTING_PR, "pr-url": EXISTING_URL}
    calls = gh_calls(env)
    edit = next(c for c in calls if c[:2] == ["pr", "edit"])
    assert edit[2] == EXISTING_PR
    assert edit[edit.index("--title") + 1] == f"docs: update README for #{PR_NUMBER}"
    assert "--body" in edit
    assert not any(c[:2] == ["pr", "create"] for c in calls)
    assert calls[-1] == ["stack", "link", "--base", "main", PR_NUMBER, EXISTING_PR]
    assert_no_forbidden(calls)

    new = remote_sha(origin, BRANCH, env)
    assert new != stale
    assert git(origin, "rev-parse", f"{BRANCH}^", env=env) == head  # reset to head.sha
    assert git(origin, "show", f"{BRANCH}:README.md", env=env).endswith("Updated again.")
    assert remote_sha(origin, "feature", env) == head


def test_unchanged_with_existing_pr_closes_and_deletes_branch(tmp_path, env, repo):
    clone, origin = repo
    git(clone, "push", "origin", f"feature:refs/heads/{BRANCH}", env=env)

    result, outputs, summary = run_publish(
        tmp_path, env, clone, FAKE_GH_EXISTING_PR=EXISTING_PR, FAKE_GH_EXISTING_URL=EXISTING_URL
    )

    assert result.returncode == 0, result.stderr
    assert outputs == {"status": "closed", "pr-number": EXISTING_PR, "pr-url": EXISTING_URL}
    assert "status=closed" in summary.read_text()
    calls = gh_calls(env)
    close = next(c for c in calls if c[:2] == ["pr", "close"])
    assert close[2] == EXISTING_PR
    assert close[close.index("--comment") + 1] == (
        f"README no longer needs changes for #{PR_NUMBER}."
    )
    assert not remote_has(origin, BRANCH, env)
    assert not any(c[0] == "stack" for c in calls)
    assert_no_forbidden(calls)


def test_unchanged_without_pr_does_nothing(tmp_path, env, repo):
    clone, origin = repo
    refs_before = git(origin, "show-ref", env=env)

    result, outputs, summary = run_publish(tmp_path, env, clone)

    assert result.returncode == 0, result.stderr
    assert outputs == {"status": "unchanged", "pr-number": "", "pr-url": ""}
    assert "status=unchanged" in summary.read_text()
    calls = gh_calls(env)
    assert not any(c[0] == "stack" for c in calls)
    assert not any(c[:2] in (["pr", "create"], ["pr", "edit"], ["pr", "close"]) for c in calls)
    assert git(origin, "show-ref", env=env) == refs_before  # nothing pushed


def test_readme_commit_contains_only_readme_and_feature_untouched(tmp_path, env, repo):
    clone, origin = repo
    feature_before = remote_sha(origin, "feature", env)
    (clone / "README.md").write_text("# Project\n\nChanged.\n")
    # Other dirty and untracked files must not end up in the README commit.
    (clone / "app.py").write_text("print('dirty')\n")
    (clone / "notes.txt").write_text("scratch\n")

    result, outputs, _ = run_publish(tmp_path, env, clone)

    assert result.returncode == 0, result.stderr
    assert outputs["status"] == "created"
    files = git(origin, "show", "--name-only", "--format=", BRANCH, env=env)
    assert files.splitlines() == ["README.md"]
    assert remote_sha(origin, "feature", env) == feature_before
    assert git(origin, "show", f"{BRANCH}:app.py", env=env) == "print('hi')"


def test_old_gh_version_fails(tmp_path, env, repo):
    clone, origin = repo
    (clone / "README.md").write_text("# Project\n\nChanged.\n")

    result, outputs, _ = run_publish(tmp_path, env, clone, FAKE_GH_VERSION="2.89.3")

    assert result.returncode != 0
    assert "gh >= 2.90" in result.stderr
    assert "2.89.3" in result.stderr
    assert outputs == {}
    assert gh_calls(env) == [["--version"]]
    assert not remote_has(origin, BRANCH, env)


def test_missing_required_env_fails(tmp_path, env, repo):
    clone, _ = repo
    result, _, _ = run_publish(tmp_path, env, clone, PR_HEAD_SHA="")
    assert result.returncode != 0
    assert "PR_HEAD_SHA" in result.stderr
