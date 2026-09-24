# T7 — ChangeSet

Parent plan: `plans/readme-generation.md` ("Decisions", "Pipeline" step 8, "Project structure").
Depends on: T1 (`core/errors.py`, `core/models/manifest.py`) and T6 (`core/models/docs_state.py`, `publishing/markers.py`, `publishing/manifest_store.py`). Consumed by: the commit stage (`workflow/stages/commit.py`) and `cli/output.py` (prints the dry-run diff).

## 1. Goal

Turn "the final files we want on disk" into a safe, reviewable, atomic set of filesystem changes:
- **compute** a `ChangeSet` from the desired files, the T6 `DocsState` (manifest and ownership), the current disk contents and a `force` flag. This step only reads; it never writes.
- **render** a summary and a unified diff for `--dry-run`. Both are pure functions over the ChangeSet.
- **apply** it: re-verify, then write atomically (temp file in the same dir, then `os.replace`), delete owned-clean files, remove owned directories left empty, and write the manifest last.

Invariants:
- A file that is not owned by the valid manifest is never written or deleted. The two exceptions are the README (when the caller permits replacing an unowned README) and collisions under `force`.
- A hand-edited owned file is never overwritten or deleted unless `force` is set.
- The manifest stays truthful: every entry's `sha256` equals the normalized hash of what is on disk after apply, or the hash of the old entry for files we deliberately left alone.
- Applying the same inputs twice changes nothing the second time. No write happens, not even to the manifest.

## 2. Files

| File | Action | Contents |
|---|---|---|
| `src/readme_stack/publishing/changeset.py` | new | types, `compute_changeset`, `render_summary`, `render_diff`, `apply_changeset`, `atomic_write_text`, error classes |
| `tests/unit/publishing/test_changeset.py` | new | unit tests (section 6) |

T7 does not touch `core/`, T6's modules or `pyproject.toml`. If a T1 or T6 name differs from what section 5 assumes, adapt the imports only and note the difference in the PR.

## 3. Public interface

