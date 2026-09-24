# readme-stack

A GitHub Action that runs a Python script, managed with [uv](https://docs.astral.sh/uv/).

## Usage

```yaml
- uses: actions/checkout@v7
- uses: <owner>/readme-stack@v1
  id: readme-stack
  with:
    readme-path: README.md
- run: echo "${{ steps.readme-stack.outputs.result }}"
```

### Inputs

| Name             | Default     | Description                              |
| ---------------- | ----------- | ---------------------------------------- |
| `readme-path`    | `README.md` | Path to the README, relative to the repo |
| `python-version` | `3.12`      | Python version used to run the script    |

### Outputs

| Name     | Description                  |
| -------- | ---------------------------- |
| `result` | Result produced by the script |

## Development

```sh
uv sync              # create .venv and install dev deps
uv run pytest        # run tests
uv run ruff check    # lint
uv run ruff format   # format
uv run readme-stack  # run the script locally
```

Inputs are passed to the script as `INPUT_<NAME>` environment variables, so you can
simulate the action locally:

```sh
INPUT_README_PATH=README.md uv run readme-stack
```

## Layout

```
action.yml                  # composite action: installs uv, runs the script
pyproject.toml              # project + dev deps (pytest, ruff)
src/readme_stack/main.py    # script entry point
tests/                      # pytest tests
.github/workflows/ci.yml    # lint/test + runs the action against this repo
```
