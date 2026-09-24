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
Plain, testable methods on `tools.RepoTools(root, files)`, with no `ai` import. `agent.py` wraps them as `@ai.tool` functions, so only `agent.py` touches the SDK:
- `list_files(path: str = "") -> str`: the allowed files under `path` as an indented tree, capped (e.g. 500 entries, with a "… N more" line).
- `read_file(path: str, start_line: int = 1, max_lines: int = 400) -> str`: numbered lines, byte cap. Only paths in the allowed file list are readable (this blocks `..`, absolute paths, ignored and denylisted files). Errors are returned as `"error: …"` strings rather than raised, so the agent can recover.

## Files
```
src/readme_stack/
  __init__.py        # __version__
  __main__.py        # python -m readme_stack
  cli.py             # argparse, main(argv=None) -> int, exit-code mapping
  config.py          # resolve_llm(env, model_override) -> LLMConfig(provider, model, api_key)
  git.py             # repo_root, repo_files, diff (+ truncation)
  tools.py           # RepoTools: list_files / read_file logic (pure, no ai)
  agent.py           # build model, wrap RepoTools as @ai.tool, run agent, return markdown
  prompts/system.md, prompts/create.md, prompts/update.md   # loaded via importlib.resources
tests/
  test_config.py  test_git.py  test_tools.py  test_cli.py  test_agent.py
```
- Remove `src/readme_stack/main.py`. Move `get_input`/`set_output` out, or drop them; the action isn't in scope.
- Replace `tests/test_main.py`.
- `pyproject.toml`: add `ai[anthropic,openai]==0.7.0` (pinned exactly, beta) and `pydantic`; entry point `readme_stack.cli:main`; update the description. Details are in module 6.
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

### 2. `git.py` — repo root, file list, diff
**Purpose:** everything that shells out to git. It gives `tools.py` the allowed file list and `agent.py` the diff for update mode. No `ai` import, and no knowledge of the LLM.

**Interface**
```python
MAX_DIFF_CHARS = 60_000
DENYLIST = (".env", ".env.*", "*.pem", "*.key")     # matched against the basename

class GitError(Exception):
    """Git failure or invalid repo/range; the CLI maps it to exit code 2."""

@dataclass(frozen=True)
class Diff:
    stat: str            # `git diff --stat` output
    patch: str           # unified diff, possibly truncated
    truncated: bool
    @property
    def empty(self) -> bool: ...   # True when there are no changes (stat and patch blank)

def repo_root(path: Path) -> Path: ...
def repo_files(root: Path) -> list[str]: ...
def diff(root: Path, rev_range: str, max_chars: int = MAX_DIFF_CHARS) -> Diff: ...
```
Named `repo_files` rather than `list_files` to avoid confusion with the tool of that name in `tools.py`.

**Behavior**
- **`_run(root, *args) -> str`** (internal):
  - Runs `subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)`, with no shell.
  - `FileNotFoundError` → `GitError("git executable not found")`.
  - `TimeoutExpired` → `GitError("git … timed out")`.
  - Non-zero exit → `GitError(f"git {args[0]} failed: {stderr.strip()}")`.
- **`repo_root(path)`:** `git rev-parse --show-toplevel` → a resolved `Path`. Works from any subdirectory. Not a repo, or a missing directory → `GitError("not a git repository: <path>")`.
- **`repo_files(root)`:** `git ls-files -z -co --exclude-standard`, split on NUL, de-duplicated. It skips:
  - basenames matching `DENYLIST`
  - paths that no longer exist (deleted but still tracked)
  - symlinks and non-regular files
  - binaries: a NUL byte in the first 8 KiB

  Returns POSIX relative paths, sorted.