```python
# src/readme_stack/publishing/changeset.py
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from readme_stack.core.errors import ExitCode, ReadmeStackError, RepoStateError
from readme_stack.core.models.docs_state import DocsState, ManifestStatus
from readme_stack.core.models.manifest import Manifest, ManifestEntry


class Action(StrEnum):
    CREATE = "create"                        # path absent on disk (or owned + missing); will be written
    MODIFY = "modify"                        # path exists; will be overwritten
    DELETE = "delete"                        # owned file no longer desired; will be removed
    SKIP_HAND_EDITED = "skip_hand_edited"    # owned + hand-edited; left untouched (warn)
    UNCHANGED = "unchanged"                  # disk already matches (or kept as-is)


class Prior(StrEnum):
    ABSENT = "absent"                        # not owned, nothing on disk
    OWNED_CLEAN = "owned_clean"
    OWNED_HAND_EDITED = "owned_hand_edited"  # hash mismatch, symlink or non-regular file (T6 rule)
    OWNED_MISSING = "owned_missing"
    UNOWNED = "unowned"                      # not in the valid manifest, but something exists on disk


@dataclass(frozen=True, slots=True, kw_only=True)
class DesiredFile:
    """One file of the final doc set. content=None means keep the current on-disk file as-is
    (the page is still part of the doc set but was not regenerated: DIFF_UPDATE-unaffected pages, or
    pages the write stage skipped as hand-edited). Metadata becomes the new ManifestEntry fields."""
    path: str                       # POSIX, repo-relative, already normalized
    page_id: str                    # README: "readme"
    kind: str                       # README: "readme"
    title: str
    summary: str
    source_paths: tuple[str, ...]
    content: str | None             # final text (marker included for README), LF only


@dataclass(frozen=True, slots=True, kw_only=True)
class ManifestMeta:
    tool_version: str               # readme_stack.__version__
    source_commit: str              # HEAD sha documented by this run
    provider: str
    model: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Change:
    path: str                       # repo-relative POSIX
    action: Action
    prior: Prior
    old_text: str | None            # disk text (normalized, see 4.3) if a regular file existed
    new_text: str | None            # set for CREATE/MODIFY only
    observed_sha: str | None        # normalized sha256 of disk at compute time; None if absent
                                    # or not a regular file
    observed_kind: str              # "absent" | "file" | "symlink" (dirs/other raise in compute)
    reason: str                     # short human text, e.g. "hand-edited; use --force to overwrite"


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangeSet:
    repo_root: Path                          # absolute, resolved
    changes: tuple[Change, ...]              # file changes (README + pages), sorted by path
    manifest_change: Change                  # path == state.manifest_path; CREATE/MODIFY/UNCHANGED
    manifest: Manifest                       # final manifest (canonicalized)
    recovery_source_commit: str              # used for the recovery manifest (4.5)
    previous_entries: tuple[ManifestEntry, ...]  # entries of the valid old manifest, else ()
    warnings: tuple[str, ...] = field(default=())

    @property
    def is_noop(self) -> bool: ...           # no CREATE/MODIFY/DELETE and manifest UNCHANGED
    def by_action(self, action: Action) -> tuple[Change, ...]: ...
    def counts(self) -> dict[Action, int]: ...   # every Action key present, 0 allowed


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplyResult:
    written: tuple[str, ...]        # in write order
    deleted: tuple[str, ...]
    removed_dirs: tuple[str, ...]   # repo-relative POSIX
    manifest_written: bool


class ChangeSetError(ReadmeStackError):
    exit_code = ExitCode.FAILURE    # 1


class ChangeSetConflictError(ChangeSetError):
    """Disk changed between compute and apply. Nothing was written."""
    paths: tuple[str, ...]


class ChangeSetApplyError(ChangeSetError):
    """An OS error occurred mid-apply. Some changes may have been applied (see `applied`);
    a recovery manifest was attempted (`recovery_manifest_written`)."""
    applied: tuple[str, ...]
    recovery_manifest_written: bool


def compute_changeset(
    repo_root: Path,
    state: DocsState,
    desired: Sequence[DesiredFile],
    meta: ManifestMeta,
    *,
    force: bool = False,
    replace_unowned_readme: bool = False,
) -> ChangeSet: ...
    # Raises ValueError (programming errors: bad desired set, NEWER state),
    # RepoStateError (exit 4: collisions without force, dir/unsafe ancestor at a target path).

def render_summary(cs: ChangeSet) -> str: ...
def render_diff(cs: ChangeSet, *, context: int = 3) -> str: ...
def apply_changeset(cs: ChangeSet) -> ApplyResult: ...
    # Raises ChangeSetConflictError / ChangeSetApplyError; on a no-op returns an empty result.
def atomic_write_text(path: Path, content: str, *, mode_from: Path | None = None) -> None: ...
```

`__all__` lists every name above. The error classes take a message plus their keyword fields, and `str(err)` is the message.

## 4. Detailed behavior and edge cases

### 4.1 Input validation (`compute_changeset`, before any disk read). Every failure raises `ValueError`
- `state.manifest_status == NEWER` (T2 should already have raised exit 3).
- Each `desired.path`: `normalize_rel_path(p) == p`. The path is `state.readme_path` or starts with `state.docs_dir + "/"`, and it is not `state.manifest_path`.
- Paths are unique, and also unique after `casefold()` (macOS and Windows filesystems ignore case). `page_id`s are unique.
- Exactly one desired file has `path == state.readme_path`. Its `content`, when not None, must satisfy `parse_marker(content) is not None`.
- `content`, when not None, must not contain `"\r"` (Assemble normalizes line endings to LF).
- `content=None` is allowed only for a path owned by a VALID manifest.

### 4.2 Owned set and disk probe
- **Owned** means `state.manifest_status == VALID` and the path is in `state.manifest.files`. For ABSENT, INVALID and OLDER, nothing is owned.
- `state.file_statuses` is a snapshot from preflight and may be minutes old, so it is **not** trusted. Each path in `desired ∪ owned` is probed fresh:
  1. Check that every ancestor, from `repo_root` down to the parent, is a real directory (4.6).
  2. `os.lstat` the target. The result is one of:
     - **absent**;
     - **symlink**: not followed and not read;
     - **file**: read the bytes, then `observed_sha = hash_bytes(data)` and `old_text = normalize_for_hash(data).decode("utf-8", errors="replace")`;
     - **directory or other** (FIFO, socket, device): `RepoStateError("{path} exists and is not a regular file")`, even with force.
