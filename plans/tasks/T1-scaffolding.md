# T1 — Scaffolding and shared contracts

Parent plan: [`plans/readme-generation.md`](../readme-generation.md) ("Project structure", "Other file changes", "Verification").
Depends on: nothing. Every wave-2 task depends on T1. Cross-task naming decisions and the
amendments that the other plans need are in `plans/tasks/RECONCILIATION.md`. Where a wave-2
plan disagrees with this file, **this file wins**.

## 1. Goal
- Replace the GitHub-Action boilerplate with the layered `readme_stack` package skeleton. After
  T1, `readme-stack --version` works and nothing else does yet.
- Own every **shared contract**: the exception hierarchy, ports, cross-task models, constants,
  `RunConfig`, and the scripted `FakeLLM`, so that wave-2 tasks can be built in parallel against
  fixed names.
- Add all runtime dependencies now (one `uv.lock` change), set up the test tree, the `git_repo`
  fixture, an import-layering test, and the CI change.
- T1 contains no business logic. The only real code is small: `core/paths.py`, model
  validators and helpers, `FakeLLM`, the minimal CLI, and the moved GitHub-Action helpers.

### Layering rules (enforced by `tests/unit/test_layering.py`, §8 test 27)
| Package | May import (besides stdlib, `pydantic`) |
|---|---|
| `core` | `core` only |
| `config`, `model_flags` (shared kernel; pure) | `config`, `core` |
| `analysis` | `core`, `config` |
| `publishing` | `core`, `config`, `publishing` |
| `prompts` | `core` |
| `agents` | `core`, `config`, `prompts`, `agents` |
| `infra` | `core`, `config`, `model_flags`, `infra` |
| `tools` | `core`, `config`, `analysis`, `agents`, `tools` |
| `workflow` | everything except `cli`, `integrations` (it is the composition root that injects `infra`) |
| `cli` | `config`, `model_flags`, `core`, `workflow` |
| `integrations` | `config`, `core`, `cli` |
- `ai` may only be imported by `src/readme_stack/infra/llm/adapter.py`. Ruff `TID251` enforces
  this too (§4).
- `tree_sitter` / `tree_sitter_language_pack` may only be imported under `analysis/parsing/`.
- Tests are exempt.

## 2. Files
Create (empty unless noted):
| Path | Content |
|---|---|
| `src/readme_stack/__init__.py` | edit: `__version__ = "0.1.0"` (single source; see §5) |
| `src/readme_stack/__main__.py` | `from readme_stack.cli.app import main` / `raise SystemExit(main())` |
| `src/readme_stack/config.py` | §3.1 |
| `src/readme_stack/cli/__init__.py`, `cli/app.py` | `app.py`: §3.9 (replaced wholesale by T17) |
| `src/readme_stack/core/__init__.py` | empty |
| `src/readme_stack/core/errors.py` | §3.2 |
| `src/readme_stack/core/paths.py` | §3.3 |
| `src/readme_stack/core/ports.py` | §3.4 |
| `src/readme_stack/core/models/__init__.py` | empty (no re-exports; import from the defining module) |
| `src/readme_stack/core/models/usage.py` | §3.5 `Usage` |
| `src/readme_stack/core/models/git.py` | §3.5 `RevRange`, `CommitInfo` |
| `src/readme_stack/core/models/impact.py` | §3.5 changed files, `ImpactReport` |
| `src/readme_stack/core/models/plan.py` | §3.5 `PageKind`, `PageSpec`, `DocPlan`, `Section`, `PageDraft` |
| `src/readme_stack/core/models/manifest.py` | §3.5 constants, `ManifestEntry`, `Manifest` |
| `src/readme_stack/core/models/repo.py` | §3.5 `FileIndex` family, `Component`, `RepoModel` |
| `src/readme_stack/core/models/docs_state.py` | §3.5 `ManifestStatus`, `FileStatus`, `VersionCmp`, `LegacyEntry`, `DocsState` |
| `src/readme_stack/workflow/__init__.py`, `workflow/stages/__init__.py` | empty |
| `src/readme_stack/agents/__init__.py`, `tools/__init__.py` | empty |
| `src/readme_stack/analysis/__init__.py`, `publishing/__init__.py` | empty |
| `src/readme_stack/infra/__init__.py`, `infra/llm/__init__.py` | empty |
| `src/readme_stack/infra/llm/fake.py` | §7 `FakeLLM` |
| `src/readme_stack/integrations/__init__.py`, `integrations/github_action.py` | §3.8 |
| `tests/conftest.py` | §3.10 `git_repo`, `git_commit` |
| `tests/unit/core/test_errors.py`, `test_paths.py`, `test_models.py`, `test_ports.py`, `test_docs_state.py` | §8 |
| `tests/unit/infra/test_fake_llm.py` | §8 |
| `tests/unit/cli/test_version.py` | §8 test 31–32 (must keep passing after T17) |
| `tests/unit/cli/test_app_stub.py` | `main([])` returns 1 with `pipeline is not available` on stderr (T17 deletes this file) |
| `tests/unit/integrations/test_github_action.py` | the 2 `get_input` tests + a `set_output` test, moved from `tests/test_main.py` |
| `tests/unit/test_layering.py`, `tests/unit/test_packaging.py`, `tests/unit/test_git_fixture.py` | §8 |
| `tests/unit/analysis/.gitkeep`, `tests/unit/publishing/.gitkeep`, `tests/integration/.gitkeep`, `tests/fixtures/repos/.gitkeep`, `tests/e2e/.gitkeep` | placeholders so git tracks the dirs |

Delete: `src/readme_stack/main.py`, `tests/test_main.py`.
Edit: `pyproject.toml`, `uv.lock` (regenerated), `.github/workflows/ci.yml`.
Untouched: `action.yml`, `README.md` (see §9).

