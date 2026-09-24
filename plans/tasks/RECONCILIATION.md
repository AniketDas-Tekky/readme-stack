# Wave-2 reconciliation against T1

Source of truth: `plans/tasks/T1-scaffolding.md`. Every implementation agent applies the
amendments for its own task below on top of its plan. Where a plan and this file disagree, this
file wins. Section (d) decisions were resolved by the user on 2026-09-24.
Implement them as recorded there.

## (a) Canonical names

| Concept | Final name | Module (owner) |
|---|---|---|
| Exit codes | `ExitCode.{OK, FAILURE, USAGE, ENVIRONMENT, REPO_STATE, BUDGET_EXCEEDED, INTERRUPTED}` | `core/errors.py` (T1) |
| Base error | `ReadmeStackError(message)`, `exit_code: ClassVar[ExitCode]` | `core/errors.py` (T1) |
| Exit 2 / 3 / 4 / 5 | `UsageError` / `EnvError` / `RepoStateError` / `BudgetExceededError` | `core/errors.py` (T1) |
| Git errors | `GitUnavailable`, `NotAGitRepository`, `NotGitTopLevel`, `InvalidRevision`, `InvalidRange` (all `EnvError`), `GitCommandError` (1) | `core/errors.py` (T1) |
| Tool errors | `ToolError` > `SandboxViolation`, `FileAccessError` | `core/errors.py` (T1) |
| LLM errors | `LLMError(message, *, usage)` > `LLMOutputError`, `LLMStepLimitError`, `LLMProviderError` | `core/errors.py` (T1) |
| Task-local errors | `ChangeSetError*` (T7), `PromptError*` (T12), `AgentError`/`AgentConfigError` (T12), `GlobError(ValueError)` (T9), `ParseError` (T10) | the task's own module |
| Token usage | `Usage` (re-exported by `core.ports`) | `core/models/usage.py` (T1) |
| Ports | `LLM`, `Git` (+ `ls_tree`), `FileSystem`, `SummaryCache`, `ToolSpec`, `ToolHandler` | `core/ports.py` (T1) |
| Git value types | `RevRange`, `CommitInfo` (re-exported by `core.ports`) | `core/models/git.py` (T1) |
| Changed files | `ChangeStatus`, `ChangedFile`, `ChangedFiles` | `core/models/impact.py` (T1) |
| Impact result | `ImpactReport`, `ProposedPage`, `LeftoverNote`, `LeftoverAction` | `core/models/impact.py` (T1) |
| Impact mapping (deterministic) | `ImpactMapping`, `PageHit`, `ReadmeTrigger`, `IgnoredChange`, `ImpactMatcher`, `compile_glob`, `glob_matches` | `analysis/impact.py` (T9) |
| Plan | `PageKind{README, OVERVIEW, COMPONENT}`, `PageSpec`, `DocPlan` (README **in** `pages`), `Section`, `PageDraft`, `PAGE_ID_PATTERN` | `core/models/plan.py` (T1) |
| Manifest | `Manifest`, `ManifestEntry` (frozen, `extra="forbid"`, `kind: PageKind`, README listed in `files`) | `core/models/manifest.py` (T1) |
| Manifest constants | `MANIFEST_FORMAT_VERSION` (**not** `FORMAT_VERSION`), `MANIFEST_FILENAME`, `TOOL_NAME`, `README_PATH`, `README_PAGE_ID` | `core/models/manifest.py` (T1) |
| Docs state | `DocsState` (**not** `DocsSnapshot`), `ManifestStatus`, `FileStatus`, `VersionCmp`, `LegacyEntry` | `core/models/docs_state.py` (**T1**, not T6) |
| Docs-state loader | `load_docs_state`, `load_manifest`, `render_manifest`, hashing | `publishing/manifest_store.py` (T6) |
| Atomic writer | `atomic_write_text(path, content, *, mode_from=None)` | `publishing/atomic.py` (**T6**) |
| Path rules | `normalize_rel_path`, `is_within_dir` | `core/paths.py` (T1) |
| File index | `FileIndex`, `FileEntry`, `ExcludedFile`, `ExclusionReason` | `core/models/repo.py` (T1) |
| LLM repo model | `Component`, `RepoModel` | `core/models/repo.py` (T1) |
| Facts / candidates | `ProjectFacts` (+ sub-models), `ComponentCandidate`, `ComponentRole` | `core/models/facts.py` (T10) |
| Code models | `Symbol`, `Span`, `ImportGraph`, `InterfaceReport`, `DiffHunk`, `ChangedSymbol`, ... | `core/models/code.py` (T10) |
| Mode | `Mode`, `ModeDecision`, `resolve_mode(state: DocsState, ...)` | `core/modes.py` (T2) |
| Provider enum, defaults, run config | `Provider`, `DEFAULT_MODELS`, `DEFAULT_*`, `PROGRESS_LOGGER`, `RunConfig` (`docs_dir: str`) | `config.py` (T1) |
| Model-flag parsing | `ModelSpec`, `parse_model_flag`, `check_provider_flags`, `M_MODEL_EMPTY`, `M_MODEL_NO_ID`, `M_CONFLICT` | `model_flags.py` (**T3**; shared kernel) |
| Provider resolution | `ResolvedProvider`, `resolve_provider`, `API_KEY_ENV_VARS` | `infra/llm/providers.py` (T3) |
| Git / sandbox / cache | `GitRepo`, `Sandbox`, `FileSummaryCache`, `NullSummaryCache` | `infra/git.py`, `infra/sandbox.py`, `infra/cache.py` (T4) |
| FileSystem implementation | `LocalFileSystem` (over `Sandbox`) | `infra/fs.py` (T14) |
| Fake LLM | `FakeLLM`, `FakeResponse` (+ `when`), `FakeToolCall`, `FakeCall` | `infra/llm/fake.py` (T1) |
| Agent base | `AgentSpec`, `run_agent`, `subagent_tool`, `UsageSink` | `agents/base.py` (T12) |
| Budget / context | `TokenBudget`, `UsageReport`, `format_usage`, `RunContext`, `PreflightResult` | `workflow/budget.py`, `workflow/context.py` (T16) |
| CLI result contract | `RunResult`, `Outcome`, `PipelineRunner` | `workflow/result.py` (T17) |
| Test fixtures | `git_repo`, `git_commit` | `tests/conftest.py` (T1) |

