# T12 — Prompt loader and agent base

Parent plan: [`plans/readme-generation.md`](../readme-generation.md) (sections "Agents", "Project structure": `prompts/`,
`agents/base.py`, `core/ports.py`, `infra/llm/`). Depends on: T1 (core errors, `core.ports.LLM`,
`Usage`, `ToolSpec`, `FakeLLM`). Consumers: T11 (`infra/llm/adapter.py` implements the port
defined in §5), T15 (tool registry binds tool names to `ToolSpec`s), T16 (workflow budget is the
usage sink), and the per-agent module tasks (`agents/explorer.py` and the others).

## 1. Goal
- Store prompts as package data files and give them a small loader. The loader reads
  `prompts/<name>.md` via `importlib.resources`, caches it, and substitutes `{{ var }}`
  placeholders strictly. A missing prompt, a missing variable, or an unused variable raises.
- Add one placeholder prompt per agent. Real prompt text comes in a later step.
- Add `agents/base.py`:
  - `AgentSpec` is a frozen declaration of an agent: name, prompt name, tool names,
    output type, and step cap.
  - `run_agent` renders the system prompt, builds the user message, resolves tool names to
    neutral `ToolSpec`s, calls `core.ports.LLM.run_structured`, reports `Usage` to a callback,
    and returns the typed output.
  - `subagent_tool` wraps an `AgentSpec` as a `ToolSpec`. This is the mechanism behind
    `explore_component`.
- No `ai` import, no network, no file I/O except reading package resources. Stdlib + pydantic.

## 2. Files
Create:
| File | Content |
|---|---|
| `src/readme_stack/prompts/__init__.py` | loader, renderer, errors (§3.1) |
| `src/readme_stack/prompts/explorer.md` | placeholder |
| `src/readme_stack/prompts/component_explorer.md` | placeholder |
| `src/readme_stack/prompts/outliner.md` | placeholder |
| `src/readme_stack/prompts/page_writer.md` | placeholder |
| `src/readme_stack/prompts/readme_writer.md` | placeholder |
| `src/readme_stack/prompts/impact_analyst.md` | placeholder |
| `src/readme_stack/agents/__init__.py` | empty (create only if T1 has not) |
| `src/readme_stack/agents/base.py` | `AgentSpec`, `run_agent`, `subagent_tool`, `build_user_message`, errors (§3.2) |
| `tests/unit/prompts/test_prompts.py` | §6 A |
| `tests/unit/agents/test_base.py` | §6 B, C |

Do not modify `core/*` or `infra/llm/fake.py`. Both are owned by T1. If a T1 name or signature
differs from §5, adapt to T1 and note the difference in the PR description. Hatchling includes
non-`.py` files under the package directory in the wheel by default, so `pyproject.toml` needs no
change. The implementer checks this once with
`uv build && unzip -l dist/*.whl | grep prompts/`.

Placeholder prompt body. Use the same shape for every agent, with role-specific text in the
bracketed parts. Placeholders contain **no** `{{ }}` variables.
```markdown
# <Agent title>

<!-- PLACEHOLDER: real prompt written in the prompts step. -->

You are the <role> agent of readme-stack. <One sentence on the job.>
Use only the provided tools. Paths are repo-relative. Return your answer as the structured
output requested; do not add prose outside it.
```

## 3. Public interface: exact signatures

### 3.1 `readme_stack/prompts/__init__.py`
```python
from __future__ import annotations

import functools
import re
from collections.abc import Mapping
from importlib.resources import files
from typing import Final

from readme_stack.core.errors import ReadmeStackError

PROMPT_PACKAGE: Final[str] = "readme_stack.prompts"
PROMPT_SUFFIX: Final[str] = ".md"
_NAME_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_VAR_RE: Final = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")


class PromptError(ReadmeStackError):          # exit_code inherited = 1 (programming/config error)
    """Base for prompt loading/rendering failures."""

class PromptNotFoundError(PromptError):
    def __init__(self, name: str) -> None: ...  # message: f"prompt not found: {name!r}"
    name: str

class PromptRenderError(PromptError):
    def __init__(self, name: str, *, missing: frozenset[str] = frozenset(),
                 unused: frozenset[str] = frozenset(), detail: str | None = None) -> None: ...
    name: str
    missing: frozenset[str]
    unused: frozenset[str]


def list_prompts() -> tuple[str, ...]:
    """Sorted names (without suffix) of every *.md resource in the package."""

@functools.cache
def load_prompt(name: str) -> str:
    """Raw, normalized template text. Raises PromptNotFoundError / PromptRenderError(format)."""

def prompt_variables(name: str) -> frozenset[str]:
    """Variable names referenced by the template (via load_prompt)."""

def render_template(template: str, variables: Mapping[str, str], *, name: str = "<inline>") -> str:
    """Pure substitution; strict on missing and unused. Used by render_prompt and tests."""

def render_prompt(name: str, variables: Mapping[str, str] | None = None) -> str:
    """load_prompt(name) + render_template(..., variables or {})."""
```

