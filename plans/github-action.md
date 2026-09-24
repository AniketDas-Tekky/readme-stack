# readme-stack GitHub Action — stacked README PRs

## Context
The prototype CLI (`plans/prototype.md`) can update a README from a git diff. The goal is to run it automatically. When a PR is published in a repo that uses the action, the action:
1. runs `readme-stack --diff` on that PR's changes;
2. commits the README update to a new branch;
3. opens a **PR stacked on top of the original PR** (its base is the original PR's branch).

Reviewers see the docs change separately and can merge it into the feature branch before the feature lands.

Saved as `plans/github-action.md` on `prototype-readme`. It builds on the prototype CLI (it needs task P6 `cli.py` to be done) and replaces the boilerplate `action.yml`.

## Decisions (confirmed with user)
- **Triggers:** `pull_request` types `opened`, `ready_for_review` and `synchronize`. Draft PRs are skipped. A new push regenerates the stacked branch and force-pushes it, so the README PR stays current.
- **No README in the repo:** skip with a notice. The action only uses update mode.
- **Implementation:** a composite action (`action.yml`) plus the `gh` CLI, which is preinstalled on GitHub-hosted runners. The shell logic lives in scripts so it can be linted and tested. No new Python code for GitHub.
- **Stacking:** use GitHub's built-in stacked PRs through the official `github/gh-stack` extension ([quickstart](https://docs.github.com/en/pull-requests/get-started/stacked-prs-quickstart)), not a hand-rolled "PR against the feature branch". The README PR is linked to the feature PR as a native GitHub **Stack**. The extension is in **public preview** and needs gh ≥ 2.90 and git ≥ 2.20.
- **Token:** a **required** `github-token` input (a PAT or GitHub App token with contents and pull-requests write). GitHub doesn't trigger workflows for PRs created with `GITHUB_TOKEN`, so using a real token means CI runs on the stacked PR.

## Consumer usage (documented in README)
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
      - uses: <owner>/readme-stack@v1
        with:
          github-token: ${{ secrets.README_STACK_TOKEN }}
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}   # or openai-api-key
```
The action does its own checkout, so consumers don't need a separate checkout step. The `concurrency` block has to live in the consumer's workflow, because composite actions can't set it.

## `action.yml` interface
- **Inputs:**
  - `github-token` (required).
  - `anthropic-api-key` / `openai-api-key`. Exactly one should be set; if neither is, the action fails with a clear error.
  - `model` (optional).
  - `branch-prefix` (default `readme-stack/`).
  - `python-version` (default `3.12`).
- **Outputs:**
  - `status`: one of `skipped` | `no-readme` | `unchanged` | `created` | `updated` | `closed`.
  - `pr-number`, `pr-url` (for the stacked PR, when one exists).
- The old `readme-path` input and `result` output are removed.

## Steps (composite)
1. **Guard** (`scripts/action/guard.sh`). It reads PR fields passed in as environment variables from `github.event`, and **skips** with `status=skipped` plus a `::notice::` when:
   - the event isn't `pull_request`;
   - the PR is a draft;
   - it's a fork PR (`head.repo.full_name != github.repository`), where secrets and push access aren't available;
   - the head branch starts with `branch-prefix`, i.e. it's our own stacked PR. This prevents loops, since the PAT-created stacked PR does trigger workflows.

   Every later step is conditional on the guard not skipping.
2. **Checkout:** `actions/checkout@v7` with `ref: <head.ref>`, `fetch-depth: 0` and `token: github-token`.
   - `head.ref` gives a local branch, which `gh stack` needs to adopt the feature branch. Full history makes the base...head diff and the merge-base work.
   - Then verify `HEAD == head.sha`. If not, a newer push landed after the event, so skip and let the newer run handle it.
3. **README check:** no `README.md` → `status=no-readme`, `::notice::README.md not found; readme-stack only updates existing READMEs`, then stop.
4. **Run the CLI:** `astral-sh/setup-uv@v7`, then:
   ```
   uv run --locked --no-dev --project "$GITHUB_ACTION_PATH" readme-stack "$GITHUB_WORKSPACE" --diff "$BASE_SHA...$HEAD_SHA" [--model]
   ```
   - The keys are passed only through `env` (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`) and never echoed.
   - The three-dot range diffs against the merge-base, so only the PR's own changes are included.
   - A non-zero CLI exit fails the job.