- **`diff(root, rev_range)`:**
  - Rejects blank ranges or ranges starting with `-` (option injection) → `GitError`, without running git.
  - Runs `git diff --stat <range> -- . ':(exclude)README.md'` and `git diff <range> -- . ':(exclude)README.md'`. The README is excluded so the model doesn't read its own previous output as a "change".
  - The range is passed through as given (`A..B`, `A...B`, or a single rev, which git compares against the working tree). A bad rev → `GitError` carrying git's message.
  - If the patch is longer than `max_chars`: cut at the last newline before the cap, append `"\n… diff truncated ({n} more characters); use read_file to inspect full files"`, and set `truncated=True`.

**Tests (`tests/test_git.py`)** use a `git_repo` fixture defined in `tests/conftest.py` (shared with `test_cli.py`): `git init` in `tmp_path`, set a local user name/email, write files, commit.
1. `repo_root` from a subdirectory → the repo top level.
2. `repo_root` on a non-repo directory → `GitError`.
3. `repo_files` includes tracked and untracked-but-not-ignored files, and excludes `.gitignore`d ones.
4. Excludes `.env`, `.env.local`, `certs/x.pem` and `id.key`; keeps `env.py`.
5. Excludes a binary file (contains a NUL byte); keeps a UTF-8 text file.
6. Skips a deleted-but-tracked file and a symlink.
7. Output is sorted, POSIX-style, with nested paths such as `src/pkg/mod.py`.
8. `diff("HEAD~1..HEAD")` → the stat lists the changed file, and the patch contains the change.
9. README.md changes in the range are excluded from both stat and patch.
10. `diff("HEAD..HEAD")` → `empty is True`.
11. An unknown rev → `GitError`.
12. `"--output=/tmp/x"` → `GitError`, and git is never called.
13. Truncation with a small `max_chars` → cut at a line boundary, the note is appended, `truncated is True`.
14. `subprocess.run` raising `FileNotFoundError` (monkeypatched) → `GitError("git executable not found")`.

### 3. `tools.py` — the two agent tools
**Purpose:** the only two tool implementations the agent gets. Pure Python over the repo root and the allowed file list from `git.repo_files`; no `ai` import. Every result is a string meant for the model. Problems are returned as `"error: …"` strings, never raised, so the agent can correct itself.

**Interface**
```python
MAX_LIST_ENTRIES = 500
DEFAULT_MAX_LINES = 400
MAX_READ_LINES = 2_000
MAX_READ_BYTES = 100_000

@dataclass(frozen=True)
class RepoTools:
    root: Path
    files: tuple[str, ...]          # sorted POSIX paths from git.repo_files (the allow-list)

    def list_files(self, path: str = "") -> str:
        """List repository files under a directory as an indented tree. Use '' for the repo root."""

    def read_file(self, path: str, start_line: int = 1, max_lines: int = DEFAULT_MAX_LINES) -> str:
        """Read a text file from the repository, returning numbered lines."""
```
The docstrings double as the tool descriptions the model sees once `agent.py` wraps these methods.

**Path handling** (shared helper `_normalize(path) -> str | None`):
- Strip whitespace, a leading `./` and a trailing `/`; `""` and `.` mean the repo root.
- Backslashes, absolute paths or any `..` segment → `None`, reported as `error: invalid path '<p>' (use repo-relative paths)`.
- Nothing is read unless it is in `files`, so ignored, denylisted, binary and outside-the-repo files are unreachable, even through symlinks.

**`list_files(path)`**
- Selects files equal to `path`, or starting with `path + "/"`. No match → `error: no files under '<path>'`.
- Output: a header `"<n> files under <path or .>"`, then an indented tree relative to `path` (2 spaces per level, directories end with `/`), emitted in sorted order:
  ```
  6 files under src
  readme_stack/
    __init__.py
    cli.py
    prompts/
      system.md
  ```
- More than `MAX_LIST_ENTRIES` files → the first 500, then `… <k> more files; call list_files with a narrower path`.

