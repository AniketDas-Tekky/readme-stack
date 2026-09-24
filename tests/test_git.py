"""Tests for readme_stack.git."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from readme_stack import git
from readme_stack.git import Diff, GitError, diff, repo_files, repo_root


# 1
def test_repo_root_from_subdirectory(git_repo):
    git_repo.commit({"src/pkg/mod.py": "x = 1\n"})
    assert repo_root(git_repo / "src" / "pkg") == Path(git_repo).resolve()


# 2
def test_repo_root_not_a_repo(git_repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(GitError, match="not a git repository"):
        repo_root(outside)
    with pytest.raises(GitError, match="not a git repository"):
        repo_root(tmp_path / "missing")


# 3
def test_repo_files_tracked_untracked_and_ignored(git_repo):
    git_repo.commit({".gitignore": "build/\n*.log\n", "tracked.py": "a = 1\n"})
    git_repo.write({"untracked.py": "b = 2\n", "build/out.txt": "x\n", "debug.log": "y\n"})
    assert repo_files(git_repo) == [".gitignore", "tracked.py", "untracked.py"]


# 4
def test_repo_files_denylist(git_repo):
    git_repo.commit(
        {
            ".env": "SECRET=1\n",
            ".env.local": "SECRET=2\n",
            "certs/x.pem": "-----BEGIN-----\n",
            "id.key": "key\n",
            "env.py": "ok = True\n",
        }
    )
    assert repo_files(git_repo) == ["env.py"]


# 5
def test_repo_files_excludes_binary(git_repo):
    git_repo.commit({"image.bin": b"\x89PNG\x00\x01\x02", "text.md": "héllo ✓\n"})
    assert repo_files(git_repo) == ["text.md"]


# 6
def test_repo_files_skips_deleted_and_symlink(git_repo):
    git_repo.commit({"gone.py": "x\n", "real.py": "y\n"})
    (git_repo / "gone.py").unlink()
    os.symlink("real.py", git_repo / "link.py")
    assert repo_files(git_repo) == ["real.py"]


# 7
def test_repo_files_sorted_posix_nested(git_repo):
    git_repo.commit({"src/pkg/mod.py": "m\n", "b.txt": "b\n", "a/z.txt": "z\n", "src/a.py": "a\n"})
    files = repo_files(git_repo)
    assert files == ["a/z.txt", "b.txt", "src/a.py", "src/pkg/mod.py"]
    assert files == sorted(files)
    assert all("\\" not in f for f in files)


# 8
def test_diff_range(git_repo):
    git_repo.commit({"app.py": "print('one')\n"})
    git_repo.commit({"app.py": "print('two')\n"})
    result = diff(git_repo, "HEAD~1..HEAD")
    assert "app.py" in result.stat
    assert "-print('one')" in result.patch
    assert "+print('two')" in result.patch
    assert result.truncated is False
    assert result.empty is False


# 9
def test_diff_excludes_readme(git_repo):
    git_repo.commit({"README.md": "# old\n", "app.py": "a = 1\n"})
    git_repo.commit({"README.md": "# new\n", "app.py": "a = 2\n"})
    result = diff(git_repo, "HEAD~1..HEAD")
    assert "README.md" not in result.stat
    assert "README.md" not in result.patch
    assert "app.py" in result.stat


def test_diff_only_readme_change_is_empty(git_repo):
    git_repo.commit({"README.md": "# old\n"})
    git_repo.commit({"README.md": "# new\n"})
    assert diff(git_repo, "HEAD~1..HEAD").empty is True


# 10
def test_diff_empty_range(git_repo):
    git_repo.commit({"app.py": "a = 1\n"})
    result = diff(git_repo, "HEAD..HEAD")
    assert result.empty is True
    assert result == Diff(stat="", patch="", truncated=False)


# 11
def test_diff_unknown_rev(git_repo):
    git_repo.commit({"app.py": "a = 1\n"})
    with pytest.raises(GitError, match="git diff failed"):
        diff(git_repo, "nope..HEAD")


# 12
@pytest.mark.parametrize(
    "bad", ["--output=/tmp/x", "-p", " --output=/tmp/x", "\t-p", "\n--output=/tmp/x", "", "   "]
)
def test_diff_rejects_bad_range_without_running_git(git_repo, monkeypatch, bad):
    def boom(*args, **kwargs):
        raise AssertionError("git must not be called")

    monkeypatch.setattr(git.subprocess, "run", boom)
    with pytest.raises(GitError):
        diff(git_repo, bad)


# 13
def test_diff_truncation(git_repo):
    git_repo.commit({"big.txt": "".join(f"line {i}\n" for i in range(200))})
    git_repo.commit({"big.txt": "".join(f"changed {i}\n" for i in range(200))})
    full = diff(git_repo, "HEAD~1..HEAD").patch
    max_chars = 500
    result = diff(git_repo, "HEAD~1..HEAD", max_chars=max_chars)
    assert result.truncated is True
    head, sep, note = result.patch.partition("\n… diff truncated (")
    assert sep
    assert len(head) <= max_chars
    assert full.startswith(head + "\n")  # cut at a line boundary
    remaining = len(full) - len(head)
    assert note == f"{remaining} more characters); use read_file to inspect full files"


def test_diff_under_cap_not_truncated(git_repo):
    git_repo.commit({"app.py": "a = 1\n"})
    git_repo.commit({"app.py": "a = 2\n"})
    result = diff(git_repo, "HEAD~1..HEAD", max_chars=10_000)
    assert result.truncated is False
    assert "truncated" not in result.patch


# 14
def test_git_not_found(git_repo, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(git.subprocess, "run", missing)
    with pytest.raises(GitError, match="^git executable not found$"):
        repo_files(git_repo)


def test_git_timeout(git_repo, monkeypatch):
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr(git.subprocess, "run", slow)
    with pytest.raises(GitError, match="timed out"):
        repo_files(git_repo)