### 3.2 `readme_stack/agents/base.py`
```python
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from pydantic import BaseModel

from readme_stack.core.errors import LLMError, ReadmeStackError, ToolError
from readme_stack.core.ports import LLM, ToolSpec, Usage
from readme_stack.prompts import load_prompt, render_prompt

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS: Final[int] = 25
_IDENT_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")

type UsageSink = Callable[[str, Usage], None]
"""Called as sink(agent_name, usage) after every LLM call (success or failure).
May raise (e.g. T16's BudgetExceededError); the exception propagates out of run_agent."""


class AgentError(ReadmeStackError):           # exit 1
    """Agent contract violation at run time (e.g. output of the wrong type)."""

class AgentConfigError(AgentError):           # exit 1
    """Static misconfiguration: bad spec, unknown tool name, recursive subagent."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentSpec[OutT: BaseModel]:
    name: str                          # snake_case, unique per agent, e.g. "explorer"
    prompt: str                        # prompt resource name, e.g. "explorer"
    tools: tuple[str, ...]             # tool NAMES, resolved at run time
    output_type: type[OutT]
    max_steps: int = DEFAULT_MAX_STEPS # tool-loop cap forwarded to the LLM port

    def __post_init__(self) -> None: ...   # validation, see §4.3


def build_user_message(task: str, context: BaseModel | Mapping[str, str] | None = None) -> str:
    """Deterministic user message: task text + optional context block (§4.4)."""


async def run_agent[OutT: BaseModel](
    spec: AgentSpec[OutT],
    *,
    llm: LLM,
    tools: Mapping[str, ToolSpec],
    user: str,
    on_usage: UsageSink,
    variables: Mapping[str, str] | None = None,
) -> OutT: ...


def resolve_tools(spec: AgentSpec[Any], tools: Mapping[str, ToolSpec]) -> tuple[ToolSpec, ...]:
    """Tool names -> ToolSpecs in spec order. AgentConfigError on unknown or mismatched names."""


def subagent_tool[ArgsT: BaseModel, OutT: BaseModel](
    spec: AgentSpec[OutT],
    *,
    tool_name: str,
    description: str,
    args_type: type[ArgsT],
    build_user: Callable[[ArgsT], str],
    llm: LLM,
    tools: Mapping[str, ToolSpec],
    on_usage: UsageSink,
    variables: Mapping[str, str] | None = None,
) -> ToolSpec: ...
```
Python 3.12 PEP 695 generics (`class AgentSpec[OutT: BaseModel]`, `type UsageSink = ...`) are
allowed and satisfy ruff `UP`.

## 4. Detailed behavior and edge cases

### 4.1 Prompt file format and loading
- One file per prompt: `prompts/<name>.md`, UTF-8. The **whole file is the system-prompt
  template**. The user message is built in code (§4.4) because it carries run data.
- **No front matter.** If the first line is exactly `---`, raise
  `PromptRenderError(name, detail="front matter is not supported")`. This keeps a future
  metadata format (model, version) unambiguous and stops YAML from reaching the model silently.
  No YAML dependency.
- HTML comments are **not** stripped. They go to the model. Placeholders use them only as a marker.
- Normalization: decode UTF-8 (a decode failure raises `PromptRenderError(detail=...)`), drop a
  leading BOM, convert `\r\n`/`\r` to `\n`, strip leading and trailing whitespace, and append
  exactly one `\n`. This keeps output stable across editors and OSes.
