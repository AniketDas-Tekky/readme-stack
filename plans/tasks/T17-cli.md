# T17 — CLI surface

Parent plan: [`plans/readme-generation.md`](../readme-generation.md) (sections "CLI", "Decisions", "Project structure").
Depends on: T1 (`config.py`, `core/errors.py`, `__version__`, entry point). Uses T3's public
`parse_model_flag` (pure, no I/O). Consumed by: T22 (implements the real `run_pipeline` against
the contract defined here). Reuses message text from T2 (R0a/R0b) and T3 (`M_CONFLICT`,
`M_MODEL_EMPTY`, `M_MODEL_NO_ID`) and the output formats of T7 (`render_summary`, `render_diff`).

## 1. Goal
Build the `readme-stack` command-line interface: argparse spec, flag validation (all usage errors
→ exit 2 **before** any git, network or LLM work), construction of a frozen `RunConfig`, logging
and progress output on stderr, the dry-run diff on stdout, an end-of-run summary, and one
central exception → exit code mapping (`ReadmeStackError.exit_code`, `KeyboardInterrupt` → 130,
anything else → 1). The pipeline itself is an injected async callable, so the CLI can be built
and fully tested before T22 exists. `readme-stack --version` must work with no pipeline and no
API key, because CI's `self-test` job runs it.

### Decision: provider resolution
- **CLI does the flag-level check only.** It validates `--model` syntax and the `--model p:id`
  vs `--provider` conflict (exit 2). It does **not** read API keys and does **not** call
  `resolve_provider`.
- **Preflight (T18) calls `resolve_provider(os.environ, config.provider, config.model)`**, which
  performs the key checks (exit 3) and returns `ResolvedProvider`. That object lives in
  `RunContext`. `RunConfig` keeps the raw flags (as T3 §5 specifies).