- The owned status is derived from the probe: absent means OWNED_MISSING, symlink means OWNED_HAND_EDITED, and a file is OWNED_CLEAN if `observed_sha == entry.sha256` and OWNED_HAND_EDITED otherwise. This is T6's `FileStatus` rule, recomputed on fresh disk contents.
- `new_sha = hash_text(content)`. Per T6 4.2, the manifest hash is always computed from exactly the string that gets written.

### 4.3 Action decision table
"Permitted" means `force`, or (path is the README and `replace_unowned_readme`). The caller sets `replace_unowned_readme` when the mode decision allows replacing the README: CREATE or RECREATE, after the T18 git-clean guard has passed. "Equal" means `observed_kind == "file" and observed_sha == new_sha`, so CRLF or BOM-only differences count as equal and are never rewritten.

| # | Owned | Desired | Disk / status | force | Action | Prior | New manifest entry | Warning |
|---|---|---|---|---|---|---|---|---|
| 1 | no | content | absent | any | CREATE | ABSENT | new (new_sha) | none |
| 2 | no | content | exists, permitted, equal | - | UNCHANGED | UNOWNED | new (adopted) | "adopting existing identical {p}" |
| 3 | no | content | exists, permitted, not equal | - | MODIFY | UNOWNED | new | README: "replacing README not generated by readme-stack"; else "overwriting unowned {p} (--force)" |
| 4 | no | content | exists, not permitted | - | **collision** | - | - | collected; after the full scan, `RepoStateError` lists all collisions (exit 4) |
| 5 | no | None | any | any | ValueError (4.1) | | | |
| 6 | yes | content | missing | any | CREATE | OWNED_MISSING | new | "recreating deleted generated file {p}" |
| 7 | yes | content | clean, new_sha == entry.sha256 | any | UNCHANGED | OWNED_CLEAN | new (same sha, metadata refreshed) | none |
| 8 | yes | content | clean, differs | any | MODIFY | OWNED_CLEAN | new | none |
| 9 | yes | content | hand-edited, equal | any | UNCHANGED | OWNED_HAND_EDITED | new (sha = new_sha, now clean) | none |
| 10 | yes | content | hand-edited, not equal | no | SKIP_HAND_EDITED | OWNED_HAND_EDITED | **old entry unchanged** | "{p} was edited by hand; skipped (use --force to overwrite)" |
| 11 | yes | content | hand-edited, not equal | yes | MODIFY | OWNED_HAND_EDITED | new | "overwriting hand-edited {p} (--force)" |
| 12 | yes | None | clean or hand-edited | any | UNCHANGED | its status | old sha plus new metadata | none (hand-edited stays hand-edited) |
| 13 | yes | None | missing | any | no Change | - | dropped | "kept page {p} is missing; removed from manifest" |
| 14 | yes | absent from desired | missing | any | no Change | - | dropped | none (debug only) |
| 15 | yes | absent from desired | clean | any | DELETE | OWNED_CLEAN | dropped | none |
| 16 | yes | absent from desired | hand-edited | no | SKIP_HAND_EDITED | OWNED_HAND_EDITED | **dropped (ownership released)** | "{p} is no longer generated but was edited by hand; left in place and no longer tracked" |
| 17 | yes | absent from desired | hand-edited | yes | DELETE | OWNED_HAND_EDITED | dropped | "deleting hand-edited {p} (--force)" |

Notes:
- The README can never reach rows 14 to 17, because 4.1 requires it to be in `desired`.
- In rows 11 and 17, a symlink at an owned path is replaced or unlinked; its target is never touched. In row 3, a symlink is also replaced as a link. `os.replace` and `os.unlink` act on the link itself.
- Unowned paths outside `desired` are never probed, listed or touched.
- **Case-only rename** (owned `docs/x/CLI.md` is being deleted while desired `docs/x/cli.md` is created; the two are equal after casefold): both Changes stay in the ChangeSet, and 4.5 orders the delete before the write so a case-insensitive filesystem does not delete the new file.