- Name validation: `name` must match `_NAME_RE`. Anything else raises `PromptNotFoundError`
  without touching resources. Examples: `"../x"`, `"Explorer"`, `"explorer.md"`, `""`.
  This rules out path traversal.
- Lookup: `files(PROMPT_PACKAGE).joinpath(name + PROMPT_SUFFIX)`. If `.is_file()` is false, raise
  `PromptNotFoundError(name)`. Read with `.read_text(encoding="utf-8")`. This works from a wheel,
  an sdist install, or an editable install.
- `load_prompt` is `functools.cache`d. Exceptions are not cached. Tests call
  `load_prompt.cache_clear()` in a fixture when they monkeypatch resources.
- `list_prompts()` iterates `files(PROMPT_PACKAGE).iterdir()`, keeps regular files ending in
  `.md` whose stem matches `_NAME_RE`, and returns them sorted.

### 4.2 Variable substitution
- Syntax: `{{ name }}` with optional inner whitespace. `name` matches `[a-z_][a-z0-9_]*`. It uses
  double braces rather than `string.Template` `$var`, because prompts will contain shell and code
  snippets (`$HOME`, `${VAR}`) and `str.format` (`{}` would clash with JSON examples).
- Anything that does not match the regex is left verbatim. This covers `{{facts:commands}}`
  (the publishing placeholders from the parent plan, which prompts may need to *mention* to
  writers), `{{ Foo }}`, `{ x }`, and `{{}}`. So no escape syntax is needed. A prompt cannot
  contain a literal `{{ lower_ident }}`. That limitation is accepted and documented in the
  module docstring.
- Strictness:
  - Referenced but not supplied: raise `PromptRenderError(missing=...)`.
  - Supplied but not referenced: raise `PromptRenderError(unused=...)`.
  - Both are reported together in one error. The message lists names sorted, e.g.
    `prompt 'explorer': missing variables: repo_name; unused variables: tre`. Values are never
    included because they may contain repo content.
  - Rationale: prompts and agent modules change separately, and a typo should fail in unit
    tests, not produce a worse prompt silently.
- Values must be `str`. Any other type raises `TypeError` (programming error). Callers format
  numbers and lists themselves.
- Single pass via `_VAR_RE.sub`. Substituted values are **not** rescanned, so a README containing
  `{{ x }}` cannot inject variables. The same variable may appear more than once.
- `render_prompt(name, None)` is the same as `{}`, which is correct for all placeholders.

### 4.3 `AgentSpec` validation (`__post_init__`, raises `AgentConfigError`)
- `name` and `prompt` must match `_IDENT_RE`.
- `tools` must be a `tuple` (a list would break hashing and frozen semantics), each item must
  match `_IDENT_RE`, and there must be no duplicates.
- `output_type` must be a class and a subclass of `pydantic.BaseModel`.
- `max_steps` must be `>= 1`. An agent without tools also passes the port's `max_steps`. The
  adapter then does a single structured call.
- The spec does **not** check that the prompt exists. Specs are module-level constants, and
  loading resources at import time is undesirable. The per-agent tasks add a test that
  `load_prompt(spec.prompt)` succeeds. T12 covers that via `list_prompts()` (§6 A1).

### 4.4 User message
`build_user_message(task, context)` is a helper for agent modules. `run_agent` takes a plain
`user: str`, so callers keep full control.
- `task` is stripped. An empty task raises `ValueError`.
- With `context=None`, the result is `task + "\n"`.
- With a `BaseModel` context, append
  `"\n\n## Context\n\n```json\n" + context.model_dump_json(indent=2) + "\n```\n"`.
  Pydantic field order is deterministic.
- With a `Mapping[str, str]` context, append `"\n\n## <key>\n\n<value stripped>"` for each item,
  keys in insertion order, ending with one `"\n"`. Use this for large text blobs such as the tree
  summary or old README, where JSON escaping would waste tokens.

