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

## Proposed Tasks
Every code task's acceptance also requires `uv run ruff check && uv run ruff format --check && uv run pytest` to pass in its worktree, and `shellcheck` to pass on any script it touches. File ownership is exclusive and respects the prototype split: `pyproject.toml`, `uv.lock` and `tests/conftest.py` belong to P1 (action tasks must not edit them; test helpers live in the action test files themselves). `.github/workflows/ci.yml` is edited by P1 first and then only by A3. `README.md` is edited by P7 first and then only by A4.

Test file decision: each script gets its own test file. A1 owns `tests/test_action_guard.py` (the guard needs no fake `gh`; it only sets env vars and reads `$GITHUB_OUTPUT`). A2 owns `tests/test_action_publish.py`, with the fake-`gh` fixture and the bare-origin/clone fixture defined in that file. These two files replace the single `tests/test_action_scripts.py` listed in "Files".

### A0: gh-stack spike (manual, sandbox repo)
- **Scope:** no repo code. In a throwaway sandbox repo, run a workflow with a PAT (`GH_TOKEN`) that installs `github/gh-stack` and exercises the "Publish via gh-stack" sequence against a feature branch that has an open PR. Append a `## A0 spike findings` section to the end of `plans/github-action.md` (the only file edited).
- **Plan refs:** "Decisions" (Stacking, Token); "Steps" 5 (Setup, README changed, Feature branch safety); "Edge cases" (gh-stack preview risk); "Proposed task split" A0.
- **Acceptance:** the findings section answers each question with evidence (commands, output, links to the sandbox PRs and run):
  1. `gh stack init --base <base> <head> <branch>` adopts an existing branch that already has an open PR, non-interactively;
  2. `gh stack submit --auto --open` creates the top PR as ready for review and links it into a Stack with the existing PR;
  3. `GH_TOKEN` authentication works for every gh-stack command in CI;
  4. re-submitting after resetting the top branch to a new `head.sha` updates the existing PR (force-push) rather than creating a duplicate;
  5. whether `submit` pushes the bottom (feature) branch, and whether it force-pushes it;
  6. the exact gh-stack version to pin, plus the gh and git versions on `ubuntu-latest` at the time.

  If 1, 3 or 4 fails, the findings say so and the work stops for a user decision (no fallback design).
- **Depends on:** none.

### A1: `guard.sh` + tests
- **Scope:** create `scripts/action/guard.sh` (executable, `set -euo pipefail`, reads only env vars, writes `status`/`skip` to `$GITHUB_OUTPUT`, emits `::notice::` on skip). Create `tests/test_action_guard.py` (runs the script via `subprocess` with a temp `GITHUB_OUTPUT` file). The env var names are defined here and documented in a header comment so A3 can wire them up.
- **Plan refs:** "Steps" 1 (Guard); "Files" (script conventions); "Verification" guard cases.
- **Acceptance:** guard cases 1–4 (draft, fork, `readme-stack/` head, normal PR), plus a non-`pull_request` event → skipped, and a custom branch prefix is honored; `shellcheck scripts/action/guard.sh` is clean.
- **Depends on:** none.

### A2: `publish.sh` + tests
- **Scope:**
  - Create `scripts/action/publish.sh` (executable, `set -euo pipefail`, env-only inputs): gh version check, `gh extension install github/gh-stack --pin <A0 version>`, bot author, then lookup / close / init / reset / commit / submit / edit, then `$GITHUB_OUTPUT` and `$GITHUB_STEP_SUMMARY`. Adjust the commands to match the A0 findings. Document the env var names in a header comment for A3.
  - Create `tests/test_action_publish.py` with the bare-origin/clone fixtures and the fake `gh`. The fake records its arguments, returns canned JSON, and handles `--version`, `extension install`, `stack init|submit` and `pr list|edit|close`; its `submit` pushes to the bare origin.
- **Plan refs:** "Steps" 5 (Publish via gh-stack, Feature branch safety); "Files"; "Verification" publish cases; "A0 spike findings".
- **Acceptance:** publish cases 5–10; `shellcheck scripts/action/publish.sh` is clean; the pinned version matches the A0 findings.
- **Depends on:** A0.