**`read_file(path, start_line, max_lines)`**
- A path that isn't in `files` → `error: '<path>' is not a readable file; use list_files to find files`.
- `start_line` is clamped to ≥ 1, and `max_lines` is clamped to `[1, MAX_READ_LINES]`.
- Reads the file as UTF-8 with `errors="replace"`. An `OSError` → `error: cannot read '<path>': <reason>`.
- An empty file → `<path> (empty file)`. `start_line` past the end → `error: start_line <s> is past the end of '<path>' (<total> lines)`.
- Output:
  - A header `"<path> (lines <a>-<b> of <total>)"`.
  - Then the lines, formatted as `f"{n:>5}  {line}"`.
  - Stops early if the output would exceed `MAX_READ_BYTES`.
  - If lines remain: `… <r> more lines; call read_file with start_line=<next>`.

**Tests (`tests/test_tools.py`)** build `RepoTools` over a `tmp_path` with a hand-written file tuple. No git is needed.
1. `list_files("")` → header count, and a nested tree with correct indentation and trailing `/` on directories.
2. `list_files("src")` and `list_files("./src/")` → the same output, relative to `src`.
3. `list_files("nope")` → the `error: no files under` message.
4. More than 500 files → exactly 500 entries plus the "… N more files" line.
5. `list_files("../x")`, `"/etc"` and `"a\\b"` → the invalid-path error.
6. `read_file` → the header with the line range, numbered lines, and `max_lines` honoured.
7. `start_line=3, max_lines=2` → lines 3–4, and a continuation note with `start_line=5`.
8. `start_line` past the end → error; `start_line=0` → clamped to 1.
9. A file on disk that isn't in `files` (e.g. `.env`) → the "not a readable file" error.
10. `../outside.txt` → the invalid-path error.
11. An empty file → `(empty file)`; invalid UTF-8 bytes → replaced, no exception.
12. A huge file → output capped near `MAX_READ_BYTES`, with a continuation note.
13. A file removed after indexing → `error: cannot read`.

### 4. `agent.py` + `prompts/` — run the agent
**Purpose:** the only module that imports `ai`. It builds the SDK model from `LLMConfig`, wraps `RepoTools` as SDK tools, assembles the prompts for the mode, runs one agent loop, and returns the README markdown.

**SDK facts used** (from ai-python.dev docs):
- `ai.get_provider(name, api_key=...)` + `ai.Model(id=..., provider=...)`.
- `ai.Agent(tools=[...])`, with `async with agent.run(model, messages, output_type=...) as stream`, then `stream.output`.
- `@ai.tool` on async functions: the docstring becomes the description and the type hints become the schema. A tool that raises becomes an error result the model sees.
- `ai.events.ToolEnd.tool_call.tool_name` for logging.
- `ai.testing.FakeModel` / `ai.testing.tool_call` for offline tests.

**Interface**
```python
Mode = Literal["create", "update"]
MAX_TOOL_CALLS = 80

class AgentError(Exception):
    """LLM/agent failure; the CLI maps it to exit code 1."""

class ReadmeOutput(pydantic.BaseModel):
    markdown: str = pydantic.Field(description="The complete README.md content")

@dataclass(frozen=True)
class ReadmeRequest:
    mode: Mode
    repo_name: str
    current_readme: str | None = None   # required for update
    diff: Diff | None = None            # required for update (git.Diff)

def load_prompt(name: str) -> str: ...                       # importlib.resources: readme_stack/prompts/<name>.md
def build_prompts(req: ReadmeRequest, tools: RepoTools) -> tuple[str, str]: ...   # (system, user); pure
def build_model(cfg: LLMConfig) -> ai.Model: ...
def make_sdk_tools(tools: RepoTools, log: Callable[[str], None] | None = None) -> list: ...
async def generate_readme(cfg: LLMConfig, tools: RepoTools, req: ReadmeRequest, *,
                          model: ai.Model | None = None,
                          log: Callable[[str], None] | None = None) -> str: ...
```