- No duplication. The CLI calls `check_provider_flags` from `infra/llm/providers.py`, a small
  helper that runs T3 algorithm steps 1–2 (parse + conflict). `resolve_provider` calls the same
  helper internally, so the rule lives in one place. When Preflight calls it again, the check is
  redundant but harmless. If T3 lands without this helper, T17 adds it (move steps 1–2 into it
  and call it from `resolve_provider`; T3's tests must still pass unchanged). That edit to
  `providers.py` is the only change T17 makes outside `cli/`.
- Layering note: `cli → infra.llm.providers` is a direct import of a pure function (it imports
  only `config` and `core.errors`, and never `ai`). This is acceptable at the composition root.
  See Open question 1.

## 2. Files
| File | Action | Contents |
|---|---|---|
| `src/readme_stack/cli/__init__.py` | create if absent (empty) | |
| `src/readme_stack/cli/app.py` | new | parser, validation, `build_config`, `main` |
| `src/readme_stack/cli/output.py` | new | logging setup, error printing, summary, dry-run output |
| `src/readme_stack/workflow/result.py` | new | `RunResult`, `Outcome`, `UsageLike`, `PipelineRunner` (the contract T22 implements; lives in `workflow` so the pipeline never imports `cli`) |
| `src/readme_stack/workflow/__init__.py` | create if absent (empty) | |
| `src/readme_stack/infra/llm/providers.py` | edit **only if** `check_provider_flags` is missing (see above) | |
| `tests/unit/cli/test_app.py` | new | parser, validation, exit codes, main wiring |
| `tests/unit/cli/test_output.py` | new | logging levels, summary and dry-run formatting |

No changes to `pyproject.toml`, `config.py`, `core/errors.py` or `__main__.py` (all owned by T1).

## 3. Public interface

```python
# src/readme_stack/workflow/result.py
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from readme_stack.config import RunConfig


class Outcome(StrEnum):
    WRITTEN = "written"         # changes applied to disk
    NO_CHANGES = "no_changes"   # no-op: empty diff range, or the ChangeSet is a no-op
    DRY_RUN = "dry_run"         # --dry-run: nothing written, diff returned


class UsageLike(Protocol):
    """Structural view of core.ports.Usage (T12); keeps T17 independent of T12."""
    @property
    def input_tokens(self) -> int: ...
    @property
    def output_tokens(self) -> int: ...
    @property
    def cache_read_tokens(self) -> int: ...
    @property
    def requests(self) -> int: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class RunResult:
    """Returned by the pipeline on success (exit 0). Failures are raised, not returned."""
    outcome: Outcome
    mode: str                                   # core.modes.Mode value (a StrEnum), e.g. "update"
    mode_reason: str = ""                       # ModeDecision.reason
    model: str | None = None                    # ResolvedProvider.describe()-style, no key
                                                # e.g. "anthropic:claude-sonnet-5"; None if no LLM call
    usage_by_agent: Mapping[str, UsageLike] = field(default_factory=dict)  # agent name → summed
    summary: str = ""                           # T7 render_summary(cs); "" if no ChangeSet built
    diff: str = ""                              # T7 render_diff(cs); MUST be "" unless DRY_RUN
    detail: str = ""                            # one-line note, e.g. "empty diff range a..b"


type PipelineRunner = Callable[[RunConfig], Awaitable[RunResult]]
"""Contract for T22 (`readme_stack.workflow.pipeline.run_pipeline`):
- async; called once via asyncio.run() by the CLI with a fully validated RunConfig.
- Reports progress by logging to logging.getLogger(PROGRESS_LOGGER) at INFO (see §4.5); other
  diagnostics go to module loggers under "readme_stack.*". It never prints to stdout/stderr.
- Returns RunResult on success (exit 0). Signals failure only by raising ReadmeStackError
  subclasses (exit code = err.exit_code); anything else is treated as an internal error (exit 1).
- Does not catch KeyboardInterrupt/CancelledError.
- Must not put secrets in RunResult, exception messages or logs."""
```

```python
# src/readme_stack/cli/app.py
from collections.abc import Sequence
import argparse

from readme_stack.config import RunConfig
from readme_stack.workflow.result import PipelineRunner

PROG = "readme-stack"

def build_parser() -> argparse.ArgumentParser: ...
def build_config(args: argparse.Namespace) -> RunConfig: ...   # validation; raises UsageError
def main(
    argv: Sequence[str] | None = None,
    *,
    run_pipeline: PipelineRunner | None = None,   # None → load T22's default lazily
) -> int: ...

# private
class _Parser(argparse.ArgumentParser):          # error() raises _ArgparseError instead of exit
    def error(self, message: str) -> NoReturn: ...
class _ArgparseError(Exception): ...             # carries argparse's message
def _positive_int(value: str) -> int: ...        # argparse type; ArgumentTypeError if < 1
def _validate_docs_dir(value: str) -> PurePosixPath: ...
def _validate_diff(value: str | None, *, recreate: bool) -> str | None: ...
def _validate_repo(value: str) -> Path: ...
def _load_default_runner() -> PipelineRunner: ...
```

```python
# src/readme_stack/cli/output.py
import logging
from typing import TextIO

from readme_stack.config import RunConfig
from readme_stack.workflow.result import RunResult, UsageLike

def configure_logging(verbosity: int, stream: TextIO | None = None) -> None: ...
def print_error(message: str, *, usage: str | None = None, stream: TextIO | None = None) -> None: ...
def print_internal_error(exc: BaseException, *, verbosity: int, stream: TextIO | None = None) -> None: ...
def format_summary(result: RunResult, config: RunConfig, *, elapsed: float, verbosity: int) -> str: ...
def emit_result(result: RunResult, config: RunConfig, *, elapsed: float, verbosity: int,
                out: TextIO | None = None, err: TextIO | None = None) -> None: ...
def format_tokens(n: int) -> str: ...        # 2000000 → "2,000,000"
def format_elapsed(seconds: float) -> str: ...   # 4.2 → "4.2s", 192.0 → "3m12s", 3725 → "1h02m05s"
```
`stream=None`/`out=None`/`err=None` means `sys.stderr`/`sys.stdout`, looked up **at call time** so
pytest's `capsys` works.

### RunConfig fields T17 needs (T1 defines; see §5)
```python
@dataclass(frozen=True, slots=True, kw_only=True)
class RunConfig:
    repo: Path                        # absolute, resolved; existence checked by CLI, top-level by preflight
    diff_range: str | None = None
    provider: Provider | None = None  # raw --provider
    model: str | None = None          # raw --model (stripped), incl. optional "provider:" prefix
    docs_dir: PurePosixPath = PurePosixPath(DEFAULT_DOCS_DIR)   # repo-relative, normalized
    force: bool = False
    recreate: bool = False
    dry_run: bool = False
    max_pages: int = DEFAULT_MAX_PAGES          # 12
    max_tokens: int = DEFAULT_MAX_TOKENS        # 2_000_000
    concurrency: int = DEFAULT_CONCURRENCY      # 4
    verbosity: int = 0                          # 0..2; informational (e.g. agent debug dumps)
```

## 4. Detailed behavior

### 4.1 argparse spec
`_Parser(prog="readme-stack", description=..., formatter_class=argparse.RawDescriptionHelpFormatter,
epilog=EXIT_CODES_EPILOG, allow_abbrev=False)`.

Description: `Generate and maintain architecture documentation (README.md + docs pages) for a git
repository using an LLM (Anthropic or OpenAI).`

| Flag | argparse | Help string |
|---|---|---|
| `repo` | positional, `nargs="?"`, `default="."`, `metavar="REPO"` | `path to the git repository top level (default: current directory)` |
| `--diff` | `metavar="RANGE"`, `dest="diff_range"`, `default=None` | `update docs for the changes in a git range, e.g. main..HEAD or HEAD~1..HEAD (requires existing readme-stack docs)` |
| `--provider` | `choices=[p.value for p in Provider]`, `default=None` | `LLM provider; required only if both ANTHROPIC_API_KEY and OPENAI_API_KEY are set` |
| `--model` | `metavar="ID|PROVIDER:ID"`, `default=None` | `model id, optionally prefixed with the provider (default: claude-sonnet-5 for anthropic, gpt-5 for openai)` (generated from `DEFAULT_MODELS`) |
| `--docs-dir` | `metavar="DIR"`, `default=DEFAULT_DOCS_DIR` | `directory for generated subpages, relative to REPO (default: %(default)s)` |
| `--force` | `store_true` | `overwrite hand-edited generated files, write into a docs dir without a manifest, and replace an untracked or modified foreign README` |
| `--recreate` | `store_true` | `regenerate all docs from scratch even if up-to-date readme-stack docs exist (cannot be combined with --diff)` |
| `--dry-run` | `store_true` | `do everything except write files; print a unified diff to stdout` |
| `--max-pages` | `type=_positive_int`, `metavar="N"`, `default=DEFAULT_MAX_PAGES` | `maximum number of subpages (default: %(default)s)` |
| `--max-tokens` | `type=_positive_int`, `metavar="N"`, `default=DEFAULT_MAX_TOKENS` | `abort before writing anything if total LLM tokens exceed N (default: %(default)s)` |
| `--concurrency` | `type=_positive_int`, `metavar="N"`, `default=DEFAULT_CONCURRENCY` | `number of pages written in parallel (default: %(default)s)` |
| `-v`, `--verbose` | `action="count"`, `default=0` | `more output: -v for details, -vv for debug logs and tracebacks` |
| `--version` | `action="version"`, `version=f"%(prog)s {__version__}"` | `show version and exit` |

`EXIT_CODES_EPILOG`:
```
exit codes:
  0  success, nothing to do, or dry run
  1  runtime or LLM failure
  2  invalid usage
  3  environment problem (API keys, git, bad range, newer docs format)
  4  repository state blocks the run (use --force or see message)
  5  token budget exceeded (nothing written)
  130 interrupted
```
`_positive_int`: `int(value.strip())` (so `2_000_000` works). A `ValueError`, or a value < 1,
raises `argparse.ArgumentTypeError(f"must be an integer >= 1, got {value!r}")`. argparse
formats that as `argument --max-pages: must be an integer >= 1, got '0'`.

`_Parser.error(message)` raises `_ArgparseError(message)` and does not call `sys.exit(2)`.
`--help`/`--version` still go through `parser.exit(0)` and raise `SystemExit(0)`. `main` catches
that and returns the code. Nothing in the CLI lets `SystemExit` escape.

### 4.2 Validation order (first failure wins; all raise `UsageError` → exit 2)
Runs in `build_config`, after argparse succeeds. There is no I/O except step 6.
1. **argparse phase** (unknown flag, missing value, bad `--provider` choice, `--max-pages`,
   `--max-tokens` or `--concurrency` < 1 or non-integer). Handled via `_ArgparseError`.
2. `--diff` + `--recreate` → `--diff and --recreate cannot be combined` (the same text as T2 R0b).
3. `--diff` value: strip it. Empty → `--diff requires a non-empty git range` (the same text as T2 R0a).
   Starts with `-` → `--diff range must not start with '-': {value!r}`. This blocks git option
   injection even though `--diff=-x` gets past argparse. The stripped value is stored. Real range
   validation (exit 3) happens in preflight.
4. `--docs-dir` via `_validate_docs_dir(raw)`, a lexical check with no filesystem access:
   - empty or whitespace only → `--docs-dir must not be empty`
   - contains `\` → `--docs-dir must use '/' separators, got {raw!r}`
   - `PurePosixPath(raw).is_absolute()` or `PureWindowsPath(raw).drive` →
     `--docs-dir must be relative to the repository root, got absolute path {raw!r}`
   - `n = posixpath.normpath(raw)`: `n == "."` →
     `--docs-dir must not be the repository root, got {raw!r}`
   - `n == ".."` or `n.startswith("../")` → `--docs-dir must stay inside the repository, got {raw!r}`
   - first component is `.git` → `--docs-dir must not be inside .git, got {raw!r}`
   - Result: `PurePosixPath(n)` (trailing slash and `a/./b` normalized). Symlink escapes are
     checked by preflight and the sandbox (T4), not here.
5. Provider flags: `check_provider_flags(provider_flag, model_flag)` (T3 helper). It raises
   `UsageError` with T3's `M_MODEL_EMPTY`, `M_MODEL_NO_ID` or `M_CONFLICT`. The stripped raw
   `--model` string goes into `RunConfig.model`.
6. REPO: `Path(raw).expanduser().resolve()` and then `is_dir()`. On failure:
   `REPO {raw!r} does not exist or is not a directory`. Whether it is the git top level is
   checked by preflight (exit 3).

Verbosity is capped at 2 (`-vvv` = `-vv`).

### 4.3 `main` flow
```
start = time.monotonic()
try:
    try:
        args = build_parser().parse_args(argv)          # argv None → sys.argv[1:]
    except _ArgparseError as e: print_error(str(e), usage=parser.format_usage()); return 2
    except SystemExit as e: return int(e.code or 0)     # --help / --version
    verbosity = min(args.verbose, 2)
    configure_logging(verbosity)
    try: config = build_config(args)
    except UsageError as e: print_error(str(e), usage=parser.format_usage()); return 2
    log.debug("config: %r", config)                     # RunConfig has no secrets
    runner = run_pipeline or _load_default_runner()
    result = asyncio.run(runner(config))
    emit_result(result, config, elapsed=time.monotonic() - start, verbosity=verbosity)
    return 0
except KeyboardInterrupt:
    print_error("interrupted"); return 130
except ReadmeStackError as e:
    print_error(str(e)); if verbosity >= 2: print traceback; return int(e.exit_code)
except BrokenPipeError:
    # stdout closed (e.g. `| head`): per Python docs, point stdout at devnull and exit 1
    os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno()); return 1
