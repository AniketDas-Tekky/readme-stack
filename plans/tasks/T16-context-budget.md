# T16 — Workflow context and token budget

Parent plan: [`plans/readme-generation.md`](../readme-generation.md) (sections "Pipeline": budget paragraph, "Project
structure": `workflow/context.py`, `workflow/budget.py`; exit code 5). Depends on: T1 (`config.py`
`RunConfig`/`Provider`, `core/errors.py`, `core/ports.py`, core model types). Consumers: T12
(`UsageSink` = `TokenBudget.record`), T15 (tool registry wires `ctx.budget.record` into
`subagent_tool` and uses `guard_tool`), T17/T18 (pipeline and stages build and read `RunContext`
and `PreflightResult`), and the CLI output task (prints `format_usage`).

## 1. Goal
- `workflow/context.py`: `RunContext` is a **frozen** bundle of run-wide services: the frozen
  `RunConfig`, the resolved provider/model (no API key), the injected ports (`LLM`, `Git`,
  `FileSystem`, `SummaryCache`), the `TokenBudget`, and a logger. Run-scoped data produced by
  preflight lives in a separate frozen `PreflightResult`, which stages receive as an explicit
  argument. There is no mutable "RunState" bag.
- `workflow/budget.py`: `TokenBudget` accumulates `Usage` from every LLM call, including concurrent
  asyncio calls, with a per-(stage, agent) breakdown. It raises `BudgetExceededError` (exit 5) once
  the `--max-tokens` total is exceeded, and refuses to *start* a call when nothing is left. It
  produces an immutable `UsageReport` plus a plain-text rendering for the usage summary.
- The cost estimate is **deferred** (§7).
- Stdlib + pydantic only. No `ai` import, no I/O, and no imports from `agents/`, `infra/`, or
  `tools/`. So T16 depends on T1 only.

### Design decision: frozen context + explicit stage outputs
Options considered: (a) one mutable context whose fields are filled as stages run; (b) a frozen
context plus a mutable `RunState`; (c) a frozen context plus frozen stage outputs passed explicitly.
**Chosen: (c).**
- Ordering is enforced by types. A stage that needs the `FileIndex` takes a `PreflightResult`
  parameter, so it cannot be called before preflight. With (a) or (b), every field is
  `X | None` and every consumer needs a None check or an assert.
- Concurrency safety. Page writers run in parallel, so shared mutable state would be a hazard.
  With (c) the only mutable object reachable from the context is the budget, and it has a narrow,
  lock-protected API.
- Testability. Stage tests build a `PreflightResult` literal directly, without running preflight.
- Later stage outputs (`ImpactReport`, `RepoModel`, `DocPlan`, drafts) are return values that
  `pipeline.py` passes on. They are **not** stored on the context. This matches the parent plan's
  "Python-driven, deterministic control".
- "Frozen" is shallow. The ports and the budget are service objects with their own internal state.
  Freezing the context stops fields from being *re-bound* mid-run, not those services from working.

## 2. Files
Create:
| File | Content |
|---|---|
| `src/readme_stack/workflow/__init__.py` | empty (create only if T1 has not) |
| `src/readme_stack/workflow/budget.py` | `TokenBudget`, `UsageRow`, `UsageReport`, `format_usage`, `UNATTRIBUTED_STAGE` |
| `src/readme_stack/workflow/context.py` | `RunContext`, `PreflightResult` |
| `tests/unit/workflow/test_budget.py` | §6 A–D |
| `tests/unit/workflow/test_context.py` | §6 E |

Do not modify `core/*` or `config.py`. Both are owned by T1. If a T1 name differs from §5, adapt
the import and note the difference in the PR description.

## 3. Public interface: exact signatures