**Behavior**
- **`build_prompts`:** system = `system.md`. User = `create.md` or `update.md` rendered with `string.Template.substitute`, so a missing variable fails loudly. The variables:
  - create: `$repo_name`, `$file_tree` (= `tools.list_files("")`, already capped).
  - update: `$repo_name`, `$current_readme`, `$diff_stat`, `$diff_patch`, `$truncation_note` (a note to read files for detail when `diff.truncated`, else empty).
  - Update without `current_readme` or `diff` → `ValueError` (a programming error; the CLI checks first).
- **`make_sdk_tools`:** async `@ai.tool` wrappers named `list_files` and `read_file`, with the same parameters and docstrings as the `RepoTools` methods, delegating to them.
  - They share a call counter. After `MAX_TOOL_CALLS`, they return `error: tool call limit reached; write the README now with what you know`. This is a runaway guard that doesn't depend on any SDK step-limit option.
  - Each call is logged as `tool: read_file src/cli.py` via `log` (for `-v`).
- **`generate_readme`:**
  - `model = model or build_model(cfg)`.
  - Messages: `[ai.system_message(system), ai.user_message(user)]`.
  - Run `ai.Agent(tools=make_sdk_tools(...))` with `output_type=ReadmeOutput`, and drain the stream.
  - Result = `stream.output.markdown.strip() + "\n"`.
  - Blank markdown → `AgentError("model returned an empty README")`.
  - Any exception from the SDK run → `AgentError(f"LLM run failed: {type(e).__name__}: {e}")`. The config's key is never interpolated.

**Prompts** (`src/readme_stack/prompts/`, package data)
- `system.md`:
  - Role: a senior engineer writing a README for technical readers.
  - The required section order from "README format" above.
  - Rules:
    - Only state facts verified by reading files.
    - Commands must be copied from real manifests or scripts.
    - Paths must exist; use relative links.
    - No marketing language and no invented features.
  - Return the complete README in the `markdown` field.
- `create.md`: the repo name and top-level tree. Steps:
  1. Read the manifest and build files.
  2. Find the entry points.
  3. Read the core modules of each major directory.
  4. Then write.
- `update.md`:
  - Includes the repo name, current README, diff stat and diff patch (+ truncation note).
  - Steps:
    1. Decide which sections the change affects.
    2. Verify with the tools.
    3. Edit only those sections and keep all other text verbatim.
  - If nothing documentation-relevant changed, return the README unchanged.

**Tests (`tests/test_agent.py`)** are offline and use `ai.testing.FakeModel`.
1. All three prompts load; `system.md` names every required section.
2. `build_prompts` in create mode → the user prompt contains the repo name and the `list_files("")` tree, and no `$` placeholders remain.
3. In update mode → contains the current README, stat and patch. The truncation note appears only when `diff.truncated`.
4. Update without a diff → `ValueError`.
5. SDK tool wrappers delegate to `RepoTools`. After `MAX_TOOL_CALLS` (monkeypatched small) they return the limit error. `log` receives `tool: <name> <arg>`.
6. `generate_readme` with a FakeModel scripted as: an assistant `read_file` tool call, then `{"markdown": "# Demo\n..."}` → returns the markdown ending in one newline.
7. A FakeModel returning `{"markdown": "  "}` → `AgentError`.
8. A model whose run raises → `AgentError`, and the message doesn't contain the API key.
9. `build_model` passes the provider name and key to `ai.get_provider` (monkeypatched).

**Risks and fallbacks** (verify during implementation):
- If `@ai.tool` rejects closures, build the tools with `ai.Tool(...)` directly, or bind them through a module-level registry.
- If `output_type` isn't supported on `agent.run` for a provider, drop it: ask for the README as the final text and use `stream.text`.
- If FakeModel's strict history matching makes test 6 brittle, monkeypatch `ai.Agent.run` instead.

### 5. `cli.py` + `__main__.py` — wire it together
**Purpose:** parse arguments, run the flow in order, turn errors into exit codes, and write the result. It holds no business logic beyond ordering and I/O.