Test layout for everyone: there is no `__init__.py` under `tests/`, and pytest runs with
`--import-mode=importlib`. Helpers shared between test files must be fixtures, because test
modules cannot import from `conftest.py`.
Task numbers: **T18** = preflight, **T22** = pipeline, **T14** = tools/fs, **T11** = adapter,
**T15** = tool registry.

## (b) Amendments per task

### T2 (mode resolution): 8
- Delete `DocsSnapshot`. `resolve_mode(state: DocsState, *, diff_range, force, recreate, current_version=MANIFEST_FORMAT_VERSION)`. Remove the `__post_init__` invariants and test 47 (T1 tests `DocsState`).
- Map manifest states from `state.manifest_status`: ABSENT→ABSENT, INVALID→UNOWNED, OLDER, NEWER, VALID→OURS. `current_version` is used **only** for the README marker comparison. Older-manifest test rows set `manifest_status=OLDER` directly instead of using `current_version=2`.
- `manifest_source_commit` becomes `state.manifest.source_commit`.
- Rename `EnvironmentConfigError` to `EnvError` and `FORMAT_VERSION` to `MANIFEST_FORMAT_VERSION`. Import `MANIFEST_FILENAME` from `core.models.manifest`.
- `needs_readme_clean_check = not force and readme_exists and (readme_is_foreign or README_PATH not in state.owned_paths)`. This guards a marker-without-manifest README as well (resolves T2 Q3; see (c)2).
- R3 detail strings come from the status: `manifest invalid` (INVALID) and `manifest is v{n}` (OLDER). The R1 "found" version is `state.manifest_format_version` or the marker version.
- Rebuild the test presets as `DocsState` literals with `readme_path=README_PATH` and the T1 invariants satisfied, for example: VALID needs a `Manifest`, and `file_statuses` keys must equal `owned_paths`.
- Remove Q6 (resolved: the type lives in `core/models/docs_state.py`).