### 3.1 `readme_stack/workflow/budget.py`
```python
from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from readme_stack.core.errors import BudgetExceededError
from readme_stack.core.ports import ToolSpec, Usage

UNATTRIBUTED_STAGE: Final[str] = "-"
_current_stage: contextvars.ContextVar[str] = contextvars.ContextVar(
    "readme_stack_stage", default=UNATTRIBUTED_STAGE
)


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageRow:
    stage: str
    agent: str
    calls: int              # number of record() invocations (one per run_agent that hit the LLM)
    usage: Usage            # summed


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageReport:
    rows: tuple[UsageRow, ...]   # first-seen order of (stage, agent)
    total: Usage
    calls: int
    max_tokens: int | None       # None = unlimited
    exceeded: bool

    @property
    def total_tokens(self) -> int: ...          # total.total_tokens
    @property
    def remaining(self) -> int | None: ...      # max(0, max_tokens - total_tokens); None if unlimited


class TokenBudget:
    def __init__(self, max_tokens: int | None, *, logger: logging.Logger | None = None) -> None:
        """max_tokens: positive int, or None for unlimited. ValueError if <= 0."""

    @property
    def max_tokens(self) -> int | None: ...
    @property
    def spent(self) -> int: ...                 # total_tokens so far
    @property
    def remaining(self) -> int | None: ...      # max(0, max - spent); None if unlimited
    @property
    def exceeded(self) -> bool: ...             # sticky: True once spent > max_tokens

    def record(self, agent: str, usage: Usage) -> None:
        """UsageSink (T12). Always accumulates; then raises BudgetExceededError if over."""

    def check(self, agent: str) -> None:
        """Pre-call guard. Raises BudgetExceededError if remaining <= 0 (or already exceeded)."""

    @contextlib.contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Attribute calls made inside (and in tasks created inside) to stage `name`."""

    def guard_tool(self, tool: ToolSpec) -> ToolSpec:
        """Copy of `tool` whose handler calls check(tool.name) before delegating."""

    def report(self) -> UsageReport: ...        # consistent snapshot, taken under the lock


def format_usage(report: UsageReport) -> str:
    """Plain-text summary table (§4.7). No trailing newline. Pure."""
```

### 3.2 `readme_stack/workflow/context.py`
```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from readme_stack.config import Provider, RunConfig
from readme_stack.core.models.docs_state import DocsState
from readme_stack.core.models.repo import FileIndex, ProjectFacts
from readme_stack.core.modes import ModeDecision
from readme_stack.core.ports import LLM, FileSystem, Git, RevRange, SummaryCache
from readme_stack.workflow.budget import TokenBudget

DEFAULT_LOGGER_NAME = "readme_stack"


@dataclass(frozen=True, slots=True, kw_only=True)
class RunContext:
    config: RunConfig
    provider: Provider                   # resolved (T3); the API key is NOT kept here
    model: str                           # resolved model id, without the "provider:" prefix
    llm: LLM
    git: Git
    fs: FileSystem
    cache: SummaryCache
    budget: TokenBudget
    logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger(DEFAULT_LOGGER_NAME)
    )

    @property
    def repo_root(self) -> Path: ...     # self.git.root

    @classmethod
    def create(
        cls,
        config: RunConfig,
        *,
        provider: Provider,
        model: str,
        llm: LLM,
        git: Git,
        fs: FileSystem,
        cache: SummaryCache,
        logger: logging.Logger | None = None,
    ) -> RunContext:
        """Builds TokenBudget(config.max_tokens, logger=...) and the context."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PreflightResult:
    head_sha: str | None                 # None on an unborn HEAD
    docs_state: DocsState                # T6
    decision: ModeDecision               # T2 (mode, reason, flags)
    diff_range: RevRange | None          # UPDATE / DIFF_UPDATE range; None otherwise
    file_index: FileIndex                # T5
    tree_summary: str                    # T5 render_tree output, seeded to the Explorer
    facts: ProjectFacts                  # facts task; T1 type
```
`PreflightResult` is only the container. T18 (preflight) fills it. Field additions later are
additive and keyword-only, so existing tests keep working.

## 4. Detailed behavior and edge cases

### 4.1 What is counted
- The unit is `Usage.total_tokens` (= `input_tokens + output_tokens`). Cached input is part of
  `input_tokens`, so it counts in full. The flag is a token cap, not a cost cap, and counting
  cached tokens keeps the rule provider-neutral and predictable. `cache_read_tokens` is still
  summed and shown in the report.
- `record` validates its input. A negative field raises `ValueError` (adapter bug). An empty
  `agent` string raises `ValueError`. `Usage()` (all zeros) is accepted and counts as a call.
  T12 never sends that, but FakeLLM might.
- "Exceeded" means `spent > max_tokens`, strictly. Spending exactly the limit is allowed. This
  matches the wording "once the total is exceeded".

