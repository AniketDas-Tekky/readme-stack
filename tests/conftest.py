"""Shared pytest fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_GIT_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
)


class GitRepo(Path):
    """Path to a temporary git repository, with helpers to write files and commit.

    It is a real ``Path`` (usable anywhere a path is expected). Paths derived from it
    (``repo / "src"``, ``repo.parent``) are plain ``Path`` objects.
    """

    def with_segments(self, *pathsegments: str | Path) -> Path:
        return Path(*pathsegments)

    def git(self, *args: str) -> str:
        """Run ``git <args>`` in the repo and return stdout; raises on failure."""
        result = subprocess.run(
            ["git", "-C", str(self), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def write(self, files: dict[str, str | bytes]) -> None:
        """Write files (repo-relative POSIX paths), creating parent directories.

        ``str`` content is written as UTF-8 text, ``bytes`` content as-is. Nothing is staged.
        """
        for rel, content in files.items():
            path = Path(self, rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")

    def commit(self, files: dict[str, str | bytes] | None = None, message: str = "commit") -> str:
        """Write ``files``, stage everything (``git add -A``), commit, and return the new SHA.

        With nothing to commit, an empty commit is created.
        """
        if files:
            self.write(files)
        self.git("add", "-A")
        self.git("commit", "--allow-empty", "--no-verify", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    """An initialised, empty git repository (no commits) on branch ``main``.

    Global and system git config are ignored, and inherited repo-location variables
    (e.g. set when pytest runs inside a git hook) are cleared, so tests are hermetic.
    """
    for var in _GIT_LOCATION_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-global"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = GitRepo(tmp_path / "repo")
    repo.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.name", "Test User")
    repo.git("config", "user.email", "test@example.com")
    repo.git("config", "commit.gpgsign", "false")
    return repo