except Exception as e:
    print_internal_error(e, verbosity=verbosity); return 1
```
(`verbosity` is initialised to 0 before the outer `try`.) `_load_default_runner` imports
`readme_stack.workflow.pipeline` and returns its `run_pipeline`. If that raises
`ModuleNotFoundError` with `e.name == "readme_stack.workflow.pipeline"` (T22 not merged), the loader
raises `ReadmeStackError("the generation pipeline is not available in this build")` (exit 1).
Any other import error propagates as an internal error, so real bugs are not hidden.
`--version`/`--help` never load the pipeline.

### 4.4 Error output (stderr, written directly, not via logging)
- Usage errors: `usage: readme-stack [-h] [--diff RANGE] ...` (argparse's `format_usage()`),
  then `readme-stack: error: {message}`. The layout matches argparse's own.
- `ReadmeStackError`: `readme-stack: error: {message}`. At `-vv`, `traceback.format_exception(e)`
  follows.
- Unexpected exception: `readme-stack: internal error: {type(e).__name__}: {e}`. Below 2 it adds
  `(run with -vv for a traceback)`. At 2 it prints the full traceback instead of the hint.
- Interrupt: `readme-stack: interrupted`.

### 4.5 Logging (`configure_logging`)
- Package logger `readme_stack`. The CLI attaches one `StreamHandler(stream or sys.stderr)`
  marked with an attribute (`_readme_stack_cli = True`). Any previously attached marked
  handler is removed first, so repeated `main()` calls in tests don't duplicate output.
  `propagate = False`.
- Level by verbosity: 0 → WARNING, 1 → INFO, 2 → DEBUG.
- **Progress logger** `PROGRESS_LOGGER = "readme_stack.progress"` is always set to INFO, so
  progress shows at verbosity 0. The pipeline logs one INFO line per stage start and per
  page/agent completion. Suggested messages for T22 (not enforced by T17):
  `preflight: mode=update (updating from 1a2b3c4..HEAD)`, `impact: 3 pages affected, README unchanged`,
  `explore: scoped to src/readme_stack/cli`, `outline: 7 pages`, `write pages: 2/7 docs/architecture/cli.md`,
  `write readme`, `assemble: 1 broken link dropped`, `commit: dry run`.
- Third-party loggers (`ai`, `httpx`, `anthropic`, `openai`, …) are not touched and are never
  lowered below WARNING, even at `-vv`. This avoids leaking request details and keys.
- Format (`_CliFormatter`):
  - verbosity 0–1: progress records → `==> {msg}`. Other INFO/DEBUG → `    {msg}`. WARNING →
    `warning: {msg}`. ERROR+ → `error: {msg}`.
  - verbosity 2: every record gets the prefix `{HH:MM:SS.mmm} {LEVEL:<7} {logger name}: ` followed
    by the same body (progress lines keep `==> `).
- No color, no `\r` spinners, no TTY detection. Output is line-based so it reads the same in a
  terminal and in CI logs. (`NO_COLOR` is trivially honoured.)

### 4.6 End-of-run output (`emit_result`, success only)
1. If `config.dry_run` and `result.diff`, write `result.diff` to **stdout** exactly as
   returned (T7 guarantees a trailing `\n`). Nothing else ever goes to stdout, so
   `readme-stack --dry-run > docs.patch && git apply docs.patch` works. If `config.dry_run`
   is false, `result.diff` is ignored. A non-empty `result.diff` there only produces a DEBUG
   log line noting the contract violation.
2. Write `format_summary(...)` to **stderr**. At verbosity 0 it is printed too, because it is
   the main feedback of a run.

`format_summary` example (verbosity 0, dry run):
```
--- readme-stack summary ---
mode:     update (updating from 1a2b3c4..HEAD)
model:    anthropic:claude-sonnet-5
result:   dry run, nothing written (diff on stdout)
changes:  modify            README.md
          modify            docs/architecture/cli.md
          create            docs/architecture/output.md
          modify            docs/architecture/.readme-stack.json
          4 unchanged
          1 to create, 3 to modify, 0 to delete, 0 skipped