**Manifest Change** (`path = state.manifest_path`):
- The final manifest is `Manifest(tool=TOOL_NAME, tool_version=meta.tool_version, format_version=MANIFEST_FORMAT_VERSION, docs_dir=state.docs_dir, source_commit=meta.source_commit, provider=meta.provider, model=meta.model, files=<entries per table>)`, then passed through T6 `canonicalize_manifest`. `new_text = render_manifest(manifest)`.
- The path is probed like the other files. A directory there raises `RepoStateError`. If nothing is there, the action is CREATE. If `hash_bytes(disk) == hash_text(new_text)`, it is UNCHANGED. Otherwise it is MODIFY, and the prior is OWNED_CLEAN for VALID/OLDER status or UNOWNED for INVALID.
- INVALID status without `force` raises `RepoStateError`. This is defensive, because T2 already refuses that case.
- `recovery_source_commit` is the old manifest's `source_commit` when the old manifest was VALID, and `meta.source_commit` otherwise.

### 4.4 Summary and diff format
- `render_summary`: one line per Change, sorted by path, then the manifest line. Format: `f"{label:<18}{path}"`. The labels are `create`, `modify`, `delete`, `skip (hand-edited)` and `unchanged`, except that UNCHANGED lines are omitted and folded into a final count line such as `3 unchanged`. After that comes a totals line, for example `2 to create, 1 to modify, 0 to delete, 1 skipped`, or `No changes.` when `is_noop`. The output ends with `"\n"`.
- `render_diff`: output for CREATE, MODIFY and DELETE Changes in path order, followed by the manifest Change when it is not UNCHANGED. SKIP and UNCHANGED produce no output. Each file uses a git-style block that `git apply` and `patch -p1` accept:
  ```
  diff --git a/{path} b/{path}
  new file mode 100644          # CREATE only
  deleted file mode 100644      # DELETE only
  --- a/{path}                  # "--- /dev/null" for CREATE
  +++ b/{path}                  # "+++ /dev/null" for DELETE
  @@ -l,s +l,s @@
  ...
  ```
  The hunks come from `difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), n=context, lineterm="\n")`, with difflib's own `---`/`+++` header lines replaced by the ones above. If the last old or new line lacks a trailing newline, a `\ No newline at end of file` line follows it (difflib does not emit this, so it is added by hand). The old side is `old_text`, which is normalized (BOM stripped, CRLF converted to LF), so line-ending noise never appears. A symlink old side is shown as the single line `symlink -> {os.readlink}` in the diff; the link is not followed. A MODIFY with an empty diff cannot happen, because equal content means UNCHANGED.
- An empty string is returned when there are no diffable changes. The function is deterministic: it uses no timestamps and no absolute paths.
- The CLI prints the summary to stderr and the diff to stdout. That is T7's contract with `cli/output.py`; T7 itself does no printing.

### 4.5 Apply: ordering and partial failure
If `cs.is_noop`, return `ApplyResult((), (), (), False)` immediately and write nothing.

1. **Verify (no writes).** For every CREATE, MODIFY and DELETE Change, and for the manifest Change when it is not UNCHANGED, re-run the ancestor check (4.6) and re-probe the target. The kind and hash must equal `observed_kind`/`observed_sha`, where absent must still be absent. Any mismatch raises `ChangeSetConflictError(paths=...)` before anything is written. The message says "files changed during the run; re-run readme-stack". This narrows the window in which a hand edit made during the LLM stages could be lost.
2. **Case-rename deletes:** DELETEs whose path casefold-matches a CREATE/MODIFY path.
3. **Page writes:** CREATE/MODIFY under `docs_dir`, sorted by path.
4. **README write**, if CREATE/MODIFY. It comes after the pages, so an interruption never leaves the README linking to pages that do not exist yet.
5. **Remaining deletes**, sorted by path, using `os.unlink`. They come after the README, so the new README no longer links to them.
6. **Empty directory cleanup:** for each deleted path, walk parent directories bottom-up while strictly below `docs_dir`. Call `os.rmdir` if the directory is a real directory (lstat, not a symlink) and is empty. Stop at the first non-empty directory or error (`OSError` is ignored). `docs_dir` itself and anything above it are never removed. Every removed directory is recorded.
7. **Manifest**, last: `atomic_write_text(repo_root / manifest_path, render_manifest(cs.manifest))`, if not UNCHANGED.