### T3 (provider resolution): 5
- `EnvError` is confirmed and `ExitCode.BUDGET_EXCEEDED` is confirmed. No change.
- Create `src/readme_stack/model_flags.py` with `ModelSpec`, `parse_model_flag` and the new `check_provider_flags(provider_flag: Provider | None, model_flag: str | None) -> ModelSpec | None` (algorithm steps 1–2), plus the `M_MODEL_EMPTY`, `M_MODEL_NO_ID` and `M_CONFLICT` constants. It imports only `config` and `core.errors`.
- `providers.py` imports these names and re-exports them, and `resolve_provider` calls `check_provider_flags`. Existing tests stay unchanged.
- Add `tests/unit/test_model_flags.py`: the three T17 rows (conflict → UsageError, `(None, None)` → None, `(None, "openai:gpt-5")` → ModelSpec), plus an import guard (no `ai`, no `infra`).
- `Provider` and `DEFAULT_MODELS` are in `config.py` as assumed. Remove Q2 and Q3.

### T4 (git, sandbox, cache): 9
- The error classes exist in `core/errors.py` as listed, but **every constructor takes a single message**. T4 formats the text, for example `NotGitTopLevel(f"REPO must be the git top level (got {p}, top level is {t})")` and `InvalidRange(f"invalid range {spec!r}: {reason}")`.
- `SandboxViolation` subclasses `ToolError` (exit 1). Tool handlers can let it propagate, and the port turns it into an error tool result. Resolves Q4.
- Import `RevRange` and `CommitInfo` from `core.models.git`. Import-through `core.ports` also works. Resolves Q2.
- **Add `ls_tree(rev) -> list[str]`**: resolve the commit, run `ls-tree -r -z --full-tree <sha>`, keep `type == "blob"` entries only (drop gitlinks), `os.fsdecode`, sort. Test: files at an older commit, a deleted file absent, and a submodule gitlink excluded.
- `ChangedFile` has no `type_changed` field. Map git `T` to plain `MODIFIED`.
- The Protocols are `@runtime_checkable`, so test 20 uses `isinstance`.
- Use the root-conftest fixtures `git_repo` and `git_commit`. Do not add `tests/unit/infra/conftest.py` helpers for them. No `__init__.py` files.
- `FileSystem` is implemented by T14 in `infra/fs.py` on top of `Sandbox`. Resolves Q1.
- `head_sha()` stays `str | None` on the port. Preflight (T18) rejects an unborn HEAD (see (c)6).

### T5 (repo index): 4
- The models exist exactly as in the T5 plan §5, including `paths()`, `get()`, `__contains__` and `readme`. Validation rejects unsorted or duplicate `files`, so build them sorted.
- `is_owned_output(path, docs_dir)` normalizes `docs_dir` with `core.paths.normalize_rel_path` and checks with `is_within_dir`. The strict `normalize_path` stays as planned.
- No `tests/unit/analysis/__init__.py`.
- `id_*` is narrowed to SSH key names and `.env.example`-style templates are allow-listed, per decision (d)3 (accepted).