5. **Publish via gh-stack** (`scripts/action/publish.sh`, with `GH_TOKEN=github-token`):
   - **Setup:**
     - Check `gh --version` ≥ 2.90, or fail with a clear message.
     - `gh extension install github/gh-stack --pin <version>`, pinned because it's a preview.
     - Configure the git author as `readme-stack[bot]`.
   - `branch = <prefix>pr-<N>`. Look up an existing open README PR with `gh pr list --head <branch> --state open --json number,url`.
   - **README unchanged** (`git diff --quiet -- README.md`):
     - If a README PR exists, close it with a comment ("README no longer needs changes for #N") and delete the branch → `status=closed`.
     - Otherwise `status=unchanged`.
   - **README changed:**
     1. `gh stack init --base <base.ref> <head.ref> <branch>`. This adopts the existing feature branch as the bottom of the stack and creates our branch on top, non-interactively.
     2. `git switch <branch>`, then reset it to `head.sha`, so it's regenerated from the current PR head on every run. Commit `README.md` only: `docs: update README for #<N>`.
     3. `gh stack submit --auto --open`. It pushes the stack, creates the README PR (or updates it if it already exists), and links it into a GitHub Stack with the existing feature PR. `--open` makes it ready for review instead of a draft.
     4. Set a clear title and body on the README PR with `gh pr edit <branch> --title "docs: update README for #<N>" --body <body>`, since `--auto` generates titles. The body links to #N and names the head SHA and model.
     5. `status=created` or `updated`, depending on whether the lookup found a PR.
   - Write `status`, `pr-number` and `pr-url` to `$GITHUB_OUTPUT`, and a line to `$GITHUB_STEP_SUMMARY`.
   - **Feature branch safety:** the action must never rewrite the contributor's branch. The HEAD == head.sha check makes any push of the feature branch a no-op. The A0 spike confirms whether `submit` pushes the bottom branch at all, and whether it force-pushes.

## Edge cases
- **Original PR merged or closed:** GitHub's Stack handles the chain. `gh stack sync` rebases the remaining branches onto the merge target. The action doesn't run on `closed` in this version; a documented follow-up could add a `closed` trigger that runs `gh stack sync`.
- **gh-stack preview risk:** the extension is pinned, and the gh version is checked. If the A0 spike finds that adopting an existing PR, or authenticating with `GH_TOKEN`, doesn't work in CI, stop and revisit with the user rather than silently falling back.
- **Two pushes in quick succession:** handled by the consumer's `concurrency` group with cancel-in-progress. The force-push makes the result idempotent.
- **Branch protection on the feature branch:** doesn't affect us. We only push to our own branch and open a PR.
- **Large diffs:** the CLI already truncates them.

## Files
```
action.yml                       # rewritten composite action (inputs/outputs/steps above)
scripts/action/guard.sh          # skip logic → writes status/skip to $GITHUB_OUTPUT
scripts/action/publish.sh        # gh-stack init/submit + README commit + gh pr edit/close
tests/test_action_scripts.py     # runs the scripts against temp repos with a fake `gh` on PATH
.github/workflows/ci.yml         # add shellcheck + actionlint to the test job
.github/workflows/readme.yml     # dogfood: runs `uses: ./` on this repo's PRs (skipped without secrets)
README.md                        # "GitHub Action" section: setup, token scopes, usage YAML, limitations
```
Scripts use `set -euo pipefail`, read everything from environment variables (no interpolation of `${{ }}` into script bodies, to avoid injection), and are shellcheck-clean.

## Verification
- **`tests/test_action_scripts.py`** (pytest, offline):
  - A temp "origin" bare repo plus a clone, with a fake `gh` script on `PATH` that records its arguments and returns canned JSON. It also handles `gh --version`, `gh extension install` and `gh stack init|submit`; the fake `submit` pushes the branch to the bare origin.
  - `guard.sh` cases:
    1. draft → skipped
    2. fork → skipped
    3. head starting with `readme-stack/` → skipped
    4. normal PR → proceed
  - `publish.sh` cases:
    5. README changed, no existing PR → `gh stack init --base main feature readme-stack/pr-7`, then `gh stack submit --auto --open`, then `gh pr edit` with the title and body; `status=created`
    6. changed with an existing PR → the branch is reset to `head.sha` and re-committed, then `submit` and `gh pr edit`; `status=updated`
    7. unchanged with an existing PR → `gh pr close` plus branch delete, `status=closed`
    8. unchanged, no PR → `status=unchanged`, and no `gh stack` calls
    9. the README commit contains only `README.md`, and the feature branch in origin is unchanged
    10. `gh --version` below 2.90 → fails with the version message
- **CI:** `shellcheck scripts/action/*.sh` and `actionlint` pass.
- **End to end (manual, in a sandbox repo with the secrets set):**
  - Open a PR that changes code → a README PR `docs: update README for #N` appears, shown in the same GitHub Stack as the feature PR, and CI runs on it.
  - Push again → the stacked PR updates.
  - Push a change that needs no docs → the stacked PR is closed.
  - Mark a PR as draft → skipped.
  - Open a fork PR → skipped.

## Proposed task split (for the Break Down stage)
- A0: **gh-stack spike** (a manual run in a sandbox repo, from a workflow using a PAT). Confirm each of these and record the findings in this plan:
  - `gh stack init --base` adopts an existing branch that already has an open PR;
  - `submit --auto --open` links the new PR into a Stack alongside the existing PR;
  - `GH_TOKEN` authentication works for gh-stack;
  - re-submitting after resetting the top branch updates rather than duplicates;
  - whether the bottom branch gets pushed.

  Pin the extension version.
- A1: guard.sh + tests.
- A2: publish.sh + tests. Depends on A0.
- A3: action.yml rewrite + CI lint + dogfood workflow + README section. Depends on A1, A2 and prototype P6.
