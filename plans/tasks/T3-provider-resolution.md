# T3 — Provider / model resolution

Parent plan: [`plans/readme-generation.md`](../readme-generation.md) (sections "Decisions → Keys", "CLI", "Preflight", `infra/llm/providers.py`).

## 1. Goal
Provide one pure, deterministic function that turns the process environment plus the `--provider`
and `--model` CLI flags into a resolved LLM configuration (provider, model id, API key). The
function raises typed errors that map to the documented exit codes (2 = usage, 3 = environment).
The module must not import `ai`, must do no I/O except reading the mapping it is given, and must
never log or echo API key values. `infra/llm/adapter.py` (T11) uses the result to call
`ai.get_provider(resolved.provider.value, api_key=resolved.api_key)` and
`ai.Model(id=resolved.model, provider=...)`.

## 2. Files
| File | Action |
|---|---|
| `src/readme_stack/infra/__init__.py` | create empty if T1/other tasks have not created it yet |
| `src/readme_stack/infra/llm/__init__.py` | create empty if not present |
| `src/readme_stack/infra/llm/providers.py` | **create**: the whole implementation |
| `tests/unit/infra/test_providers.py` | **create**: table tests |
| `tests/unit/__init__.py`, `tests/unit/infra/__init__.py` | only if the repo's test layout uses packages (follow T1's convention; pytest rootdir discovery works without them) |

No changes to `config.py`, `core/errors.py`, `cli/app.py` (T1 or the CLI task owns those).

## 3. Public interface

```python
# src/readme_stack/infra/llm/providers.py
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from readme_stack.config import DEFAULT_MODELS, Provider
from readme_stack.core.errors import EnvError, UsageError

logger = logging.getLogger(__name__)

API_KEY_ENV_VARS: Final[Mapping[Provider, str]] = {
    Provider.ANTHROPIC: "ANTHROPIC_API_KEY",
    Provider.OPENAI: "OPENAI_API_KEY",
}


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Parsed form of the --model flag."""
    provider: Provider | None   # set only if the value had a recognised "provider:" prefix
    model_id: str               # never empty, stripped


@dataclass(frozen=True, slots=True)
class ResolvedProvider:
    """Resolved LLM configuration consumed by infra/llm/adapter.py.

    `api_key` is excluded from repr/eq so it cannot show up in logs,
    tracebacks, or assertion diffs.
    """
    provider: Provider
    model: str                  # model id without the provider prefix
    api_key_env_var: str        # e.g. "ANTHROPIC_API_KEY" (safe to log)
    api_key: str = field(repr=False, compare=False)  # stripped, non-empty

    def describe(self) -> str:
        """Log-safe one-liner, e.g. "anthropic:claude-sonnet-5 (key from ANTHROPIC_API_KEY)"."""


def parse_model_flag(value: str) -> ModelSpec:
    """Parse `--model ID` or `--model provider:ID`.

    - Strip surrounding whitespace; empty -> UsageError.
    - If the value contains ":" and the text before the first ":" (stripped, compared
      case-insensitively) is a Provider value, that is the provider and the rest (stripped)
      is the id; an empty id -> UsageError.
    - Otherwise the whole stripped value is a bare id with provider=None. Colons are allowed
      in ids, e.g. OpenAI fine-tunes "ft:gpt-4o:org:x:id".
    """


def present_providers(env: Mapping[str, str]) -> frozenset[Provider]:
    """Providers whose API key env var is set to a value that is non-empty after strip()."""


def resolve_provider(
    env: Mapping[str, str],
    provider_flag: Provider | None,
    model_flag: str | None,
) -> ResolvedProvider:
    """Resolve provider, model id and API key.

    `env` is normally `os.environ`; tests pass plain dicts. `provider_flag` is the already
    converted --provider value (CLI uses `type=Provider, choices=list(Provider)`).
    `model_flag` is the raw --model string or None.

    Raises UsageError (exit 2) for bad or conflicting flags, and EnvError (exit 3) for
    missing or ambiguous keys. Usage errors are checked before environment errors.
    Emits one DEBUG log line (describe()) and never logs the key.
    """
```

No Pydantic here. Plain frozen dataclasses are enough, and `field(repr=False)` hides the key.

## 4. Detailed behavior and edge cases