### 4.5 `run_agent` algorithm
1. `system = render_prompt(spec.prompt, variables)`. Prompt errors propagate unchanged.
2. `tool_specs = resolve_tools(spec, tools)`:
   - For each name in `spec.tools`, in order, look it up in `tools`.
   - Collect all missing names and raise one
     `AgentConfigError(f"agent {spec.name!r}: unknown tools: a, b")`.
   - If `tools[n].name != n`, raise `AgentConfigError` (registry bug).
   - Names in the mapping that are not declared by the spec are **ignored**. T15 can pass one
     run-wide registry, and each agent only sees its own declared subset. This is how tool names
     resolve at run time: the spec holds names, the workflow supplies the name-to-`ToolSpec`
     mapping bound to the run's sandbox, git, and index, and nothing is resolved at import time.
3. `output, usage = await llm.run_structured(system=system, user=user, tools=tool_specs,
   output_type=spec.output_type, max_steps=spec.max_steps, agent_name=spec.name)`.
4. On `LLMError as e`:
   - Call `on_usage(spec.name, e.usage)` if `e.usage.requests > 0`, because failed calls still
     cost tokens and count against the budget.
   - Re-raise `e` unchanged. If the sink itself raises (budget exceeded), that exception wins,
     chained `from e`.
   - Other exceptions (such as `BudgetExceededError` raised by a nested subagent's sink, or
     `CancelledError`) propagate untouched, with no usage report. Those usages were already
     reported by the nested call, and the outer adapter's partial usage is lost. That is
     accepted and noted in §8.
5. On success, call `on_usage(spec.name, usage)` **before** validating the output, so tokens are
   always counted.
6. If `not isinstance(output, spec.output_type)`, raise
   `AgentError(f"agent {spec.name!r} returned {type(output).__name__}, expected ...")`. This
   guards against a misbehaving adapter or FakeLLM. No coercion.
7. Log at DEBUG: `agent=%s tools=%d in=%d out=%d requests=%d`. Never log prompt or user text
   at any level (prompts and inputs may contain repo content). Transcript logging is deferred.
8. Return `output`.

`run_agent` does **not** retry and does **not** enforce the budget. See §4.6 and §4.7.

### 4.6 Retries: where they live
| Concern | Owner |
|---|---|
| Transport errors, 429, 5xx, timeouts (backoff, capped attempts) | T11 adapter |
| Invalid structured output (schema/validation failure), with re-prompting that includes the validation error, up to N repairs | T11 adapter |
| Tool loop exceeding `max_steps` | T11 adapter raises `LLMStepLimitError` |
| Semantic validation (DocPlan page cap, globs, links) with one repair round | workflow stages (T16/T17) |
| Usage summing across retries/steps inside one `run_structured` call | T11 adapter (one `Usage` returned) |

The adapter raises `LLMError` subclasses only after it gives up, with the accumulated `usage`
attached. `run_agent` is a thin, single-attempt layer. That keeps retry policy in one place and
makes FakeLLM tests deterministic.

### 4.7 Budget callback
- `on_usage` is required. There is no default, so budget accounting cannot be skipped by
  accident. Tests use a list-appending sink. T16 passes `RunContext.budget.record`, which adds
  to totals and raises `BudgetExceededError` (exit 5) when over the limit.
- It is called exactly once per `run_agent` invocation that reached the LLM: success, or
  `LLMError` with `requests > 0`. It is never called for prompt or tool config errors, since
  those happen before the LLM call.
- The sink is synchronous. Under asyncio concurrency (parallel page writers, parallel
  subagents), calls interleave but never race, because everything runs on a single thread.

### 4.8 Subagent-as-tool (`subagent_tool`) and the `explore_component` contract
Generic mechanism, implemented and tested in T12:
- At construction time (fail fast):
  - Validate `tool_name` with `_IDENT_RE`.
  - Raise `AgentConfigError` if `tool_name in spec.tools` (direct recursion), or if any of the
    subagent's declared tools, as resolved from `tools`, has `is_subagent=True`. Only one level
    of subagents is allowed.
  - Call `resolve_tools(spec, tools)` once so unknown names fail when the registry is built, not
    mid-run.