### T6 (markers, manifest store): 9
- **Do not create `core/models/docs_state.py`** (T1 owns it). `DocsState.manifest_path` is a property, not a field. Add the new `legacy_files` field.
- Import `MANIFEST_FORMAT_VERSION`, `MANIFEST_FILENAME`, `TOOL_NAME`, `README_PATH` and `README_PAGE_ID` from `core.models.manifest`. The `manifest_store` module may re-export `MANIFEST_FILENAME`, but must not redefine it.
- `normalize_rel_path` comes from `core.paths`. `manifest_store` re-exports it, and T6 tests 12 stay.
- `ManifestEntry.kind` is `PageKind`. An unknown kind makes the manifest INVALID. Test helpers use `PageKind.COMPONENT`/`OVERVIEW`.
- **OLDER manifests (legacy ownership):** after step 7 returns OLDER, read the ownership subset leniently. If `files` is a list, collect every object with a str `path` and a `sha256` matching `SHA256_PATTERN`, where the path passes `normalize_rel_path` unchanged and is `README_PATH` or inside the **current** `docs_dir`. Skip malformed entries with a debug log. `ManifestLoad` gets `legacy_files: tuple[LegacyEntry, ...]`. `load_docs_state` computes `file_statuses` for legacy entries through the same `file_status` rule (build a `ManifestEntry`-free variant taking `path, sha256`).
- **Create `publishing/atomic.py`** with `atomic_write_text(path, content, *, mode_from=None)`, using exactly T7 §4.5's algorithm (O_EXCL at 0o666 subject to umask, fsync, mode copy, `os.replace`, best-effort dir fsync, temp cleanup). `save_manifest` uses it (this fixes the 0600 bug). Move T7 test 31 (permissions) here.
- `compare_format_version` returns `core.models.docs_state.VersionCmp`.
- `is_our_format` means VALID and the marker equals the current version (T1 property). Resolves T6 Q2 together with T2 R1.
- Close Q1 (README in `files`: yes), Q5 (no `ManifestError`) and Q6 (`extra="forbid"`: yes).

### T7 (ChangeSet): 7
- Import `atomic_write_text` from `publishing.atomic` (T6) and delete the local definition. It may stay in `__all__` as a re-export. Test 31 moves to T6, and T7 keeps one MODIFY-keeps-mode check.
- **Owned set** = `state.owned_paths`: VALID manifest entries **or** OLDER `legacy_files` (path and sha only). OLDER-owned rows: 6, 8, 11, 15 and 17 as for VALID. Row 7 is UNCHANGED. Row 10 (hand-edited, no force) becomes SKIP **and ownership is released**, like row 16, because there is no old metadata to keep. `content=None` for an OLDER-owned path raises `ValueError` (RECREATE regenerates everything). Rewrite test 17 (OLDER): matching old pages are MODIFY or DELETE, not collisions. Closes Q1.
- Rename `replace_unowned_readme` to `replace_readme`. The README is *permitted* when `force` is set, or when `replace_readme` is set and the README is **unowned or its on-disk text has no marker**. This also permits MODIFY in rows 10 and 11 for a marker-less owned README. See (c)2.
- `DesiredFile.kind` is `PageKind`. Use `README_PATH` and `README_PAGE_ID` from core.
- Use `core.paths.normalize_rel_path` (not the T6 re-export).
- `ChangeSetConflictError` stays at exit 1. Closes Q7.
- Q3 (release ownership), Q4 (adoption needs permission), Q5 (drop the missing kept page with a warning) and Q6 (CREATE recovery manifest) are resolved as planned.

### T8 (render, links): 5
- `PageKind` = `readme`/`overview`/`component` is final (closes Q4). The README is in `DocPlan.pages` (id `readme`, path `README.md`).
- Use `DocPlan.readme`, `DocPlan.subpages`, `DocPlan.page(id)` and `FileIndex.paths()` from T1.
- `PageSpec` lists (`source_paths`, `sections`, `links_to`) are `list[str]` with defaults. `id` must match `PAGE_ID_PATTERN`, so fixture ids must be slugs.
- No `tests/unit/publishing/__init__.py`. Templates stay in `publishing/templates/` (T8 creates it).
- Broken links: auto-apply the deterministic `suggestion`, then drop what is still broken (text kept). Code paths stay report-only and feed the repair round. Per decision (d)5, option (b), accepted.