### Algorithm (order matters)
1. **Parse model flag.** If `model_flag is not None`, set `spec = parse_model_flag(model_flag)`, which can raise UsageError. `--model ""` or `"   "` is a usage error, not "unset".
2. **Conflict check.** If `provider_flag` and `spec.provider` are both set and differ, raise UsageError.
3. **Requested provider** = `provider_flag or spec.provider` (either may be None). A `provider:` prefix on `--model` counts as an explicit provider choice, same as `--provider`.
4. **Key presence.** Build `present = present_providers(env)`. A key counts only if `env.get(var, "").strip() != ""`. Env var names are case-sensitive and read exactly.
5. **Pick provider.**
   - If a provider was requested: its key must be present, else EnvError. The other key is ignored, whether or not it is set.
   - If none was requested: exactly one key present means use that provider; two means EnvError (ambiguous); zero means EnvError (missing).
6. **Model** = `spec.model_id` if spec is set, else `DEFAULT_MODELS[provider]` (`anthropic → "claude-sonnet-5"`, `openai → "gpt-5"`).
7. **Key value** = `env[var].strip()`.
8. Log `logger.debug("LLM provider: %s", resolved.describe())` and return.

A bare `--model ID` never infers a provider from the id (no `claude-*` → anthropic heuristics).

### Decision table
A = ANTHROPIC_API_KEY set (non-blank), O = OPENAI_API_KEY set. "—" = flag absent.

| # | A | O | --provider | --model | Result |
|---|---|---|---|---|---|
| 1 | ✓ | ✗ | — | — | anthropic / claude-sonnet-5 |
| 2 | ✗ | ✓ | — | — | openai / gpt-5 |
| 3 | ✓ | ✓ | — | — | EnvError(3) M_BOTH |
| 4 | ✗ | ✗ | — | — | EnvError(3) M_NONE |
| 5 | ✓ | ✓ | anthropic | — | anthropic / claude-sonnet-5 |
| 6 | ✓ | ✓ | openai | — | openai / gpt-5 |
| 7 | ✓ | ✗ | openai | — | EnvError(3) M_REQ_MISSING(openai, "--provider") |
| 8 | ✗ | ✗ | anthropic | — | EnvError(3) M_REQ_MISSING(anthropic, "--provider") |
| 9 | ✓ | ✗ | — | `claude-opus-5` | anthropic / claude-opus-5 |
| 10 | ✓ | ✓ | — | `claude-opus-5` | EnvError(3) M_BOTH (bare id does not choose) |
| 11 | ✓ | ✓ | — | `openai:gpt-5-mini` | openai / gpt-5-mini |
| 12 | ✓ | ✗ | — | `openai:gpt-5-mini` | EnvError(3) M_REQ_MISSING(openai, "--model") |
| 13 | any | any | anthropic | `anthropic:claude-x` | anthropic / claude-x (if A), else M_REQ_MISSING(anthropic, "--provider") |
| 14 | any | any | anthropic | `openai:gpt-5` | UsageError(2) M_CONFLICT (even with no keys) |
| 15 | any | any | any | `""` / `"  "` | UsageError(2) M_MODEL_EMPTY |
| 16 | any | any | any | `openai:` / `openai:  ` | UsageError(2) M_MODEL_NO_ID |
| 17 | ✗ | ✓ | — | `ft:gpt-4o:org:x:id` | openai / ft:gpt-4o:org:x:id (unknown prefix means bare id) |
| 18 | ✓ | ✗ | — | `  Anthropic:claude-x ` | anthropic / claude-x (prefix case-insensitive, stripped) |
| 19 | `"  "` | ✓ | — | — | openai / gpt-5 (blank counts as unset) |
| 20 | `"\t\n"` | `""` | — | — | EnvError(3) M_NONE |
| 21 | `" sk-a "` | ✗ | — | — | api_key == "sk-a" (stripped) |

### Exact error messages
Keys are never included. Only env var names, provider values and flag values appear.
- `M_MODEL_EMPTY`: `--model must not be empty`
- `M_MODEL_NO_ID`: `--model '{raw}' has no model id after the '{provider}:' prefix`
- `M_CONFLICT`: `--model '{raw}' selects provider '{model_provider}' but --provider is '{provider_flag}'`
- `M_NONE`: `No API key found: set ANTHROPIC_API_KEY or OPENAI_API_KEY`
- `M_BOTH`: `Both ANTHROPIC_API_KEY and OPENAI_API_KEY are set; choose one with --provider anthropic|openai`
- `M_REQ_MISSING`: `Provider '{provider}' was selected by {source} but {ENV_VAR} is not set or empty` where `source` ∈ {`--provider`, `--model`}. If both flags name the same provider, source is `--provider`.

