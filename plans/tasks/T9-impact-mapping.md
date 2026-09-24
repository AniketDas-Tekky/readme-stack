# T9 — Impact mapping (`analysis/impact.py`)

Parent plan: [`plans/readme-generation.md`](../readme-generation.md) (Pipeline step 2 "Impact", Verification "impact mapping").
Depends on: T1 (`core/models/impact.py`: `ChangeStatus`, `ChangedFile`, `ChangedFiles`, `ImpactReport`;
`core/models/manifest.py`: `Manifest`, `ManifestEntry`). Consumes the output of T4's
`Git.changed_files()` and the T6 manifest. Deterministic: no LLM, no network, no subprocess, no
filesystem access.

## 1. Goal
Given the changed files of a commit range and the current manifest, compute deterministically:
- which **pages** are affected, using each manifest entry's `source_paths` globs, with the glob and
  path that caused each hit;
- whether the **README** must be regenerated, and why (top-level dirs added or removed, build/entry
  files changed, pages orphaned, README entry's own globs);
- the **leftover** changes that nothing claimed, which the ImpactAnalyst agent classifies;
- the **ignored** changes: our own outputs, lockfiles, and denylisted files. They are never sent to an agent.

It also exposes the glob engine (`compile_glob`, `glob_matches`) so the Outline validator ("globs
match") uses exactly the same semantics, and an `ImpactMatcher` extension point for the later
symbol-based mapping (`diff_symbols`).

## 2. Files
| File | Change |
|---|---|
| `src/readme_stack/analysis/__init__.py` | create empty if T1/T5 have not |
| `src/readme_stack/analysis/impact.py` | new |
| `tests/unit/analysis/__init__.py` | only if the test layout uses packages (follow T1) |
| `tests/unit/analysis/test_impact.py` | new |

No changes to `core/` (owned by T1). Any gap is listed in section 5.

## 3. Public interface
```python
from collections.abc import Callable, Collection, Iterable, Sequence
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable
import re

from pydantic import BaseModel, ConfigDict

from readme_stack.core.models.impact import ChangedFile, ChangedFiles, ChangeStatus, ImpactReport
from readme_stack.core.models.manifest import Manifest, ManifestEntry

README_PAGE_ID: Final = "readme"          # matches T6's README entry page_id
README_KIND: Final = "readme"

BUILD_FILE_NAMES: Final[frozenset[str]]    # exact basenames, section 4.3
BUILD_FILE_GLOBS: Final[tuple[str, ...]]   # basename globs (fnmatchcase), section 4.3
LOCKFILE_NAMES: Final[frozenset[str]]      # section 4.4


# ---- glob engine -----------------------------------------------------------------
class GlobError(ValueError): ...

def compile_glob(pattern: str) -> re.Pattern[str]: ...   # lru_cache(maxsize=4096); GlobError
def glob_matches(pattern: str, path: str) -> bool: ...   # compile_glob(pattern).fullmatch(path)
def validate_glob(pattern: str) -> str | None: ...       # None if valid, else error message


# ---- classifiers (pure, name-only) -------------------------------------------------------
def is_build_file(path: str) -> bool: ...
def is_lockfile(path: str) -> bool: ...
def top_level_dir(path: str) -> str | None: ...          # "src/a.py" -> "src"; "a.py" -> None


# ---- result types ----------------------------------------------------------------
class MatchReason(StrEnum):
    GLOB = "glob"
    # SYMBOL = "symbol"   # added by the diff_symbols task, not here

class ReadmeTriggerKind(StrEnum):
    TOP_LEVEL_DIR_ADDED = "top_level_dir_added"
    TOP_LEVEL_DIR_REMOVED = "top_level_dir_removed"
    BUILD_FILE = "build_file"              # detail = status letter(s)
    PAGE_ORPHANED = "page_orphaned"        # path = page file path, detail = page_id
    README_SOURCE = "readme_source"        # README entry's own source_paths matched

class IgnoreReason(StrEnum):
    OWNED_OUTPUT = "owned_output"
    LOCKFILE = "lockfile"
    EXCLUDED = "excluded"                  # caller's `exclude` predicate (secret denylist)

_FROZEN = ConfigDict(frozen=True, extra="forbid")

class PageHit(BaseModel):
    model_config = _FROZEN
    page_id: str
    path: str                  # the path that matched (old or new side of a rename)
    status: ChangeStatus
    reason: MatchReason
    pattern: str | None = None # the glob, for GLOB hits

class ReadmeTrigger(BaseModel):
    model_config = _FROZEN
    kind: ReadmeTriggerKind
    path: str                  # dir name, file path, or page path
    detail: str = ""

class IgnoredChange(BaseModel):
    model_config = _FROZEN
    change: ChangedFile
    reason: IgnoreReason

class ImpactMapping(BaseModel):
    model_config = _FROZEN
    base: str
    head: str
    affected_pages: tuple[str, ...] = ()       # page_ids, sorted, README excluded
    hits: tuple[PageHit, ...] = ()
    readme_triggers: tuple[ReadmeTrigger, ...] = ()
    orphaned_pages: tuple[str, ...] = ()       # subset of affected_pages
    leftovers: tuple[ChangedFile, ...] = ()
    ignored: tuple[IgnoredChange, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def readme_affected(self) -> bool: ...     # bool(readme_triggers)
    @property
    def is_noop(self) -> bool: ...             # no hits, no triggers, no leftovers


# ---- extension point -------------------------------------------------------------
@runtime_checkable
class ImpactMatcher(Protocol):
    name: str
    def match(self, change: ChangedFile, pages: Sequence[ManifestEntry]) -> Iterable[PageHit]: ...

class GlobMatcher:                              # the built-in matcher; implements ImpactMatcher
    name: str = "glob"
    def __init__(self, pages: Sequence[ManifestEntry]) -> None: ...  # precompiles, records bad globs
    @property
    def warnings(self) -> tuple[str, ...]: ...
    def match(self, change: ChangedFile, pages: Sequence[ManifestEntry]) -> Iterable[PageHit]: ...
    def page_has_files(self, page_id: str, paths: Iterable[str]) -> bool: ...


# ---- entry points ----------------------------------------------------------------
def map_changes(
    changes: ChangedFiles,
    manifest: Manifest,
    *,
    head_paths: Collection[str],               # all tracked file paths at changes.head
    readme_path: str = "README.md",
    exclude: Callable[[str], bool] | None = None,   # e.g. repo_index.is_denylisted
    extra_matchers: Sequence[ImpactMatcher] = (),
) -> ImpactMapping: ...

def initial_report(mapping: ImpactMapping) -> ImpactReport: ...
    # deterministic ImpactReport used as-is when there are no leftovers (analyst skipped),
    # or as the seed that the impact stage merges the analyst's output into
```

## 4. Detailed behavior and edge cases

### 4.1 Glob semantics
Why custom: `fnmatch` lets `*` cross `/` (`src/*.py` would match `src/a/b.py`), has no `**`, and
`fnmatch.fnmatch` applies `os.path.normcase` (case-insensitive on Windows). `PurePath.match` in 3.12
has no recursive `**` and matches from the right. `PurePath.full_match` and `glob.translate` only
exist in 3.13+. The translator is about 50 lines (pattern → anchored regex), is cached, and uses
gitignore/wcmatch-style semantics that LLM-written `source_paths` usually assume.

| Pattern element | Meaning | Example: matches / does not match |
|---|---|---|
| literal chars | exact, **case-sensitive** on every OS | `src/app.py` / `src/App.py` |
| `*` | any run of chars except `/` (can be empty; matches dotfiles) | `src/*.py`: `src/a.py`, `src/.x.py` / `src/a/b.py` |
| `?` | exactly one char except `/` | `a?.py`: `ab.py` / `a/.py` |
| `[abc]`, `[a-z]`, `[!x]`, `[^x]` | one char from the class, never `/` | `v[0-9].md`: `v1.md` |
| unclosed `[` | literal `[` (same as fnmatch) | `a[b`: `a[b` |
| `**` as a whole segment, leading (`**/x`) | zero or more leading dirs | `**/conftest.py`: `conftest.py`, `a/b/conftest.py` |
| `**` as a whole segment, middle (`a/**/b`) | zero or more dirs | `src/**/x.py`: `src/x.py`, `src/a/b/x.py` |
| `**` as a whole segment, trailing (`a/**`) | everything below `a/`, at any depth (not `a` itself) | `src/**`: `src/a/b.py` / `src` |
| `**` alone | every path | |
| `**` inside a segment (`a**b`) | same as `*` | `a**b`: `axyb` / `ax/yb` |
| trailing `/` (`src/cli/`) | same as `src/cli/**` | |
| pattern **without any magic** (`src/cli`) | the exact file **or** anything below that dir | `src/cli`: `src/cli`, `src/cli/app.py` / `src/client.py` |
| leading `./` | stripped | `./src/**` = `src/**` |
| `{a,b}` braces, `\` escapes | **not supported**: literal chars (Outliner prompt says so) | |
| empty, leading `/`, a `..` segment, NUL, `\` | `GlobError` | |

- Paths are compared as given: repo-relative POSIX strings, which is how T4 and T6 store them. They are not normalized.
- `compile_glob` builds `re.compile("(?s:" + body + r")\Z")` and matches with `fullmatch`.
  Consecutive `/` in a pattern are collapsed before translation.
- The "no magic ⇒ also a directory prefix" rule only applies to patterns without `*?[`, so a
  pattern that names a directory by its literal path still works. `src/*` still matches only
  direct children.

### 4.2 `map_changes` algorithm
Inputs: `docs_dir = manifest.docs_dir` (T6 guarantees it equals `--docs-dir`). `pages` = manifest
entries with `kind != "readme"`. `readme_entry` = the entry with `kind == "readme"`, if any.

1. **Pre-filter each change** (section 4.4). The result is either an `IgnoredChange` or an *effective*
   change. A one-sided rename or copy may be reduced to an A or a D.
2. **Page hits.** For each effective change, run `GlobMatcher` and then each of `extra_matchers`,
   in order. Every hit's `page_id` must be a page id in the manifest and its `path` must be one of the
   change's paths. Otherwise the hit is dropped and a warning is added. Duplicate hits (same
   page_id, path, reason and pattern) collapse.
3. **README triggers** (section 4.3). These come from the effective changes and from `head_paths`.
4. **Orphaned pages.** An affected page whose valid patterns match **none** of the filtered
   `head_paths` is orphaned: all of its sources are gone. This emits `PAGE_ORPHANED` (so the README
   changes because a page will be removed). Pages with no valid pattern are never orphaned.
5. **Leftovers.** An effective change is a leftover iff its *current path* (`path`. For D this is
   the deleted path, and for R/C it is the new path) has no page hit **and** did not raise a
   `BUILD_FILE` or `README_SOURCE` trigger. A rename whose old path hit page X but whose new path
   matches nothing gives a hit for X **and** a leftover, so the analyst can extend X's
   `source_paths` or propose a page. A `TOP_LEVEL_DIR_*` trigger does not claim files: files in a new
   top-level dir stay leftovers so the analyst can propose pages.
6. **Assemble** `ImpactMapping` with the ordering from section 4.7.

Complexity: O(changes × patterns) for hits, and O(|head_paths| × pages) for the orphan check, using one
combined alternation regex per page. The orphan check only runs for affected pages.

### 4.3 README triggers (full list)
**T-DIR: top-level dir added or removed.** `head_files` = filtered `head_paths`. Owned outputs,
`exclude`d paths and the README are removed. Then
`base_files = (head_files − {current paths of A, R, C}) ∪ {paths of D, old paths of R}`, using effective
changes. `dirs(S) = {top_level_dir(p) for p in S} − {None}`, minus names starting with `.`
(`.github`, `.vscode`, `.devcontainer` are tooling, not architecture). Each dir in
`dirs(head) − dirs(base)` gives `TOP_LEVEL_DIR_ADDED`, and each dir in `dirs(base) − dirs(head)` gives
`TOP_LEVEL_DIR_REMOVED`. Consistency warnings (no raise): an A/M/R-new/C-new path missing from
`head_paths`, or a D/R-old path present in it.

**T-BUILD: build/entry file changed.** `is_build_file(p)` is true when `basename(p) in BUILD_FILE_NAMES`
or it matches any `BUILD_FILE_GLOBS` with `fnmatch.fnmatchcase`:

| Ecosystem | Names / globs |
|---|---|
| Python | `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, `Pipfile`, `environment.yml` |
| JS/TS | `package.json`, `deno.json`, `deno.jsonc` |
| Go | `go.mod`, `go.work` |
| Rust | `Cargo.toml` |
| JVM | `pom.xml`, `build.gradle`, `build.gradle.kts`, `settings.gradle`, `settings.gradle.kts`, `build.sbt` |
| Ruby / PHP / Elixir / Dart / Swift / Zig | `Gemfile`, `*.gemspec`, `composer.json`, `mix.exs`, `pubspec.yaml`, `Package.swift`, `build.zig` |
| .NET / Haskell | `*.csproj`, `*.fsproj`, `*.sln`, `*.cabal`, `stack.yaml` |
| C/C++ / Bazel | `CMakeLists.txt`, `meson.build`, `configure.ac`, `WORKSPACE`, `WORKSPACE.bazel`, `MODULE.bazel` |
| Containers / processes | `Dockerfile`, `Dockerfile.*`, `*.Dockerfile`, `Containerfile`, `docker-compose.yml`, `docker-compose.yaml`, `compose.yml`, `compose.yaml`, `Procfile` |
| Task runners | `Makefile`, `GNUmakefile`, `justfile`, `Justfile`, `Taskfile.yml`, `Rakefile` |
| Actions / entry | `action.yml`, `action.yaml` |

Rules:
- **Root-level** build file (no `/` in the path): any status (A, M, D, R, C) triggers.
- **Nested** build file: only **A, D, R, C** trigger (a sub-package or component appears, disappears
  or moves). Nested **M** does not trigger, because monorepo sub-manifests churn. It is mapped by globs,
  or it becomes a leftover.
- For R, the old path is evaluated as a removal and the new path as an addition. Both sides can
  trigger.
- `detail` = the status letter (`"M"`, `"A"`, `"D"`, `"R"`, `"C"`). `path` = the path that triggered.
- Entry *source* files (`main.py`, `main.go`, `__main__.py`) are **not** triggers. They belong to pages,
  and the README consumes page summaries (open question 2).

**T-PAGE: pages added or removed.** T9 emits `PAGE_ORPHANED` (section 4.2 step 4). Pages *added or dropped
by the ImpactAnalyst* are applied by the impact stage (another task) when it merges the analyst
output into the `initial_report`. That stage sets the README flag when the final set of pages
differs from the manifest's pages.

**T-README-SRC.** If the README entry has non-empty `source_paths`, a match on them gives
`README_SOURCE` (`detail` = pattern), and that path counts as claimed.

**Lockfiles are not triggers** (section 4.4).

### 4.4 Pre-filter: ignored changes and one-sided reduction
A path is **ignored** if, checked in this order:
1. it is owned output: `path == readme_path`, `path == docs_dir` or `path.startswith(docs_dir + "/")`
   (this covers every page and `.readme-stack.json`). This is a local 3-line helper with the same
   semantics as T5's `is_owned_output`. Reconcile to import it if T5 lands first (see section 5);
2. `exclude(path)` is true, giving `EXCLUDED` (the stage passes T5's `is_denylisted`, so secret-looking
   files never reach an agent);
3. `is_lockfile(path)`, giving `LOCKFILE`. `LOCKFILE_NAMES`: `uv.lock`, `poetry.lock`, `Pipfile.lock`,
   `pdm.lock`, `package-lock.json`, `npm-shrinkwrap.json`, `yarn.lock`, `pnpm-lock.yaml`,
   `bun.lockb`, `bun.lock`, `deno.lock`, `Cargo.lock`, `go.sum`, `Gemfile.lock`, `composer.lock`,
   `mix.lock`, `pubspec.lock`, `Package.resolved`, `flake.lock`, `packages.lock.json`. They are ignored
   because they churn on every dependency bump and never change architecture (direct dependencies
   show up via the manifest file).

Per status (T4 maps git's `T` to `MODIFIED`, so a type change is a plain M):
| Status | Paths examined | Rule |
|---|---|---|
| A, M | `path` | ignored → `IgnoredChange`; else effective as is |
| D | `path` (the deleted file) | same. A deleted source still hits its page, because the page must drop that content |
| R | `old_path` and `path` | both ignored → `IgnoredChange` (reason of the new side). Old side only ignored → effective `ChangedFile(A, path)`. New side only ignored → effective `ChangedFile(D, old_path)`. Neither → effective R, and **both** sides are matched |
| C | `path` only for matching. `old_path` is unchanged by a copy, so it is not a hit | new side ignored → ignored. Otherwise effective C (old side ignored or not) |

- `similarity` is carried through but not used.
- `IgnoredChange.change` keeps the **original** change, not the reduced one.
- A `ChangedFile` with R/C and `old_path is None` is malformed. It is treated as A of `path` and a warning is added.

### 4.5 Extension point (symbol mapping, not implemented)
`ImpactMatcher.match(change, pages)` returns hits for one effective change. Hits from all matchers
are unioned. A change is claimed if **any** matcher hits its current path. The future
`diff_symbols` matcher is built by the stage beforehand, with the diff hunks, symbol index and
per-page symbol lists captured in its instance, so this signature does not change. It adds
`MatchReason.SYMBOL`. Matchers must be pure and deterministic, and their output order does not matter
(section 4.7 sorts). No registry or plugin loading: just the `extra_matchers` argument.

### 4.6 Invalid globs and odd manifests
- An invalid pattern (a `GlobError`) is skipped. It adds the warning `"page '<id>': invalid glob '<p>': <msg>"`
  and logs it at `WARNING` via `logging.getLogger(__name__)`. The run does not fail, because the manifest
  is already validated by T6 and the Outline validator rejects bad globs upstream.
- A page with `source_paths == []` never gets glob hits and is never orphaned.
- A file that matches several pages hits **all** of them. A file that matches one page hits only that page.
- An empty `changes.files` gives an empty mapping (`is_noop` True). The stage turns this into a no-op.

### 4.7 Deterministic output ordering
The result is identical for any permutation of `changes.files`, `manifest.files`, `source_paths` and
`head_paths`:
- `affected_pages`, `orphaned_pages`: sorted `page_id` strings, deduplicated.
- `hits`: sorted by `(page_id, path, reason, pattern or "")`.
- `readme_triggers`: deduplicated, sorted by `(kind, path, detail)` (StrEnum value order).
- `leftovers`: sorted by `(path, old_path or "", status)`.
- `ignored`: sorted by `(change.path, change.old_path or "", reason)`.
- `warnings`: deduplicated, sorted.

### 4.8 `initial_report`
This maps `affected_pages` to the report's affected page ids, sets `readme_affected`, and copies the triggers
and hit summaries into reasons. Analyst-only fields (dropped or proposed pages, leftover
classification) are left at their defaults. It is pure and does not validate against the manifest.

## 5. Requires from T1 / T4 / T6
From T1 `core/models/impact.py` (the same shape T4's plan assumes):
```python
class ChangeStatus(StrEnum): ADDED="A"; MODIFIED="M"; DELETED="D"; RENAMED="R"; COPIED="C"
class ChangedFile(BaseModel, frozen=True):
    status: ChangeStatus; path: str; old_path: str | None = None; similarity: int | None = None
class ChangedFiles(BaseModel, frozen=True):
    base: str; head: str; files: tuple[ChangedFile, ...] = ()
```
- `path` is the new path for R/C, and `old_path` is set only for R/C. An optional `type_changed: bool` is
  tolerated and ignored.
- `ImpactReport` (minimum assumed, reconcile names with T1):
  `affected_pages: list[str]`, `readme_affected: bool`, `reasons: dict[str, list[str]]`
  (key = page_id or `"readme"`), plus analyst fields with defaults (`dropped_pages`,
  `proposed_pages`, `leftover_notes`). If T1's shape differs, only `initial_report` changes.

From T1 `core/models/manifest.py` (as in T6 section 5): `Manifest.docs_dir: str`,
`Manifest.files: list[ManifestEntry]`, and `ManifestEntry.page_id`, `.path`, `.kind` (includes
`"readme"`), `.source_paths: list[str]`.

From T4: `Git.changed_files(rng) -> ChangedFiles` (from `diff -z --name-status -M`). The stage supplies
`head_paths`. When `changes.head` is the HEAD commit, `git ls-files` (tracked) is fine. Otherwise a
tree listing at `head` is needed (open question 1).

From T6: the manifest is VALID (paths normalized, docs_dir matches, README entry `page_id == "readme"`).

From T5 (optional, avoids duplication): `is_owned_output(path, docs_dir)` and `is_denylisted(path)`.
T9 does **not** import T5, so the two tasks stay parallel. The stage passes `is_denylisted` as `exclude`.

## 6. Test plan (`tests/unit/analysis/test_impact.py`)
Pure tests with no git. Helpers: `cf(status, path, old=None)`, `entry(page_id, path, globs, kind="page")`,
`manifest(*entries, docs_dir="docs/architecture")` (README entry with `kind="readme"`,
`source_paths=[]` included by default).

Glob engine (parametrized table, one case per row of section 4.1):
1. `*` does not cross `/`. `*` matches dotfiles.
2. `?` and character classes (`[a-z]`, `[!x]`, `[^x]`). An unclosed `[` is literal.
3. Leading `**/` matches at root and at depth. Middle `/**/` matches zero dirs. Trailing `/**` does not
   match the dir itself. `**` alone matches everything.
4. `a**b` behaves like `*`.
5. Trailing `/` equals `/**`. A no-magic literal matches the exact file and the dir prefix but not
   `src/client.py` for `src/cli`.
6. Case-sensitive (`README.MD` does not match `README.md`). A leading `./` is stripped. Braces are literal.
7. `GlobError` for `""`, `/abs`, `a/../b`, `a\\b`, and a NUL char. `validate_glob` returns the message.
8. `compile_glob` is cached (the same object is returned).

Mapping (acceptance):
9. A file matching one page's glob gives exactly that page in `affected_pages`, one `PageHit` with the
   pattern, and no leftovers or README triggers.
10. A file matching two pages' globs hits both.
11. An unmatched file is returned in `leftovers`, and `affected_pages` is empty.
12. Rename, old path matches page A and new path matches page B: both pages are affected, with no leftover.
13. Rename, old path matches A and new path matches nothing: A is affected and the change is in leftovers.
14. Rename, old path matches nothing and new path matches A: A is affected, with no leftover.
15. Copy: only the new path is matched (an `old_path` matching page A does not hit A).
16. Delete of a matched file hits its page.
17. `extra_matchers`: a fake matcher claims an otherwise-leftover file, so it is not a leftover. A hit
    with an unknown page_id is dropped with a warning.

README triggers:
18. Root `pyproject.toml` M gives `BUILD_FILE` "M" and is not a leftover. The same holds, parametrized, for `package.json`,
    `go.mod`, `Cargo.toml`, `Dockerfile`, `Dockerfile.dev`, `Makefile`, `action.yml`, `x.csproj`.
19. Nested `pkg/sub/package.json` M: no trigger (leftover if unmatched). Nested A or D: triggers.
20. A rename of a root build file emits triggers for both paths.
21. A new top-level dir (A `newpkg/a.py`, not in base) gives `TOP_LEVEL_DIR_ADDED newpkg`, and the file stays a leftover.
22. A new file in an existing dir (another head path in the same dir) gives no dir trigger.
23. Deleting the last file of a top-level dir gives `TOP_LEVEL_DIR_REMOVED`. Deleting one file of several gives none.
24. A dot dir (`.github/workflows/ci.yml` added into a new `.github`) gives no dir trigger.
25. A page whose only glob's files were all deleted gives `orphaned_pages` plus `PAGE_ORPHANED`.
26. The README entry with `source_paths=["docs-src/**"]` gives `README_SOURCE` and claims the file.
27. Consistency warning when an A path is missing from `head_paths`.

Ignored:
28. Changes to `README.md`, `docs/architecture/x.md` and `docs/architecture/.readme-stack.json` go to `ignored`
    (`OWNED_OUTPUT`). They produce no hits, no leftovers, and no dir trigger for `docs`.
29. Lockfiles (`uv.lock`, `package-lock.json`, `go.sum`) give `LOCKFILE`, with no README trigger.
30. The `exclude` predicate gives `EXCLUDED`.
31. One-sided rename: `docs/architecture/a.md → notes/a.md` is evaluated as A of `notes/a.md`, and
    `src/x.py → .env` (excluded) as D of `src/x.py`. `IgnoredChange` keeps the original change.

Robustness and determinism:
32. An invalid glob in one page adds a warning, and other pages still match.
33. A page with empty `source_paths` is never hit and never orphaned.
34. Empty `ChangedFiles` gives `is_noop`, and `base`/`head` are carried through.
35. Determinism: shuffle `changes.files`, `manifest.files`, `source_paths` and `head_paths` (fixed-seed
    `random.Random`, 20 permutations). The results are `==` and `model_dump_json()` is byte-identical.
36. R/C with `old_path=None` is treated as A, with a warning.
37. `initial_report` maps affected pages and `readme_affected` (adapt to T1's field names).
38. `ImpactMapping` and the hit models are frozen (assignment raises `ValidationError`).

Run: `uv run ruff check && uv run ruff format --check && uv run pytest tests/unit/analysis`.

## 7. Out of scope / deferred
- Symbol-based mapping (`diff_symbols`, `MatchReason.SYMBOL`). Only the `ImpactMatcher` hook is included.
- The ImpactAnalyst agent, the merge of its output, and the README flag for analyst-added or dropped pages
  (impact stage task).
- Getting `head_paths` and the diff (T4 plus the stage). The full-review fallback when `source_commit` is
  unreachable (the stage marks every page affected without calling T9).
- Uncommitted worktree changes (T4 compares commits only).
- Brace expansion, escapes, and negated (`!`) globs in `source_paths`.
- Content-aware triggers, e.g. only `[project.scripts]` or dependency edits in `pyproject.toml`.

## 8. Open questions
1. **Head tree listing:** `DIFF_UPDATE` with `--diff A..B` where `B != HEAD` needs files at `B`.
   Proposal: T4 adds `Git.ls_tree(rev) -> list[str]` (`ls-tree -r -z --name-only <rev>`). Until then
   the stage passes `ls_files()` and accepts the inaccuracy (the consistency warnings will show it).
2. **Entry source files** (`main.py`, `__main__.py`, `main.go`, `cmd/*/main.go`) as README triggers?
   Currently no, because pages own them. Adding them would be a small change to `is_build_file`.
3. **Nested build manifests:** should nested M trigger when the nested dir is a detected component
   (depth-1 dir such as `frontend/package.json`)? Currently no.
4. **Dot dirs:** should `.github/` (CI) count for the top-level-dir trigger, or should
   `.github/workflows/*` changes feed a future CI section? Currently it is ignored for the trigger, and its files become leftovers.
5. **Rename out of scope:** this plan reports both a hit on the old page and a leftover. The alternative is
   hit only, with the page writer told to update `source_paths` itself.
6. **Owned-output helper:** duplicate T5's `is_owned_output` locally (current plan, so T9 has no T5 dependency) or
   import it and add T5 as a dependency?
7. **`ImpactReport` field names** (section 5) are assumed. Only `initial_report` depends on them.