tokens:   450,510 (412,300 in incl. 120,000 cached, 38,210 out) in 57 requests, 22.5% of 2,000,000 budget
elapsed:  3m12s
```
Rules:
- `mode:` is `result.mode`, with ` ({mode_reason})` appended when that is non-empty.
- `model:` is omitted when `result.model is None`.
- `result:` by outcome. WRITTEN → `docs written`. NO_CHANGES → `no changes, docs are up to date`.
  DRY_RUN → `dry run, nothing written (diff on stdout)`, or `dry run, no changes` when the diff is
  empty. ` ({detail})` is appended when `detail` is set.
- `changes:` is `result.summary` with each line after the first indented by 10 spaces. It is
  omitted when the summary is empty.
- `tokens:` sums `usage_by_agent` values. `requests` is the summed count. The percentage is
  `total / config.max_tokens`, one decimal. With no usage it reads
  `tokens:   0 (no LLM calls)`. `total = input_tokens + output_tokens`.
- At verbosity ≥ 1, a per-agent block follows `tokens:`, sorted by agent name, with columns
  aligned:
  ```
            explorer          180,120 in    9,800 out   14 req
            page_writer       200,000 in   25,410 out   36 req
  ```
- The block ends with `"\n"`. No trailing spaces.

On failure no summary is printed. The error message itself (for example T16's
`BudgetExceededError` text) must contain what the user needs. See Open question 3.

## 5. Requires from T1 (exact types assumed)
- `readme_stack/__init__.py`: `__version__: str` (currently "0.1.0").
- `readme_stack/config.py`:
  - `class Provider(StrEnum): ANTHROPIC = "anthropic"; OPENAI = "openai"` (same as T3).
  - `DEFAULT_MODELS: Final[Mapping[Provider, str]]` (same as T3).
  - `DEFAULT_DOCS_DIR: Final[str] = "docs/architecture"`, `DEFAULT_MAX_PAGES: Final[int] = 12`,
    `DEFAULT_MAX_TOKENS: Final[int] = 2_000_000`, `DEFAULT_CONCURRENCY: Final[int] = 4`.
  - `PROGRESS_LOGGER: Final[str] = "readme_stack.progress"`. It sits in `config` so `workflow`
    can use it without importing `cli`. If T1 doesn't have it, T17 defines it in
    `workflow/result.py` instead.
  - `RunConfig` exactly as in §3 (frozen, slots, kw_only). If T1's field names differ, T17
    adapts `build_config` only. `verbosity` is optional; drop it if T1 omits it.
- `readme_stack/core/errors.py`:
  - `ExitCode(IntEnum)` with `OK=0, FAILURE=1, USAGE=2, ENVIRONMENT=3, REPO_STATE=4, <5>,
    INTERRUPTED=130`. The name for 5 differs between siblings (T2: `BUDGET`, T3/T12:
    `BUDGET_EXCEEDED`). T17 uses only `USAGE`, `FAILURE` and `INTERRUPTED` in code, and literal
    ints in tests.
  - `ReadmeStackError(Exception)`, `exit_code: ClassVar[ExitCode]` (default `FAILURE`),
    `Error(message)` with `str(err) == message`. `UsageError` (2).
  - The exit-3 class is named `EnvironmentConfigError` in T2 and `EnvError` in T3. The CLI maps
    generically through `exit_code` and never names it. Tests import whichever T1 ships.
    `RepoStateError` (4) and `BudgetExceededError` (5, T12 §5.2) are likewise only used in tests.
- `pyproject.toml` entry point `readme-stack = "readme_stack.cli.app:main"`, and
  `__main__.py` doing `raise SystemExit(main())`. Both are T1. If T1 left the entry point on
  `readme_stack.main:main`, flag it in the PR and don't fix it here.
- From T3: `readme_stack.infra.llm.providers.check_provider_flags(provider_flag: Provider | None,
  model_flag: str | None) -> ModelSpec | None`, which raises `UsageError`. It is added by T17 if
  absent (§1).

## 6. Test plan (`tests/unit/cli/`)
Fixtures: `repo` = `tmp_path` (a plain directory, since the CLI never runs git). `spy_runner`, an
async fake that records the `RunConfig` it receives and returns a configurable `RunResult` or
raises a configurable exception. `FakeUsage` is a frozen dataclass that satisfies `UsageLike`.
Every `main` call passes `run_pipeline=spy_runner` and `[str(repo), ...]`. Output is checked
with `capsys`.

**test_app.py: parser and defaults**
1. `main([repo])` → 0. The runner gets `RunConfig` with `repo == repo.resolve()`,
   `diff_range None`, `provider None`, `model None`, `docs_dir == PurePosixPath("docs/architecture")`,
   `force/recreate/dry_run False`, `max_pages 12`, `max_tokens 2_000_000`, `concurrency 4`,
   `verbosity 0`.
2. Every flag set → all fields land. `--provider openai` gives `Provider.OPENAI`,
   `--max-tokens 2_000_000` is accepted, and `--model " openai:gpt-5 "` is stored stripped.
3. REPO omitted, with `monkeypatch.chdir(repo)` → `config.repo == repo.resolve()`.
4. `main(None)` reads `sys.argv` (monkeypatched).
5. `-v` → 1, `-vv` → 2, `-vvv` → 2.
6. `--version` → 0, stdout is `readme-stack 0.1.0\n` (from `__version__`), and the runner is not
   called (the default loader isn't touched either: pass `run_pipeline=None` and assert no import
   of `readme_stack.workflow.pipeline` via a `sys.modules` check).
7. `--help` → 0. Stdout contains every flag, `exit codes:` and `(default: docs/architecture)`.

**test_app.py: usage errors → 2, runner never called, stderr has `usage:` and `readme-stack: error:`**
8. `--diff a..b --recreate` → the T2 R0b message.
9. `--diff ""` and `--diff "  "` → `--diff requires a non-empty git range`.
10. `--diff=-p` → `must not start with '-'`.
11. `--docs-dir` parametrized: `/abs/docs`, `C:/docs`, `.`, `./`, `docs/..`, `..`, `../x`,
    `docs/../../x`, `""`, `"  "`, `docs\\arch`, `.git/docs` → exit 2 with the matching message
    substring.
12. `--docs-dir` accepted and normalized: `docs/`, `./docs//arch/`, `a/./b`, `docs/x/../y`
    → `PurePosixPath("docs")`, `("docs/arch")`, `("a/b")`, `("docs/y")`.