`{raw}` is the stripped `--model` value. Messages are module-level string constants or format templates so the tests can reference them.

### Secret handling
- The module logs only `describe()` at DEBUG. `ResolvedProvider.__repr__` leaves out `api_key`.
- Don't put `env` or the key value into exception args, and don't use f-string debugging on `env`.
- Never mutate `env`.

## 5. Requires from T1
In `readme_stack/config.py`:
- `class Provider(StrEnum): ANTHROPIC = "anthropic"; OPENAI = "openai"`. It lives in `config.py`, not in infra, so `RunConfig`/CLI can use it without depending on `infra`. `str(Provider.ANTHROPIC) == "anthropic"`.
- `DEFAULT_MODELS: Final[Mapping[Provider, str]] = {Provider.ANTHROPIC: "claude-sonnet-5", Provider.OPENAI: "gpt-5"}`. Wrapping it in `MappingProxyType` is optional.
- (Not consumed here, for reconciliation:) `RunConfig.provider: Provider | None`, `RunConfig.model: str | None` hold the raw flags. The resolved `ResolvedProvider` belongs to `RunContext` (workflow), not `RunConfig`.

In `readme_stack/core/errors.py`:
- `class ExitCode(IntEnum)`: `OK=0, FAILURE=1, USAGE=2, ENVIRONMENT=3, REPO_STATE=4, BUDGET_EXCEEDED=5, INTERRUPTED=130`.
- `class ReadmeStackError(Exception)` with class attribute `exit_code: ClassVar[ExitCode]` (default `FAILURE`), constructed as `Error(message: str)`, where `str(err) == message`.
- `class UsageError(ReadmeStackError)`: `exit_code = ExitCode.USAGE` (2).
- `class EnvError(ReadmeStackError)`: `exit_code = ExitCode.ENVIRONMENT` (3). The name deliberately avoids shadowing the builtin `EnvironmentError` (an alias of `OSError`). If T1 picks another name, such as `EnvironmentConfigError`, T3 adopts it. Only the import line changes.
- `cli/app.py` catches `ReadmeStackError`, prints `str(err)` to stderr, and returns `err.exit_code`. That belongs to the CLI task, not T3.

If T1 has not landed when T3 starts, implement against these names exactly. The tests only rely on `exit_code` values and message text.

## 6. Test plan (`tests/unit/infra/test_providers.py`)
Helpers: `A = "sk-ant-TESTSECRET"`, `O = "sk-oa-TESTSECRET"`, and a builder `env(a=None, o=None) -> dict` that only includes keys whose value is not None. `resolve_provider` rows use `pytest.mark.parametrize` with ids.

**Success table** (asserts `provider`, `model`, `api_key_env_var`, `api_key`):
1. `{A}`, —, — → anthropic, claude-sonnet-5, ANTHROPIC_API_KEY, A
2. `{O}`, —, — → openai, gpt-5, OPENAI_API_KEY, O
3. `{A,O}`, provider=anthropic → anthropic, claude-sonnet-5
4. `{A,O}`, provider=openai → openai, gpt-5
5. `{A}`, model=`claude-opus-5` → anthropic, claude-opus-5
6. `{A,O}`, model=`openai:gpt-5-mini` → openai, gpt-5-mini
7. `{A,O}`, provider=anthropic, model=`anthropic:claude-x` → anthropic, claude-x
8. `{A,O}`, provider=openai, model=`gpt-5-mini` → openai, gpt-5-mini
9. `{O}`, model=`ft:gpt-4o:org:x:id` → openai, `ft:gpt-4o:org:x:id`
10. `{A}`, model=`  Anthropic:claude-x ` → anthropic, claude-x
11. `{A:"   ", O}` → openai (blank anthropic ignored)
12. `{A:" sk-a \n"}` → api_key == "sk-a"
13. `{A, "OTHER": "x"}`, provider=anthropic → ignores unrelated vars
14. `{A, O:""}` → anthropic (empty string counts as unset)

