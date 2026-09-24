# T4 — Git wrapper, sandbox, summary cache

Parent plan: `plans/readme-generation.md`. Depends on: T1 (core errors, ports, `ChangedFiles`, test `git_repo` fixture).

## 1. Goal
Provide the three infra adapters every later stage relies on:
- `infra/git.py`: a safe, synchronous `git` subprocess wrapper (fixed argv, no shell, timeouts,
  typed errors) that implements `core.ports.Git`.
- `infra/sandbox.py`: a path-confinement primitive that turns an untrusted repo-relative path
  (from an agent tool call or a manifest) into a real path inside the repo root, or raises.
- `infra/cache.py`: a content-hash keyed, best-effort file-summary cache implementing
  `core.ports.SummaryCache`.

No LLM, no network, stdlib only (pydantic only via T1 models).

## 2. Files
Create:
- `src/readme_stack/infra/__init__.py` (empty, if T1 has not created it)
- `src/readme_stack/infra/git.py`
- `src/readme_stack/infra/sandbox.py`
- `src/readme_stack/infra/cache.py`
- `tests/unit/infra/__init__.py` (only if the tests tree uses packages; follow T1)
- `tests/unit/infra/test_git.py`
- `tests/unit/infra/test_sandbox.py`
- `tests/unit/infra/test_cache.py`

Do not modify: `core/*` (owned by T1). If a T1 type is missing or differs, adapt to T1's version and
record the delta in the PR description; do not edit `core/` from T4.

## 3. Public interface

### `infra/git.py`
```python
from collections.abc import Sequence
from pathlib import Path

from readme_stack.core.models.impact import ChangedFiles
from readme_stack.core.ports import CommitInfo, RevRange  # see §5

DEFAULT_TIMEOUT: float = 30.0      # seconds, metadata commands
LONG_TIMEOUT: float = 120.0        # diff / log / show
DEFAULT_MAX_BYTES: int = 1_000_000 # cap for diff/show text returned to agents


class GitRepo:  # structurally implements core.ports.Git (no inheritance needed; Protocol)
    @classmethod
    def open(
        cls, path: Path, *, git_bin: str = "git", timeout: float = DEFAULT_TIMEOUT
    ) -> "GitRepo":
        """Resolve `path`, verify git is runnable and `path` is the work-tree top level.
        Raises GitUnavailable / NotAGitRepository / NotGitTopLevel (all exit 3)."""

    @property
    def root(self) -> Path: ...                        # resolved absolute top level

    def head_sha(self) -> str | None: ...              # None on unborn HEAD (no commits)
    def is_shallow(self) -> bool: ...
    def resolve_commit(self, rev: str) -> str: ...     # full sha; InvalidRevision (3)
    def resolve_range(self, spec: str) -> RevRange: ...# InvalidRange (3)
    def is_reachable(self, sha: str) -> bool: ...      # exists AND ancestor of HEAD
    def ls_files(self) -> list[str]: ...               # tracked + untracked-not-ignored
    def changed_files(self, rng: RevRange) -> ChangedFiles: ...
    def diff(
        self, rng: RevRange, paths: Sequence[str] = (), *, context: int = 3,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> str: ...
    def show(
        self, rev: str, path: str | None = None, *, max_bytes: int = DEFAULT_MAX_BYTES
    ) -> str: ...
    def log(
        self, paths: Sequence[str] = (), *, rev: str = "HEAD", max_count: int = 20
    ) -> list[CommitInfo]: ...
    def is_tracked_and_clean(self, path: str) -> bool: ...
```
The constructor `GitRepo(root, git_bin, timeout)` is internal; callers use `GitRepo.open`.
`workflow` injects the instance as `core.ports.Git`. A module-level
`_: type[Git] = GitRepo` line is not used (Protocols are structural); instead a test asserts
`isinstance(repo, Git)` if T1 marks the Protocol `@runtime_checkable`, else a typed assignment
`g: Git = repo` in the test suffices for type checkers.