- Returns `ToolSpec(name=tool_name, description=description, parameters=args_type,
  handler=_handler, is_subagent=True)`, where `_handler(args: ArgsT) -> str`:
  1. `user = build_user(args)`.
  2. `out = await run_agent(spec, llm=llm, tools=tools, user=user, on_usage=on_usage,
     variables=variables)`. The subagent's usage goes to the **same** sink under the
     subagent's own name. The outer adapter's `Usage` covers only the outer model's calls,
     so nothing is double counted.
  3. Return `out.model_dump_json()`, compact JSON, which is the text the outer model sees.
  4. `LLMError` or `AgentError` from the subagent is converted to
     `ToolError(f"{tool_name} failed: {e}")`. The adapter feeds it back to the outer model as
     an error tool result, so the Explorer can continue without that component. Everything
     else propagates and aborts the outer run: `BudgetExceededError`, `AgentConfigError`,
     `PromptError`, `CancelledError`.

`explore_component` contract. This is a declaration only. The ComponentExplorer module task
creates it and T15 wires it.
- Lives in `agents/component_explorer.py`:
  `COMPONENT_EXPLORER = AgentSpec(name="component_explorer", prompt="component_explorer",
  tools=("list_dir", "read_file", "glob", "grep"), output_type=Component)`, where `Component`
  comes from `core/models/repo.py` (T1).
- Args model, also in that module:
  ```python
  class ExploreComponentArgs(BaseModel):
      model_config = ConfigDict(extra="forbid")
      name: str = Field(min_length=1, max_length=100)        # component name chosen by Explorer
      paths: list[str] = Field(min_length=1, max_length=20)   # repo-relative dirs/globs to focus on
      focus: str | None = Field(default=None, max_length=500) # optional question
  ```
  plus `build_explore_component_user(args) -> str`, which uses `build_user_message`.
- Returns `Component` as JSON. On failure it returns a tool error. It is never an exception to
  the Explorer.
- T15 registry: build `base` (the fs/git/code tools), then
  `registry = {**base, "explore_component": subagent_tool(COMPONENT_EXPLORER,
  tool_name="explore_component", description=..., args_type=ExploreComponentArgs,
  build_user=build_explore_component_user, llm=ctx.llm, tools=base, on_usage=ctx.budget.record)}`.
  The subagent gets `base`, which does not include itself.
- Concurrency: whether one step's parallel tool calls run concurrently is the adapter's
  decision (T11). If concurrency is capped, T15 wraps the handler with the run's semaphore.
  T12 does not add a semaphore parameter.

## 5. Requires from T1
All of this lives in `core` (no `ai` import) unless noted. T11's adapter and T1's FakeLLM
implement the same Protocol.

### 5.1 `core/ports.py`
```python
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from pydantic import BaseModel, ConfigDict

class Usage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    input_tokens: int = 0            # includes cached input
    output_tokens: int = 0
    cache_read_tokens: int = 0       # subset of input_tokens; 0 if provider doesn't report
    requests: int = 0                # model API calls made (incl. retries)

    @property
    def total_tokens(self) -> int: return self.input_tokens + self.output_tokens
    def __add__(self, other: "Usage") -> "Usage": ...   # field-wise sum; NotImplemented for non-Usage

type ToolHandler = Callable[[Any], Awaitable[str]]   # receives a validated `parameters` instance

@dataclass(frozen=True, slots=True, kw_only=True)
class ToolSpec:
    name: str                        # [a-z][a-z0-9_]*, unique within one call
    description: str                 # shown to the model
    parameters: type[BaseModel]      # args schema; adapter uses model_json_schema()/model_validate
    handler: ToolHandler             # async; returns text for the model
    is_subagent: bool = False

@runtime_checkable
class LLM(Protocol):
    async def run_structured[T: BaseModel](
        self,
        *,
        system: str,
        user: str,
        tools: Sequence[ToolSpec],
        output_type: type[T],
        max_steps: int,
        agent_name: str,
    ) -> tuple[T, Usage]: ...
```
Port semantics that both implementations must honour:
- Run a tool loop of at most `max_steps` model turns. For each tool call: validate the args with
  `parameters.model_validate`, then `await handler(args)`, and send the returned string as the
  tool result.
- Invalid tool args (`ValidationError`) and `ToolError` raised by a handler become an **error
  tool result** that the model sees. The loop continues.
- **Any other exception from a handler propagates out of `run_structured` unchanged.**
  `BudgetExceededError` and `CancelledError` must not be swallowed.
- Unknown tool name from the model: error tool result.
- Final answer: an instance of `output_type` (validated), plus the summed `Usage` for every
  request made in this call, including retries.