**Error table** (asserts exception type, `exit_code`, and `str(exc)` equal to the exact message):
15. `{}` → EnvError, 3, M_NONE
16. `{A:"\t", O:""}` → EnvError, 3, M_NONE
17. `{A,O}` → EnvError, 3, M_BOTH
18. `{A,O}`, model=`claude-opus-5` → EnvError, 3, M_BOTH
19. `{A}`, provider=openai → EnvError, 3, "Provider 'openai' was selected by --provider but OPENAI_API_KEY is not set or empty"
20. `{}`, provider=anthropic → EnvError, 3, M_REQ_MISSING(anthropic, --provider)
21. `{A}`, model=`openai:gpt-5` → EnvError, 3, "... selected by --model but OPENAI_API_KEY ..."
22. `{A,O}`, provider=anthropic, model=`openai:gpt-5` → UsageError, 2, M_CONFLICT
23. `{}`, provider=anthropic, model=`openai:gpt-5` → UsageError, 2 (usage wins over env)
24. `{A}`, model=`""` → UsageError, 2, M_MODEL_EMPTY
25. `{A}`, model=`"   "` → UsageError, 2, M_MODEL_EMPTY
26. `{A}`, model=`"openai:"` → UsageError, 2, M_MODEL_NO_ID
27. `{A}`, model=`"anthropic:  "` → UsageError, 2, M_MODEL_NO_ID

**parse_model_flag unit rows:**
28. `"gpt-5"` → (None, "gpt-5")
29. `"openai:gpt-5"` → (OPENAI, "gpt-5")
30. `"OPENAI : gpt-5"` → (OPENAI, "gpt-5")
31. `"ft:gpt-4o:org"` → (None, "ft:gpt-4o:org")
32. `"anthropic:claude:v2"` → (ANTHROPIC, "claude:v2") (split on the first colon only)

**Secret safety:**
33. For a success case: `A not in repr(resolved)`, `A not in resolved.describe()`, and `describe() == "anthropic:claude-sonnet-5 (key from ANTHROPIC_API_KEY)"`.
34. For every error case in 15–27: `A not in str(exc)`, `O not in str(exc)`, and `A`/`O` are not in `repr(exc.args)`.
35. With `caplog.set_level(logging.DEBUG)`, run a success case and one failure case, then assert that no record's `getMessage()` contains `A` or `O`.
36. `env` is unchanged after the call (compare with a copy).

**Constants and wiring:**
37. `API_KEY_ENV_VARS` equals `{ANTHROPIC: "ANTHROPIC_API_KEY", OPENAI: "OPENAI_API_KEY"}`, and `DEFAULT_MODELS` values are the two defaults.
38. Import guard: `providers.py` source contains no `import ai`/`from ai` (read the file with `inspect.getsource` and use a regex `^\s*(import ai\b|from ai\b)`).
39. `ResolvedProvider` equality ignores `api_key` (`compare=False`) and the object is frozen (assignment raises `FrozenInstanceError`).

Gate: `uv run ruff check && uv run ruff format --check && uv run pytest tests/unit/infra/test_providers.py`.

## 7. Out of scope / deferred
- Building the `ai` provider/model objects, and any base-URL, proxy or org settings: T11 (`adapter.py`).
- Argparse wiring (`--provider` choices, `type=Provider`), catching errors and mapping them to exit codes: CLI task.
- Checking that a model id actually exists or is supported by the provider (no network). A wrong id fails later at the first API call, with exit 1 from the adapter.
- Inferring a provider from a bare model id (`claude-*`/`gpt-*`).
- Other providers (Azure, Bedrock, Vertex, Gemini), `*_BASE_URL` overrides, and key files or keyrings.
- Confirming that OpenAI's default id `gpt-5` is current (the high-level plan defers this to adapter time; changing it only touches T1's `DEFAULT_MODELS`).
- GitHub Action input → env mapping (later action step).

## 8. Open questions
1. **Should a `--model provider:id` prefix count as a provider choice when both keys are set?** This plan says yes (row 11). The acceptance text only mentions `--provider`. If rejected, row 11 becomes M_BOTH and `source="--model"` disappears.
2. **Error class name for exit 3.** This plan uses `EnvError` to avoid shadowing the builtin `EnvironmentError`. To be reconciled with T1.
3. **Where `Provider` lives.** This plan puts it in `config.py` and T1 owns it. The alternative is `core/models/` or `providers.py`, but that would make `config`/`cli` import from `infra`.
4. **Case-insensitive provider prefix in `--model`** (row 18). The alternative is exact lowercase match, where `Anthropic:x` would be treated as a bare id and fail at the API.
5. **Blank `--model ""`.** This plan treats it as a usage error (2). The alternative is to treat it as unset, which may matter when the GitHub Action passes empty inputs through. Revisit in the action step.
