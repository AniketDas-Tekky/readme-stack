# readme-stack — Happy-path prototype

## Context
The full design (docs hierarchy, ownership manifest, 4 modes, 25 tasks, 13 task plans) grew too large to validate the core idea quickly. This prototype proves the core loop end to end: **an LLM agent explores a repo with a couple of tools and writes, or updates, a single README for a technical reader.** It deliberately skips format detection, user-edit protection, key-conflict handling and the deterministic-analysis layer. Those can be layered back on once the loop works.

Delivery: the plan is saved as `plans/prototype.md` on a **new branch `prototype-readme` created from `main`**. The earlier full-design plans stay untouched on `worktree-plan-readme-generation`.

## Scope
- **In:**
  - One output file, `README.md` at the repo root.
  - Two modes:
    - **create**: explore the repo and write a README from scratch, overwriting any existing one (git is the backup).
    - **update**: given `--diff RANGE`, revise the existing README to reflect the diff.
  - One API key, either `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, whichever is set.
  - Two agent tools: `list_files` and `read_file`.
- **Out:** a docs/ hierarchy, manifest, markers, format detection, hand-edit protection, a provider flag, token budgets, parallel agents, tree-sitter/analysis tools, the GitHub Action wrapper.

## CLI
```
readme-stack [REPO=.] [--diff RANGE] [--model ID] [--dry-run] [-v]
```
- No `--diff` → **create**. With `--diff` → **update**, which requires an existing README.
- `--dry-run` prints the README to stdout instead of writing it. `-v` logs tool calls to stderr.
- `--model` overrides the default model for the detected provider (anthropic `claude-sonnet-5`, openai `gpt-5`; confirm the ids when implementing).
- Exit codes: `0` ok (also an empty diff, which is a no-op); `1` runtime/LLM failure; `2` usage/config errors: no API key, not a git repo, bad range, `--diff` without a README.

## Flow
```
parse args → resolve key/model → git checks → [update: compute diff] → run agent → write README
```
1. **Key resolution** (`config.py`): use whichever of `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` is set, ignoring empty values. If neither is set → exit 2. Never log the key.
2. **Git** (`git.py`, fixed argv, no shell):
   - REPO must be inside a git work tree; use the repo top level as root.
   - File list: `git ls-files -co --exclude-standard`, minus a small denylist (`.env*`, `*.pem`, `*.key`) and binaries.
   - In update mode: `git diff --stat RANGE` plus `git diff RANGE`. The diff text is truncated at a character cap with a note, since the agent can read full files instead. An empty diff → print "no changes" and exit 0.
3. **Agent** (`agent.py`): one `ai.Agent(tools=[list_files, read_file])` run.
   - **System prompt:** README structure and style for a technical audience.
   - **User prompt:**
     - create: repo name, top-level file listing, "explore then write".
     - update: current README, diff stat, truncated diff, "revise only what the diff affects; keep everything else verbatim".
   - Output via `output_type=ReadmeOutput(markdown: str)` (Pydantic), read from `stream.output`.
4. **Write**: atomic write of `README.md` (temp file + `os.replace`), or print it for `--dry-run`.

## README format (system prompt, single file)
Title + one-paragraph summary → **Architecture** (components, how they interact, data/control flow; Mermaid diagram optional) → **Key components** (one subsection per major module/dir: responsibility, main entry points, notable implementation details) → **Getting started** (install, configure, run, from real manifests/scripts) → **Development** (test/lint/build commands) → **Project layout** (short annotated tree). Rules: every command and path must come from files actually read; no invented features.

## Tools (the only 2 new tool implementations)
Plain, testable functions in `tools.py`, wrapped with `@ai.tool` in a `make_tools(root, files)` factory (closures over the repo root and the allowed file list):
- `list_files(path: str = "") -> str`: the allowed files under `path` as an indented tree, capped (e.g. 500 entries, with a "… N more" line).
- `read_file(path: str, start_line: int = 1, max_lines: int = 400) -> str`: numbered lines, byte cap. Only paths in the allowed file list are readable (this blocks `..`, absolute paths, ignored and denylisted files). Errors are returned as `"error: …"` strings rather than raised, so the agent can recover.

## Files
```
src/readme_stack/
  __init__.py        # __version__
  __main__.py        # python -m readme_stack
  cli.py             # argparse, main(argv=None) -> int, exit-code mapping
  config.py          # resolve_llm(env, model_override) -> LLMConfig(provider, model, api_key)
  git.py             # repo_root, list_files, diff_stat, diff (+ truncation)
  tools.py           # list_files / read_file logic + make_tools()
  agent.py           # build model (ai.get_provider + ai.Model), run agent, return markdown
  prompts/system.md, prompts/create.md, prompts/update.md   # loaded via importlib.resources