- `agent_name` is for logging, tracing, and FakeLLM routing only. It must not change behaviour
  in the real adapter.
- `tools=()` is allowed (single structured call).
- The model/provider is fixed at adapter construction (T11), not per call.

### 5.2 `core/errors.py`
Existing names from sibling plans: `ReadmeStackError` (with `exit_code: ClassVar[ExitCode]`,
default `FAILURE=1`), `ExitCode` including `BUDGET_EXCEEDED=5`. Add:
```python
class LLMError(ReadmeStackError):                 # exit 1
    def __init__(self, message: str, *, usage: Usage | None = None) -> None: ...
    usage: Usage                                  # accumulated before failure; Usage() if None
class LLMOutputError(LLMError): ...               # structured output invalid after repairs
class LLMStepLimitError(LLMError): ...            # max_steps exhausted
class LLMProviderError(LLMError): ...             # auth/transport failure after retries
class ToolError(ReadmeStackError):                # exit 1; handler -> "error tool result"
    ...                                           # str(err) is shown to the model
class BudgetExceededError(ReadmeStackError):      # exit 5; raised by T16's sink, not by T12
    ...
```
`Usage` is imported by `errors.py` from `ports.py`. Alternatively `Usage` can move to
`core/models/usage.py` to avoid a cycle, which T1 decides. T12 imports `Usage` from
`core.ports` either way, and T1 re-exports it there.

### 5.3 `infra/llm/fake.py` (FakeLLM)
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

@dataclass(frozen=True, slots=True)
class FakeCall:              # recorded per run_structured invocation
    agent_name: str
    system: str
    user: str
    tool_names: tuple[str, ...]
    output_type: type[BaseModel]
    max_steps: int
    tool_results: tuple[tuple[str, str, bool], ...]  # (tool name, result text, is_error)

class FakeLLM:               # satisfies core.ports.LLM (isinstance check passes)
    def __init__(self, script: Mapping[str, Sequence[FakeResponse | BaseModel]] | None = None): ...
    def add(self, agent_name: str, *responses: FakeResponse | BaseModel) -> None: ...
    calls: list[FakeCall]
    async def run_structured(...) -> tuple[T, Usage]: ...   # signature exactly as the Protocol