### T9 (impact mapping): 6
- Map `ImpactReport` fields exactly as in T1: `affected_pages`, `readme_affected`, `reasons`, `dropped_pages`, `proposed_pages`, `leftover_notes`. Closes Q7.
- Replace the local owned-output helper with `core.paths.is_within_dir(path, docs_dir) or path == README_PATH`. Closes Q6.
- Use `README_PAGE_ID` and `PageKind.README` from core, and delete the local `README_PAGE_ID`/`README_KIND` constants. The test helper `entry(..., kind="page")` becomes `PageKind.COMPONENT`.
- `head_paths` comes from `Git.ls_tree(changes.head)` in the impact stage. Closes Q1.
- `ChangedFile` has no `type_changed` field.
- No `tests/unit/analysis/__init__.py`.

### T10 (analysis stubs): 6
- Owning `core/models/facts.py` and `core/models/code.py` is **accepted** (closes Q2). T1 ships no `ProjectFacts` placeholder, so delete the `repo.py` re-export edit. T10 does not touch `repo.py`.
- Models follow T1 conventions: frozen, `extra="forbid"`, tuples, deterministic.
- `FakeParser`, `write_tree` and `index_for` are exposed as **fixtures** in `tests/unit/analysis/conftest.py` (for example `fake_parser_cls` returns the class). Test modules cannot import them. No `__init__.py`.
- Test 39: keep the network, subprocess and `tree_sitter` bans. Layer rules are also covered globally by T1 `test_layering.py`, and both must pass.
- Test 40: `tests/fixtures/repos/` is empty after T1, so parametrize over the tmp fixtures, plus any checked-in repos found at runtime.
- **Defer the extras** per decision (d)8 (user override). Remove dependency extraction and the real `parse_diff_hunks`/`symbols_for_hunks` implementation, plus the extra parser Protocol methods (`resolve_import`, `references`) and the component-candidate unwrap heuristics. Keep their typed signatures and models where the brief needs them; they return empty results. Keep the `interface_extractors` name from the parent plan. Drop the tests for the removed behaviour. Move the removed items to the later "tools in detail" step.

### T12 (prompts, agent base): 5
- `Usage` lives in `core/models/usage.py` and is re-exported by `core.ports`. Importing it from `core.ports` is fine. Closes Q4.
- The error set in `core/errors.py` is as listed. `ToolError` is the base of `SandboxViolation`/`FileAccessError`, and `subagent_tool` still converts `LLMError`/`AgentError` into `ToolError`.
- T1 provides `FakeLLM` per T12 §5.3 plus: `FakeResponse.when` routing, `calls_for()`, `pending()`, `assert_exhausted()`, and invalid dict output raises `LLMOutputError` with usage. Error tool-result texts: `error: unknown tool '<n>'`, `error: invalid arguments: ...`, `error: <msg>`. Test 28 asserts `is_error` only.
- T12 creates the `prompts/` package (T1 does not). `agents/__init__.py` already exists.
- Unused prompt variables raise (strict), per decision (d)4 (accepted). Q2 (tuple return) is closed: keep `tuple[T, Usage]`.

### T16 (context, budget): 6
- The task references change: the "T17 pipeline/stages" become **T22** (pipeline, stages, the `call_agent` helper) and **T18** (preflight). T17 is the CLI.
- `ExitCode.BUDGET_EXCEEDED` is confirmed (closes Q5).
- `DocsState` comes from `core.models.docs_state` (T1). Import `ProjectFacts` (`core.models.facts`, T10) and `ModeDecision` (`core.modes`, T2) **under `TYPE_CHECKING` only**, so T16 depends on T1 alone. The dataclasses use `from __future__ import annotations`.
- `PreflightResult.head_sha: str`, not Optional, because preflight rejects an unborn HEAD ((c)6).
- `FileSystem` is the T1 port shape (reads only).
- Q1 (count cached tokens fully), Q2 (exit 5 wins), Q3 (`--max-tokens 0` → exit 2) and Q4 (accept the gap) are resolved as proposed. Q6 is dropped.