**`atomic_write_text(path, content, mode_from=None)`:**
- `path.parent.mkdir(parents=True, exist_ok=True)`.
- Create the temp file `path.parent / f".{path.name}.{secrets.token_hex(6)}.tmp"` with `os.open(tmp, O_WRONLY | O_CREAT | O_EXCL, 0o666)`. The umask applies, so new files get normal permissions, unlike `NamedTemporaryFile`, which creates files as 0600.
- Write `content.encode("utf-8")`, then `os.fsync` and close.
- If `mode_from` is a regular file, `os.chmod(tmp, S_IMODE(lstat(mode_from).st_mode))`, so a MODIFY keeps the old file's permissions.
- `os.replace(tmp, path)`, then fsync the parent directory on a best-effort basis (POSIX only; errors are ignored).
- On any exception, unlink `tmp` if it exists and re-raise.

**Partial failure** (an `OSError` or `BaseException`, such as a `KeyboardInterrupt`, in steps 2 to 7):
- Everything already applied stays. A single file is never half-written, because each write is atomic.
- Unless the failure happened while writing the manifest itself, write a **recovery manifest** on a best-effort basis, using the final manifest's metadata except that `source_commit = cs.recovery_source_commit`, so the next UPDATE re-diffs the full range. Its entries are:
  - new entries for applied writes;
  - old entries (from `previous_entries`) for files not yet written or not yet deleted;
  - nothing for applied deletes;
  - entries per the table for UNCHANGED and SKIP rows.

  Without this manifest, the files written in this run would look hand-edited next run, and a CREATE run would leave an unowned docs dir that then triggers REFUSE.
- Then raise `ChangeSetApplyError(applied=..., recovery_manifest_written=...)` chained from the original error, or re-raise a `KeyboardInterrupt` after recovery.
- If the recovery write fails, it is logged at ERROR and swallowed, and the original error wins.
- Temp files never outlive a failed write.

### 4.6 Path safety
- All stored paths are POSIX and repo-relative, validated with T6 `normalize_rel_path`. Absolute paths, `..` segments, backslashes and NUL are rejected (4.1). Scope is limited to `readme_path`, `docs_dir/**` and the manifest path. Paths from the old manifest were already validated by T6 (section 4.4, step 9).
- `repo_root` is resolved once (`Path.resolve(strict=True)`), and every target is `repo_root / rel`.
- **Ancestor check** (compute and verify): for each proper prefix of `rel` (for example `docs`, then `docs/architecture`), `os.lstat` it:
  - missing is fine, because it will be created. Everything below it must then also be missing;
  - a symlink raises `RepoStateError("{prefix} is a symlink; refusing to write through it")`;
  - an existing non-directory raises `RepoStateError`.

  This stops writes from escaping the repo or landing at a path the manifest does not record, even when a symlink points inside the repo.
- The final path component is never followed (lstat only). Symlinks are replaced or unlinked as links.
- `publishing` must not import `infra.sandbox` (layering), so this check is local and small.
- Temp files live in the target's own directory, so `os.replace` is never a cross-device move. Their names are hidden and unique, and they are created with `O_EXCL`, so they never collide with page names.

## 5. Requires from T1 and T6

**T1, `core/errors.py`:** `ExitCode(IntEnum)` with `FAILURE = 1` and `REPO_STATE = 4`. `ReadmeStackError(Exception)` with class attribute `exit_code` and a single-message constructor. `RepoStateError(ReadmeStackError)` with exit code 4 (the name used by T2).

**T1, `core/models/manifest.py`** (as specified in T6 section 5):
- `MANIFEST_FORMAT_VERSION: Final[int]` (T2 calls it `FORMAT_VERSION`; T1 must pick one, and T7 imports whichever T1 exports);
- `TOOL_NAME: Final[str] = "readme-stack"`;
- `ManifestEntry(path, page_id, kind, title, summary, sha256, source_paths: list[str])`, frozen;
- `Manifest(tool, tool_version, format_version, docs_dir, source_commit, provider, model, files: list[ManifestEntry])`, frozen;
- the README is listed in `files` with `page_id="readme"` and `kind="readme"`.

T7 builds entries with `source_paths=list(desired.source_paths)`.

**T6, `core/models/docs_state.py`:** `DocsState` fields `readme_path`, `docs_dir`, `manifest_path`, `manifest_status`, and `manifest` (set when VALID). `ManifestStatus` values `ABSENT`, `INVALID`, `VALID`, `OLDER` and `NEWER`. `file_statuses` is deliberately not used (see 4.2).