### 4.2 `record(agent, usage)` algorithm
1. Validate (§4.1).
2. Take the lock. Add `usage` to the total, and to the row keyed `(_current_stage.get(), agent)`,
   creating the row on first sight with `calls += 1`. Read `spent`. If
   `max_tokens is not None and spent > max_tokens`, set `_exceeded = True`. Release the lock.
3. Log at DEBUG: `usage stage=%s agent=%s in=%d out=%d cached=%d req=%d spent=%d/%s`.
4. If exceeded, raise `BudgetExceededError(msg)` with
   `msg = f"token budget exceeded: {spent:,} tokens used, limit {max:,} (--max-tokens); "
   "nothing was written"`. The first time it trips, also log it once at WARNING.
- The call is **always accumulated before raising**. Later calls that finish (siblings that were
  already in flight) are also accumulated, and each raises again. The summary is therefore as
  complete as the program can know. `exceeded` stays sticky.
- `record` never awaits, so it is atomic with respect to the event loop. The `threading.Lock`
  additionally makes it safe if an adapter ever reports from a worker thread (for example, a sync
  SDK run via `asyncio.to_thread`). The lock is never held across an `await`, so it cannot
  deadlock the loop.

### 4.3 Pre-call check (`check`) — decision: check before AND after
- `check(agent)` raises `BudgetExceededError` if `exceeded` is true, or if
  `max_tokens is not None and spent >= max_tokens` (remaining ≤ 0). Message:
  `f"token budget exhausted before starting {agent!r}: {spent:,} of {max:,} tokens used "
  "(--max-tokens); nothing was written"`. It does not record anything.
- Why also before: checking only after calls means that when one page writer trips the budget,
  every page writer queued on the semaphore (and every subagent the Explorer launches later) still
  *starts* a call and burns tokens before learning the run is dead. The check is O(1) and cannot
  predict the next call's cost, so it does not replace the after-call check. It only stops
  starting work that is certain to be wasted. At `spent == max` a new call would necessarily
  exceed, hence `>=`.
- Remaining overshoot is bounded by (calls already in flight) × (cost of one call). The docs call
  `--max-tokens` a limit that aborts the run, not a hard ceiling on spend.
- Who calls it. `run_agent` (T12) has no pre-call hook, so the contract for stages (T17) is:
  ```python
  ctx.budget.check(spec.name)
  out = await run_agent(spec, llm=ctx.llm, tools=tools, user=user,
                        on_usage=ctx.budget.record, variables=variables)
  ```
  T17 wraps this in one helper (`workflow/stages/_agent.py` or similar). It is not in T16, so that
  T16 does not depend on T12.
- Subagents. `explore_component` calls happen inside the adapter's tool loop, so T15 wraps the
  subagent tool: `budget.guard_tool(subagent_tool(...))`. `guard_tool` returns
  `dataclasses.replace(tool, handler=guarded)`, where
  `async def guarded(args): self.check(tool.name); return await tool.handler(args)`. Name, schema
  and `is_subagent` are unchanged. `BudgetExceededError` is not a `ToolError`, so per the T12/T1
  port semantics it propagates out of the outer `run_structured` and aborts the Explorer. It is
  not fed back to the model.

### 4.4 Stage attribution under concurrency
- The `UsageSink` signature is `(agent_name, usage)` (T12), so the stage comes from a
  `ContextVar`. `with ctx.budget.stage("write_pages"):` sets it and resets it via the token on
  exit, including on exceptions.
- asyncio tasks copy the current context **when they are created**. Tasks created inside the
  `with` block (TaskGroup children, `gather`) are attributed correctly, even if they finish after
  the block exits. Nested `stage()` blocks override and then restore. Calls outside any stage go
  to `UNATTRIBUTED_STAGE`.
- `stage(name)` validates that `name` is non-empty (`ValueError`). Stage names are plain strings
  chosen by T17. The expected set is `impact, explore, outline, write_pages, write_readme,
  assemble`. Assemble's link-repair round may call an LLM.
- The context manager is on the budget instance for discoverability. The `ContextVar` is module
  level, so two budgets in one process (tests) share the *current stage name*, which is harmless.