All path arguments (`paths`, `path`) are repo-relative POSIX strings. The wrapper does **not**
sandbox them (callers in `tools/` pass them through `Sandbox.relpath` first), but it does pass
them after `--` with `--literal-pathspecs`, so they can never become options or globs.

### `infra/sandbox.py`
```python
from pathlib import Path, PurePath

class Sandbox:
    def __init__(self, root: Path) -> None: ...     # root = root.resolve(strict=True)
    @property
    def root(self) -> Path: ...
    def resolve(self, rel: str | PurePath) -> Path: ...   # real absolute path inside root
    def relpath(self, rel: str | PurePath) -> str: ...    # normalized POSIX rel path ("." = root)
    def contains(self, path: Path) -> bool: ...           # resolved path is inside root
```
Raises `SandboxViolation` (from `core.errors`) on any rejection.

### `infra/cache.py`
```python
from collections.abc import Mapping
from pathlib import Path

CACHE_FORMAT = 1
CACHE_DIR_ENV = "README_STACK_CACHE_DIR"

def default_cache_dir(
    env: Mapping[str, str] | None = None, platform: str | None = None
) -> Path: ...

def summary_key(
    content_sha256: str, *, model: str, prompt_version: str, kind: str = "file-summary"
) -> str: ...  # sha256 hex of the canonical tuple; pure

class FileSummaryCache:           # implements core.ports.SummaryCache
    def __init__(self, directory: Path) -> None: ...
    def get(self, key: str) -> str | None: ...
    def put(self, key: str, value: str) -> None: ...

class NullSummaryCache:           # implements core.ports.SummaryCache; always misses
    def get(self, key: str) -> str | None: ...
    def put(self, key: str, value: str) -> None: ...
```

## 4. Detailed behavior and edge cases

### 4.1 Subprocess runner (private)
```python
def _run(self, *args: str, ok: tuple[int, ...] = (0,), timeout: float | None = None,
         input_: bytes | None = None) -> subprocess.CompletedProcess[bytes]
```
- argv = `[git_bin, "-C", str(root), "--no-pager", "--literal-pathspecs",
  "-c", "core.fsmonitor=false", "-c", "color.ui=never", "-c", "core.quotepath=off", *args]`.
  `subprocess.run(argv, shell=False, stdin=DEVNULL, capture_output=True, timeout=..., env=env)`.
- `env` = `os.environ` copy plus `GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0` (status never
  writes the index), `LC_ALL=C` (stable messages), `GIT_PAGER=cat`. Nothing removed.
- Output kept as bytes; text decoded with `utf-8`, `errors="replace"` for diff/show/log content;
  paths (from `-z` output) decoded with `os.fsdecode` so non-UTF-8 names round-trip.
- Error mapping:
  | Condition | Exception | exit |
  |---|---|---|
  | `FileNotFoundError` / `PermissionError` launching `git_bin` | `GitUnavailable` | 3 |
  | `subprocess.TimeoutExpired` | `GitCommandError("git <sub> timed out after Ns")` | 1 |
  | returncode not in `ok` | `GitCommandError(argv-summary, returncode, stderr tail ≤2 KB)` | 1 |
  Specific callers catch `GitCommandError` and re-raise the exit-3 types below where the failure
  means a bad environment/input (top level, range, revision).
- The error message never includes the environment; argv is included (it contains no secrets).

### 4.2 `open` / top level
1. `path.resolve(strict=True)`; missing dir → `NotAGitRepository` (3).
2. `git -C <path> rev-parse --show-toplevel` (note: run with `-C path`, before root is known).
   - launch failure → `GitUnavailable` (3), message: "git executable not found: 'git'".
   - rc≠0 (stderr "not a git repository", or bare repo) → `NotAGitRepository` (3).