```
Behaviour:
1. Pop the next response FIFO from the queue for `agent_name`. A bare `BaseModel` is shorthand
   for `FakeResponse(output=model)`. An empty or missing queue raises
   `AssertionError(f"FakeLLM: no scripted response for {agent_name!r}")`.
2. Record a `FakeCall` before running tools and append it to `calls` immediately. This keeps
   call order correct for nested subagent calls: the outer call is recorded first, and its
   `tool_results` are filled in afterwards via a replace in the list.
3. Run `tool_calls` sequentially, applying the §5.1 semantics exactly. An unknown name, a
   `ValidationError`, or a `ToolError` is recorded as an error result. Other exceptions
   propagate.
4. If `output` is an `Exception`, raise it. Tests pass `LLMError(..., usage=...)` to simulate
   failures.
5. If `output` is a dict, return `output_type.model_validate(dict)`. If it is a `BaseModel`,
   return it as is. There is no isinstance check here, so T12 can test its own wrong-type guard
   (§4.5 step 6).
6. Return `(output, response.usage)`.

It does not import `ai`. It lives in `src/` (not `tests/`) so integration tests and other tasks
can use it.

## 6. Test plan
Local test models in `tests/unit/agents/test_base.py`: `class Out(BaseModel): answer: str`,
`class Other(BaseModel): x: int`, `class EchoArgs(BaseModel): text: str`, an `echo` tool
(`ToolSpec` whose handler returns `f"echo:{args.text}"`), a `boom` tool (raises
`ToolError("nope")`), and a sink fixture `usage_log: list[tuple[str, Usage]]`. Tests that need a
prompt monkeypatch `readme_stack.agents.base.render_prompt` or use the real `explorer` placeholder.
Async tests use `asyncio.run(...)` inside sync tests, so no pytest-asyncio dependency.

**A. `tests/unit/prompts/test_prompts.py`**
1. `list_prompts()` equals exactly `("component_explorer", "explorer", "impact_analyst",
   "outliner", "page_writer", "readme_writer")`.
2. Parametrized over `list_prompts()`: `render_prompt(n, {v: "x" for v in prompt_variables(n)})`
   returns a non-empty string ending in exactly one `"\n"`.
3. The six placeholders have `prompt_variables(n) == frozenset()` and
   `render_prompt(n) == load_prompt(n)`.
4. `load_prompt("does_not_exist")` raises `PromptNotFoundError`, `.name` matches, `exit_code == 1`,
   and it is an instance of `ReadmeStackError`.
5. Invalid names raise `PromptNotFoundError`: `""`, `"../explorer"`, `"Explorer"`, `"explorer.md"`,
   `"a/b"`.
6. `load_prompt` returns the identical object on the second call (cache).
7. `render_template("Hi {{ who }} and {{who}}!", {"who": "A"}, name="t") == "Hi A and A!"`.
8. Missing: `render_template("{{ a }} {{ b }}", {"a": "1"})` raises `PromptRenderError` with
   `missing == {"b"}`, and `"b"` is in the message.
9. Unused: `render_template("{{ a }}", {"a": "1", "zz": "2"})` raises with `unused == {"zz"}`.
   Both missing and unused together are reported in one error.
10. Non-matching braces are verbatim: `"{{facts:commands}} {{ Foo }} {x} {{}} $HOME ${X}"`
    renders unchanged with `{}`.
11. No rescan: `render_template("{{ a }}", {"a": "{{ b }}"}) == "{{ b }}"`.
12. A non-str value (`{"a": 1}`) raises `TypeError`.
13. Error messages never contain values: render with a missing var plus a supplied value
    `"SECRET"`, and assert `"SECRET" not in str(err)`.
14. Normalization and front matter via a fake resource: monkeypatch `readme_stack.prompts.files`
    to return a `tmp_path` directory, and clear the cache.
    - `"﻿\r\n# T\r\nbody\r\n\r\n"` loads as `"# T\nbody\n"`.
    - A file starting with `"---\n"` raises `PromptRenderError`.
    - Invalid UTF-8 raises `PromptRenderError`.
    - `list_prompts()` ignores `notes.txt` and `Bad-Name.md`.

**B. `tests/unit/agents/test_base.py`: AgentSpec and helpers**
15. A valid spec constructs. It is frozen: assigning raises `FrozenInstanceError`.
16. Parametrized invalid specs raise `AgentConfigError`: bad name, bad prompt, list instead of
    tuple for `tools`, duplicate tool, bad tool name, `output_type=dict`, `output_type=Out()`
    (an instance), `max_steps=0`.
17. `resolve_tools` returns specs in `spec.tools` order. Extra registry entries are ignored.
18. `resolve_tools` with two unknown names raises one `AgentConfigError` naming both.
    A mismatched `ToolSpec.name` also raises `AgentConfigError`.
19. `build_user_message`:
    - Task only gives `"do it\n"`.
    - A `BaseModel` context gives the `## Context` JSON block, and output is byte-stable across
      two calls.
    - A mapping context gives `## key` sections in order.
    - An empty task raises `ValueError`.

**C. `run_agent` and `subagent_tool` via FakeLLM**
20. Happy path: `FakeLLM` scripted `{"explorer": [Out(answer="ok")]}`, with
    spec(name="explorer", prompt="explorer", tools=("echo",)).
    - Returns `Out` with `answer == "ok"`.
    - `usage_log == [("explorer", <scripted usage>)]`.
    - The recorded `FakeCall` has `system == load_prompt("explorer")`, the passed `user`,
      `tool_names == ("echo",)`, `output_type is Out`, and `max_steps == spec.max_steps`.
21. A dict output (`{"answer": "d"}`) is validated to `Out`.
22. Variables are forwarded: monkeypatch `render_prompt` to a spy and assert it was called with
    `(spec.prompt, variables)`.
23. Wrong type: the script returns `Other(x=1)` for an `Out` spec. `AgentError` is raised, **and**
    usage is still reported (one entry).
24. An `LLMError("fail", usage=Usage(input_tokens=7, requests=1))` from FakeLLM is re-raised as
    the same object, and the usage is reported once. With `usage=Usage()` (no requests), nothing
    is reported.