### 4.5 In-flight calls when the budget trips (contract for T17)
- **Cancel the siblings.** Exit 5 writes nothing, so every further token is wasted. T17 runs
  parallel page writers in an `asyncio.TaskGroup`. When one task raises `BudgetExceededError`,
  the TaskGroup cancels the others, and together with `check()` no new calls start.
- A cancelled call cannot report usage. The adapter's partial `Usage` is lost (the same issue as
  T12 §8 Q3). When `report().exceeded` is true, `format_usage` therefore appends the note
  `note: totals exclude calls cancelled after the budget was exceeded`.
- The TaskGroup raises a `BaseExceptionGroup`. Rule for T17's `pipeline.py`: if an exception
  escapes the LLM stages **and `ctx.budget.exceeded` is true**, raise a single
  `BudgetExceededError` (exit 5) chained from the group. The budget error takes precedence over
  concurrent `LLMError`s, because the actionable fix is raising `--max-tokens`, and those errors
  are usually cancellation fallout anyway. T16 gives the check: `budget.exceeded`.
- Commit never runs after a budget error. The parent plan requires "exceeding → exit 5 before
  Commit", and T17 owns that ordering.

### 4.6 Failed calls
- T12 already reports the usage of `LLMError` paths when `requests > 0`. The budget treats those
  exactly like successful calls: they cost real tokens. The sink cannot tell success from
  failure, and there is no separate "failed" column. The error itself is logged by the stage.
- Precedence when a failed call also trips the budget: T12 raises `BudgetExceededError` from the
  `LLMError`, so exit 5 wins. That is consistent with §4.5.
- A `check()` rejection records nothing, and nothing is added to `calls`.

### 4.7 Report and format
- `report()` copies rows and totals under the lock, into immutable dataclasses. Rows keep
  first-seen order, which follows pipeline order for a sequential pipeline and is stable for tests.
- `format_usage(report)` (numbers use `,` thousands separators, columns are right-aligned, and
  widths come from the data):
  ```
  Token usage: 1,234,567 of 2,000,000 (61.7%) in 9 agent calls, 61 requests
    stage        agent               calls  requests      input     cached   output      total
    explore      explorer                1        14    412,300    120,000   18,200    430,500
    explore      component_explorer      3        22    301,000          0   12,000    313,000
    write_pages  page_writer             4        20    410,000     80,000   40,000    450,000
    ...
    total                                9        61  1,150,000    200,000   84,567  1,234,567
  ```
  - With an unlimited budget, the header reads `Token usage: 1,234,567 in 9 agent calls, 61
    requests` (no "of" and no percentage).
  - When exceeded, the header ends with ` — LIMIT EXCEEDED`, and the §4.5 note is the last line.
  - With no calls at all, the output is the single line `Token usage: 0 (no agent calls)`.
    Examples are a no-op update and an empty-range diff.
- The CLI task decides where to print it: stderr at INFO, emitted on exit 0, 1 and 5 whenever
  `calls > 0`. The report exists even when the run failed, because the context outlives the
  pipeline exception.

### 4.8 `RunContext`
- Built once per run by the pipeline entry (T17) after provider resolution and port
  construction. It is never rebuilt. Tests construct it directly with FakeLLM and fakes.
- It does not store the API key. The adapter (T11) receives the key at construction and
  `ResolvedProvider` stays in infra. The context keeps only `provider` and `model`, for logging
  and cache keys. This keeps `workflow` free of `infra` types. The repo root comes from `git.root`.
- Semaphores are **not** on the context. A run-wide `asyncio.Semaphore(concurrency)` shared by
  page writers and subagents can deadlock: an outer agent holds a slot while its subagents wait
  for one. Stages own their own semaphores, sized from `config.concurrency`.
- `create` passes `logger` through to the budget, so budget messages go to the run's logger.
  Otherwise the budget uses `logging.getLogger(__name__)`.

## 5. Requires from T1
- `config.py`:
  - `class Provider(StrEnum)` (T3 §5).
  - `RunConfig`: frozen (a frozen dataclass or pydantic `frozen=True`) with at least
    `max_tokens: int` (default 2_000_000, validated `>= 1` by the CLI; exit 2 otherwise),
    `concurrency: int`, `max_pages: int`, `docs_dir`, `diff_range: str | None`, `force`,
    `recreate`, `dry_run`, `provider: Provider | None`, `model: str | None`. T16 reads only
    `max_tokens`. The rest are stored opaquely.