### T17 (CLI): 7
- Import `check_provider_flags` and the messages from `readme_stack.model_flags` (T3). **Drop** the conditional edit of `infra/llm/providers.py`. The CLI never imports `infra`. Closes Q1.
- `RunConfig.docs_dir` is `str`. `_validate_docs_dir` returns the normalized `str` via `core.paths.normalize_rel_path` after its own checks. Update tests 1 and 12 to expect strings.
- Delete `UsageLike`. Use `RunResult.usage_by_agent: Mapping[str, Usage]` with `Usage` from `core.models.usage`.
- The exit-3 class is `EnvError`, and `BUDGET_EXCEEDED` is 5 (closes Q5).
- Replace T1's stub `cli/app.py` wholesale and delete `tests/unit/cli/test_app_stub.py`. Keep T1's `tests/unit/cli/test_version.py` green, including `--bogus` → 2.
- `RunConfig.__post_init__` raises `ValueError` for invalid values. `build_config` must validate first, so `ValueError` never reaches the user.
- A REPO that does not exist or is not a directory → exit 2, per decision (d)6. Print the `tokens:` usage line on exits 1 and 5, using the `usage_report` T22 attaches to the error, per decision (d)7. Both accepted. Q4: keep the full block.

## (c) Cross-task behaviour conflicts and resolutions