3. `Path(stdout.strip()).resolve() != path` → `NotGitTopLevel(path, toplevel)` (3), message
   "REPO must be the git top level (got <path>, top level is <toplevel>)". Compare resolved paths
   so `/tmp` vs `/private/tmp` (macOS) and symlinked checkouts compare equal.

### 4.3 Revisions and ranges
- `resolve_commit(rev)`:
  - reject `""`, leading `-`, NUL, whitespace/newline → `InvalidRevision` (3) (option-injection guard;
    avoids depending on `--end-of-options`, git ≥ 2.24).
  - `rev-parse --verify --quiet <rev>^{commit}` (ok=(0,1)); rc 1 / empty → `InvalidRevision`,
    message adds "(shallow clone: use fetch-depth: 0)" when `is_shallow()`.
- `resolve_range(spec)` → `RevRange(base, head, spec)` with full SHAs:
  | spec | base | head |
  |---|---|---|
  | `A..B` | `A` | `B` |
  | `A...B` | `merge-base A B` | `B` |
  | `A..` / `A...` | `A` / `merge-base A HEAD` | `HEAD` |
  | `..B` | `HEAD` | `B` (git semantics) |
  | `A` (no dots) | `A` | `HEAD` |
  Split on the first `...` if present, else first `..`. More than one range operator, or `^!`,
  `^@`, `@{...}` reflog forms containing `..` are just passed to `resolve_commit` and fail there if
  invalid. Each side validated by `resolve_commit`; failure re-raised as `InvalidRange(spec, reason)`
  (3). Merge-base: `merge-base <a> <b>` ok=(0,1); rc 1 → `InvalidRange("no common ancestor")`.
  `base == head` is valid (empty range; the pipeline treats it as no-op).