**T6, `publishing/manifest_store.py`:** `normalize_for_hash(bytes) -> bytes`, `hash_bytes(bytes) -> str`, `hash_text(str) -> str`, `normalize_rel_path(str) -> str`, `canonicalize_manifest(Manifest) -> Manifest` and `render_manifest(Manifest) -> str`. `save_manifest` is not used; see open question 2.

**T6, `publishing/markers.py`:** `parse_marker(str) -> int | None`.

## 6. Test plan (`tests/unit/publishing/test_changeset.py`)

Tests use pytest with `tmp_path`, and no git or network. The tests use these helpers:
- `make_state(root, manifest=None, status=...)`, which builds a `DocsState` directly;
- `desired_readme(body)`, which applies the marker via T6 `apply_marker`;
- `desired_page(name, content)`;
- `write_owned(root, files)`, which writes files and returns a VALID manifest with matching hashes;
- `META`.

**Validation**
1. `ValueError` for each of:
   - path outside scope;
   - `..` or absolute path;
   - manifest path in desired;
   - duplicate path;
   - case-only duplicate;
   - duplicate page_id;
   - README missing from desired;
   - README content without a marker;
   - `\r` in content;
   - `content=None` for an unowned path;
   - NEWER state.

**Decision table.** One test per row, parametrized where possible. Each asserts the action, prior, manifest entry and warning substring.

2. Row 1: CREATE on an empty repo, for the README and 2 pages. The manifest Change is CREATE, and entry hashes equal `hash_text(content)`.
3. Rows 2 and 4: an unowned page exists with identical content. Without force it is a collision (`RepoStateError`, exit 4, listing the paths). With force it is UNCHANGED and adopted.
4. Row 3: a foreign README with `replace_unowned_readme=True` gives MODIFY/UNOWNED. Without the flag or force it raises `RepoStateError`. An unowned page collision under `force` gives MODIFY with a warning.
5. Row 4: all collisions are reported in one error, and nothing is written.
6. Row 6: an owned file deleted on disk gives CREATE/OWNED_MISSING.
7. Rows 7 and 8: clean with the same content gives UNCHANGED, and clean with new content gives MODIFY.
8. Row 9: hand-edited content that already equals the desired content gives UNCHANGED, and the entry sha is the new sha.
9. Row 10: hand-edited without force gives SKIP. The manifest keeps the old entry byte for byte, including the old title.
10. Row 11: hand-edited with force gives MODIFY with a warning.
11. Row 12: `content=None` gives UNCHANGED, with the old sha and new metadata, whether the file is clean or hand-edited.
12. Row 13: `content=None` for a missing file drops the entry with a warning, and produces no Change.
13. Rows 14 to 17: owned but not desired. Missing means dropped silently. Clean means DELETE. Hand-edited without force means SKIP, released from the manifest with a warning. Hand-edited with force means DELETE.
14. CRLF and BOM tolerance: an owned clean file converted to CRLF, with the same desired content, gives UNCHANGED, and apply does not rewrite it.
15. An owned path that is a symlink gives OWNED_HAND_EDITED, then SKIP without force. With force the link is replaced by a regular file, and the link's target file is untouched.
16. A directory at a desired path raises `RepoStateError`, even with force.
17. The manifest Change is UNCHANGED when the rendered bytes match the existing file (also when the existing file has CRLF endings), MODIFY on a `source_commit` bump alone, and CREATE when the manifest is absent. INVALID status without force raises `RepoStateError`. OLDER status gives MODIFY and owns no files, so old pages at the same paths are collisions.
18. Unowned files in `docs_dir` that are not desired are never touched. A snapshot of the tree (paths, bytes, mtimes) outside the changed set is identical after apply.

**Rendering**

19. `render_summary`: labels, the unchanged count, the totals line, and `No changes.` for a no-op.
20. `render_diff` golden test for CREATE, MODIFY and DELETE: headers, `/dev/null` sides and `new file mode` lines. The output applies cleanly with `git apply --check` if git is available (skipped otherwise). The pure part is compared against an inline expected string.
21. The `\ No newline at end of file` marker appears for an old or new side without a trailing newline.
22. SKIP and UNCHANGED produce no diff output. The manifest diff appears last. The output is deterministic across two calls and contains no absolute paths.
23. Dry-run writes nothing: `compute_changeset` plus both render functions leave the tree identical (bytes, mtimes, no temp files, no new directories).

