# readme-stack

A command-line tool that uses an LLM agent to write or update a repository's `README.md`.
The agent explores the repo with two read-only tools (`list_files` and `read_file`), then
returns a README for a technical reader: architecture, key components, getting started,
development commands and project layout. It has two modes:

- **create** (default): explore the repo and write a README from scratch. An existing
  `README.md` is overwritten; git is your backup.
- **update** (`--diff RANGE`): revise the existing `README.md` so it reflects the changes in a
  git range, keeping unaffected sections verbatim.

This is a prototype. A GitHub Action wrapper is coming and will be documented here when it
lands.

## Install

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and `git` on `PATH`.

```sh
uv sync                # dev environment; run the CLI with `uv run readme-stack`
uv tool install .      # or install the `readme-stack` command globally
```

## Configure

Set one API key in the environment:

| Variable            | Provider  | Default model     |
| ------------------- | --------- | ----------------- |
| `ANTHROPIC_API_KEY` | Anthropic | `claude-sonnet-5` |
| `OPENAI_API_KEY`    | OpenAI    | `gpt-5`           |

If both are set, Anthropic is used. Empty values are ignored. `--model` overrides the model id
for whichever provider was detected; it does not switch provider. The key is never logged, and
it is masked as `***` in error messages.

## Usage

```sh
# Preview a new README for the current repo on stdout, logging tool calls to stderr
readme-stack --dry-run -v

# Create (or overwrite) README.md for another repo; any path inside the repo works
readme-stack path/to/repo

# Update README.md to reflect everything on this branch since main
readme-stack --diff main..HEAD

# Use a different model for the detected provider
readme-stack --model <id>
```

Progress messages and errors go to stderr. Only `--dry-run` writes to stdout, so
`readme-stack --dry-run > NEW_README.md` works.

### Options

```
readme-stack [-h] [--diff RANGE] [--model ID] [--dry-run] [-v] [--version] [REPO]
```

| Option            | Description                                                                                                                        |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `REPO`            | Path inside the git repository (default: current directory). The repo's top level is used as the root.                             |
| `--diff RANGE`    | Update mode: revise the existing `README.md` to reflect this git range (e.g. `HEAD~1..HEAD`). Without it, a README is created from scratch. |
| `--model ID`      | Override the default model for the detected provider.                                                                              |
| `--dry-run`       | Print the README to stdout instead of writing `README.md`.                                                                         |
| `-v`, `--verbose` | Log agent tool calls to stderr (and a traceback on unexpected failures).                                                           |
| `--version`       | Show the program's version number and exit.                                                                                        |
| `-h`, `--help`    | Show the help message and exit.                                                                                                    |

### Exit codes

| Code  | Meaning                                                                                                                                                 |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0`   | Success. Also returned when the `--diff` range has no changes (nothing is written) or the updated README is identical to the current one.              |
| `1`   | Runtime or LLM failure (the agent run failed, the model returned an empty README, or an unexpected error).                                             |
| `2`   | Usage or configuration error: bad arguments, no API key, not a git repository, invalid range, `--diff` without an existing `README.md`, or a symlinked `README.md`. |
| `130` | Interrupted (Ctrl-C).                                                                                                                                   |

## How it works

```
parse args → resolve key/model → find git root → [update: compute diff] → run agent → write README
```

1. **Key and model** (`config.py`): pick the provider from whichever API key is set, and the
   default model or `--model`.
2. **Git** (`git.py`, fixed argv, no shell): resolve the repo's top level, then build the list
   of files the agent may see with `git ls-files -co --exclude-standard` (tracked plus
   untracked-but-not-ignored). In update mode, compute `git diff --stat RANGE` and
   `git diff RANGE`. The patch is truncated at 60,000 characters with a note telling the agent
   to read full files instead. An empty diff exits 0 without calling the LLM.
3. **Agent** (`agent.py`): one agent run with a system prompt (README structure and style
   rules, including "only state facts from files you actually read") and a mode-specific user
   prompt. Create mode gets the repo name and the top-level file tree. Update mode gets the
   current README, the diff stat and the (possibly truncated) patch, and is told to edit only
   the affected sections. The agent returns the README as structured output.
4. **Write** (`cli.py`): print it for `--dry-run`, or write `README.md` atomically (temp file in
   the same directory, then `os.replace`), preserving the existing file's permissions.

### Agent tools

| Tool                                           | Behavior                                                                                                                              |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `list_files(path="")`                          | The allowed files under a directory as an indented tree, capped at 500 entries with a "… N more files" line.                           |
| `read_file(path, start_line=1, max_lines=400)` | Numbered lines from an allowed file, at most 2,000 lines and 100,000 bytes per call. The result says which `start_line` to use next. |

Tool errors (unknown file, invalid path, past end of file) come back as `error: …` strings so
the agent can correct itself instead of the run failing.

### Safety properties

- **Allow-list only.** `read_file` accepts only paths from the git file list. Gitignored files,
  denylisted files (`.env`, `.env.*`, `*.pem`, `*.key`), binary files (a NUL byte in the first
  8 KiB), symlinks and non-regular files are never listed and cannot be read. Absolute paths,
  backslashes and `..` segments are rejected, and a file that resolves outside the repo root is
  refused.
- **No symlinked README.** If `README.md` is a symlink, readme-stack refuses to read it (update
  mode) or replace it (create mode without `--dry-run`) and exits 2, so it can never send a file
  from outside the repo to the LLM or overwrite a link.
- **README excluded from the diff.** Both diff commands exclude `README.md`, so the agent works
  from code changes, not from edits to the README itself.
- **Bounded exploration.** The agent gets at most 80 tool calls per run. After that, each tool
  returns `error: tool call limit reached; write the README now with what you know`.

## Development

```sh
uv sync                     # create .venv with runtime and dev dependencies
uv run pytest               # run the tests (no network, no API key needed)
uv run ruff check           # lint
uv run ruff format          # format (CI runs `ruff format --check`)
uv run readme-stack --help  # run the CLI from the checkout
uv run python -m readme_stack --version
uv build                    # build the sdist and wheel into dist/
```

CI (`.github/workflows/ci.yml`) runs `uv sync --locked`, ruff and pytest, plus a self-test that
runs `readme-stack --help` and `--version`. It has no API key, so it never calls a model.

The LLM SDK (`ai`, pinned at `0.7.0` because it is a beta) is imported only by `agent.py`;
`grep -rn "^import ai\|^from ai" src/` should show only that file.

## Project layout

```
src/readme_stack/
  __init__.py          # __version__, read from package metadata
  __main__.py          # `python -m readme_stack`
  cli.py               # argparse, flow ordering, exit codes, atomic write
  config.py            # API key → provider and model (resolve_llm)
  git.py               # repo root, allowed file list, diff with truncation
  tools.py             # RepoTools: list_files / read_file (pure Python, no SDK)
  agent.py             # the only module importing `ai`: model, tool wrappers, agent run
  prompts/             # system.md, create.md, update.md (shipped in the wheel)
tests/                 # pytest suite; conftest.py provides a temporary git repo fixture
action.yml             # placeholder GitHub Action, to be replaced by the upcoming wrapper
plans/                 # design and task plans
pyproject.toml         # package metadata, `readme-stack` entry point, ruff/pytest config
.github/workflows/ci.yml
```

## Prototype limitations

- Only one output file: `README.md` at the repository root.
- Only two modes, create and update. Create overwrites the existing README; there is no
  protection for hand edits.
- One API key at a time (Anthropic or OpenAI), with no provider flag.
- No live-model tests in CI: the test suite replaces the SDK call, so real runs are checked
  manually with a key.
