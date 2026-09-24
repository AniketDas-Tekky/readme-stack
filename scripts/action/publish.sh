#!/usr/bin/env bash
# publish.sh - publish the regenerated README as a PR stacked on the feature PR.
#
# Runs after the readme-stack CLI has updated README.md in the working tree of a
# checkout of the feature branch (HEAD == PR_HEAD_SHA). It pushes ONLY our own
# README branch, creates or edits the README PR with plain `gh pr`, and links
# it into a native GitHub Stack with `gh stack link` using PR numbers, so the
# contributor's feature branch is never pushed. `gh stack submit` and `--open`
# are deliberately not used (see plans/github-action.md, "A0 spike findings").
#
# Inputs (environment variables only; never interpolate ${{ }} into this file):
#   GH_TOKEN          required  token for gh (contents + pull-requests write).
#                               Git pushes use the credentials set up by
#                               actions/checkout (`token:`).
#   PR_NUMBER         required  number of the feature PR (github.event.pull_request.number)
#   PR_BASE_REF       required  base branch of the feature PR (pull_request.base.ref)
#   PR_HEAD_REF       required  head branch of the feature PR (pull_request.head.ref)
#   PR_HEAD_SHA       required  head commit of the feature PR (pull_request.head.sha)
#   BRANCH_PREFIX     optional  prefix of the README branch (default "readme-stack/")
#   MODEL             optional  model name, mentioned in the PR body when set
#   GITHUB_WORKSPACE  optional  repository checkout to work in (default: current directory)
#   GITHUB_OUTPUT     required  file that receives the step outputs
#   GITHUB_STEP_SUMMARY optional file that receives a one-line summary
#
# Outputs (written to $GITHUB_OUTPUT):
#   status     created | updated | closed | unchanged
#   pr-number  number of the README PR (the closed one for status=closed; empty for unchanged)
#   pr-url     URL of the README PR (same rules as pr-number)

set -euo pipefail

GH_STACK_VERSION="v0.1.1"
MIN_GH_MAJOR=2
MIN_GH_MINOR=90
MIN_GIT_MAJOR=2
MIN_GIT_MINOR=20
BOT_NAME="readme-stack[bot]"
BOT_EMAIL="readme-stack[bot]@users.noreply.github.com"

die() {
  echo "::error::$*" >&2
  exit 1
}

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${PR_NUMBER:?PR_NUMBER is required}"
: "${PR_BASE_REF:?PR_BASE_REF is required}"
: "${PR_HEAD_REF:?PR_HEAD_REF is required}"
: "${PR_HEAD_SHA:?PR_HEAD_SHA is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"
BRANCH_PREFIX="${BRANCH_PREFIX:-readme-stack/}"
MODEL="${MODEL:-}"
[[ "$PR_NUMBER" =~ ^[0-9]+$ ]] || die "PR_NUMBER must be a number, got '$PR_NUMBER'"

cd "${GITHUB_WORKSPACE:-.}"

# version_at_least <version string> <min major> <min minor>
version_at_least() {
  local major minor
  if [[ "$1" =~ ([0-9]+)\.([0-9]+) ]]; then
    major="${BASH_REMATCH[1]}"
    minor="${BASH_REMATCH[2]}"
  else
    return 1
  fi
  ((major > $2 || (major == $2 && minor >= $3)))
}

# --- Setup -----------------------------------------------------------------
gh_version="$(gh --version | head -n 1)"
version_at_least "$gh_version" "$MIN_GH_MAJOR" "$MIN_GH_MINOR" ||
  die "readme-stack needs gh >= $MIN_GH_MAJOR.$MIN_GH_MINOR for gh-stack; found: $gh_version"
git_version="$(git --version)"
version_at_least "$git_version" "$MIN_GIT_MAJOR" "$MIN_GIT_MINOR" ||
  die "readme-stack needs git >= $MIN_GIT_MAJOR.$MIN_GIT_MINOR for gh-stack; found: $git_version"

gh extension install github/gh-stack --pin "$GH_STACK_VERSION"

git config user.name "$BOT_NAME"
git config user.email "$BOT_EMAIL"

branch="${BRANCH_PREFIX}pr-${PR_NUMBER}"
title="docs: update README for #${PR_NUMBER}"

write_outputs() {
  local status="$1" number="$2" url="$3"
  {
    echo "status=$status"
    echo "pr-number=$number"
    echo "pr-url=$url"
  } >>"$GITHUB_OUTPUT"
  local line="readme-stack: status=$status"
  if [[ -n "$number" ]]; then
    line+=" (README PR #$number: $url)"
  fi
  echo "$line"
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    echo "$line" >>"$GITHUB_STEP_SUMMARY"
  fi
}

# Existing open README PR, as "<number>\t<url>" (empty when there is none).
existing=""
existing_url=""
lookup="$(gh pr list --head "$branch" --state open --json number,url \
  --jq '.[] | "\(.number)\t\(.url)"')"
if [[ -n "$lookup" ]]; then
  IFS=$'\t' read -r existing existing_url <<<"${lookup%%$'\n'*}"
fi

# --- README unchanged ------------------------------------------------------
if git diff --quiet -- README.md; then
  if [[ -n "$existing" ]]; then
    gh pr close "$existing" --comment "README no longer needs changes for #${PR_NUMBER}."
    if git ls-remote --exit-code --heads origin "refs/heads/$branch" >/dev/null; then
      git push origin --delete "refs/heads/$branch"
    fi
    write_outputs closed "$existing" "$existing_url"
  else
    write_outputs unchanged "" ""
  fi
  exit 0
fi

# --- README changed --------------------------------------------------------
# Regenerate our branch from the PR head on every run, committing README.md only.
readme_tmp="$(mktemp)"
trap 'rm -f "$readme_tmp"' EXIT
cp README.md "$readme_tmp"
git switch --discard-changes -C "$branch" "$PR_HEAD_SHA"
cp "$readme_tmp" README.md
git add -- README.md
git commit -m "$title" -- README.md

# Push only our own branch; never the feature branch (PR_HEAD_REF).
git push --force origin "HEAD:refs/heads/$branch"

body="Automated README update for #${PR_NUMBER}, generated by readme-stack from \`${PR_HEAD_SHA}\`"
if [[ -n "$MODEL" ]]; then
  body+=" with model \`${MODEL}\`"
fi
body+=".

Merge this PR into \`${PR_HEAD_REF}\` to include the docs change in #${PR_NUMBER}. It is regenerated on every push to #${PR_NUMBER}."

if [[ -z "$existing" ]]; then
  url="$(gh pr create --base "$PR_HEAD_REF" --head "$branch" --title "$title" --body "$body" | tail -n 1)"
  pr="${url##*/}"
  [[ "$pr" =~ ^[0-9]+$ ]] || die "could not parse the PR number from gh pr create output: $url"
  status=created
else
  gh pr edit "$existing" --title "$title" --body "$body"
  pr="$existing"
  url="$existing_url"
  status=updated
fi

# PR numbers only: gh stack link pushes nothing and is idempotent. No --open.
gh stack link --base "$PR_BASE_REF" "$PR_NUMBER" "$pr"

write_outputs "$status" "$pr" "$url"