**Not created by T1** (owned elsewhere, so there are no merge conflicts): `core/modes.py` (T2),
`model_flags.py` (T3), `core/models/facts.py` and `core/models/code.py` (T10),
`prompts/` package (T12), `analysis/parsing/` (T10), `publishing/templates/` (T8),
`infra/fs.py` (tools/fs task T14), `workflow/result.py` (T17).

**Test layout:** no `__init__.py` anywhere under `tests/`. Pytest runs with
`--import-mode=importlib`, so duplicate basenames (`tests/unit/infra/test_git.py`, a later
`tests/unit/tools/test_git.py`) are fine. Consequence: test modules cannot import from
`conftest.py`. Shared helpers must be fixtures, which can return classes or callables.

## 3. Public interface (canonical definitions)
Conventions for all models:
- **Deterministic models** (computed by Python): `ConfigDict(frozen=True, extra="forbid")`,
  collections are `tuple[...]`.
- **LLM-facing models** (an agent's `output_type` or part of one): `ConfigDict(frozen=True,
  extra="forbid")`, collections are `list[...]` (plain JSON-schema arrays). Semantic validation
  (page cap, glob matching, collisions) is **not** done in the models. It belongs to the stages.
- All paths are repo-relative POSIX `str`. The repo root is `"."` only where a model says so.

### 3.1 `config.py` (shared kernel: imports stdlib and `core` only)
```python
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final
from collections.abc import Mapping

class Provider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"

DEFAULT_MODELS: Final[Mapping[Provider, str]] = MappingProxyType(
    {Provider.ANTHROPIC: "claude-sonnet-5", Provider.OPENAI: "gpt-5"}
)
DEFAULT_DOCS_DIR: Final[str] = "docs/architecture"
DEFAULT_MAX_PAGES: Final[int] = 12
DEFAULT_MAX_TOKENS: Final[int] = 2_000_000
DEFAULT_CONCURRENCY: Final[int] = 4
PROGRESS_LOGGER: Final[str] = "readme_stack.progress"

@dataclass(frozen=True, slots=True, kw_only=True)
class RunConfig:
    repo: Path                          # absolute, resolved (CLI checks is_dir; preflight checks top level)
    diff_range: str | None = None       # stripped raw --diff; validated by git in preflight
    provider: Provider | None = None    # raw --provider
    model: str | None = None            # raw --model, stripped, may carry "provider:" prefix
    docs_dir: str = DEFAULT_DOCS_DIR    # normalized repo-relative POSIX str (core.paths.normalize_rel_path)
    force: bool = False
    recreate: bool = False
    dry_run: bool = False
    max_pages: int = DEFAULT_MAX_PAGES
    max_tokens: int = DEFAULT_MAX_TOKENS
    concurrency: int = DEFAULT_CONCURRENCY
    verbosity: int = 0                  # 0..2

    def __post_init__(self) -> None:
        """ValueError (programming error; the CLI validates first) if: repo not absolute;
        max_pages/max_tokens/concurrency < 1; verbosity not in 0..2;
        normalize_rel_path(docs_dir) != docs_dir; diff_range == "" ; diff_range and recreate."""
```
`RunConfig` never holds an API key. The resolved provider, model and key live in infra/workflow
(T3 `ResolvedProvider`, T16 `RunContext`).

### 3.2 `core/errors.py`
```python
from enum import IntEnum
from typing import ClassVar
from readme_stack.core.models.usage import Usage

class ExitCode(IntEnum):
    OK = 0
    FAILURE = 1
    USAGE = 2
    ENVIRONMENT = 3
    REPO_STATE = 4
    BUDGET_EXCEEDED = 5
    INTERRUPTED = 130

class ReadmeStackError(Exception):
    exit_code: ClassVar[ExitCode] = ExitCode.FAILURE
    def __init__(self, message: str) -> None: ...      # str(err) == message; err.message == message

class UsageError(ReadmeStackError):        exit_code = ExitCode.USAGE            # 2
class EnvError(ReadmeStackError):          exit_code = ExitCode.ENVIRONMENT      # 3: keys, git, range, newer format
class RepoStateError(ReadmeStackError):    exit_code = ExitCode.REPO_STATE       # 4
class BudgetExceededError(ReadmeStackError): exit_code = ExitCode.BUDGET_EXCEEDED  # 5

# git (raised by infra/git.py, T4); all take a single message
class GitUnavailable(EnvError): ...        # 3: git binary missing / not runnable
class NotAGitRepository(EnvError): ...     # 3
class NotGitTopLevel(EnvError): ...        # 3: REPO is not the work-tree top level
class InvalidRevision(EnvError): ...       # 3
class InvalidRange(EnvError): ...          # 3
class GitCommandError(ReadmeStackError): ...  # 1: unexpected git failure / timeout

# agent tools: a ToolError raised by a tool handler becomes an *error tool result* for the model
class ToolError(ReadmeStackError): ...     # 1 if it ever escapes
class SandboxViolation(ToolError): ...     # path escapes root, absolute, "..", ".git", NUL (T4)
class FileAccessError(ToolError): ...      # missing, not a regular file, too large, binary, unreadable (T14)

# LLM (raised by infra/llm/adapter.py T11 and FakeLLM)
class LLMError(ReadmeStackError):          # 1
    usage: Usage                           # accumulated before the failure; Usage() if none
    def __init__(self, message: str, *, usage: Usage | None = None) -> None: ...
class LLMOutputError(LLMError): ...        # structured output invalid after repairs
class LLMStepLimitError(LLMError): ...     # max_steps exhausted
class LLMProviderError(LLMError): ...      # auth / transport failure after retries