- `core/errors.py`:
  - `ExitCode(IntEnum)` with `BUDGET_EXCEEDED = 5`. T2 wrote `BUDGET`, while T3 and T12 wrote
    `BUDGET_EXCEEDED`. T16 only uses the class below.
  - `class BudgetExceededError(ReadmeStackError)` with `exit_code = ExitCode.BUDGET_EXCEEDED`,
    constructed as `BudgetExceededError(message: str)`. It carries no extra fields, because the
    numbers live in the budget.
- `core/ports.py` (as in T12 §5.1 and T4 §5):
  - `Usage` (frozen pydantic, fields `input_tokens`, `output_tokens`, `cache_read_tokens`,
    `requests`, property `total_tokens`, `__add__`).
  - `ToolSpec` (frozen dataclass with `handler`, so `dataclasses.replace` works).
  - Protocols `LLM`, `Git` (with `root: Path`), `FileSystem`, `SummaryCache`.
  - `RevRange`. It may live in `core/models/impact.py`; T16 imports it from wherever T1 puts it.
- Model types (import only):
  - `FileIndex` and `ProjectFacts` from `core/models/repo.py` (T1/T5).
  - `DocsState` from `core/models/docs_state.py` (T6).
  - `ModeDecision` from `core/modes.py` (T2).
  - If T6/T2 have not landed, T1's stubs suffice. Only the names are needed.

## 6. Test plan
Helpers: `U(i=0, o=0, c=0, r=1) -> Usage`. Async tests use `asyncio.run(...)` inside sync tests,
so there is no pytest-asyncio dependency.

**A. Accumulation (`test_budget.py`)**
1. A new budget has `spent == 0`, `remaining == max`, `exceeded is False`, and
   `report().calls == 0`.
2. Three `record` calls (two with the same agent) give total fields equal to the field-wise sums,
   and `spent == sum(total_tokens)`. The same agent in the same stage produces one row with
   `calls == 2`.
3. `cache_read_tokens` is summed in the report but is not added on top of `input_tokens` in
   `spent`.
4. Invalid input raises `ValueError`: an empty agent name, and a negative field (use
   `Usage.model_construct` to bypass validation).
5. `TokenBudget(0)` and `TokenBudget(-1)` raise `ValueError`. `TokenBudget(None)` is unlimited:
   huge usage never raises, and `remaining is None`.

**B. Limit semantics**
6. `max=100`: recording exactly 100 does not raise, and `exceeded` is False. A following
   `check("x")` raises `BudgetExceededError` (remaining 0).
7. `max=100`: recording 60 and then 50 makes the second call raise. The usage **is** accumulated
   (`spent == 110`), `exceeded` is True, `exit_code == 5`, and the message contains `110`, `100`
   and `--max-tokens`.
8. After it is exceeded, another `record` still accumulates and raises again. `check` raises.
9. `check` below the limit returns None and records nothing (`report().calls` is unchanged).
10. The WARNING is logged exactly once across repeated trips (`caplog`).

**C. Concurrency and stage attribution**
11. 50 tasks in an `asyncio.TaskGroup`, each doing `await asyncio.sleep(0)` and then
    `record("page_writer", U(i=10, o=1))`, give `spent == 550` and one row with `calls == 50`.
12. Threads: 8 `threading.Thread`s × 1000 records give an exact total. This exercises the lock.
13. `with budget.stage("explore"): record("explorer", ...)`, then outside the block
    `record("x", ...)`. The rows are `("explore", "explorer")` and `("-", "x")`.
14. Tasks created inside `with budget.stage("write_pages")` that record **after** the block has
    exited (released through an `asyncio.Event`) are still attributed to `write_pages`.
15. Nested stages restore the outer stage. The stage is reset when the body raises.
16. Budget trip under TaskGroup: `max=100`. Task A records 150 and raises. Task B awaits an event
    that is never set, and has a `finally` that sets a flag. The TaskGroup raises an
    `ExceptionGroup` containing `BudgetExceededError`, B is cancelled (the flag is set), and
    `budget.exceeded` is True. This documents the §4.5 contract.
17. Queued work is refused: `max=100`, `Semaphore(1)`, three tasks, each running
    `async with sem: budget.check(name); budget.record(name, U(i=60))`. The first succeeds, the
    second trips on record, and the third is rejected by `check`. `report().calls == 2`.