**Interface**
```python
GenerateFn = Callable[[LLMConfig, RepoTools, ReadmeRequest], Awaitable[str]]   # + log kwarg

def build_parser() -> argparse.ArgumentParser: ...
def main(argv: list[str] | None = None, *,
         env: Mapping[str, str] | None = None,
         generate: GenerateFn | None = None) -> int: ...     # pyproject entry point
```
`env` and `generate` are injectable for tests. They default to `os.environ` and `agent.generate_readme`. `__main__.py` is just `raise SystemExit(main())`.

**Arguments:**
- `repo`: positional, default `.`.
- `--diff RANGE`: update mode.
- `--model ID`
- `--dry-run`
- `-v/--verbose`
- `--version`

argparse's own errors exit with code 2, and `--help` exits 0.

**Flow** (each step fails fast)
1. `cfg = resolve_llm(env, args.model)`. `ConfigError` → `error: <msg>` on stderr, return 2.
2. `root = repo_root(Path(args.repo))`. `GitError` → return 2.
3. **Update mode** (`--diff` given):
   - `README.md` must exist, or: `error: --diff requires an existing README.md; run without --diff to create one`, return 2.
   - `d = diff(root, args.diff)` (`GitError` → 2).
   - If `d.empty`: print `no changes in <range>; README unchanged` to stderr, return 0.
   - Read the current README.
4. `tools = RepoTools(root, tuple(repo_files(root)))`. Build `ReadmeRequest(mode, repo_name=root.name, current_readme, diff)`.
5. Progress on stderr: `readme-stack: <creating|updating> README.md with <provider>/<model>…`. With `-v`, `log` prints each tool call to stderr.
6. `markdown = asyncio.run(generate(cfg, tools, req, log=log))`:
   - `AgentError` → `error: <msg>`, return 1.
   - `KeyboardInterrupt` → return 130.
   - Any other exception → `error: unexpected failure: <e>` (traceback only with `-v`), return 1.
7. **Output:**
   - `--dry-run`: write the markdown to stdout, return 0.
   - Update mode with an unchanged result: print `README.md already up to date`, return 0.
   - Otherwise, atomic write: a temp file in `root` (`.README.md.<random>.tmp`), written as UTF-8, then `os.replace` onto `README.md`. Print `wrote README.md (<n> lines)`, return 0.

**Tests (`tests/test_cli.py`)** use the shared `git_repo` fixture (in `tests/conftest.py`, also used by `test_git.py`), a fake env `{"ANTHROPIC_API_KEY": "k"}`, and a fake async `generate` that records its arguments and returns fixed markdown.
1. `--help` → `SystemExit(0)`, and lists `--diff`, `--model`, `--dry-run`.
2. No API key → 2, and the "no API key" message is on stderr.
3. A REPO outside any git repo → 2.
4. Create mode → returns 0. `README.md` holds the fake markdown. The request has mode `create` and `repo_name == root.name`.
5. Create mode overwrites an existing README.
6. `--dry-run` → the markdown on stdout, and README.md is untouched (still absent, or unchanged).
7. `--diff HEAD~1..HEAD` with no README → 2, and `generate` is not called.
8. `--diff HEAD..HEAD` (empty) → 0, the "no changes" message, and `generate` is not called.
9. Update mode → the request has `current_readme` and a non-empty `diff`, and the result is written.
10. Update result identical to the current README → "already up to date", and the file is unchanged.
11. `generate` raising `AgentError` → 1, and the message is on stderr.
12. `-v` → `generate` receives a callable `log`; without `-v`, `log is None`.
13. `--model custom` → `generate` receives `cfg.model == "custom"`.
14. `python -m readme_stack --version` (subprocess) → prints the version, exit 0.

### 6. Packaging: `pyproject.toml`, `uv.lock`, CI, README, cleanup
**Purpose:** make the CLI installable and runnable, keep CI green without an API key, document usage, and remove the GitHub Action boilerplate code the prototype no longer uses.