25. A sink that raises `BudgetExceededError`:
    - On success, `BudgetExceededError` propagates.
    - On an `LLMError` path, `BudgetExceededError` propagates with `__cause__` being the
      `LLMError`.
26. An unknown tool raises `AgentConfigError` **before** any LLM call: `FakeLLM.calls` stays
    empty and the sink is not called.
27. A missing prompt raises `PromptNotFoundError` before any LLM call.
28. Tool execution through the port: the response has
    `tool_calls=(FakeToolCall(name="echo", args={"text": "hi"}), FakeToolCall(name="boom", args={}))`.
    Recorded `tool_results` are `("echo", "echo:hi", False)` and the `boom` result has
    `is_error=True`.
29. `subagent_tool` happy path:
    - Inner spec `component_explorer` (tools `("echo",)`, output `Out`) is wrapped as
      `explore_component` (args `EchoArgs`).
    - The outer spec `explorer` has tools `("explore_component",)`.
    - FakeLLM scripts the outer call with a `tool_calls` entry for `explore_component`, and
      scripts the inner call.
    - Assert:
      - The outer result is `Out`.
      - The outer `tool_results[0]` is `("explore_component", Out(...).model_dump_json(), False)`.
      - `usage_log` names are `["component_explorer", "explorer"]`, with the inner reported first
        and each listed once.
      - The inner `FakeCall.user` equals `build_user(args)`.
30. A subagent failure is converted: the inner script raises `LLMError`. The outer call still
    succeeds, the outer `tool_results[0]` is an error containing `"explore_component failed"`,
    and the inner usage is reported.
31. A subagent budget abort propagates: the sink raises `BudgetExceededError` on the
    `"component_explorer"` entry. The outer `run_agent` raises `BudgetExceededError`.
32. Construction-time guards raise `AgentConfigError`:
    - `tool_name` is in `spec.tools`.
    - The inner registry contains an `is_subagent=True` tool that the inner spec declares.
    - The inner spec declares an unknown tool.
    - `tool_name="Bad"`.
33. The returned `ToolSpec` has `is_subagent=True`, `parameters is EchoArgs`, and the given
    description.

Run: `uv run ruff check && uv run ruff format --check && uv run pytest tests/unit/prompts tests/unit/agents`.

## 7. Out of scope / deferred
- Real prompt content, and prompt versioning (for example, feeding a prompt hash into the
  summary-cache key from T4). This comes in the prompts step.
- The concrete agent modules (`explorer.py`, `component_explorer.py`, ...), their `AgentSpec`
  constants, input models, and `ExploreComponentArgs`. Only the contract is fixed here (§4.8).
- Tool implementations and the registry (T15). The `ai` adapter, retries, and step-limit
  enforcement (T11). The budget object and `BudgetExceededError` raising (T16).
- Per-agent model overrides (for example, a cheaper model for ComponentExplorer). That would add
  a `model: str | None` to `AgentSpec` and to the port later.
- Streaming, progress events, and tracing of tool calls to the CLI output.
- Prompt caching hints (provider-specific cache-control). That is adapter territory.
- Front matter and templating features beyond flat `{{ var }}` substitution (conditionals,
  loops, includes).

## 8. Open questions
1. Strict "unused variable" errors: keep them (proposed), or allow extras so the workflow can
   pass one shared variable set to every prompt?
2. Should `run_structured` return a small `LLMResult[T]` (output, usage, and maybe step count or
   transcript) instead of `tuple[T, Usage]`? The tuple is proposed for minimal surface.
3. Partial usage is lost when a non-`LLMError` exception (a nested `BudgetExceededError` or a
   cancellation) aborts an outer `run_structured`. Should the port attach partial usage to every
   exception, for example via an `add_note` or a wrapper? Proposed: accept the loss, because the
   run aborts anyway and exit 5 writes nothing.
4. Location of `Usage`: `core/ports.py` (proposed, re-exported) or `core/models/usage.py`, to
   avoid an `errors.py` to `ports.py` import cycle.
5. Should subagent failures become tool errors (proposed, lets the Explorer degrade gracefully)
   or abort the run?
6. `DEFAULT_MAX_STEPS = 25`: acceptable default, or per-agent values only (Explorer higher,
   ReadmeWriter lower)?
