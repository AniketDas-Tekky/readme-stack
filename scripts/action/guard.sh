#!/usr/bin/env bash
# readme-stack guard: decide whether the action should run for this event.
#
# Inputs (environment variables only; never interpolate ${{ }} into this script):
#   EVENT_NAME         github.event_name; anything other than "pull_request" skips
#   PR_DRAFT           github.event.pull_request.draft; "true" skips
#   PR_HEAD_REPO       github.event.pull_request.head.repo.full_name; empty or
#                      different from GITHUB_REPOSITORY skips (fork PR)
#   GITHUB_REPOSITORY  owner/repo of the workflow run (set by the runner)
#   PR_HEAD_REF        github.event.pull_request.head.ref; starting with
#                      BRANCH_PREFIX skips (our own stacked PR, prevents loops)
#   BRANCH_PREFIX      branch-prefix input; defaults to "readme-stack/" when unset
#                      or empty, so an empty prefix never matches every branch
#   GITHUB_OUTPUT      step output file (set by the runner); required
#
# Checks run in order: event, draft, fork, prefix.
#
# Outputs (appended to $GITHUB_OUTPUT):
#   skip=true, status=skipped   when a check fails (plus a ::notice:: with the reason)
#   skip=false                  when the action should proceed
set -euo pipefail

: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must be set}"

skip() {
  echo "::notice::readme-stack skipped: $1"
  {
    echo "skip=true"
    echo "status=skipped"
  } >>"$GITHUB_OUTPUT"
  exit 0
}

event_name="${EVENT_NAME:-}"
pr_draft="${PR_DRAFT:-}"
head_repo="${PR_HEAD_REPO:-}"
repository="${GITHUB_REPOSITORY:-}"
head_ref="${PR_HEAD_REF:-}"
prefix="${BRANCH_PREFIX:-readme-stack/}"

if [[ "$event_name" != "pull_request" ]]; then
  skip "event '${event_name}' is not pull_request"
fi

if [[ "$pr_draft" == "true" ]]; then
  skip "pull request is a draft"
fi

if [[ -z "$head_repo" || "$head_repo" != "$repository" ]]; then
  skip "pull request comes from a fork ('${head_repo}')"
fi

if [[ "$head_ref" == "$prefix"* ]]; then
  skip "head branch '${head_ref}' starts with '${prefix}' (readme-stack's own PR)"
fi

echo "skip=false" >>"$GITHUB_OUTPUT"
