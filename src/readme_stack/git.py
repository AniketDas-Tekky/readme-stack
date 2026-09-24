"""Everything that shells out to git: repo root, allowed file list and diff.

Fixed argv, no shell. No LLM knowledge and no ``ai`` import.
"""

from __future__ import annotations

import fnmatch
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_DIFF_CHARS = 60_000
DENYLIST = (".env", ".env.*", "*.pem", "*.key")  # matched against the basename

_TIMEOUT = 30
_BINARY_SNIFF_BYTES = 8192
_README_EXCLUDE = ("--", ".", ":(exclude)README.md")


class GitError(Exception):
    """Git failure or invalid repo/range; the CLI maps it to exit code 2."""


class _GitCommandFailed(GitError):
    """Git ran but exited non-zero."""


@dataclass(frozen=True)
class Diff:
    stat: str  # `git diff --stat` output
    patch: str  # unified diff, possibly truncated
    truncated: bool

    @property
    def empty(self) -> bool:
        """True when there are no changes (stat and patch blank)."""
        return not self.stat.strip() and not self.patch.strip()


def _run(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out") from exc
    if result.returncode != 0:
        raise _GitCommandFailed(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def repo_root(path: Path) -> Path:
    """Return the resolved top level of the git work tree containing ``path``."""
    if not Path(path).is_dir():
        raise GitError(f"not a git repository: {path}")
    try:
        out = _run(path, "rev-parse", "--show-toplevel")
    except _GitCommandFailed as exc:
        raise GitError(f"not a git repository: {path}") from exc
    top = out.strip()
    if not top:
        raise GitError(f"not a git repository: {path}")
    return Path(top).resolve()


def _denied(rel: str) -> bool:
    name = PurePosixPath(rel).name
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in DENYLIST)


def _is_binary(path: Path) -> bool:
    with path.open("rb") as fh:
        return b"\0" in fh.read(_BINARY_SNIFF_BYTES)


def repo_files(root: Path) -> list[str]:
    """Tracked and untracked-but-not-ignored text files, minus the denylist; sorted POSIX paths."""
    out = _run(root, "ls-files", "-z", "-co", "--exclude-standard")
    files: set[str] = set()
    for rel in out.split("\0"):
        if not rel or rel in files or _denied(rel):
            continue
        path = Path(root, rel)
        try:
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode) or _is_binary(path):
                continue
        except OSError:
            continue  # deleted but still tracked, or unreadable
        files.add(rel)
    return sorted(files)


def _truncate(patch: str, max_chars: int) -> tuple[str, bool]:
    if len(patch) <= max_chars:
        return patch, False
    head = patch[:max_chars]
    cut = head.rfind("\n")
    if cut != -1:
        head = head[:cut]
    remaining = len(patch) - len(head)
    note = f"\n… diff truncated ({remaining} more characters); use read_file to inspect full files"
    return head + note, True


def diff(root: Path, rev_range: str, max_chars: int = MAX_DIFF_CHARS) -> Diff:
    """Diff stat and (possibly truncated) patch for ``rev_range``, excluding README.md."""
    if not rev_range or not rev_range.strip():
        raise GitError("diff range must not be empty")
    if rev_range.startswith("-"):
        raise GitError(f"invalid diff range: {rev_range!r}")
    stat_out = _run(root, "diff", "--stat", rev_range, *_README_EXCLUDE)
    patch = _run(root, "diff", rev_range, *_README_EXCLUDE)
    patch, truncated = _truncate(patch, max_chars)
    return Diff(stat=stat_out, patch=patch, truncated=truncated)
