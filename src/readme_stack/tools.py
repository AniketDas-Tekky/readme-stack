"""The two repository tools the agent can call: ``list_files`` and ``read_file``.

Pure Python over the repo root and the allowed file list; no ``ai`` import. Every result is a
string meant for the model, and problems are returned as ``"error: …"`` strings rather than
raised, so the agent can correct itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAX_LIST_ENTRIES = 500
DEFAULT_MAX_LINES = 400
MAX_READ_LINES = 2_000
MAX_READ_BYTES = 100_000


def _normalize(path: str) -> str | None:
    """Return a normalized repo-relative POSIX path, or None if the path is not allowed.

    ``""`` and ``"."`` mean the repo root and normalize to ``""``.
    """
    p = path.strip()
    if "\\" in p or p.startswith("/"):
        return None
    while p.startswith("./"):
        p = p[2:]
    p = p.rstrip("/")
    if p == ".":
        p = ""
    if any(segment == ".." for segment in p.split("/")):
        return None
    return p


def _invalid(path: str) -> str:
    return f"error: invalid path '{path}' (use repo-relative paths)"


@dataclass(frozen=True)
class RepoTools:
    root: Path
    files: tuple[str, ...]  # sorted POSIX paths from git.repo_files (the allow-list)

    def list_files(self, path: str = "") -> str:
        """List repository files under a directory as an indented tree.

        `path` is a repo-relative directory such as 'src' or 'docs/api'; use '' for the repo
        root. Directories end with '/' and each nesting level is indented by 2 spaces. Only
        files you are allowed to read are listed. Large listings are truncated; call again with
        a narrower path to see more.
        """
        norm = _normalize(path)
        if norm is None:
            return _invalid(path)
        if norm:
            prefix = norm + "/"
            selected = sorted(f for f in self.files if f == norm or f.startswith(prefix))
        else:
            selected = sorted(self.files)
        if not selected:
            return f"error: no files under '{norm}'"

        lines = [f"{len(selected)} files under {norm or '.'}"]
        prev_dirs: list[str] = []
        for file in selected[:MAX_LIST_ENTRIES]:
            if not norm:
                rel = file
            elif file == norm:
                rel = file.rsplit("/", 1)[-1]
            else:
                rel = file[len(norm) + 1 :]
            parts = rel.split("/")
            dirs = parts[:-1]
            common = 0
            while common < min(len(dirs), len(prev_dirs)) and dirs[common] == prev_dirs[common]:
                common += 1
            for level in range(common, len(dirs)):
                lines.append("  " * level + dirs[level] + "/")
            lines.append("  " * len(dirs) + parts[-1])
            prev_dirs = dirs

        remaining = len(selected) - MAX_LIST_ENTRIES
        if remaining > 0:
            lines.append(f"… {remaining} more files; call list_files with a narrower path")
        return "\n".join(lines)

    def read_file(self, path: str, start_line: int = 1, max_lines: int = DEFAULT_MAX_LINES) -> str:
        """Read a text file from the repository, returning numbered lines.

        `path` must be a repo-relative file path exactly as shown by list_files. Reads up to
        `max_lines` lines (at most 2000) starting at `start_line` (1-based). The header shows
        which lines were returned; if the file continues, a final note gives the start_line to
        use for the next call.
        """
        norm = _normalize(path)
        if norm is None:
            return _invalid(path)
        if norm not in self.files:
            return f"error: '{path}' is not a readable file; use list_files to find files"

        start = max(1, start_line)
        limit = min(max(1, max_lines), MAX_READ_LINES)

        try:
            text = (self.root / norm).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            reason = exc.strerror or str(exc)
            return f"error: cannot read '{norm}': {reason}"

        all_lines = text.splitlines()
        total = len(all_lines)
        if total == 0:
            return f"{norm} (empty file)"
        if start > total:
            return f"error: start_line {start} is past the end of '{norm}' ({total} lines)"

        body: list[str] = []
        used = 0
        end = min(total, start + limit - 1)
        for n in range(start, end + 1):
            entry = f"{n:>5}  {all_lines[n - 1]}"
            size = len(entry.encode("utf-8")) + 1  # + newline
            if used + size > MAX_READ_BYTES:
                if not body:
                    # A single oversized line: emit it truncated so the range is never empty.
                    cut = entry.encode("utf-8")[: MAX_READ_BYTES - 1]
                    body.append(cut.decode("utf-8", errors="ignore"))
                break
            body.append(entry)
            used += size

        last = start + len(body) - 1
        out = [f"{norm} (lines {start}-{last} of {total})", *body]
        remaining = total - last
        if remaining > 0:
            out.append(f"… {remaining} more lines; call read_file with start_line={last + 1}")
        return "\n".join(out)