**D. `guard_tool`, report, format**
18. `guard_tool` returns a `ToolSpec` with the same name, parameters, description and
    `is_subagent`, and a different handler. Below the limit, the inner handler is awaited and its
    result returned. After the budget is exhausted, it raises `BudgetExceededError` and the inner
    handler is **not** called (spy).
19. The row order in `report()` is first-seen order across stages. The report is immutable
    (`FrozenInstanceError`), and a later `record` does not change an earlier report.
20. `format_usage` golden strings, compared exactly:
    - limited, not exceeded (header with a percentage)
    - unlimited (no percentage)
    - exceeded (the `LIMIT EXCEEDED` suffix and the note line)
    - no calls (`Token usage: 0 (no agent calls)`)
21. `format_usage` output has no trailing newline, and every line is ≤ 100 chars for agent names
    ≤ 20 chars.

**E. Context (`test_context.py`)**
22. `RunContext.create(config, ...)` with fakes (FakeLLM, a minimal fake Git with `root`, stub fs
    and cache) gives `ctx.budget.max_tokens == config.max_tokens`, `ctx.repo_root == git.root`,
    and the default logger name `readme_stack`.
23. `RunContext` and `PreflightResult` are frozen: assigning a field raises
    `FrozenInstanceError`. Both are keyword-only: positional construction raises `TypeError`.
24. A custom logger passed to `create` is used by the budget's WARNING (`caplog` with that logger
    name).
25. No API key is stored: `RunContext` has no field whose name contains `key` (introspect
    `dataclasses.fields`).
26. The import-layering guard: `readme_stack.workflow.budget` and `readme_stack.workflow.context`
    do not import `ai`, `readme_stack.infra`, `readme_stack.agents` or `readme_stack.tools`.
    Check this in a subprocess (`python -c "import ...; import sys; print(...)"` over
    `sys.modules`).

Run: `uv run ruff check && uv run ruff format --check && uv run pytest tests/unit/workflow`.

## 7. Out of scope / deferred
- **Cost estimate: deferred.** A price table needs per-model input, output and cache-read prices,
  which change often. The OpenAI default model id is not even confirmed yet (parent plan), and a
  wrong dollar figure is worse than none. `UsageReport` already keeps the input, cached and output
  split per row, so a later `workflow/pricing.py` can compute an estimate from the report without
  changing the budget. The later work is a `pricing: Mapping[str, Price]` table plus an
  `estimate_cost(report, model)` function, shown as "≈ $X (estimate)".
- The `call_agent` helper that combines `check` and `run_agent` (T17), and the wiring of
  `guard_tool` and `subagent_tool` (T15).
- `pipeline.py`, the stages, the TaskGroup and exception-group unwrapping, and the semaphores
  (T17/T18). The CLI printing of `format_usage` and the `--max-tokens` validation (CLI task).
- Per-stage or per-agent sub-budgets, and a pre-call estimate of the next call's size (for
  example, refusing a call whose prompt alone exceeds the remaining budget).
- A JSON usage output for the GitHub Action (`set_output`) (action step).
- Recording partial usage of cancelled calls (T11/T12 port change; see §8).

## 8. Open questions
1. Should `--max-tokens` count cached input tokens at full weight (proposed, simple and
   provider-neutral), at a discount, or not at all? Counting them fully makes long agent loops
   with prompt caching hit the cap sooner than their cost suggests.
2. Should exit 5 take precedence over a concurrent `LLMError` when both occur in one TaskGroup
   (proposed: yes, whenever `budget.exceeded`)?
3. Should `--max-tokens 0` mean unlimited at the CLI level (maps to `TokenBudget(None)`) or be a
   usage error (proposed: usage error; `None` is for tests and library use only)?
4. Should the adapter attach partial usage to `CancelledError` and other non-`LLMError` exceptions
   (T12 §8 Q3), so the summary after a budget trip is exact? Proposed: accept the gap and print
   the note.
5. `ExitCode` member name: `BUDGET_EXCEEDED` (T3, T12, and this plan) or `BUDGET` (T2)? T1 must
   pick one.
6. Should a warning be emitted when usage crosses a threshold (for example 80%) so long runs give
   early notice? Not included, to keep the scope small.