- `head_sha()`: `rev-parse --verify --quiet HEAD^{commit}` ok=(0,1); rc 1 → `None` (unborn).
- `is_shallow()`: `rev-parse --is-shallow-repository` → `stdout.strip() == "true"`.
- `is_reachable(sha)`: reject invalid syntax (same guard) → `False`;
  `cat-file -e <sha>^{commit}` ok=(0,1,128); non-zero → `False` (object missing, e.g. shallow clone
  or GC'd rewrite). Then `merge-base --is-ancestor <sha> HEAD` ok=(0,1): 0 → True, 1 → False.
  Unborn HEAD → False. Never raises on "not found"; the pipeline maps False → full-review update.

### 4.4 `ls_files`
- `ls-files -z -c -o --exclude-standard`. Split on `\0`, drop empties, `os.fsdecode`.
- Deduplicate (a path can appear twice during merges), sort.
- Drop entries that are not regular files or symlinks on disk (`os.path.lexists` false → deleted in
  worktree; directory → submodule gitlink or nested repo). Symlinks are kept; the sandbox /
  FileIndex decides whether they are readable.
- Secret/binary filtering is **not** done here (that is `analysis/repo_index.py`).

### 4.5 `changed_files`
- `diff -z --name-status -M --no-color --no-ext-diff <base> <head> --`.
- `-z` output is a flat NUL-separated token stream: `STATUS\0PATH\0` or, for `R`/`C`,
  `R<score>\0OLD\0NEW\0`. Parse with an index cursor:
  - status token's first char: `A`, `M`, `D`, `T` → one path; `R`, `C` → two paths and
    `similarity = int(token[1:])` (e.g. `R100` → 100, `R087` → 87); `U`, `X`, `B` or unknown →
    `GitCommandError("unexpected diff status ...")`.
  - `T` (type change) is mapped to `ChangeStatus.MODIFIED` with `type_changed=True` if T1's model has
    the field, else plain MODIFIED.
  - Truncated stream (odd tail) → `GitCommandError`.
- Returns `ChangedFiles(base=rng.base, head=rng.head, files=tuple(...))` in git's output order.
- Rename detection uses git's default 50 % similarity and `diff.renameLimit`; if git prints
  "inexact rename detection was skipped" on stderr, log a warning (renames then appear as D+A).
- Compares commits only; uncommitted worktree changes are intentionally ignored.

### 4.6 `diff`
- `diff --no-color --no-ext-diff --no-textconv -M -U<context> <base> <head> -- <paths...>`.
  `context` clamped to `0..20`. Empty `paths` = whole range.
- Timeout `LONG_TIMEOUT`. Output decoded, then truncated to `max_bytes` at a line boundary with a
  final line `[... diff truncated: N more bytes]`.

### 4.7 `show`
- `path is None`: `show --no-color --no-ext-diff --no-textconv -M --stat --patch
  --format=commit %H%nAuthor: %an%nDate: %aI%n%n%B <sha>`
  (format passed as one argv element `--format=...`). `sha = resolve_commit(rev)` first.
- `path` given: `show <sha>:<path>` (blob at revision). Missing path at rev → `GitCommandError`
  (exit 1; tools translate it into a tool error for the agent). Binary blob (NUL in first 8 KB) →
  return `"[binary file]"`.
- Same `max_bytes` truncation as `diff`.

### 4.8 `log`
- `log -z --no-color --format=%H%x1f%aI%x1f%an%x1f%s --max-count=<n> <sha> -- <paths...>`,
  `sha = resolve_commit(rev)`, `max_count` clamped `1..200`.
- Records separated by `\0`, fields by `\x1f` → `CommitInfo(sha, date, author, subject)`.
  Unborn HEAD → `[]`. Author email is deliberately not collected.

### 4.9 `is_tracked_and_clean(path)`
1. `ls-files -z -c -- <path>` → empty → `False` (untracked or missing).
2. `status --porcelain=v1 -z --untracked-files=no -- <path>` → empty → `True`, else `False`
   (catches unstaged edits, staged edits, staged-new `A `, deletions).
- `path` must be a file path; a directory path returns the aggregate answer (documented, not used).

### 4.10 Shallow clones
- Detected via `is_shallow()`; used only to enrich messages: `InvalidRevision`/`InvalidRange` add
  "repository is a shallow clone; fetch full history (actions/checkout fetch-depth: 0)".
- `is_reachable` returns `False` for commits beyond the shallow boundary → the pipeline does a
  full-review update instead of failing (per parent plan).
- `log` on a shallow clone simply returns fewer commits; no error.

### 4.11 Sandbox
`resolve(rel)` steps, any failure → `SandboxViolation(rel, reason)`:
1. `str(rel)`; reject if contains `\0`.
2. `p = PurePosixPath(rel)`; reject `p.is_absolute()`. A leading `~` is **not** rejected (no
   expansion happens; it is a literal name).
3. Reject if any part is `..` (lexical, before resolution; stricter than necessary, simpler to audit).
4. `""` / `"."` → root.
5. `real = (root / p).resolve(strict=False)`; catch `OSError`/`RuntimeError` (symlink loop) → reject.
6. Reject if `not real.is_relative_to(root)` (symlink escape, incl. intermediate dir symlinks).
7. Reject if `real.relative_to(root).parts[:1] == (".git",)` (protects `.git/config` credentials
   such as CI `extraheader` tokens), and also if the lexical path's first part is `.git`.
- Symlinks whose target stays inside root are allowed (resolved path returned).
- `relpath(rel)` = `resolve(rel).relative_to(root).as_posix()` or `"."`.
- `contains(path)` = resolve (non-strict) and `is_relative_to(root)`; never raises.
- Non-goal: TOCTOU races (read-only tool on a local checkout).

### 4.12 Summary cache
**Location decision: user cache dir, not in-repo.**
- `README_STACK_CACHE_DIR` if set; else `$XDG_CACHE_HOME/readme-stack` if set; else
  macOS `~/Library/Caches/readme-stack`, Windows `%LOCALAPPDATA%\readme-stack\Cache`,
  other `~/.cache/readme-stack`.
- Justification: an in-repo cache (e.g. `.readme-stack-cache/`) would show up in
  `git ls-files -o` and the FileIndex (feeding cache files to agents), dirty the working tree,
  cause PR noise or get committed by the Action, and require writing a `.gitignore` we don't own ("never touch files we didn't create").
  Entries are keyed by content hash + model + prompt version, so sharing across repos is safe and
  even beneficial. CI can persist it with `actions/cache` via the env var.
- Layout: `<dir>/v{CACHE_FORMAT}/<key[:2]>/<key>.json`, body
  `{"format": 1, "key": "<key>", "value": "<summary>"}` (UTF-8, `sort_keys`).
- `summary_key`: `sha256("\x1f".join([f"v{CACHE_FORMAT}", kind, model, prompt_version,
  content_sha256]))`. `content_sha256` must be 64 lowercase hex else `ValueError`.
  Changing the content, model or prompt version → different key → miss.
- `get(key)`: key must match `^[0-9a-f]{64}$` else `ValueError` (path-traversal guard). Missing file
  → `None`. Unreadable/corrupt JSON/format mismatch/`key` mismatch → `None` (debug log), no raise.
- `put(key, value)`: create parent dirs (`mode 0o700` for the root), write to `tempfile` in the same
  dir, `os.replace` (atomic; concurrent writers of the same key are harmless — same value).
  `OSError` → log one warning per instance ("summary cache disabled: ..."), set `_disabled=True`,
  subsequent `get`/`put` are no-ops. Cache failures never fail the run.
- No eviction/size cap in v1 (deferred). Values larger than 1 MB are not stored.

## 5. Requires from T1
`core/errors.py`:
```python
class ReadmeStackError(Exception):          exit_code: ClassVar[int] = 1
class EnvError(ReadmeStackError):           exit_code = 3   # name avoids builtin EnvironmentError
class GitUnavailable(EnvError): ...
class NotAGitRepository(EnvError): ...
class NotGitTopLevel(EnvError): ...
class InvalidRevision(EnvError): ...
class InvalidRange(EnvError): ...
class GitCommandError(ReadmeStackError):    exit_code = 1   # runtime git failure / timeout
class SandboxViolation(ReadmeStackError):   exit_code = 1   # tools catch → tool error to agent
```
If T1 prefers fewer classes, the minimum is `ReadmeStackError`, an exit-3 environment class and
an exit-1 runtime class; T4 can define the git-specific subclasses in `infra/git.py` subclassing them
(but `SandboxViolation` must live in `core` so `tools/` can catch it without importing infra).

`core/ports.py` (all sync, `typing.Protocol`, preferably `@runtime_checkable`):
```python
class RevRange(BaseModel, frozen=True):  base: str; head: str; spec: str
class CommitInfo(BaseModel, frozen=True): sha: str; date: str; author: str; subject: str

class Git(Protocol):
    @property
    def root(self) -> Path: ...
    def head_sha(self) -> str | None: ...
    def is_shallow(self) -> bool: ...
    def resolve_commit(self, rev: str) -> str: ...
    def resolve_range(self, spec: str) -> RevRange: ...
    def is_reachable(self, sha: str) -> bool: ...
    def ls_files(self) -> list[str]: ...
    def changed_files(self, rng: RevRange) -> ChangedFiles: ...
    def diff(self, rng: RevRange, paths: Sequence[str] = (), *, context: int = 3,
             max_bytes: int = ...) -> str: ...
    def show(self, rev: str, path: str | None = None, *, max_bytes: int = ...) -> str: ...
    def log(self, paths: Sequence[str] = (), *, rev: str = "HEAD",
            max_count: int = 20) -> list[CommitInfo]: ...
    def is_tracked_and_clean(self, path: str) -> bool: ...

class SummaryCache(Protocol):
    def get(self, key: str) -> str | None: ...
    def put(self, key: str, value: str) -> None: ...
```
(`RevRange`/`CommitInfo` may instead live in `core/models/impact.py`; T4 imports from wherever T1
puts them.) `FileSystem` is not implemented by T4 (see §8).

`core/models/impact.py`:
```python
class ChangeStatus(StrEnum): ADDED="A"; MODIFIED="M"; DELETED="D"; RENAMED="R"; COPIED="C"
class ChangedFile(BaseModel, frozen=True):
    status: ChangeStatus; path: str; old_path: str | None = None; similarity: int | None = None
class ChangedFiles(BaseModel, frozen=True):
    base: str; head: str; files: tuple[ChangedFile, ...] = ()
```
`tests/conftest.py`:
- `git_repo` fixture → `Path` to a fresh `git init -b main` repo in `tmp_path`, with local
  `user.name`/`user.email`, `commit.gpgsign=false`, and env isolation
  (`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1` via `monkeypatch`). No initial commit.
- A helper (fixture or function) `commit(repo, files: dict[str, str | None], msg) -> str` that
  writes/deletes (`None`) files, `git add -A`, commits, returns the sha. If T1 does not provide it,
  T4 defines it privately in `tests/unit/infra/conftest.py`.
- Skip all git tests when `shutil.which("git") is None`.

## 6. Test plan
`tests/unit/infra/test_git.py`
1. `open` on repo root succeeds; `root` equals resolved path (use `tmp_path` symlink on macOS).
2. `open` on a subdirectory → `NotGitTopLevel`, `exit_code == 3`.
3. `open` on a non-repo dir → `NotAGitRepository`, exit 3.
4. `open(..., git_bin="readme-stack-no-such-git")` → `GitUnavailable`, exit 3; also with
   `monkeypatch.setenv("PATH", "")` and default `git_bin`.
5. `head_sha()` is `None` on an unborn repo and equals `rev-parse HEAD` after a commit.
6. `resolve_range("HEAD~1..HEAD")` returns full SHAs; `"A...B"` returns merge-base as base;
   `"<sha>"` alone → head = HEAD; `"HEAD.."` → head = HEAD.
7. Bad ranges → `InvalidRange`, exit 3: `"nope..HEAD"`, `"HEAD..nope"`, `"--output=x..HEAD"`,
   `""`, `"HEAD~99..HEAD"` on a 2-commit repo; two unrelated roots with `...` → no common ancestor.
8. `resolve_commit("-p")` → `InvalidRevision` without invoking git (assert via a spy on `_run`).
9. Rename detection: commit `a.py`, then `git mv a.py b.py` (+ small edit) → one `ChangedFile`
   status RENAMED, `old_path="a.py"`, `path="b.py"`, `similarity` in 50..100; pure rename → 100.
10. `changed_files` covers A, M, D in one range; order preserved; paths with spaces and a
    non-ASCII name (`dir/ü x.md`) parse correctly (NUL separation).
11. Parser unit test on a canned byte string `b"M\0a\0R100\0old\0new\0D\0gone\0"` and a truncated
    stream → `GitCommandError` (tests the private `_parse_name_status` directly).
12. `ls_files` includes tracked + untracked, excludes `.gitignore`d files, excludes a tracked file
    deleted from the worktree, sorted and unique.
13. `is_tracked_and_clean`: committed README → True; modified → False; staged-only → False;
    untracked → False; missing → False.
14. `is_reachable`: ancestor sha → True; sha from a sibling branch not merged → False; unknown
    40-hex sha → False; garbage string → False.
15. `diff` with `paths=["a.py"]` limits output to that file; `max_bytes=100` truncates with marker;
    a path named `*.py` is treated literally (`--literal-pathspecs`).
16. `show(sha)` contains subject and patch; `show(sha, "a.py")` returns blob content; missing path →
    `GitCommandError` (exit 1).
17. `log(paths=["a.py"], max_count=1)` returns one `CommitInfo` with correct sha/subject.
18. Timeout: monkeypatch `subprocess.run` to raise `TimeoutExpired` → `GitCommandError`, exit 1.
19. Shallow clone: `git clone --depth 1 file://<repo>`; `is_shallow()` True; resolving the old
    root sha → `InvalidRevision` whose message mentions `fetch-depth: 0`; `is_reachable(old)` False.
20. Protocol conformance: `isinstance(GitRepo.open(repo), Git)` (if runtime_checkable).

`tests/unit/infra/test_sandbox.py`
21. `resolve("src/a.py")` and `resolve("./src/a.py")` return paths under root; `relpath("")=="."`.
22. `..` rejected: `"../x"`, `"a/../../x"`, `"a/../b"`, `".."`.
23. Absolute rejected: `"/etc/passwd"`, `str(root / "a.py")` (absolute even if inside).
24. NUL byte rejected.
25. Symlink to a file outside root → rejected; symlink to a directory outside root, then
    `"link/file"` → rejected; symlink inside root → allowed and resolved.
26. Symlink loop (`a -> b`, `b -> a`) → `SandboxViolation`, not `RuntimeError`/`OSError`.
27. `.git/config` and `.git` rejected; `.github/workflows/ci.yml` allowed.
28. `contains()` never raises on escapes, returns False.
(Symlink tests `skipif(sys.platform == "win32")`.)

`tests/unit/infra/test_cache.py`
29. `put(k, v)` then `get(k)` → `v` (hit), also from a fresh `FileSummaryCache` instance on the same dir.
30. `summary_key` with same inputs is stable; changing content hash, model or prompt version
    yields a different key → `get` misses (`None`).
31. `get` on unknown key → `None`; malformed key (`"../x"`, uppercase, short) → `ValueError`.
32. Corrupt entry file (garbage bytes) → `None`, no exception.
33. Unwritable dir (`chmod 0o500`, skip if root user) → `put` logs one warning, no raise; later
    `get` returns `None`.
34. `default_cache_dir`: env override wins; `XDG_CACHE_HOME` used; darwin/linux/win32 fallbacks
    (pure function, injected `env`/`platform`).
35. `NullSummaryCache` always misses.

## 7. Out of scope / deferred
- `FileSystem` port implementation (windowed reads, grep, binary/secret refusal) — uses `Sandbox`,
  belongs to the tools/fs task.
- FileIndex, secret/binary denylist (`analysis/repo_index.py`).
- Agent-facing `tools/git.py` wrappers, `diff_symbols`.
- Async API; callers use `asyncio.to_thread` if needed.
- Cache eviction/size limits, `--no-cache` CLI flag, cache stats in the usage summary.
- Worktree (uncommitted) diffs, submodule contents, monorepo subdirectories, Windows path quirks.
- Minimum git version check (design avoids features newer than git 2.15).

## 8. Open questions
1. Should T4 also ship `LocalFileSystem` implementing `core.ports.FileSystem` on top of `Sandbox`,
   or is that owned by the tools/fs task? (Plan assumes the latter.)
2. `RevRange`/`CommitInfo`: in `core/ports.py` or `core/models/impact.py`? T1 to decide.
3. Should a single rev `A` mean `A..HEAD` (proposed) or be rejected as not-a-range?
4. Error class naming (`EnvError` etc.) must match T1; `SandboxViolation` exit code 1 vs 3?
   (Proposed 1: it is a runtime/agent issue, normally converted to a tool error.)
5. Should `diff.renameLimit` be raised (e.g. `-c diff.renameLimit=5000`) for large refactors?
6. Summary cache key: is `kind + model + prompt_version + content_sha256` enough, or should the
   file path (context-dependent summaries) be included? Including it reduces cross-repo reuse.