__all__ = [...all of the above...]
```
Rules:
- The CLI maps `ReadmeStackError` to `err.exit_code`, `KeyboardInterrupt` to 130, and anything
  else to 1. There is no exception class for 130.
- Tasks may define **module-local subclasses** of these bases for their own failure modes, with
  extra keyword-only fields and `str(err) == message`: T7 `ChangeSetError*`, T12 `PromptError*`
  and `AgentError`/`AgentConfigError`. Classes that are raised in one layer and caught in another
  live here.
- There is no `ManifestError`. T6 reports manifest problems through `ManifestStatus`. T2 raises
  `EnvError` for a newer format and `RepoStateError` for REFUSE.
- `errors.py` imports `Usage` from `core/models/usage.py`, not from `ports.py`, so there is no
  import cycle.

### 3.3 `core/paths.py` (pure, shared path rules)
```python
def normalize_rel_path(path: str) -> str:
    """Repo-relative POSIX normalization (T6 §4.5 semantics). Reject (ValueError): "" ; NUL;
    "\\" ; absolute ("/x", PureWindowsPath(p).drive) ; any ".." segment. Collapse "." segments
    and duplicate "/" via PurePosixPath; strip a trailing "/". Reject a result of ".".
    Returns the normalized str."""

def is_within_dir(path: str, directory: str) -> bool:
    """path == directory or path.startswith(directory + "/"). Both must already be normalized
    (a trailing "/" on directory is stripped first). Pure string check, no I/O."""
```
Users: T6 (manifest paths), T7, T9 (owned-output check), T17 (`--docs-dir`), T5 (owned output;
T5 keeps its own stricter `normalize_path` for `git ls-files` input).

### 3.4 `core/ports.py`
```python
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable
from pydantic import BaseModel

from readme_stack.core.models.git import CommitInfo, RevRange
from readme_stack.core.models.impact import ChangedFiles
from readme_stack.core.models.usage import Usage

TOOL_NAME_PATTERN: Final[str] = r"^[a-z][a-z0-9_]*$"
type ToolHandler = Callable[[Any], Awaitable[str]]   # receives a validated `parameters` instance

@dataclass(frozen=True, slots=True, kw_only=True)
class ToolSpec:
    name: str                        # TOOL_NAME_PATTERN; unique within one call
    description: str                 # shown to the model
    parameters: type[BaseModel]      # args schema (adapter: model_json_schema / model_validate)
    handler: ToolHandler             # async; returns the text shown to the model
    is_subagent: bool = False
    def __post_init__(self) -> None: ...  # ValueError: bad name, empty description,
                                          # parameters not a BaseModel subclass

@runtime_checkable
class LLM(Protocol):
    async def run_structured[T: BaseModel](
        self, *, system: str, user: str, tools: Sequence[ToolSpec],
        output_type: type[T], max_steps: int, agent_name: str,
    ) -> tuple[T, Usage]: ...

@runtime_checkable
class Git(Protocol):                 # implemented by infra/git.py GitRepo (T4); all sync
    @property
    def root(self) -> Path: ...
    def head_sha(self) -> str | None: ...             # None on an unborn HEAD
    def is_shallow(self) -> bool: ...
    def resolve_commit(self, rev: str) -> str: ...    # full sha; InvalidRevision
    def resolve_range(self, spec: str) -> RevRange: ...   # InvalidRange
    def is_reachable(self, sha: str) -> bool: ...     # exists AND ancestor of HEAD; never raises
    def ls_files(self) -> list[str]: ...              # tracked + untracked-not-ignored, sorted, unique
    def ls_tree(self, rev: str) -> list[str]: ...     # blob paths at rev (no submodules), sorted
    def changed_files(self, rng: RevRange) -> ChangedFiles: ...
    def diff(self, rng: RevRange, paths: Sequence[str] = (), *, context: int = 3,
             max_bytes: int = 1_000_000) -> str: ...
    def show(self, rev: str, path: str | None = None, *, max_bytes: int = 1_000_000) -> str: ...
    def log(self, paths: Sequence[str] = (), *, rev: str = "HEAD",
            max_count: int = 20) -> list[CommitInfo]: ...
    def is_tracked_and_clean(self, path: str) -> bool: ...

@runtime_checkable
class FileSystem(Protocol):          # implemented by infra/fs.py LocalFileSystem (T14), over T4 Sandbox
    @property
    def root(self) -> Path: ...
    def exists(self, path: str) -> bool: ...          # regular file inside root; never raises
    def read_bytes(self, path: str, *, max_bytes: int) -> bytes: ...
    def read_text(self, path: str, *, max_bytes: int) -> str: ...
    # read_*: SandboxViolation for a rejected path; FileAccessError if missing, not a regular
    # file, size > max_bytes, unreadable, or (read_text only) binary (NUL in the first 8 KiB).
    # read_text decodes UTF-8 with errors="replace" and strips one BOM.
    # Index membership (FileIndex) is NOT checked here. tools/fs.py checks it, because the
    # port is built before preflight produces the index.

@runtime_checkable
class SummaryCache(Protocol):        # infra/cache.py (T4)
    def get(self, key: str) -> str | None: ...
    def put(self, key: str, value: str) -> None: ...

__all__ = ["TOOL_NAME_PATTERN", "ToolHandler", "ToolSpec", "LLM", "Git", "FileSystem",
           "SummaryCache", "Usage", "RevRange", "CommitInfo"]   # last three re-exported for convenience