13. `--provider anthropic --model openai:gpt-5` → 2 with T3's `M_CONFLICT` text, and **no API key
    env vars set** (`monkeypatch.delenv`). This proves no key check happens in the CLI.
14. `--model ""`, `--model openai:` → 2 (T3 messages).
15. `--max-pages 0`, `--max-tokens 0`, `--concurrency 0`, `--concurrency -1`, `--max-pages abc`
    → 2, message `argument --max-pages: must be an integer >= 1`.
16. `--provider gemini` → 2 (`invalid choice`). An unknown flag `--bogus` → 2.
17. REPO pointing to a missing path, or to a file → 2, `does not exist or is not a directory`.
18. Order: `--diff a..b --recreate --docs-dir /abs` → the message is the diff/recreate one (step 2
    before step 4). `--max-pages 0 --diff x --recreate` → the argparse message (step 1).
19. No key needed: with no `*_API_KEY` in env, valid flags → runner called, exit 0.

**test_app.py: exception mapping**
20. Parametrized: the runner raises `UsageError("u")` → 2, the exit-3 class → 3,
    `RepoStateError` → 4, `BudgetExceededError` → 5, plain `ReadmeStackError` → 1, and a
    test-local subclass with `exit_code = ExitCode.REPO_STATE` → 4. Stderr is
    `readme-stack: error: {msg}\n` with no `Traceback`, and no summary is printed.
