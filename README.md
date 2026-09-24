# readme-stack

A command-line tool that uses an LLM agent to write or update a repository's `README.md`.
The agent explores the repo with two read-only tools (`list_files` and `read_file`), then
returns a README for a technical reader: architecture, key components, getting started,
development commands and project layout. It has two modes:

- **create** (default): explore the repo and write a README from scratch. An existing
  `README.md` is overwritten; git is your backup.
- **update** (`--diff RANGE`): revise the existing `README.md` so it reflects the changes in a
  git range, keeping unaffected sections verbatim.

This is a prototype. A [GitHub Action](#github-action) wraps update mode: it keeps the README
in step with each pull request through a stacked README PR.

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

Progress messages and errors go to stderr. During a run, only `--dry-run` writes to stdout, so
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
| `2`   | Usage or configuration error: bad arguments, no API key, not a git repository (or `git` missing or failing), invalid range, `--diff` without an existing `README.md`, or a symlinked `README.md`. |
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
   prompt. Create mode gets the repo name and the file tree (the `list_files` output for the repo root,
   capped at 500 entries). Update mode gets the
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

## GitHub Action

`action.yml` is a composite action that runs readme-stack on pull requests. When a PR is
opened, marked ready for review, or pushed to (`synchronize`), the action:

1. checks out the PR head branch and runs `readme-stack --diff <base.sha>...<head.sha>` (update
   mode; the three-dot range covers only the PR's own changes);
2. if `README.md` changed, commits it to its own branch `readme-stack/pr-<N>` (regenerated from
   the PR head on every run) and opens a README PR, `docs: update README for #<N>`, whose base
   is the feature branch;
3. links the README PR and the feature PR into a native
   [GitHub Stack](https://docs.github.com/en/pull-requests/get-started/stacked-prs-quickstart)
   with `gh stack link`.

Reviewers see the docs change as a separate PR and can merge it into the feature branch before
the feature lands. The action **never pushes to the contributor's branch**: it pushes only its
own `readme-stack/pr-<N>` branch. Later runs force-push that branch and update the existing
README PR instead of opening a new one. If a later run finds that the README no longer needs
changes, it closes the stale README PR with a comment and deletes its branch.

### Setup

1. **Create a token.** The action needs a personal access token or a GitHub App token with
   **contents: write** and **pull-requests: write** on the repository. It uses the token to
   check out the repo, push the README branch, and open, edit, close and link the README PR.
   The default `GITHUB_TOKEN` is not enough: GitHub doesn't trigger workflows for PRs created
   with it, so your CI would never run on the README PR.
2. **Add repository secrets** (Settings → Secrets and variables → Actions):
   - `README_STACK_TOKEN`: the token from step 1;
   - `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`: the LLM key (see [Configure](#configure)).
3. **Add a workflow**, for example `.github/workflows/readme.yml`:

```yaml
name: README
on:
  pull_request:
    types: [opened, ready_for_review, synchronize]
concurrency:
  group: readme-stack-${{ github.event.pull_request.number }}
  cancel-in-progress: true
jobs:
  readme:
    runs-on: ubuntu-latest
    steps:
      - uses: AniketDas-Tekky/readme-stack@v1
        with:
          github-token: ${{ secrets.README_STACK_TOKEN }}
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}   # or openai-api-key
```

`@v1` assumes a `v1` release tag exists; until one is published, pin a branch or commit SHA
instead (`AniketDas-Tekky/readme-stack@<sha>`). The action does its own checkout, so no
`actions/checkout` step is needed. Keep the `concurrency` block: composite actions can't set
it, and it cancels an in-progress run when a newer push arrives.

### Inputs

| Input               | Required | Default         | Description                                                                                                                                                                          |
| ------------------- | -------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `github-token`      | yes      |                 | PAT or GitHub App token with contents and pull-requests write access. Used for checkout, pushing the README branch and opening the stacked PR. GITHUB_TOKEN is not enough: PRs it creates don't trigger workflows. |
| `anthropic-api-key` | no       | `""`            | Anthropic API key. Set this or openai-api-key.                                                                                                                                       |
| `openai-api-key`    | no       | `""`            | OpenAI API key. Set this or anthropic-api-key.                                                                                                                                       |
| `model`             | no       | `""`            | Model override passed to readme-stack --model.                                                                                                                                       |
| `branch-prefix`     | no       | `readme-stack/` | Prefix of the README branch (the branch is \<prefix\>pr-\<N\>).                                                                                                                      |
| `python-version`    | no       | `3.12`          | Python version used to run readme-stack.                                                                                                                                             |

If neither API key is set, the action fails with an error. If both are set, Anthropic is used,
as in the CLI.

### Outputs

| Output      | Description                                                         |
| ----------- | ------------------------------------------------------------------- |
| `status`    | One of skipped \| no-readme \| unchanged \| created \| updated \| closed. |
| `pr-number` | Number of the stacked README PR, when one exists.                   |
| `pr-url`    | URL of the stacked README PR, when one exists.                      |

`status` values:

| Status      | Meaning                                                                                                    |
| ----------- | ---------------------------------------------------------------------------------------------------------- |
| `skipped`   | Not a `pull_request` event, a draft or fork PR, the action's own README PR, or the branch moved on since the event. |
| `no-readme` | The repository has no `README.md`.                                                                         |
| `unchanged` | The README needs no changes and there is no open README PR.                                                |
| `created`   | A new README PR was opened and linked into the stack.                                                      |
| `updated`   | The existing README PR was regenerated and its title and body refreshed.                                   |
| `closed`    | The README no longer needs changes, so the open README PR was closed and its branch deleted.               |

`pr-number` and `pr-url` are set for `created`, `updated` and `closed` (for `closed` they hold
the PR that was closed), and are empty otherwise.

### Notes

- **gh-stack is a public preview.** The action installs `github/gh-stack` pinned at `v0.1.1`
  and checks for gh ≥ 2.90 and git ≥ 2.20. GitHub-hosted Ubuntu runners meet both.
- **Token types:** the action has only been tested with a user OAuth token. Fine-grained PATs
  and GitHub App tokens with the permissions above are expected to work but are untested.
- The CLI runs from the action's own checkout (`uv run --locked --no-dev`), so the readme-stack
  version is the one at the action ref you pin.

### Limitations

- **Fork PRs are skipped** (secrets and push access aren't available to them), as are **draft
  PRs**. A draft runs once it's marked ready for review.
- **Update mode only.** Repositories need an existing `README.md`; otherwise the action stops
  with `status=no-readme`.
- **No `closed` trigger.** When the feature PR is merged or closed, the action does nothing;
  the GitHub Stack handles the chain (for example with `gh stack sync`).
- **Dogfooding:** this repo runs the action on its own PRs with `uses: ./`
  (`.github/workflows/readme.yml`). The workflow skips cleanly when the `README_STACK_TOKEN`
  secret isn't set, which includes fork PRs.

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
action.yml             # composite GitHub Action (see "GitHub Action")
scripts/action/        # guard.sh (skip checks) and publish.sh (README branch, PR, stack link)
plans/                 # design and task plans
pyproject.toml         # package metadata, `readme-stack` entry point, ruff/pytest config
uv.lock                # locked dependencies (CI uses `uv sync --locked`)
CLAUDE.md              # development workflow for Claude Code agents
.github/workflows/ci.yml
.github/workflows/readme.yml  # dogfood: runs the action on this repo's PRs
```

## Prototype limitations

- Only one output file: `README.md` at the repository root.
- Only two modes, create and update. Create overwrites the existing README; there is no
  protection for hand edits.
- One API key at a time (Anthropic or OpenAI), with no provider flag.
- No live-model tests in CI: the test suite replaces the SDK call, so real runs are checked
  manually with a key.