### A3: `action.yml` rewrite + CI lint + dogfood workflow
- **Scope:**
  - Rewrite `action.yml`: inputs and outputs per "`action.yml` interface"; composite steps guard → checkout + `HEAD == head.sha` check → README check → setup-uv + CLI run → publish, with every step after the guard gated on it. Secrets go only through `env`, and there's no `${{ }}` in script bodies.
  - Modify `.github/workflows/ci.yml` (on top of P1's version): add `shellcheck scripts/action/*.sh` and `actionlint` to the test job, and make sure no job still uses the removed `readme-path` input or `result` output.
  - Create `.github/workflows/readme.yml`: dogfoods `uses: ./` on this repo's PRs with the consumer `concurrency` block, and is skipped when the secrets are absent.
- **Plan refs:** "Consumer usage"; "`action.yml` interface"; "Steps" 1–5; "Files"; "Verification" (CI).
- **Acceptance:**
  - `actionlint` passes on both workflows, including the input checks for `uses: ./`.
  - `shellcheck scripts/action/*.sh` and `uv run pytest` pass.
  - The CLI invocation matches P6's interface (`readme-stack <path> --diff <range> [--model]`).
  - CI is green on the task branch.
  - The manual end-to-end checks in "Verification" run after merge; they're listed in the handoff and don't block this task.
- **Depends on:** A1, A2, P1 (ci.yml), P6 (CLI interface and entry point).

### A4: README "GitHub Action" section
- **Scope:** modify `README.md` only. Add a "GitHub Action" section covering:
  - setup, and the required token and its scopes (including why `GITHUB_TOKEN` isn't enough);
  - the consumer usage YAML with `concurrency`;
  - a table of inputs and outputs;
  - a note that gh-stack is in public preview;
  - limitations: fork and draft PRs are skipped, update mode only, no `closed` trigger.
- **Plan refs:** "Consumer usage"; "`action.yml` interface"; "Decisions"; "Edge cases".
- **Acceptance:** the inputs and outputs in the README match `action.yml` exactly; the usage YAML passes `actionlint` when saved as a workflow; P7's CLI docs are untouched.
- **Depends on:** A3, P7.

### Parallelization
- **Wave 1:** P1, P2, P4, A0, A1
- **Wave 2:** P3 (after P1), A2 (after A0)
- **Wave 3:** P5
- **Wave 4:** P6
- **Wave 5:** P7, A3 (after A1, A2, P1 and P6)
- **Wave 6:** A4 (after A3 and P7)


## A0 spike findings (2026-09-24)
Sandbox: https://github.com/AniketDas-Tekky/gh-sandbox-2 (public, default branch `main`). Local: gh 2.101.0, git 2.39.5 (macOS), `gh extension install github/gh-stack` → `gh stack github/gh-stack v0.1.1`. All commands ran with stdin redirected from `/dev/null` (no TTY). "GH_TOKEN-only auth" means a wrapper that exports `GH_TOKEN="$(gh auth token)"`, points `GH_CONFIG_DIR` and `XDG_DATA_HOME` at empty temp dirs (so no keyring login and no preinstalled extensions), sets `GIT_CONFIG_NOSYSTEM=1` (drops the osxkeychain helper) and `GIT_TERMINAL_PROMPT=0`. The clone uses `credential.helper '!gh auth git-credential'` so git pushes also authenticate from `GH_TOKEN`. The token was never printed or stored. It's the user's OAuth token (`gho_…`, scopes `repo, workflow, read:org, gist`). Fine-grained PATs and GitHub App tokens were **not** tested.

Setup: `spike/feature` (commit `681f90d`) was pushed and opened with plain `gh pr create` as **#5**. Later scenarios used `spike/feature2` (#8), `spike/feature3` (#9) and `spike/feature4` (#14) the same way. All spike PRs are now closed and all `spike/*` branches deleted; the closed PRs and the run stay visible at the URLs below.

### 1. `gh stack init --base main <head> <branch>` adopts an existing branch with an open PR — **YES**
```
$ gh stack init --base main spike/feature spike/readme-pr-5      # exit 0, no prompt
✓ Adopted 2 branches: main ← spike/feature ← spike/readme-pr-5
  You're on spike/readme-pr-5 (top of stack).
  Found PRs for 1 of 2 branches.
```
- In a fresh clone (the CI case) it also prints `✓ Created local trunk branch main from origin/main`, so the checkout doesn't need a local `main`.
- The top branch is **created from the current HEAD** (the feature head) and switched to. It is not created from `origin/<branch>`, even when that remote branch exists from an earlier run. A later `git reset --hard <head.sha>` is harmless but redundant.
- Stack state is local only, in `.git/gh-stack` (JSON: trunk, branches, PR numbers). A fresh CI checkout has none, so `init` runs on every run, and a second run found `2 of 2` PRs.

### 2. `gh stack submit --auto --open` creates the top PR ready for review and links it into a Stack — **YES** (with a caveat on `--open`)
```
$ gh stack submit --auto --open                                  # exit 0, no prompt
Pushing to origin...
PR #5 (…/pull/5) for spike/feature is up to date
✓ Created PR #6 (…/pull/6) for spike/readme-pr-5
✓ Stack created on GitHub with 2 PRs (stack #7)
✓ Pushed and synced 2 branches
```
- PR #6 (https://github.com/AniketDas-Tekky/gh-sandbox-2/pull/6): `isDraft:false`, `baseRefName: spike/feature`.
- The REST API shows the link: `GET /repos/{o}/{r}/pulls/6` → `"stack":{"number":7,"size":2,"position":2,"base":{"ref":"main"}}`, and #5 has `position:1`. `GET /repos/{o}/{r}/stacks` lists stack 7 with PRs [5, 6]. The #6 timeline has an `added_to_stack` event. `gh stack view --short` / `--json` shows both PRs.
- **Title and body with `--auto`:** the title is the branch's commit subject (`docs: update README for #5`). The body is only a gh-stack footer (`<sub>Stack created with GitHub Stacks CLI • Give Feedback 💬</sub>`). (`gh stack link` instead titles new PRs from the branch name, e.g. `spike/readme pr 9`.)
- `gh pr edit spike/readme-pr-5 --title … --body …` works afterwards, with both keyring and GH_TOKEN auth, and the PR stays in stack 7.
- **Caveat:** `--open` means "mark new **and existing** PRs as ready for review" (`gh stack submit --help`). I checked this with `gh stack link --open`: after converting contributor PR #9 to draft, the run printed `✓ Marked PR #9 … as ready for review`. So `--open` would un-draft a contributor's PR if they switched it to draft after the triggering event. Without `--open`, #9 stayed a draft.

### 3. `GH_TOKEN` auth works for every gh-stack command — **YES**
Under GH_TOKEN-only auth: `gh auth status` → `Logged in … (GH_TOKEN)`. All of these exited 0 with no prompts and no rate-limit or permission errors:
- `gh extension install github/gh-stack --pin v0.1.1` (into the empty `XDG_DATA_HOME`);
- `gh stack init`, `submit --auto --open` (create path: fresh clone of `spike/feature2`, created #10 in stack #11), `submit` (update path, see 4), `view`, `link`;
- `gh pr edit`.

`gh stack` pushes through git, and those pushes authenticated with `GH_TOKEN` via the gh credential helper. On the runner, `actions/checkout` with `token:` handles git auth instead. The versions run (6) also showed that the default `GITHUB_TOKEN` is enough to *install* the extension.

### 4. Re-submit after resetting the top branch updates rather than duplicates — **YES**
Steps: the contributor pushed `c11ce95` to `spike/feature`. In a **fresh clone** with GH_TOKEN-only auth I ran `init` (same args), `git reset --hard c11ce95`, committed a different README, then:
```
$ gh stack submit --auto --open                                  # exit 0
PR #5 (…/pull/5) for spike/feature is up to date
PR #6 (…/pull/6) for spike/readme-pr-5 is up to date
✓ Linked to the existing stack on GitHub (2 PRs, already up to date) (stack #7)
✓ Pushed and synced 2 branches
```
- #6 head went from `ee86c7f` to `a52a517` (not a fast-forward). The #6 timeline shows `head_ref_force_pushed`.
- No new PR was created (`gh pr list --state all` shows only #4, #5, #6 at that point).
- No errors or prompts.

### 5. Does `submit` push the bottom (feature) branch, and does it force-push? — **YES, it force-pushes it, and it clobbered contributor commits in testing (unsafe)**
- When the local feature branch equals the remote, the push is a no-op. Remote `spike/feature` stayed `681f90d` across the first submit and `c11ce95` across the second.
- **Local feature branch behind the remote:** in the original clone, local `spike/feature` and `origin/spike/feature` were both at `681f90d` while the remote was at `c11ce95` (the contributor's newer commit). `gh stack submit --auto --open` exited 0 and printed "PR #5 … is up to date / Pushed and synced 2 branches". Afterwards **the remote `spike/feature` was `681f90d`**: the contributor's commit was erased (#5 timeline: `head_ref_force_pushed`). #6 was also rolled back to its old README commit.
- The reflog shows why. `submit` first **fetches** the stack branches (`fetch origin +refs/heads/spike/feature:… : fast-forward` to `c11ce95`), then pushes with `--force-with-lease` against the ref it just fetched (`update by push` back to `681f90d`). So the lease never protects against a stale local branch. (`gh stack push --help`: "Uses explicit per-branch --force-with-lease checks".)
- In CI this happens whenever the contributor pushes between checkout (where `HEAD == head.sha` is checked) and `submit`. That window includes the LLM call. `concurrency: cancel-in-progress` makes it less likely but doesn't close it, because cancellation isn't synchronous.
- **Alternative tested (never touches the feature branch):** `gh stack link` with **PR numbers** for the bottom resolves them as PRs and skips pushing those branches:
  - `gh stack link --base main 5 spike/readme-pr-5` printed `Pushing 1 branch to origin...` (only the README branch). The remote `spike/feature` stayed at the contributor's newer `215a3f9` while the local copy was stale at `c11ce95`.
  - Same with #9: remote `spike/feature3` stayed at `fe5134f` while the local copy was stale.
  - `gh stack link --base main 14 15` (both PR numbers; #15 created beforehand with plain `gh pr create --base spike/feature4`) pushed nothing: `✓ Created stack with 2 PRs (stack #16)`.
  - Re-running `link` on an existing stack is idempotent: `✓ Stack with 2 PRs is already up to date`.
  - Without `--open`, the draft state of the bottom PR is left alone.

### 6. Versions — **YES**
- gh-stack: **v0.1.1** (latest release, published 2026-09-02; earlier: v0.1.0 2026-07-29). `gh stack --version` → `gh stack version 0.1.1`.
- ubuntu-latest, from the run on `spike/versions` (push trigger, default `GITHUB_TOKEN`, no custom secrets): https://github.com/AniketDas-Tekky/gh-sandbox-2/actions/runs/36067456816, conclusion success:
  ```
  Image: ubuntu-24.04   Version: 20260920.314.1
  gh version 2.101.0 (2026-09-15)
  git version 2.55.0
  gh extension install github/gh-stack --pin v0.1.1  → gh stack  github/gh-stack  v0.1.1
  gh stack version 0.1.1
  ```
  Both are well above the plan's minimums (gh ≥ 2.90, git ≥ 2.20).

### Other observations
- Nothing needed a TTY. With `--auto`, or with stdin not a terminal, `submit` skips its editor. `init`, `link` and `view` never prompted.
- No rate-limit or permission errors in about 15 gh-stack calls.
- `git push --force-with-lease=<branch>` of our own README branch failed with `(stale info)` when the local tracking ref was stale. That's correct behaviour, but for our own branch a plain `--force` (or a lease against the SHA from `git ls-remote`) is what we want.
- Closing both PRs of a stack closes the stack (`/stacks` → `open:false`). No `unstack` is needed.

### BLOCKER — user decision needed (feature-branch safety, question 5)
Questions 1, 3 and 4 pass. But the planned `gh stack submit` **does force-push the contributor's feature branch**. It did erase a newer contributor commit in the sandbox whenever the local copy was behind. That breaks the plan's rule "the action must never rewrite the contributor's branch" ("Steps" 5, Feature branch safety). The `HEAD == head.sha` check doesn't prevent it, because the race happens after checkout. Per the plan, A2 should not pick a workaround on its own. Options, with evidence above:
- **(a) Keep `submit` as planned** and add `git fetch origin <head.ref>` plus "abort if `origin/<head.ref>` ≠ `head.sha`" right before `submit`. This shrinks the race window to seconds but doesn't remove it: data loss is still possible.
- **(b) Replace `init` + `submit` with our own push plus `gh stack link` using PR numbers** (tested above). The feature branch is never pushed, no local stack state is needed, and `--open` isn't needed (a `gh pr create` PR is ready for review by default), so the contributor's draft state is never changed. Uses only documented gh-stack commands.
- **(c) Stop and revisit** gh-stack as a whole.

The spike's recommendation is **(b)**.

### Implications for A2/A3
Setup, either way:
- `gh --version` check (≥ 2.90);
- `gh extension install github/gh-stack --pin v0.1.1`;
- `git config user.name/user.email` for `readme-stack[bot]`;
- `GH_TOKEN=<github-token>` in the step env (checkout's `token:` covers git pushes).

**If option (b) is chosen (recommended):**
```
branch="<prefix>pr-$N"
existing=$(gh pr list --head "$branch" --state open --json number --jq '.[0].number // empty')
# README unchanged → close/delete as already planned
git switch -C "$branch" "$HEAD_SHA"
git add README.md && git commit -m "docs: update README for #$N"
git push --force origin "HEAD:refs/heads/$branch"              # only our branch, never head.ref
if [ -z "$existing" ]; then
  url=$(gh pr create --base "$HEAD_REF" --head "$branch" --title "docs: update README for #$N" --body "$BODY")
  pr=${url##*/}; status=created                                # PR is ready for review by default
else
  gh pr edit "$existing" --title "docs: update README for #$N" --body "$BODY"
  pr=$existing; status=updated
fi
gh stack link --base "$BASE_REF" "$N" "$pr"                    # PR numbers only: pushes nothing; no --open
```
- The fake `gh` in `tests/test_action_publish.py` then needs `pr create` (printing a URL) and `stack link`, instead of `stack init|submit`.
- Publish case 9 ("feature branch in origin unchanged") becomes a real guarantee rather than a consequence of the HEAD check.

**If option (a) is chosen:** keep the plan's sequence (`gh stack init --base "$BASE_REF" "$HEAD_REF" "$branch"` → commit on the auto-created top branch → `gh stack submit --auto` → `gh pr edit`), with these changes:
- insert `git fetch origin "$HEAD_REF"` and an abort if `git rev-parse "origin/$HEAD_REF"` ≠ `$HEAD_SHA` immediately before `submit`;
- drop `--open` on update runs, since it would un-draft the contributor PR (on create runs the guard has already rejected drafts, but the same race applies);
- the `git reset --hard "$HEAD_SHA"` after `init` is redundant, but harmless;
- document the remaining race in the README limitations.

In both cases `gh pr edit` is still needed to set the body. (b) sets the title directly at create time. With (a), `--auto` takes the title from the commit subject, and the body is a gh-stack footer.

### Pinned versions
- `github/gh-stack` **v0.1.1** (`gh extension install github/gh-stack --pin v0.1.1`).
- ubuntu-latest (ubuntu-24.04, image 20260920.314.1): **gh 2.101.0**, **git 2.55.0**.
- Minimums to enforce in `publish.sh` stay gh ≥ 2.90 (plan) and git ≥ 2.20.

### User decision (2026-09-24)
Blocker resolved with option (b): publish.sh pushes ONLY the README branch (`git push --force origin <prefix>pr-<N>`), creates or edits the README PR with `gh pr create` / `gh pr edit`, and then links it into a native Stack with `gh stack link --base <base.ref> <featurePR#> <readmePR#>` (PR numbers, so nothing is pushed). `gh stack submit` and `--open` are NOT used: submit can force-push the contributor's branch, and --open un-drafts the contributor's PR. gh-stack is pinned to v0.1.1.