21. The same `RepoStateError` at `-vv` → stderr contains `Traceback`.
22. The runner raises `RuntimeError("boom")` → 1. Stderr contains
    `readme-stack: internal error: RuntimeError: boom` and `run with -vv`, and no `Traceback`.
23. Same as 22 at `-vv` → contains `Traceback` and not the hint.
24. The runner raises `KeyboardInterrupt` inside the coroutine → 130 with `readme-stack: interrupted`.
    Also patch `build_config` to raise `KeyboardInterrupt` → 130.
25. `run_pipeline=None` while `readme_stack.workflow.pipeline` is missing (monkeypatch
    `importlib.import_module`, or insert `sys.modules[...] = None`) → 1 with `pipeline is not
    available`. An `ImportError` for another module name propagates as an internal error (1,
    `internal error`).
26. `main` never raises `SystemExit` for any input in tests 6–25 (it returns ints).
27. `BrokenPipeError` raised while writing stdout (the runner returns a dry-run diff and
    `sys.stdout` is patched to raise) → 1, with no exception escaping. `os.dup2` is monkeypatched.

**test_output.py**
28. `configure_logging(0)`: `getLogger("readme_stack.x").info` is hidden, `.warning` shows as
    `warning: ...`, and `getLogger(PROGRESS_LOGGER).info("explore")` shows as `==> explore`.