**Apply**

24. CREATE/MODIFY/DELETE end-to-end. Afterwards `load_docs_state` (T6) reports VALID, every file is CLEAN, and hand-edited skipped files are still HAND_EDITED.
25. Idempotence: compute plus apply twice, and the second ChangeSet has `is_noop`. `ApplyResult` is empty, and the inodes and mtimes of every file, including the manifest, are unchanged.
26. Ordering: monkeypatch `os.replace`, `os.unlink` and `atomic_write_text` to record calls. The order is case-rename deletes, then pages, then README, then deletes, then manifest last.
27. Atomicity: monkeypatch `os.replace` to raise on the second page write. The first page is fully new, the second is fully old, and no `*.tmp` files remain. `ChangeSetApplyError.applied` lists the first page. A recovery manifest was written with the old `source_commit`, and `load_docs_state` then reports the first page CLEAN, the second page CLEAN (old content) and the README CLEAN (old content).
28. A failure while writing the manifest itself leaves no recovery attempt and no temp file, and raises `ChangeSetApplyError`.
29. A `KeyboardInterrupt` raised mid-apply triggers the recovery manifest and is re-raised as `KeyboardInterrupt`.
30. Conflict: after compute, edit one target file (or create a file at a CREATE path). Apply raises `ChangeSetConflictError` naming it, and nothing at all is written.
31. Permissions: a new file's mode equals `0o666 & ~umask`. A MODIFY keeps the old mode (for example `0o644` stays `0o644`, and `0o600` stays `0o600`).
32. Empty directory cleanup: deleting the only file in `docs/architecture/sub/` removes `sub/`. A sibling directory with an unowned file is kept. `docs_dir` itself is kept even when empty apart from the manifest.
33. Path safety: `docs/architecture` is a symlink to another directory inside the repo, and compute raises `RepoStateError`. The same happens when the symlink points outside `tmp_path`, and the outside directory is untouched. An ancestor that is a regular file also raises `RepoStateError`.
34. Case-only rename, where owned `CLI.md` becomes desired `cli.md`: after apply, `os.listdir` shows only `cli.md` with the new content, and the manifest lists only `cli.md`. This runs on case-sensitive and case-insensitive filesystems alike.
35. Directories are created for new nested pages, and `ApplyResult.written` lists them in order.

## 7. Out of scope / deferred

- Deciding the mode, the foreign-README git-clean guard and page-collision validation at outline time (T2, T18, outline task). T7 only enforces the invariants as a last line of defence.
- Building `DesiredFile`s (assemble and commit stages), and printing or logging (the `cli/output.py` task). T7 returns warnings and strings.
- Colorized diffs, `--stat` output, and pagers.
- Migrating OLDER manifests so their files count as owned (open question 1).
- Locking against concurrent runs. Windows-specific `os.replace` retries when a file is held open.

## 8. Open questions

1. **Upgrading OLDER formats:** T6 does not parse OLDER manifests, so in RECREATE the old pages at the same paths are collisions and need `--force`, and old pages absent from the new plan are orphaned. Should T6 expose a lenient `(path, sha256)` list for OLDER manifests so T7 can treat them as owned?
2. **T6 `save_manifest` uses `NamedTemporaryFile`**, which creates the manifest with mode 0600. T7 writes the manifest with its own `atomic_write_text(render_manifest(...))`. Should T6 switch to (or import) `atomic_write_text` so there is one writer?
3. **Row 16 (hand-edited page dropped from the plan):** this plan releases ownership so the user keeps the file and future runs ignore it. The alternative is to keep the old entry, which warns on every run until `--force`.
4. **Row 2 (adopting an identical unowned file):** this plan requires force or README permission. Should identical content be adopted without permission, since nothing is written?
5. **Row 13 (kept page missing on disk):** this plan drops the entry with a warning. Should it be an error instead, since the README may still link to the page?
6. **Recovery manifest in CREATE runs** uses the new `source_commit`, because there is no old one. A partially created doc set then looks like a complete UPDATE baseline, unless the README was never written, in which case T2 maps it to RECREATE. Is that acceptable, or should the pages be written after the README in CREATE mode?
7. **Exit code for `ChangeSetConflictError`:** this plan uses 1 (runtime). Exit 4 (repo state) is arguable.