```
LLM port semantics (binding for T11 adapter and `FakeLLM`):
1. Run a tool loop of at most `max_steps` model turns. For each tool call:
   `args = parameters.model_validate(raw)`, then `await handler(args)`, and send the string
   result back.
2. An unknown tool name, an args `ValidationError`, or a `ToolError` (including
   `SandboxViolation` and `FileAccessError`) becomes an **error tool result**, and the loop
   continues.
3. Any other exception from a handler propagates unchanged (for example `BudgetExceededError`
   or `CancelledError`).
4. The call returns a validated `output_type` instance plus the summed `Usage` of every request
   made in the call, retries included. The adapter raises `LLMError` subclasses only after it has
   given up, with the accumulated usage attached.
5. `agent_name` is for logging and FakeLLM routing only. `tools=()` means a single structured call.
   The model is fixed when the adapter is constructed.

### 3.5 `core/models/*`
**`usage.py`**
```python
class Usage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    input_tokens: int = Field(default=0, ge=0)       # includes cached input
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)  # subset of input_tokens
    requests: int = Field(default=0, ge=0)           # API requests incl. retries
    @property
    def total_tokens(self) -> int: ...               # input_tokens + output_tokens
    def __add__(self, other: object) -> "Usage": ... # field-wise; NotImplemented for non-Usage
```
**`git.py`**
```python
class RevRange(BaseModel):   # frozen, extra=forbid
    base: str                # full sha
    head: str                # full sha
    spec: str                # user text, e.g. "main..HEAD"; "" for an internally built range
class CommitInfo(BaseModel): # frozen, extra=forbid
    sha: str; date: str; author: str; subject: str     # date = ISO 8601 (%aI)
```
**`impact.py`**
```python
class ChangeStatus(StrEnum):
    ADDED = "A"; MODIFIED = "M"; DELETED = "D"; RENAMED = "R"; COPIED = "C"
class ChangedFile(BaseModel):          # deterministic; git "T" maps to MODIFIED
    status: ChangeStatus
    path: str                          # new path for R/C, deleted path for D
    old_path: str | None = None        # set only for R/C
    similarity: int | None = Field(default=None, ge=0, le=100)   # only for R/C
class ChangedFiles(BaseModel):         # deterministic
    base: str; head: str
    files: tuple[ChangedFile, ...] = ()   # git output order

class LeftoverAction(StrEnum):
    ASSIGN = "assign"       # belongs to an existing page (page_id set)
    NEW_PAGE = "new_page"   # warrants a proposed page
    README = "readme"       # affects the README only
    IGNORE = "ignore"       # no documentation impact
class LeftoverNote(BaseModel):         # LLM-facing
    path: str
    action: LeftoverAction
    page_id: str | None = None
    note: str = ""
class ProposedPage(BaseModel):         # LLM-facing
    title: str
    summary: str
    source_paths: list[str]
    reason: str
class ImpactReport(BaseModel):         # LLM-facing (ImpactAnalyst output); also T9 initial_report
    affected_pages: list[str] = []     # page ids, README excluded
    readme_affected: bool = False
    reasons: dict[str, list[str]] = {} # key: page id or "readme"
    dropped_pages: list[str] = []
    proposed_pages: list[ProposedPage] = []
    leftover_notes: list[LeftoverNote] = []
```
T9's `ImpactMapping`, `PageHit` and related types stay in `analysis/impact.py`, because only the
impact stage consumes them.

**`plan.py`**
```python
PAGE_ID_PATTERN: Final[str] = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"   # slug; README id "readme" matches

class PageKind(StrEnum):
    README = "readme"          # == ManifestEntry.kind of the README
    OVERVIEW = "overview"
    COMPONENT = "component"

class PageSpec(BaseModel):     # LLM-facing (Outliner)
    id: str = Field(pattern=PAGE_ID_PATTERN)
    path: str                  # "README.md" or "<docs_dir>/<slug>.md"
    kind: PageKind
    title: str = Field(min_length=1)
    summary: str = ""          # one line, used in the README index and related-page lists
    source_paths: list[str] = []   # T9 glob syntax; README may be []
    sections: list[str] = []   # planned headings
    links_to: list[str] = []   # page ids

class DocPlan(BaseModel):      # LLM-facing (Outliner)
    pages: list[PageSpec]      # ordered; INCLUDES exactly one README page (id "readme",
                               # path "README.md"); the outline stage inserts it if missing
    def page(self, page_id: str) -> PageSpec | None: ...
    @property
    def readme(self) -> PageSpec: ...          # ValueError if not exactly one kind=README page
    @property
    def subpages(self) -> tuple[PageSpec, ...]: ...   # non-README pages, plan order

class Section(BaseModel):      # LLM-facing
    heading: str               # rendered as "## heading"; may be ""
    body: str                  # markdown; may contain {{facts:*}}
class PageDraft(BaseModel):    # LLM-facing (PageWriter, ReadmeWriter)
    page_id: str
    summary: str = ""
    sections: list[Section]
```
**`manifest.py`**
```python
MANIFEST_FORMAT_VERSION: Final[int] = 1
MANIFEST_FILENAME: Final[str] = ".readme-stack.json"
TOOL_NAME: Final[str] = "readme-stack"
README_PATH: Final[str] = "README.md"
README_PAGE_ID: Final[str] = "readme"
SHA256_PATTERN: Final[str] = r"^[0-9a-f]{64}$"
COMMIT_SHA_PATTERN: Final[str] = r"^[0-9a-f]{40}([0-9a-f]{24})?$"   # SHA-1 or SHA-256

class ManifestEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    path: str                   # repo-relative; README_PATH or under docs_dir
    page_id: str = Field(pattern=PAGE_ID_PATTERN)
    kind: PageKind
    title: str
    summary: str
    sha256: str = Field(pattern=SHA256_PATTERN)   # T6 normalized hash of the file as written
    source_paths: list[str]

class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tool: Literal["readme-stack"]
    tool_version: str
    format_version: int = Field(ge=1)
    docs_dir: str
    source_commit: str = Field(pattern=COMMIT_SHA_PATTERN)
    provider: str               # Provider value; str keeps core free of config
    model: str
    files: list[ManifestEntry]  # INCLUDES the README entry (page_id "readme", kind README)
```
**Ownership contract (permanent):** every future format version keeps `tool`, `format_version`,
and `files[]` objects with `path` and `sha256` meaning exactly what they mean now. That lets
older-format manifests still prove ownership (T6 `legacy_files`, see `DocsState`). Put this in
the module docstring.

**`repo.py`**
```python
class ExclusionReason(StrEnum):
    INVALID_PATH = "invalid_path"; OWNED_OUTPUT = "owned_output"; DENYLISTED = "denylisted"
    MISSING = "missing"; SYMLINK = "symlink"; NOT_REGULAR = "not_regular"
    TOO_LARGE = "too_large"; BINARY = "binary"; UNREADABLE = "unreadable"; FILE_LIMIT = "file_limit"

class FileEntry(BaseModel):           # deterministic
    path: str
    size: int = Field(ge=0)
    language: str | None              # T5 detect_language id
    is_readme: bool = False           # root README.md only
class ExcludedFile(BaseModel):        # deterministic
    path: str
    reason: ExclusionReason
class FileIndex(BaseModel):           # deterministic; no root field
    files: tuple[FileEntry, ...]      # sorted by path, unique (validator: ValueError otherwise)
    excluded: tuple[ExcludedFile, ...] = ()   # sorted by path
    def paths(self) -> frozenset[str]: ...    # computed once (PrivateAttr set in model_post_init)
    def get(self, path: str) -> FileEntry | None: ...
    def __contains__(self, path: object) -> bool: ...
    @property
    def readme(self) -> FileEntry | None: ...

class Component(BaseModel):           # LLM-facing (ComponentExplorer); placeholder shape
    name: str
    paths: list[str]                  # dirs / globs
    summary: str
    responsibilities: list[str] = []
    key_files: list[str] = []
    depends_on: list[str] = []        # other component names
class RepoModel(BaseModel):           # LLM-facing (Explorer); placeholder shape
    summary: str
    components: list[Component]
    entry_points: list[str] = []
    notes: list[str] = []
```
`Component` and `RepoModel` are placeholders. The prompts step may **add** fields, always with
defaults. `ProjectFacts` and `ComponentCandidate` are **not** here: T10 owns
`core/models/facts.py` and `core/models/code.py` (new files, same conventions).

**`docs_state.py`** (types only. The loader is T6 `publishing/manifest_store.load_docs_state`)
```python
class ManifestStatus(StrEnum):
    ABSENT = "absent"; INVALID = "invalid"; VALID = "valid"; OLDER = "older"; NEWER = "newer"
class FileStatus(StrEnum):
    CLEAN = "clean"; HAND_EDITED = "hand_edited"; MISSING = "missing"
class VersionCmp(StrEnum):
    OLDER = "older"; CURRENT = "current"; NEWER = "newer"

@dataclass(frozen=True, slots=True, kw_only=True)
class LegacyEntry:                    # ownership subset read from an OLDER manifest
    path: str
    sha256: str

@dataclass(frozen=True, slots=True, kw_only=True)
class DocsState:
    readme_path: str = README_PATH
    readme_exists: bool                   # regular file (not a symlink)
    readme_marker_version: int | None     # N from the first-line marker; None if none
    docs_dir: str                         # normalized
    docs_dir_exists: bool                 # anything exists at docs_dir (lexists)
    manifest_status: ManifestStatus
    manifest: Manifest | None = None      # iff VALID
    manifest_format_version: int | None = None   # iff VALID/OLDER/NEWER
    manifest_error: str | None = None     # iff INVALID (non-empty)
    legacy_files: tuple[LegacyEntry, ...] = ()   # only when OLDER; sorted by path
    file_statuses: Mapping[str, FileStatus] = field(default_factory=dict)  # keys == owned_paths

    @property
    def manifest_path(self) -> str: ...   # f"{docs_dir}/{MANIFEST_FILENAME}"
    @property
    def marker_present(self) -> bool: ...
    @property
    def is_our_format(self) -> bool: ...  # VALID and readme_marker_version == MANIFEST_FORMAT_VERSION
    @property
    def owned_paths(self) -> tuple[str, ...]: ...   # VALID: manifest file paths; OLDER: legacy paths; else (); sorted
    @property
    def hand_edited_paths(self) -> tuple[str, ...]: ...
    @property
    def missing_paths(self) -> tuple[str, ...]: ...
    def __post_init__(self) -> None:
        """ValueError on impossible states: marker without readme_exists or < 1; manifest set
        iff VALID; format_version set iff VALID/OLDER/NEWER, and >= 1; error set iff INVALID;
        legacy_files only when OLDER; status != ABSENT requires docs_dir_exists; VALID requires
        manifest.docs_dir == docs_dir and manifest.format_version == manifest_format_version;
        file_statuses keys == set(owned_paths)."""
```
`DocsState` is a stdlib dataclass (internal, never serialized). T2's `DocsSnapshot` is dropped,
and `resolve_mode` takes a `DocsState`.

### 3.6 Constants summary
| Constant | Module |
|---|---|
| `DEFAULT_MODELS`, `DEFAULT_DOCS_DIR`, `DEFAULT_MAX_PAGES`, `DEFAULT_MAX_TOKENS`, `DEFAULT_CONCURRENCY`, `PROGRESS_LOGGER` | `config` |
| `MANIFEST_FORMAT_VERSION`, `MANIFEST_FILENAME`, `TOOL_NAME`, `README_PATH`, `README_PAGE_ID`, `SHA256_PATTERN`, `COMMIT_SHA_PATTERN` | `core.models.manifest` |
| `PAGE_ID_PATTERN` | `core.models.plan` |
| `TOOL_NAME_PATTERN` | `core.ports` |

### 3.7 `model_flags.py` (listed for completeness; **T3 creates it**, T1 does not)
Shared-kernel module with `ModelSpec`, `parse_model_flag`,
`check_provider_flags(provider_flag, model_flag) -> ModelSpec | None`, and the `M_MODEL_EMPTY`,
`M_MODEL_NO_ID` and `M_CONFLICT` messages. The CLI imports it instead of `infra`.
`infra/llm/providers.py` re-exports it.

### 3.8 `integrations/github_action.py`
`get_input(name: str, default: str = "") -> str` and `set_output(name: str, value: str) -> None`,
moved verbatim from `main.py` (same behavior, docstrings, and local `[output]` fallback). `run()`
and the old `main()` are deleted. Nothing imports this module yet. It is used by the action step.

### 3.9 `cli/app.py` (minimal; T17 replaces the file)
```python
def main(argv: Sequence[str] | None = None) -> int:
    """argparse prog "readme-stack", allow_abbrev=False, only --version
    (action="version", version=f"%(prog)s {__version__}").
    - --version / --help: argparse's SystemExit is caught; return its code (0).
    - argparse errors (unknown args): return 2 (argparse's usage message goes to stderr).
    - no args: print "readme-stack: error: the generation pipeline is not available in this
      build" to stderr, return 1."""
```
It never lets `SystemExit` escape. `readme_stack.workflow.pipeline` is never imported.

### 3.10 `tests/conftest.py`
```python
@pytest.fixture
def git_repo(tmp_path, monkeypatch) -> Path:
    """Fresh repo at (tmp_path.resolve() / "repo"), branch main, no commits.
    - skip (pytest.skip) if shutil.which("git") is None
    - env isolation via monkeypatch: GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM=1,
      GIT_AUTHOR_NAME/EMAIL, GIT_COMMITTER_NAME/EMAIL = "Test"/"test@example.com",
      GIT_AUTHOR_DATE = GIT_COMMITTER_DATE = "2000-01-01T00:00:00+00:00" (deterministic shas),
      removes GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE if set
    - `git init -q -b main`; if git < 2.28 rejects -b: `git init -q` + `git symbolic-ref HEAD refs/heads/main`
    - local config: commit.gpgsign=false, core.autocrlf=false"""

@pytest.fixture
def git_commit() -> Callable[..., str]:
    """Returns commit(repo: Path, files: Mapping[str, str | bytes | None], message="commit") -> str.
    Writes str (UTF-8, as given) or bytes; None deletes the file. Creates parent dirs,
    `git add -A`, `git commit -q --allow-empty -m message`, returns the full HEAD sha."""
```
Git runs through `subprocess.run([...], check=True, capture_output=True)` with no shell.

## 4. Dependency and tool configuration (`pyproject.toml`)
```toml
[project]
name = "readme-stack"
dynamic = ["version"]
description = "Generate and maintain architecture documentation for a git repository with LLM agents."
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "ai==<X.Y.Z>",                           # exact pin; beta SDK
    "pydantic>=2.9,<3",
    "tree-sitter-language-pack~=<X.Y>",      # bundles grammars; pulls a compatible tree-sitter
    "tree-sitter>=<resolved>,<<next minor>>",# explicit, bounded to what the pack supports
]

[project.scripts]
readme-stack = "readme_stack.cli.app:main"

[tool.hatch.version]
path = "src/readme_stack/__init__.py"

[tool.ruff]
line-length = 100
extend-exclude = ["tests/fixtures"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "TID"]

[tool.ruff.lint.flake8-tidy-imports.banned-api]
"ai".msg = "Only readme_stack.infra.llm.adapter may import the ai SDK."

[tool.ruff.lint.per-file-ignores]
"src/readme_stack/infra/llm/adapter.py" = ["TID251"]
"tests/**" = ["TID251"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = ["--import-mode=importlib", "--strict-markers", "-m", "not e2e"]
markers = ["e2e: opt-in end-to-end tests against a real provider (run: uv run pytest -m e2e)"]
norecursedirs = [".*", "build", "dist", "*.egg", "venv", ".venv", "node_modules", "fixtures"]
```
- Get the exact versions at implementation time: `uv add "ai==<latest>" "pydantic>=2.9,<3"
  tree-sitter-language-pack tree-sitter`, then tighten the specifiers as shown. Record the chosen
  versions in the PR. The dev group stays `pytest`, `ruff` (no pytest-asyncio; async tests use
  `asyncio.run`).
- The last `-m` given wins, so `uv run pytest -m e2e` runs only e2e tests.
- Regenerate the lock with `uv lock`, and check that `uv sync --locked` passes.

### CI (`.github/workflows/ci.yml`)
The `test` job is unchanged. Replace `self-test` with:
```yaml
  self-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v7
      - run: uv sync --locked --no-dev
      - run: uv run --locked --no-dev readme-stack --version
      - run: uv run --locked --no-dev python -m readme_stack --version
```

## 5. Version single-sourcing
`__version__ = "0.1.0"` in `src/readme_stack/__init__.py` is the only place the version is
written. Hatch reads it (`dynamic = ["version"]`). `test_packaging` asserts
`importlib.metadata.version("readme-stack") == readme_stack.__version__`.

## 7. `infra/llm/fake.py` — FakeLLM
```python
@dataclass(frozen=True, slots=True, kw_only=True)
class FakeToolCall:
    name: str
    args: dict[str, Any]

@dataclass(frozen=True, slots=True, kw_only=True)
class FakeResponse:
    output: BaseModel | dict[str, Any] | Exception
    usage: Usage = Usage(input_tokens=10, output_tokens=5, requests=1)
    tool_calls: tuple[FakeToolCall, ...] = ()
    when: Callable[[str], bool] | None = None   # predicate on the user message; None = any

@dataclass(frozen=True, slots=True, kw_only=True)
class FakeCall:
    agent_name: str
    system: str
    user: str
    tool_names: tuple[str, ...]
    output_type: type[BaseModel]
    max_steps: int
    tool_results: tuple[tuple[str, str, bool], ...] = ()   # (tool name, result text, is_error)

class FakeLLM:                     # satisfies core.ports.LLM (isinstance passes)
    calls: list[FakeCall]
    def __init__(self, script: Mapping[str, Sequence[FakeResponse | BaseModel]] | None = None) -> None: ...
    def add(self, agent_name: str, *responses: FakeResponse | BaseModel) -> None: ...
    def calls_for(self, agent_name: str) -> list[FakeCall]: ...
    def pending(self) -> dict[str, int]: ...          # agent -> unconsumed responses (non-zero only)
    def assert_exhausted(self) -> None: ...           # AssertionError listing pending responses
    async def run_structured(self, *, system, user, tools, output_type, max_steps, agent_name): ...
```
Behavior of `run_structured`:
1. **Select:** in the FIFO queue for `agent_name`, take the **first** response whose `when` is
   `None` or returns True for `user`, and remove it. A bare `BaseModel` counts as
   `FakeResponse(output=model)`. If nothing matches, raise
   `AssertionError(f"FakeLLM: no scripted response for {agent_name!r}")`. Using `when`, tests
   route concurrent same-name calls (parallel page writers) by content, for example
   `when=lambda u: "docs/architecture/cli.md" in u`.
2. **Record:** append a `FakeCall` to `calls` **before** running tools. Afterwards, replace that
   list slot with the version that has `tool_results`. This keeps nested subagent calls in the
   right order (outer call first).
3. **Tools:** run `tool_calls` one after another, following port semantics §3.4:
   - an unknown name gives the result `"error: unknown tool '<name>'"`;
   - a `ValidationError` from `parameters.model_validate` gives
     `"error: invalid arguments: <str(e)>"`;
   - a `ToolError` gives `"error: <str(e)>"`;
   - these three are recorded with `is_error=True`, and any other exception propagates.
   `max_steps` is recorded but not enforced.
4. **Output:** an `Exception` output is raised (for example `LLMError(..., usage=...)`). A dict is
   passed to `output_type.model_validate`, and a `ValidationError` there becomes
   `LLMOutputError(str(e), usage=response.usage)`. A `BaseModel` is returned as is, with **no**
   isinstance check, so T12 can test its wrong-type guard.
5. Return `(output, response.usage)`.

No `ai` import. It lives in `src/`, so integration tests and other tasks can use it.

## 8. Test plan
`tests/unit/core/test_errors.py`
1. Every class has the documented `exit_code` (parametrized table covering all of §3.2), and
   every class is a `ReadmeStackError`.
2. `str(err) == err.message == "m"`. `ExitCode` values are exactly 0, 1, 2, 3, 4, 5, 130.
3. `SandboxViolation` and `FileAccessError` are `ToolError`s. The git exit-3 classes are `EnvError`s.
4. `LLMError("x").usage == Usage()`. `LLMOutputError("x", usage=U).usage is U`.

`tests/unit/core/test_paths.py`
5. `normalize_rel_path`: `"docs//a/./b.md"` gives `"docs/a/b.md"`, and `"docs/"` gives `"docs"`.
   `ValueError` for `""`, `"/abs"`, `"C:/x"`, `"a\\b"`, `"../x"`, `"a/../b"`, `"."`, `"./"`, and
   `"a\x00"`.
6. `is_within_dir`: `("docs/a/x.md", "docs/a")` is True, `("docs/a", "docs/a")` is True,
   `("docs/ab/x.md", "docs/a")` is False, and a trailing slash on the dir works.

`tests/unit/core/test_models.py`
7. `Usage`: `+` sums field-wise, `total_tokens`, frozen, negative values rejected, and
   `sum([...], Usage())` works.
8. `RevRange` and `CommitInfo` are frozen, and `extra="forbid"` rejects unknown keys.
9. `ChangedFile` with `similarity=101` is rejected. `ChangeStatus("R") is RENAMED`.
10. `ImpactReport()` builds with all defaults, and its JSON round-trip is equal.
11. `PageSpec`: the id pattern accepts `readme`, `cli`, `data-model` and rejects `Data`, `a_b`,
    `-x`, `""`. An empty title is rejected.
12. `DocPlan.readme` returns the README page, and raises `ValueError` for zero or two README
    pages. `page()` and `subpages` keep order.
13. `ManifestEntry` and `Manifest`: a valid instance round-trips through
    `model_dump(mode="json")` / `model_validate`. Rejected: an extra key, an uppercase sha256, a
    39-char `source_commit`, `format_version=0`, `tool="other"`, `kind="page"`. A 64-char
    SHA-256 `source_commit` is accepted.
14. `FileIndex`: `paths()`, `get()`, `in`, and `readme`. Unsorted or duplicate `files` raise
    `ValidationError`. Equal inputs give equal indexes.
15. `Component` and `RepoModel` build from minimal dicts. All LLM-facing models (`PageSpec`,
    `DocPlan`, `Section`, `PageDraft`, `ImpactReport`, `ProposedPage`, `LeftoverNote`,
    `Component`, `RepoModel`) produce a `model_json_schema()` with
    `additionalProperties: false`, and use no tuple (`prefixItems`) schemas.
16. `RunConfig`: the defaults match the constants, it is frozen and kw_only. `__post_init__`
    `ValueError` cases: relative repo, `max_tokens=0`, `verbosity=3`,
    `docs_dir="docs/../x"`, `docs_dir="docs/"` (not normalized), `diff_range=""`, and
    `diff_range` together with `recreate`.
17. `DEFAULT_MODELS` is read-only (a `TypeError` on assignment), and `str(Provider.OPENAI) == "openai"`.

`tests/unit/core/test_docs_state.py`
18. Valid states build for each `ManifestStatus`, and the derived properties are correct:
    `owned_paths` (VALID → manifest paths, OLDER → legacy paths, other statuses → `()`),
    `is_our_format` (VALID with the current marker only), and `manifest_path`.
19. Each `__post_init__` invariant raises `ValueError` (parametrized).
20. Frozen: assignment raises `FrozenInstanceError`.

`tests/unit/core/test_ports.py`
21. `ToolSpec` validation (bad name, a non-BaseModel `parameters`). `dataclasses.replace` works.
22. Minimal hand-written fakes for `Git`, `FileSystem` and `SummaryCache` pass `isinstance` (the
    Protocols are runtime_checkable). `FakeLLM()` passes `isinstance(x, LLM)`.

`tests/unit/infra/test_fake_llm.py` (async through `asyncio.run`)
23. FIFO per agent. A bare `BaseModel` works as shorthand. A dict output is validated. An
    invalid dict raises `LLMOutputError` with the usage attached. An `Exception` output is
    raised. A missing or empty queue raises `AssertionError`.
24. `when` routing: two queued responses with predicates, requested in reverse order, each go to
    their matching caller. `pending()` and `assert_exhausted()` work.
25. Tools: an echo tool result, an unknown tool, invalid args and a `ToolError` are recorded as
    `is_error=True`. A `BudgetExceededError` from a handler propagates. `FakeCall` records
    `system`, `user`, `tool_names`, `output_type` and `max_steps`.
26. Nested call order: a tool handler that calls `run_structured` for a second agent gives
    `calls` = [outer, inner], and the outer call's `tool_results` are filled in.

`tests/unit/test_layering.py`
27. AST-scan every `src/readme_stack/**/*.py` (module-level **and** function-level `import` /
    `from` statements, with relative imports resolved). Every internal import must satisfy the
    §1 table. `ai` may only appear in `infra/llm/adapter.py`, and `tree_sitter*` only under
    `analysis/parsing/`. The failure message lists `file: imported module (rule)`. It must pass
    on the T1 tree, and every later task keeps it green.

`tests/unit/test_packaging.py`
28. `importlib.metadata.version("readme-stack") == readme_stack.__version__`. The console-script
    entry point `readme-stack` resolves to `readme_stack.cli.app:main`.
29. `ai`, `pydantic`, `tree_sitter` and `tree_sitter_language_pack` are importable (tests are
    exempt from the ban). `tree_sitter_language_pack.get_parser("python")` parses
    `b"def f():\n    pass\n"` with no error node. This catches incompatible pins.
30. `src/readme_stack/main.py` no longer exists, and every layer package from §2 is importable.

`tests/unit/cli/test_version.py`
31. `main(["--version"]) == 0`, and stdout is exactly `f"readme-stack {__version__}\n"`.
32. `subprocess.run([sys.executable, "-m", "readme_stack", "--version"])` gives rc 0 and the same
    output. `main(["--bogus"])` returns 2. Neither raises `SystemExit`. (Do not test `main([])`
    in this file: once the pipeline exists it runs a real run. T1 covers the no-args path in a
    separate `tests/unit/cli/test_app_stub.py`, which T17 deletes.)

`tests/unit/integrations/test_github_action.py`
33. The moved `get_input` tests (strip, default). `set_output` appends `name=value\n` to
    `$GITHUB_OUTPUT`, and prints `[output] name=value` when the variable is unset.

`tests/unit/test_git_fixture.py`
34. `git_repo` is the top level (`git rev-parse --show-toplevel` equals the path), is on branch
    `main`, and has no commits. `git_commit` writes, deletes (`None`) and writes bytes, and
    returns a 40-hex sha. The same commit in two fresh repos gives the same sha (deterministic
    dates). A user-level git config is ignored (set `HOME` to a dir with a `.gitconfig`
    containing `commit.gpgsign=true`, and the commit still succeeds).

Gate: `uv sync --locked && uv run ruff check && uv run ruff format --check && uv run pytest`
(e2e deselected). Then run `uv run readme-stack --version`.

## 9. Out of scope
- Any stage, agent, tool, prompt or adapter logic. `resolve_mode` (T2),
  `resolve_provider`/`model_flags` (T3), git/sandbox/cache (T4), the index (T5), the manifest
  loader, markers and the atomic writer (T6), the ChangeSet (T7), rendering (T8), impact (T9),
  facts and code models (T10), the adapter (T11), prompts and the agent base (T12),
  `LocalFileSystem` (T14), budget and context (T16), the full CLI (T17).
- Populating `tests/fixtures/repos/` (the integration-test task). `action.yml` and README
  changes (action step). Note that `action.yml` still runs `readme-stack` with no arguments, so
  it exits 1 until the action step. CI no longer runs it.
- Any `ai` usage beyond checking that it imports.

## 10. Open questions
1. **Exact `ai` pin:** take the latest release at implementation time and record it in the PR.
   The adapter task (T11) may bump it on purpose.
2. **OpenAI strict structured outputs** require every property to be required (no defaults).
   LLM-facing models use defaults (`summary: str = ""`, list defaults). The T11 adapter has two
   options: make every property required in the schema it sends and let pydantic fill the
   defaults, or have T1 models drop the defaults. The current choice is to keep the defaults
   and let T11 handle it.
3. **`tree-sitter-language-pack`** (one wheel, all grammars, larger install) vs per-language
   grammar wheels (smaller, a curated list). The pack is chosen because it is simpler, and T10
   and the tools step only need the parser API.
4. **Shared kernel:** `config.py` and `model_flags.py` are top-level pure modules that `cli`,
   `infra` and `workflow` may import, but `core` must not. Confirm this rather than moving
   `Provider` into `core`.