29. `configure_logging(1)`: INFO shows as `    msg`. DEBUG is hidden.
30. `configure_logging(2)`: DEBUG shows, the prefix matches the regex
    `^\d\d:\d\d:\d\d\.\d{3} DEBUG   readme_stack\.x: msg$`, and `getLogger("httpx").debug` is
    not shown.
31. Calling `configure_logging` twice leaves exactly one CLI handler (no duplicated lines).
32. `format_summary` golden tests. (a) The dry-run example from §4.6 is byte-exact given
    `FakeUsage` values. (b) NO_CHANGES with no usage gives `tokens:   0 (no LLM calls)` and no
    `changes:` or `model:` lines. (c) WRITTEN at verbosity 1 includes the sorted, aligned
    per-agent block. (d) `detail` is appended. (e) No line has trailing whitespace, and the output
    ends with `\n`.
33. `emit_result` dry run: stdout equals `result.diff` exactly, and stderr has the summary.
    A dry run with an empty diff leaves stdout empty. Non-dry-run with a non-empty `diff` leaves
    stdout empty.
34. End-to-end through `main`: `--dry-run` with the runner returning a diff → stdout is exactly the
    diff, and stderr contains `--- readme-stack summary ---`.
35. `format_tokens(0)=="0"`, `format_tokens(2000000)=="2,000,000"`. `format_elapsed`: 0.4→"0.4s",
    59.9→"59.9s", 60→"1m00s", 192→"3m12s", 3725→"1h02m05s".