**`pyproject.toml`**
- `description = "CLI that uses an LLM agent to generate or update a repository README."`
- `dependencies = ["ai[anthropic,openai]==0.7.0", "pydantic>=2.13"]`:
  - `ai` 0.7.0 is the current PyPI release (Python ≥3.12). It is pinned exactly because it's a beta.
  - The `anthropic` and `openai` extras are required for direct provider access.
  - `pydantic` is listed explicitly because `agent.py` imports it.
- `[project.scripts] readme-stack = "readme_stack.cli:main"`.
- Keep `version = "0.1.0"`, hatchling, the dev group (pytest, ruff), and the ruff settings.
- Hatchling includes `src/readme_stack/prompts/*.md` automatically as package files. Verify by building a wheel and listing it.
- Regenerate `uv.lock` with `uv lock`, so CI's `uv sync --locked` passes.

**Package files**
- `src/readme_stack/__init__.py`: `__version__ = importlib.metadata.version("readme-stack")`, so the version lives only in pyproject. Used by `--version`.
- Delete `src/readme_stack/main.py` (placeholder logic, plus the `get_input`/`set_output` Action helpers, which are out of scope) and `tests/test_main.py`.
- Add `tests/conftest.py` with the `git_repo` fixture (see module 2).

**CI (`.github/workflows/ci.yml`)**
- `test` job: unchanged (`uv sync --locked`, ruff check, ruff format --check, pytest).
- `self-test` job: replace the `uses: ./` action run with `astral-sh/setup-uv@v7`, `uv sync --locked`, `uv run readme-stack --help` and `uv run readme-stack --version`. There is no API key in CI, so no live run.
- `action.yml` is left as is for now (out of scope). Note in the README that the Action wrapper comes later.

**`README.md`** (hand-written for now; the tool can regenerate it later as a dogfood test)
- What it does (create or update a README with an LLM agent).
- Install: `uv sync`, or `uv tool install .`.
- Configure: set exactly one of `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`.
- Usage examples:
  - `readme-stack --dry-run -v`
  - `readme-stack path/to/repo`
  - `readme-stack --diff main..HEAD`
  - `readme-stack --model <id>`
- Options table and exit codes (0/1/2/130).
- How it works (a 4-step flow plus the 2 tools).
- Development commands (`uv run pytest`, `uv run ruff check`, `uv run ruff format`).
- Prototype limitations (single README, 2 modes, one key).

**Checks**
1. `uv lock && uv sync --locked` succeeds.
2. `uv run readme-stack --help` / `--version` and `uv run python -m readme_stack --version` work.
3. `uv build` → the wheel contains `readme_stack/prompts/system.md`, `create.md` and `update.md`.
4. `uv run ruff check && uv run ruff format --check && uv run pytest` all pass.
5. No module other than `agent.py` imports `ai`: `grep -rn "^import ai\|^from ai" src/` shows only `agent.py`.

## Implementation notes
- The SDK is a public beta. Confirm the exact `ai.Agent` / `ai.get_provider` / `output_type` usage and any step-limit option against https://ai-python.dev/docs when implementing. Keep all of it inside `agent.py`.
- Small enough for one or two tasks under the CLAUDE.md workflow (e.g. T-A: config + git + tools + tests; T-B: agent + prompts + cli + packaging/CI/README).

## Proposed Tasks
Every task's acceptance also requires `uv run ruff check && uv run ruff format --check && uv run pytest` to pass in its worktree. File ownership is exclusive: `pyproject.toml`, `uv.lock`, `tests/conftest.py`, `.github/workflows/ci.yml`, and the removal of `main.py`/`test_main.py` belong to P1 only. `README.md` belongs to P7 only.