tests/
  test_config.py  test_git.py  test_tools.py  test_cli.py  test_agent.py
```
- Remove `src/readme_stack/main.py`. Move `get_input`/`set_output` out, or drop them; the action isn't in scope.
- Replace `tests/test_main.py`.
- `pyproject.toml`: add `ai` (pinned exactly, beta) and `pydantic`; entry point `readme_stack.cli:main`; update the description.
- `.github/workflows/ci.yml`: self-test job → `uv run readme-stack --help` (there's no API key in CI).
- `README.md`: usage section for the CLI.
- Only `agent.py` imports `ai`, which keeps the beta SDK contained.

## Verification
- `uv run ruff check && uv run ruff format --check && uv run pytest`. No network in the tests.
  - `test_config`: one key → provider/model; none → error; `--model` override; empty values ignored.
  - `test_git`: a tmp git repo fixture — root detection, ignored and denylisted files excluded, diff for a range, empty range, bad range → error.
  - `test_tools`: tree output and cap; read window and line numbers; unlisted, `..` and absolute paths → `error:` string.
  - `test_cli`: argument parsing, `--diff` without a README → 2, dry-run writes nothing. The agent function is monkeypatched to return fixed markdown.
  - `test_agent`: prompt assembly for both modes (truncated diff included, current README included), with the SDK call monkeypatched.
- Manual smoke test with a real key:
  - `uv run readme-stack --dry-run -v` on this repo (create).
  - Then `uv run readme-stack` to write it.
  - Make a small commit, then `uv run readme-stack --diff HEAD~1..HEAD --dry-run` (update), and check that only the affected sections change.

## Module plans
Planned one module at a time, bottom-up so each module is planned after its dependencies:
1. `config.py` · 2. `git.py` · 3. `tools.py` · 4. `agent.py` + `prompts/` · 5. `cli.py` + `__main__.py` · 6. packaging (pyproject, CI, README)

### 1. `config.py` — LLM key and model resolution
**Purpose:** turn the environment and `--model` into an `LLMConfig` that `agent.py` uses to build the SDK model. Pure function: no I/O besides reading the given env mapping, and no `ai` import.

**Interface**
```python
Provider = Literal["anthropic", "openai"]

KEY_ENV_VARS: dict[Provider, str] = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
DEFAULT_MODELS: dict[Provider, str] = {"anthropic": "claude-sonnet-5", "openai": "gpt-5"}

class ConfigError(Exception):
    """Invalid configuration; the CLI maps it to exit code 2."""

@dataclass(frozen=True)
class LLMConfig:
    provider: Provider
    model: str
    api_key: str = field(repr=False)   # never shown in repr/logs

def resolve_llm(env: Mapping[str, str] | None = None, model: str | None = None) -> LLMConfig:
    """Pick the provider whose API key is set and the model to use."""
```

**Behavior**
- `env` defaults to `os.environ` (a parameter so tests don't need monkeypatching).
- Check the providers in `KEY_ENV_VARS` order (anthropic, then openai). The first whose value is non-empty after `.strip()` wins, and the key is stored stripped.
- Per the prototype assumption only one key is set. If both are, anthropic wins deterministically; no error, not documented as a feature.
- No key → `ConfigError("no API key found: set ANTHROPIC_API_KEY or OPENAI_API_KEY")`.
- `model`: if given and non-blank after strip, it is used as-is. Otherwise `DEFAULT_MODELS[provider]`. No `provider:model` parsing.
- Error messages never include key values.

**Tests (`tests/test_config.py`)**
1. Only `ANTHROPIC_API_KEY` → provider anthropic, model `claude-sonnet-5`.
2. Only `OPENAI_API_KEY` → provider openai, model `gpt-5`.
3. No keys → `ConfigError` with the message above.
4. Blank or whitespace key values → treated as unset (→ `ConfigError`, or falls through to the other provider).
5. Key surrounding whitespace is stripped.
6. `model="custom-id"` → overrides the default; `model="  "` → the default.
7. `repr(config)` does not contain the key.
8. Both keys set → anthropic.

## Implementation notes
- The SDK is a public beta. Confirm the exact `ai.Agent` / `ai.get_provider` / `output_type` usage and any step-limit option against https://ai-python.dev/docs when implementing. Keep all of it inside `agent.py`.
- Small enough for one or two tasks under the CLAUDE.md workflow (e.g. T-A: config + git + tools + tests; T-B: agent + prompts + cli + packaging/CI/README).