36. `print_error(..., usage=...)` layout: the usage line comes first, then `readme-stack: error: msg`.

**providers helper (only if T17 adds `check_provider_flags`)**
37. `tests/unit/infra/test_providers.py` still passes unchanged. Add 3 rows: conflict → UsageError,
    `(None, None)` → None, `(None, "openai:gpt-5")` → `ModelSpec(OPENAI, "gpt-5")`.

Gate: `uv run ruff check && uv run ruff format --check && uv run pytest tests/unit/cli`.

## 7. Out of scope / deferred
- The real pipeline and stages (T16/T18/T22), `resolve_provider` key checks (T18 preflight),
  git top-level and range validation (exit 3, preflight), and the symlink/realpath check of
  `--docs-dir` (preflight/T4).
- `pyproject.toml` entry point, `__main__.py`, removing `main.py`/`tests/test_main.py`, and the CI
  `self-test` change (T1 / scaffolding).
- Color, TTY-aware progress bars, `--quiet`, a JSON output mode, a config file, and env-var
  defaults for flags (for example `READMESTACK_MAX_TOKENS`). The GitHub Action mapping of
  inputs → argv happens in the action step.
- `--no-cache` and cache stats in the summary (T4 defers these too).
- Upper bounds on `--concurrency`/`--max-pages` (no cap. Provider rate limits are the adapter's
  concern).

## 8. Open questions
1. **cli → infra import.** `check_provider_flags` is pure but lives in `infra/llm/providers.py`.
   Accept the composition-root exception (this plan), or move `ModelSpec`/`parse_model_flag`/
   `check_provider_flags` into `core/` (for example `core/models/llm.py`) with `providers.py`
   re-exporting them.
2. **REPO that doesn't exist** is exit 2 here (a bad argument). The parent plan groups "git"
   problems under 3. A directory that exists but isn't the git top level stays 3 (preflight).
3. **Usage on failure.** Should the CLI print the token total when a run fails, especially exit 5?
   That needs `ReadmeStackError` subclasses (`LLMError.usage` exists in T12; `BudgetExceededError`
   would need a `usage` attribute) or a usage sink shared with the CLI. For now the error message
   alone carries it.
4. **Summary on NO_CHANGES at verbosity 0.** Should it be one line (`docs are up to date`)
   instead of the full block? That would make CI logs quieter. This plan prints the full block for
   consistency.
5. **Exit-code class names** (`EnvError` vs `EnvironmentConfigError`, `BUDGET` vs
   `BUDGET_EXCEEDED`) must be reconciled by T1. T17 code is agnostic, and only its tests import
   the names.
