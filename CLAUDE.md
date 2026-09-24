# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Workflow

All development follows this flow, in order. Do not skip stages.

**Plan → Break Down → Create Tasks → Implement Tasks → Review → Commit (on worktree branch) → User merges**

Each stage is handled by a separate agent with a single responsibility.

### 1. Plan
- A planning agent designs the feature and writes the result to a plan file at `plans/<feature-name>.md`.
- The plan file is the only output of this stage. No code changes.

### 2. Break Down
- A breakdown agent reads the plan file and appends a `## Proposed Tasks` section to the **same** plan file.
- Each proposed task should be small enough for one agent to finish on its own, and should list its dependencies on other tasks so independent tasks can run in parallel.
- Do not change the existing plan content; only append.

### 3. Create Tasks
- A task-creation agent takes the `## Proposed Tasks` list from the plan file and creates one Claude task per item (TaskCreate), keeping the dependencies and a reference back to the plan file.

### 4. Implement Tasks
- Each implementation agent works on **exactly one task**. Never take on a second task in the same agent.
- Each implementation agent works in **its own git worktree**, so multiple tasks can be implemented in parallel without conflicts.
- Update the task status as work progresses (in progress → completed).
- When implementation is finished, the implementation agent **triggers the review agent** on its changes.

### 5. Review
- The review agent reviews the implementation agent's changes in that worktree, and fixes issues or sends them back to the implementation agent.

### 6. Commit
- Once review passes, the review agent commits the changes to the task's worktree branch.
- Agents **never** merge into `main`. Only the user merges.
- After committing, prompt the user to review the changes and merge them. Include the worktree path, branch, and a summary of what changed and what the review found.