1. **Format bump orphans old pages (T2/T6/T7).** RECREATE after a format bump could not see the old ownership, so every old page was a collision that needed `--force`. **Fix:** a permanent ownership contract (`files[].path` and `sha256` never change meaning). T6 reads `legacy_files` from OLDER manifests, and T7 treats them as owned. Hand edits are detected by hash, and old pages dropped from the new plan are deleted if clean.
2. **README replaced vs preserved (T2/T7).** "Foreign README → replace if tracked and clean" conflicted with "owned + hash mismatch → preserve unless `--force`" when a user strips our marker from a README that the manifest lists (RECREATE would then loop forever). **Rule:** hash ownership governs a README that still carries a marker. A marker-less README is foreign, and the git-clean guard governs it, even if it is listed. A README whose ownership cannot be proven by hash (marker but no valid or legacy manifest) is also guarded. Preflight (T18) runs the guard when `needs_readme_clean_check` is set, and passes `replace_readme = mode in {CREATE, RECREATE}` to T7.
3. **Two atomic writers (T6/T7).** One helper, `publishing/atomic.py` (owner T6, T7's algorithm). The manifest keeps umask permissions.
4. **`is_our_format` defined twice (T2/T6).** Canonical: VALID and marker == current version (T1 property). T2 must not treat an older marker as ours.
5. **cli → infra import (T17).** The pure flag parsing moves to the shared-kernel `model_flags.py` (T3). `config.py` and `model_flags.py` may be imported by `cli`, `infra` and `workflow`, never by `core`. This is enforced by the T1 layering test.
6. **Unborn HEAD (T4/T6/T16).** A manifest requires a `source_commit`. Preflight raises `RepoStateError` (4): "repository has no commits; commit at least once before running readme-stack".
7. **Parallel page writers vs the FIFO FakeLLM (T1/T12).** Concurrent calls with the same agent name are routed through `FakeResponse.when` (a predicate on the user message).
8. **FileSystem port vs index timing (T4/T16/T14).** `RunContext.fs` exists before preflight builds the index, so the port only confines and reads. `tools/fs.py` enforces FileIndex membership (which already excludes secrets, binaries and owned outputs).
9. **Usage output duplication (T16/T17).** The CLI summary (T17, from `usage_by_agent`) is the only user-facing usage output. T22 logs `format_usage(report)` at DEBUG.
10. **Duplicated classification tables (T9/T10).** Lockfile and build-file sets exist in both `analysis/impact.py` and `analysis/facts.py`. They are accepted for wave 2 because the tasks run in parallel. A follow-up cleanup makes T9's sets canonical and has T10 import them.
11. **Owned-output semantics (T5/T9).** These differ on purpose. T5 keeps the README in the index (it is Explorer input) and excludes docs-dir files. T9 ignores changes to both. Both use `core.paths.is_within_dir`.
12. **`SandboxViolation` exit code (T4).** It is a `ToolError` (exit 1), so it reaches the model as a tool error and never aborts a run.
13. **Minor items resolved as proposed:** a `--model provider:id` prefix counts as a provider choice (T3 Q1), the prefix is case-insensitive (T3 Q4), and a single rev `A` means `A..HEAD` (T4 Q3). A `manifest.docs_dir` mismatch is INVALID, which leads to REFUSE unless `--force` (T6 Q3). An empty docs dir with no manifest still REFUSEs (T6 Q4). Subagent failures become tool errors (T12 Q5). Hand-edited pages dropped from the plan are released (T7 Q3).

## (d) Decisions for the user (resolved 2026-09-24)

1. **Newer README marker** (no manifest, or an older one) → exit 3, like a newer manifest (T2 Q1). Options: (a) exit 3; (b) treat the README as foreign. **Recommend (a):** never overwrite a newer tool's output. **Resolved: (a) exit 3.**
2. **`--diff` over docs in an older format** → exit 4 "run without --diff first" (T2 Q2). Options: (a) exit 4; (b) silently RECREATE. **Recommend (a):** `--diff` promises a scoped, cheap run. **Resolved: (a) exit 4.**
3. **Secret denylist:** narrow `id_*` to SSH key names, and allow-list `.env.example`-style templates (T5 Q1/Q2). Options: (a) narrow and allow-list; (b) the literal `id_*` and `.env*`. **Recommend (a):** otherwise `id_utils.py` and similar files vanish from the docs. The templates contain no secrets. **Resolved: (a) narrow and allow-list.**
4. **Strict unused prompt variables** (T12 Q1). Options: (a) an unused variable raises; (b) extras are ignored, so one shared variable set can be passed to every prompt. **Recommend (a):** typos fail in tests. **Resolved: (a) strict.**
5. **Broken links and code paths** (T8 Q1/Q2). Options: (a) links are dropped (text kept) and code paths are report-only; (b) additionally auto-apply the deterministic `suggestion` (repo-root reading) before dropping; (c) code paths are also un-backticked. **Recommend (b):** it fixes the most common LLM mistake for free. Code paths stay report-only and feed the repair round. **Resolved: (b) auto-fix, then drop.**
6. **REPO that does not exist or is not a directory** (T17 Q2). Options: exit 2 (bad argument) or 3 (environment). **Recommend 2.** A directory that is not the git top level stays 3. **Resolved: exit 2.**
7. **Token usage on failure** (T17 Q3). Options: (a) print nothing extra; (b) T22 attaches the budget report to the escaping `ReadmeStackError` (an optional `usage_report` attribute set in the pipeline's top-level handler), and the CLI prints the `tokens:` line on exits 1 and 5. **Recommend (b)**, especially for exit 5. **Resolved: (b) print usage on failure.**
8. **T10 extras beyond the brief:** dependency extraction from pyproject/package.json, real `parse_diff_hunks`/`symbols_for_hunks`, and component-candidate unwrap heuristics (T10 Q3/Q4). Options: keep them or stub them. **Recommend keeping** dependency extraction and diff-hunk parsing (small, pure, well tested). **Resolved: defer all extras (user override of the recommendation)**; see the T10 amendments in (b).