### P1: Foundation (packaging, entry point, shared fixture, cleanup)
- **Scope:**
  - Modify `pyproject.toml`: description, `ai[anthropic,openai]==0.7.0` + `pydantic>=2.13`, entry point `readme_stack.cli:main`.
  - Regenerate `uv.lock` (`uv lock`).
  - Modify `src/readme_stack/__init__.py`: `__version__` via `importlib.metadata`.
  - Create `src/readme_stack/__main__.py` (`raise SystemExit(main())`).
  - Create a **minimal stub** `src/readme_stack/cli.py`: `build_parser()` + `main(argv=None, *, env=None, generate=None) -> int` that supports `--help` and `--version` and otherwise returns 0. P6 replaces it.
  - Create `tests/conftest.py` with the `git_repo` fixture (git init in `tmp_path`, local user name/email, write files, commit; returns the repo path).
  - Delete `src/readme_stack/main.py` and `tests/test_main.py`.
  - Modify `.github/workflows/ci.yml`: the self-test job runs setup-uv, `uv sync --locked`, `uv run readme-stack --help`, `uv run readme-stack --version`.
- **Plan refs:** "Files"; module 6 (pyproject, package files, CI); module 2 (the `git_repo` fixture description); module 5 (interface of `main` / `__main__.py`).
- **Acceptance:** module 6 checks 1, 2 and 4.
- **Depends on:** none.

### P2: `config.py`
- **Scope:** create `src/readme_stack/config.py` and `tests/test_config.py`.
- **Plan refs:** "Flow" step 1; module 1.
- **Acceptance:** module 1 tests 1–8.
- **Depends on:** none (stdlib only; touches no shared files).

### P3: `git.py`
- **Scope:** create `src/readme_stack/git.py` and `tests/test_git.py`. The tests use the `git_repo` fixture from `tests/conftest.py`; do not modify conftest, and add extra commits or files inside the tests.
- **Plan refs:** "Flow" step 2; module 2.
- **Acceptance:** module 2 tests 1–14.
- **Depends on:** P1 (conftest fixture).

### P4: `tools.py`
- **Scope:** create `src/readme_stack/tools.py` and `tests/test_tools.py`.
- **Plan refs:** "Tools"; module 3.
- **Acceptance:** module 3 tests 1–13.
- **Depends on:** none (stdlib only; tests use `tmp_path`, not git).

### P5: `agent.py` + prompts
- **Scope:** create `src/readme_stack/agent.py`, `src/readme_stack/prompts/system.md`, `src/readme_stack/prompts/create.md`, `src/readme_stack/prompts/update.md`, and `tests/test_agent.py`. Confirm the SDK API against https://ai-python.dev/docs and apply the module 4 fallbacks if needed.
- **Plan refs:** "Flow" step 3; "README format"; module 4; "Implementation notes".
- **Acceptance:** module 4 tests 1–9; module 6 check 5 (only `agent.py` imports `ai`).
- **Depends on:** P1 (`ai`/`pydantic` deps), P2 (`LLMConfig`), P3 (`Diff`), P4 (`RepoTools`).

### P6: `cli.py` (full implementation)
- **Scope:** replace the P1 stub `src/readme_stack/cli.py` with the full flow; create `tests/test_cli.py`. `__main__.py` stays as P1 wrote it (change it only if test 14 requires it).
- **Plan refs:** "CLI"; "Flow" step 4; module 5.
- **Acceptance:** module 5 tests 1–14.
- **Depends on:** P1, P2, P3, P4, P5.

### P7: README and final packaging checks
- **Scope:** modify `README.md` (usage docs per module 6). No code changes. Verify the wheel contents.
- **Plan refs:** module 6 (`README.md`, Checks); "Verification".
- **Acceptance:** module 6 checks 1–5 (including `uv build`: the wheel contains the three prompt files); the README's commands, options and exit codes match `readme-stack --help` and module 5.
- **Depends on:** P6.

### Parallelization
- **Wave 1:** P1, P2, P4
- **Wave 2:** P3 (after P1)
- **Wave 3:** P5 (after P1–P4)
- **Wave 4:** P6
- **Wave 5:** P7
