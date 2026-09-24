# T10: Deterministic analysis interfaces and minimal implementations

Parent plan: [`plans/readme-generation.md`](../readme-generation.md), sections "Deterministic tools" and "Project structure" (`analysis/`).
Depends on: T1 (package layout, pydantic, `core/models/repo.py` `FileIndex`/`FileEntry`) and, for tests
only, T5 (`build_file_index`, `detect_language` language ids).
Rule: nothing under `analysis/` calls an LLM, the network, subprocesses or `readme_stack.infra`.
T10 must work when `tree_sitter` is not importable.

## 1. Goal
Settle the **public, typed surface** of the deterministic analysis layer now, so that agents, tools,
fact blocks and the Preflight stage can be built against it. The later "tools in detail" step then
fills in the bodies without changing signatures or result models. Scope:
- `project_facts` does real work, because it is cheap. It extracts the project name/description,
  language stats, manifest/lock/build/CI/container files detected by filename, package managers,
  commands from `pyproject.toml` and `package.json`, dependencies from the same files, and
  workspace patterns. It uses stdlib `tomllib`/`json` only.
- `component_candidates` does real work: it proposes top-level source dirs, with container expansion
  for `src/`, `packages/` and similar layouts.
- Parsing layer: a `LanguageParser` Protocol, a `ParserRegistry`, and an empty `queries/` dir.
  `default_registry()` is empty for now.
- `import_graph`, `build_symbol_index`, `extract_interfaces` get generic plumbing. It iterates
  the index, asks the registry for a parser, catches failures and sorts results. With the empty
  default registry, or no extractors, they return empty typed results. Pure helpers
  (`parse_diff_hunks`, `symbols_for_hunks`, `SymbolIndex` lookups, `aggregate_by_component`) are
  implemented for real because they are small and need no parser.

## 2. Files
| File | Action |
|---|---|
| `src/readme_stack/core/models/facts.py` | new (owned by T10): `ProjectFacts` and its sub-models, `ComponentCandidate`, `ComponentRole` |
| `src/readme_stack/core/models/code.py` | new (owned by T10): symbol, import, diff and interface models |
| `src/readme_stack/core/models/repo.py` | edit only if T1 shipped a placeholder `ProjectFacts`: replace it with a re-export `from readme_stack.core.models.facts import ProjectFacts` (see section 5) |
| `src/readme_stack/analysis/__init__.py` | create empty if T1/T5 have not |
| `src/readme_stack/analysis/_read.py` | new: confined, capped file reads (private helper) |
| `src/readme_stack/analysis/facts.py` | new: `project_facts`, `classify_file`, `is_code_language` |
| `src/readme_stack/analysis/components.py` | new: `component_candidates` |
| `src/readme_stack/analysis/imports.py` | new: `import_graph`, `aggregate_by_component` |
| `src/readme_stack/analysis/interfaces.py` | new: `InterfaceExtractor`, `InterfaceContext`, `default_interface_extractors`, `extract_interfaces` |
| `src/readme_stack/analysis/symbols.py` | new: `SymbolIndex`, `build_symbol_index`, `parse_diff_hunks`, `symbols_for_hunks`, `diff_symbols` |
| `src/readme_stack/analysis/parsing/__init__.py` | new: re-exports `LanguageParser`, `ParseError`, `ParserRegistry`, `default_registry` |
| `src/readme_stack/analysis/parsing/base.py` | new: `LanguageParser` Protocol, `ParseError` |
| `src/readme_stack/analysis/parsing/registry.py` | new: `ParserRegistry`, `default_registry` |
| `src/readme_stack/analysis/parsing/queries/.gitkeep` | new: empty. Later holds `<language>/*.scm` (hatchling ships it as package data) |
| `tests/unit/analysis/conftest.py` | new: `write_tree`, `index_for`, `FakeParser` |
| `tests/unit/analysis/test_facts.py`, `test_components.py`, `test_imports.py`, `test_interfaces.py`, `test_symbols.py`, `test_parsing_registry.py`, `test_boundaries.py` | new |

No `pyproject.toml` changes (tree-sitter was added by T1; T10 does not import it).

## 3. Public interface

### 3.1 `core/models/facts.py`
All models are `pydantic.BaseModel` with `model_config = ConfigDict(frozen=True)`, collections are
tuples, and paths are repo-relative POSIX strings. The root dir is `"."`.
```python
class LanguageStat(BaseModel):
    language: str            # id from repo_index.detect_language
    files: int
    bytes: int
    is_code: bool            # False for markup/data ids (see NON_CODE_LANGUAGES)

class FileCategory(StrEnum):
    MANIFEST = "manifest"; LOCKFILE = "lockfile"; BUILD = "build"
    CONTAINER = "container"; CI = "ci"; WORKSPACE = "workspace"

class DetectedFile(BaseModel):
    path: str
    category: FileCategory
    kind: str                # stable id, e.g. "pyproject", "package_json", "go_mod", "makefile"
    ecosystem: str | None    # "python", "node", "rust", "go", ... or None (e.g. Makefile)

class CommandKind(StrEnum):
    ENTRY_POINT = "entry_point"   # console script / package.json "bin"
    SCRIPT = "script"             # package.json "scripts"
    TASK = "task"                 # make/just/poe/hatch targets (later step)

class Command(BaseModel):
    name: str
    kind: CommandKind
    invocation: str          # how to run it from `cwd`, e.g. "npm run build", "uv run readme-stack"
    definition: str          # raw value: "readme_stack.cli.app:main", "tsc -p ."
    source: str              # manifest path
    cwd: str                 # manifest dir, "." for root

class Dependency(BaseModel):
    name: str                # normalized as written (no lower-casing)
    spec: str | None         # raw requirement / version range
    group: str               # "main" | "dev" | "peer" | "optional" | "optional:<extra>" | "group:<name>"
    ecosystem: str
    source: str              # manifest path

class ProjectFacts(BaseModel):
    name: str
    name_source: str | None = None          # manifest path, None = directory name fallback
    description: str | None = None
    primary_language: str | None = None     # code language with most bytes
    languages: tuple[LanguageStat, ...] = ()
    detected_files: tuple[DetectedFile, ...] = ()
    package_managers: tuple[str, ...] = ()  # "uv", "poetry", "npm", "pnpm", "yarn", "bun", "cargo", ...
    commands: tuple[Command, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    workspace_patterns: tuple[str, ...] = ()  # repo-relative globs, e.g. "packages/*"
    warnings: tuple[str, ...] = ()            # "<path>: <reason>", processing order

    def files_in(self, category: FileCategory) -> tuple[DetectedFile, ...]: ...

class ComponentRole(StrEnum):
    SOURCE = "source"; TESTS = "tests"; DOCS = "docs"; EXAMPLES = "examples"
    SCRIPTS = "scripts"; CONFIG = "config"; VENDOR = "vendor"

class ComponentCandidate(BaseModel):
    path: str                     # dir, no trailing slash; "." only for the flat-repo fallback
    name: str                     # basename (project name for ".")
    role: ComponentRole
    reason: str                   # "top-level dir" | "child of container 'src'" | "manifest container" | "root files"
    file_count: int
    code_file_count: int
    code_bytes: int
    languages: tuple[str, ...]    # code languages by file count desc, then id
    primary_language: str | None
    manifest: str | None = None   # manifest file directly inside `path`, first by sort order
    entry_files: tuple[str, ...] = ()   # later step
    depends_on: tuple[str, ...] = ()    # candidate paths, later step (from import graph)
```

### 3.2 `core/models/code.py`
```python
class Span(BaseModel):            # frozen
    start_line: int               # 1-based
    end_line: int                 # inclusive
    start_byte: int
    end_byte: int                 # exclusive

class SymbolKind(StrEnum):
    MODULE, NAMESPACE, CLASS, INTERFACE, STRUCT, ENUM, TYPE_ALIAS, FUNCTION, METHOD,
    CONSTRUCTOR, PROPERTY, FIELD, CONSTANT, VARIABLE, OTHER      # values = lower-case names

class Symbol(BaseModel):
    name: str
    qualified_name: str           # dotted within the file: "Parser.parse"
    kind: SymbolKind
    path: str
    span: Span
    parent: str | None = None     # qualified_name of the enclosing symbol
    signature: str | None = None  # one line, capped at 200 chars
    doc: str | None = None        # first docstring/comment line
    exported: bool | None = None  # None = language has no notion / unknown

class SymbolRef(BaseModel):
    name: str
    path: str
    line: int
    column: int                   # 0-based
    enclosing: str | None = None  # qualified_name of the enclosing symbol

class ImportRef(BaseModel):
    module: str                   # as written: "os.path", "./util", "github.com/x/y"
    names: tuple[str, ...] = ()   # imported names; () = whole module
    line: int
    level: int = 0                # Python relative-import dots
    type_only: bool = False       # TS `import type`

class ImportKind(StrEnum):
    INTERNAL = "internal"; EXTERNAL = "external"; STDLIB = "stdlib"; UNRESOLVED = "unresolved"

class ImportEdge(BaseModel):
    source: str                   # importing file path
    module: str
    kind: ImportKind
    target: str | None = None     # file path, only for INTERNAL
    line: int

class ImportGraph(BaseModel):
    edges: tuple[ImportEdge, ...] = ()        # sorted (source, line, module)
    analyzed_languages: tuple[str, ...] = ()  # () means "not analyzed", not "no imports"
    files_parsed: int = 0
    files_failed: int = 0
    def internal_edges(self) -> tuple[ImportEdge, ...]: ...
    def external_modules(self) -> tuple[str, ...]: ...   # sorted unique EXTERNAL `module`

class ComponentDependency(BaseModel):
    source: str                   # ComponentCandidate.path
    target: str
    weight: int                   # number of file-level edges

class DiffHunk(BaseModel):
    path: str                     # new path (old path when the file was deleted)
    old_path: str | None = None   # set for renames/deletions
    old_start: int; old_count: int; new_start: int; new_count: int
    deleted_file: bool = False

class SymbolChange(StrEnum):
    ADDED = "added"; MODIFIED = "modified"; DELETED = "deleted"

class ChangedSymbol(BaseModel):
    symbol: Symbol
    change: SymbolChange

class InterfaceKind(StrEnum):
    CLI = "cli"; HTTP_ROUTE = "http_route"; RPC = "rpc"; ENV_VAR = "env_var"
    CONFIG_FILE = "config_file"; PUBLIC_API = "public_api"

class InterfaceItem(BaseModel):
    kind: InterfaceKind
    name: str                     # "--max-pages", "GET /users/{id}", "OPENAI_API_KEY"
    path: str
    line: int | None = None
    detail: str | None = None     # default value, HTTP method, help text (capped)
    symbol: str | None = None     # qualified_name of the defining symbol

class InterfaceReport(BaseModel):
    items: tuple[InterfaceItem, ...] = ()    # sorted (kind, path, line or 0, name), unique
    extractors_run: tuple[str, ...] = ()
    extractors_failed: tuple[str, ...] = ()
```
Env vars are interface items, not project facts. The `{{facts:env}}` block reads `InterfaceReport`.

### 3.3 `analysis/parsing/`
```python
# base.py
class ParseError(Exception): ...

@runtime_checkable
class LanguageParser(Protocol):
    """Pure: no I/O. `source` is the raw file bytes. May raise ParseError."""
    @property
    def language_ids(self) -> frozenset[str]: ...   # e.g. {"typescript", "tsx"}
    def outline(self, source: bytes, path: str) -> tuple[Symbol, ...]: ...
    def find_symbol(self, source: bytes, path: str, name: str) -> tuple[Symbol, ...]: ...
    def imports(self, source: bytes, path: str) -> tuple[ImportRef, ...]: ...
    def resolve_import(
        self, ref: ImportRef, from_path: str, known_paths: frozenset[str]
    ) -> tuple[ImportKind, str | None]: ...
    def references(self, source: bytes, path: str, name: str) -> tuple[SymbolRef, ...]: ...

# registry.py
class ParserRegistry:
    def __init__(self, parsers: Iterable[LanguageParser] = ()) -> None: ...
    def register(self, parser: LanguageParser) -> None: ...   # id already taken -> ValueError
    def get(self, language: str | None) -> LanguageParser | None: ...
    def supports(self, language: str | None) -> bool: ...
    def languages(self) -> frozenset[str]: ...

def default_registry() -> ParserRegistry: ...   # T10: a new, empty registry per call
```

### 3.4 Analysis functions
```python
# facts.py
NON_CODE_LANGUAGES: frozenset[str]   # markdown mdx rst asciidoc text json yaml toml xml ini csv svg
def is_code_language(language: str | None) -> bool: ...
def classify_file(path: str) -> DetectedFile | None: ...          # pure, by name
def project_facts(root: Path, index: FileIndex) -> ProjectFacts: ...

# components.py
CONTAINER_DIRS: frozenset[str]
def component_candidates(
    index: FileIndex, *, facts: ProjectFacts | None = None, max_candidates: int = 40
) -> tuple[ComponentCandidate, ...]: ...

# imports.py
def import_graph(
    root: Path, index: FileIndex, *, registry: ParserRegistry | None = None
) -> ImportGraph: ...
def aggregate_by_component(
    graph: ImportGraph, candidates: Sequence[ComponentCandidate]
) -> tuple[ComponentDependency, ...]: ...

# interfaces.py
@dataclass(frozen=True, slots=True)
class InterfaceContext:
    root: Path
    index: FileIndex
    facts: ProjectFacts
    registry: ParserRegistry

class InterfaceExtractor(Protocol):
    @property
    def name(self) -> str: ...
    def extract(self, ctx: InterfaceContext) -> tuple[InterfaceItem, ...]: ...

def default_interface_extractors() -> tuple[InterfaceExtractor, ...]: ...   # T10: ()
def extract_interfaces(
    root: Path, index: FileIndex, *, facts: ProjectFacts,
    registry: ParserRegistry | None = None,
    extractors: Sequence[InterfaceExtractor] | None = None,
) -> InterfaceReport: ...

# symbols.py
class SymbolIndex:                    # immutable after construction
    def __init__(self, symbols: Iterable[Symbol] = ()) -> None: ...
    def __len__(self) -> int: ...
    def all(self) -> tuple[Symbol, ...]: ...                 # sorted (path, start_line, qualified_name)
    def symbols_in(self, path: str) -> tuple[Symbol, ...]: ...
    def find(self, name: str, *, kind: SymbolKind | None = None) -> tuple[Symbol, ...]: ...
    def at(self, path: str, line: int) -> Symbol | None: ...  # innermost enclosing

def build_symbol_index(
    root: Path, index: FileIndex, *, registry: ParserRegistry | None = None
) -> SymbolIndex: ...
def parse_diff_hunks(diff: str) -> tuple[DiffHunk, ...]: ...
def symbols_for_hunks(index: SymbolIndex, hunks: Iterable[DiffHunk]) -> tuple[ChangedSymbol, ...]: ...
def diff_symbols(diff: str, index: SymbolIndex) -> tuple[ChangedSymbol, ...]: ...

# _read.py (private)
def read_bytes(root: Path, path: str, *, max_bytes: int = 2_000_000) -> bytes | None: ...
def read_text(root: Path, path: str, *, max_bytes: int = 2_000_000) -> str | None: ...
```
Signature stability rules: new behavior goes behind new keyword-only params with defaults, or new
fields with defaults on the models. Extractors receive a context object so that it can grow.
`registry=None` always means `default_registry()`.

## 4. Detailed behavior

### 4.1 `_read`
- `read_bytes` resolves `root / path`. It returns `None` if the resolved path is not under
  `root.resolve()`, is not a regular file, is larger than `max_bytes`, or raises `OSError`.
- `read_text` decodes as `utf-8-sig`, so a BOM is tolerated. It returns `None` on
  `UnicodeDecodeError`.
- Callers only pass `FileEntry.path` values, so the FileIndex is the sandbox. No `infra` import.

### 4.2 `project_facts(root, index)`
Never raises for repo content. Every problem becomes a `warnings` entry. Steps:
1. **Languages**: group `index.files` by `language`, skipping `None`, and sum `files`/`bytes`.
   Sort by `(-bytes, language)`. `primary_language` is the first entry with `is_code`, else `None`.
2. **Detected files**: `classify_file` on every indexed path. Skip a path if any dir segment,
   compared lower-case, is in `IGNORED_SEGMENTS` = `node_modules vendor third_party .venv venv
   site-packages dist build fixtures testdata __fixtures__`. This avoids treating test fixture
   repos and vendored code as project manifests. Sort by path. Matching table (basename unless noted):
   - MANIFEST: `pyproject.toml` (pyproject, python), `setup.py`/`setup.cfg` (python),
     `requirements*.txt` (python), `Pipfile` (python), `package.json` (node), `deno.json[c]` (deno),
     `Cargo.toml` (rust), `go.mod` (go), `pom.xml`/`build.gradle[.kts]`/`settings.gradle[.kts]`
     (jvm), `Gemfile` (ruby), `composer.json` (php), `mix.exs` (elixir), `pubspec.yaml` (dart),
     `Package.swift` (swift), `*.csproj`/`*.fsproj`/`*.sln` (dotnet).
   - LOCKFILE: `uv.lock` `poetry.lock` `Pipfile.lock` `pdm.lock` `package-lock.json`
     `npm-shrinkwrap.json` `yarn.lock` `pnpm-lock.yaml` `bun.lock` `bun.lockb` `Cargo.lock` `go.sum`
     `Gemfile.lock` `composer.lock`.
   - BUILD: `Makefile` `GNUmakefile` `makefile` `justfile` `Justfile` `Taskfile.yml` `CMakeLists.txt`
     `meson.build` `BUILD` `BUILD.bazel` `WORKSPACE` `MODULE.bazel` `noxfile.py` `tox.ini` `Rakefile`.
   - CONTAINER: `Dockerfile`, `Dockerfile.*`, `Containerfile`, `docker-compose.y[a]ml`, `compose.y[a]ml`.
   - CI (full path): `.github/workflows/*.y[a]ml`, `.gitlab-ci.yml`, `.circleci/config.yml`,
     `azure-pipelines.yml`, `Jenkinsfile`, `.travis.yml`.
   - WORKSPACE: `pnpm-workspace.yaml` `lerna.json` `nx.json` `turbo.json` `go.work`.
   The tables are `MappingProxyType`/`frozenset` module constants.
3. **Package managers**: from lockfiles (`uv.lock`→uv, `poetry.lock`→poetry, `Pipfile.lock`→pipenv,
   `pdm.lock`→pdm, `package-lock.json`/`npm-shrinkwrap.json`→npm, `yarn.lock`→yarn,
   `pnpm-lock.yaml`→pnpm, `bun.lock[b]`→bun, `Cargo.lock`/`Cargo.toml`→cargo, `go.mod`→go,
   `Gemfile.lock`→bundler, `composer.lock`→composer). Sorted unique.
4. **Parse manifests**: only `pyproject.toml`, `package.json`, `Cargo.toml` and `go.mod`. At most
   `MAX_PARSED_MANIFESTS = 50`, ordered by `(depth, path)`. Each skipped extra adds one warning
   `"<n> manifests not parsed (limit 50)"`. For each file:
   - `read_text` returns None → warning `"<path>: unreadable"`.
   - `tomllib.TOMLDecodeError` / `json.JSONDecodeError` → warning `"<path>: invalid TOML|JSON: <msg>"`,
     and nothing else is taken from that file. The file stays in `detected_files`.
   - Top-level value is not a table/object → warning, skip.
   - Every field is read through type-checked accessors (`_get_str`, `_get_table`,
     `_get_str_list`). A wrong type is ignored with a warning naming the key, e.g.
     `"pyproject.toml: [project.scripts] is not a table"`. Individual non-string entries are
     dropped with a warning.
   - **pyproject**: `project.name`, `project.description`; `tool.poetry.name`/`description` as
     fallback. Commands: `[project.scripts]` and `[project.gui-scripts]` become ENTRY_POINT.
     `[tool.poetry.scripts]` values that are a str, or a table with a str `reference`, also become
     ENTRY_POINT. Dependencies: `project.dependencies` → `main`, `project.optional-dependencies.<x>`
     → `optional:<x>`, `[dependency-groups].<g>` → `group:<g>` (only string entries; skip
     `{include-group=...}` tables silently), `tool.poetry.dependencies` → `main` (skip key `python`).
     The name is the PEP 508 prefix regex `^\s*([A-Za-z0-9][A-Za-z0-9._-]*)`, and spec is the full
     string. Strings that do not match are dropped silently. Workspace: `tool.uv.workspace.members`.
   - **package.json**: `name`, `description`. `scripts` (object of str) → SCRIPT. `bin`: a str
     gives an ENTRY_POINT named after the unscoped package name (`@a/b` → `b`); an object gives one
     ENTRY_POINT per key. `dependencies`→`main`, `devDependencies`→`dev`, `peerDependencies`→`peer`,
     `optionalDependencies`→`optional`. `workspaces`: list[str] or `{"packages": list[str]}`.
   - **Cargo.toml**: `package.name`, `package.description`, `workspace.members`. No commands or
     dependencies yet.
   - **go.mod**: first line matching `^module\s+(\S+)`. The name is the last path segment, or the
     one before it when the last is `v\d+`. Nothing else.
   - Workspace patterns are joined with the manifest dir (`posixpath.join`, root dir → as is),
     then sorted unique.
5. **Invocation**: `cwd` = manifest dir. The runner is found from the nearest lockfile in `cwd` or
   an ancestor, up to root.
   - Python ENTRY_POINT: `uv run <n>` (uv.lock), `poetry run <n>` (poetry.lock), `pdm run <n>`,
     `pipenv run <n>`, otherwise `<n>`.
   - package.json SCRIPT: `<pm> run <n>` with pm from pnpm-lock/yarn.lock/bun.lock[b]/package-lock,
     default `npm`.
   - package.json `bin`: `<n>`.
   - Commands sort by `(cwd != ".", cwd, source, kind, name)`, so root commands come first.
     Dependencies sort by `(source, group, name)`.
6. **Name**: this chain uses root-level manifests only. `pyproject project.name` →
   `tool.poetry.name` → `package.json name` → `Cargo package.name` → `go.mod` module → the first
   non-empty value wins. `name_source` is that manifest path. The fallback is
   `root.resolve().name` with `name_source=None`, or `"project"` if that is empty. Nested manifests
   never set the project name. `description` follows the same order.

Monorepo-ish layouts: nested manifests outside `IGNORED_SEGMENTS` are detected, and their commands
and deps are included with their own `cwd`/`source`. A repo with both a root `pyproject.toml` and a
root `package.json` takes its name from pyproject, and commands from both are included.

### 4.3 `component_candidates(index, *, facts=None, max_candidates=40)`
Pure; uses only the index (`facts` is accepted for later use and T10 ignores it).
- `CONTAINER_DIRS` = `src lib libs packages apps services cmd internal pkg crates plugins projects`.
  This matches the lower-cased basename.
- Role by segment, lower-cased (the first non-SOURCE role among all segments of the path wins):
  TESTS `tests test spec specs __tests__ e2e testing`; DOCS `docs doc documentation`;
  EXAMPLES `examples example samples sample demo demos`; SCRIPTS `scripts script bin tools hack`;
  VENDOR `vendor third_party external`; CONFIG: any segment starting with `.`, plus `ci deploy`.
  Everything else is SOURCE.
- Algorithm:
  1. Build the dir tree from `index.files` (dirs are only those implied by indexed files).
  2. For each top-level dir `d` (sorted): if `d` is a container, meaning its basename is in
     `CONTAINER_DIRS` or at least 2 immediate child dirs each directly contain a MANIFEST file per
     `classify_file`, the candidates are its immediate child dirs (reason
     `"child of container '<d>'"` / `"manifest container"`). Otherwise `d` itself is a candidate
     (`"top-level dir"`).
  3. **Single-package unwrap**: if a container produced exactly one child `c`, and `c` has at least
     2 child dirs containing code, replace `c` with its children. This covers `src/<pkg>/{a,b}`.
     Applied once. Max depth is 3 segments.
  4. Drop candidates with `code_file_count == 0`. Files directly inside a container or inside an
     unwrapped package dir are not attributed to any candidate (accepted).
  5. **Flat repo fallback**: if no candidates remain and the root has code files, emit one
     candidate `path="."`, `name` = root dir name, reason `"root files"`. Root files are counted
     non-recursively, and `role=SOURCE`.
  6. Stats come from all files below the path. `languages` holds code languages sorted by
     `(-count, id)`. `manifest` is the first sorted MANIFEST file directly in the dir.
  7. Cap: if more than `max_candidates`, keep the top N by `(-code_bytes, path)`, then re-sort by
     path. `max_candidates < 1` → `ValueError`.
- Empty index → `()`.

### 4.4 `import_graph`, `build_symbol_index` (generic plumbing)
- For each `FileEntry` (sorted), `parser = registry.get(entry.language)`. Skip if `None`. Read with
  `read_bytes`; `None` counts as `files_failed`.
- `import_graph`: calls `parser.imports`, then `parser.resolve_import(ref, path, known_paths)` for
  each ref, where `known_paths` = frozenset of index paths. `target` is kept only for INTERNAL, and
  only if it is in `known_paths`; otherwise the edge becomes UNRESOLVED. Any exception from the
  parser → `files_failed += 1`, `logger.debug`, continue. `analyzed_languages` = sorted registry
  ids that matched at least one file.
- `build_symbol_index`: `parser.outline` per file. Exceptions are handled the same way (logged,
  file skipped). Returns `SymbolIndex(symbols)`.
- With `default_registry()` (empty) both return empty results without reading any file.
- `aggregate_by_component`: for each INTERNAL edge, map source and target to the candidate with
  the longest matching path prefix (`"."` matches everything). Skip unmapped edges and
  self-edges. Count the rest, sorted `(source, target)`. Empty graph → `()`.

### 4.5 `extract_interfaces`
- `extractors=None` → `default_interface_extractors()` (T10: `()`). Build `InterfaceContext` and run
  each extractor in order. Exceptions add the name to `extractors_failed` and are logged. Items are
  deduplicated and sorted. `extractors_run` lists the names attempted.

### 4.6 Symbols
- `SymbolIndex`: stores sorted `all()`, a per-path dict and a name dict. `find` matches `name` or
  `qualified_name`, optionally filtered by `kind`. `at(path, line)` returns the symbol with the
  smallest span containing `line` (tie → deeper `qualified_name`), or `None`.
- `parse_diff_hunks(diff)`: parses `git diff` unified output (default `a/`/`b/` prefixes, may
  contain `-M` renames).
  - Tracks `diff --git`, `rename from/to`, `--- a/x`, `--- /dev/null`, `+++ b/y`, `+++ /dev/null`,
    and `@@ -s[,c] +s[,c] @@`. A missing count means 1.
  - C-quoted paths (`"a\tb"`) are unquoted: backslash escapes and octal bytes, decoded as UTF-8.
  - `+++ /dev/null` → `deleted_file=True`, `path` = old path.
  - Binary-file notices and `\ No newline` lines are ignored. Unparseable lines are ignored, and
    it never raises. Empty input → `()`.
- `symbols_for_hunks`: skips `deleted_file` hunks (they need the old tree; later step). The new-side
  range is `[new_start, new_start + new_count - 1]`, or `[new_start, new_start]` when
  `new_count == 0` (pure deletion). Report each symbol in `hunk.path` whose span overlaps the range,
  except symbols where the range lies entirely inside one of their child symbols. Each is reported
  with `change=MODIFIED`, deduped by `(path, qualified_name)`, and sorted
  `(path, span.start_line, qualified_name)`.
- `diff_symbols(diff, index)` = `symbols_for_hunks(index, parse_diff_hunks(diff))`.

### 4.7 General
- `logging.getLogger(__name__)` only; no prints.
- Output is deterministic: identical output for the same index and file contents regardless of
  input order.
- No module under `analysis/` imports `tree_sitter` at import time. The later step imports it
  lazily inside `default_registry()` and falls back to an empty registry on `ImportError`.

## 5. Requires from T1/T5; model placement
From T1 (`core/models/repo.py`), with the same shapes as T5 section 5:
```python
class FileEntry(BaseModel):  # frozen
    path: str; size: int; language: str | None; is_readme: bool = False
class FileIndex(BaseModel):  # frozen
    files: tuple[FileEntry, ...]      # sorted, unique
    excluded: tuple[ExcludedFile, ...] = ()
```
T10 does not use `excluded` or any `FileIndex` helper methods. If T1 uses `list` instead of
`tuple`, only iteration is needed. T1 must provide pydantic and a `core/models/` package.

From T5: language ids of `detect_language` (e.g. `python`, `typescript`, `tsx`, `markdown`, `toml`,
`json`, `dockerfile`), which `NON_CODE_LANGUAGES` and later parser `language_ids` rely on. Tests
also use `build_file_index`.

`ProjectFacts`: the parent plan lists it under `repo.py`. T10 defines it in `core/models/facts.py`.
If T1 already created a `ProjectFacts` placeholder in `repo.py`, T10 replaces it with a re-export,
so `from readme_stack.core.models.repo import ProjectFacts` keeps working. `Component` and
`RepoModel` stay in T1's `repo.py`. `ComponentCandidate` is a separate, deterministic type:
Python's proposal, not the LLM's `Component`.

Placement rule: **anything consumed outside `analysis/`/`tools/` lives in `core/models`**, because
of the dependency rule `publishing`/`agents`/`workflow` → `core` only:
- core: `ProjectFacts` and sub-models, and `ComponentCandidate` (Explorer prompt, `{{facts:commands}}`).
  Also `ImportGraph`/`ImportEdge`/`ComponentDependency` (`{{facts:deps}}` diagram), `InterfaceReport`/
  `InterfaceItem` (`{{facts:env}}`, writers), `Symbol`/`SymbolRef`/`Span`/`ImportRef` (tool
  results), and `DiffHunk`/`ChangedSymbol` (ImpactReport, ImpactAnalyst).
- analysis: behavior only. That is `LanguageParser`, `ParseError`, `ParserRegistry`,
  `SymbolIndex`, `InterfaceExtractor`, `InterfaceContext`, the classification tables and the
  functions.

## 6. Test plan (`tests/unit/analysis/`)
The conftest provides `write_tree(root, {path: str | bytes})`, and
`index_for(root) = build_file_index(root, <all files via rglob>, docs_dir="docs/architecture")`.
It also provides `FakeParser(language_ids, symbols_by_path, imports_by_path, resolve_map,
raise_for=set())`. Fixture repos are built in `tmp_path`, not checked in.

`test_facts.py`
1. Python repo (`pyproject` with name/description/scripts/deps/optional/dependency-groups, `uv.lock`):
   name, `name_source="pyproject.toml"`, ENTRY_POINT with `invocation="uv run <n>"`,
   `definition="pkg.cli:main"`, deps with correct groups, `package_managers=("uv",)`.
2. Node repo (`package.json` scripts/bin/deps, `pnpm-lock.yaml`): `pnpm run build`; scoped `bin`
   str → unscoped name; `devDependencies` → `dev`.
3. Poetry-only pyproject: name from `tool.poetry`, `tool.poetry.scripts` str and table forms,
   `python` key skipped, `poetry run`.
4. Name fallback chain: only `Cargo.toml` gives the Cargo name; only `go.mod` (`.../foo/v2`) gives
   `foo`; no manifests gives the dir name with `name_source is None`.
5. Root pyproject plus root package.json: name from pyproject, commands from both.
6. Malformed TOML: warning contains path and "invalid TOML", no exception, the file is still in
   `detected_files`, the name falls back.
7. Malformed JSON, a top-level JSON array, and a UTF-8 BOM `package.json` (BOM case parses fine).
8. Wrong types: `[project] scripts = "x"`, `scripts: {"a": 1, "b": "ok"}`, `name = 3` → warnings,
   only valid entries kept, name falls back.
9. Undecodable bytes in `pyproject.toml` (latin-1 `\xff`) → `unreadable` warning.
10. Monorepo: `packages/a/package.json`, `packages/b/package.json`, root `package.json` with
    `workspaces: ["packages/*"]` → nested commands have `cwd="packages/a"`, root ones sort first,
    `workspace_patterns=("packages/*",)`, root name only. `{"packages": [...]}` form too.
11. `tests/fixtures/x/pyproject.toml` and `node_modules/y/package.json` are ignored (not detected,
    no commands).
12. `classify_file` table: about 20 parametrized paths across all categories, including
    `.github/workflows/ci.yml`, `Dockerfile.dev`, `requirements-dev.txt`, `app.csproj`, and `None`
    for `src/main.py` and `docs/workflows/ci.yml`.
13. Languages: stats and ordering, `primary_language` ignores markdown/json even if larger, and
    `is_code` flags are correct.
14. Manifest cap: with `MAX_PARSED_MANIFESTS` monkeypatched to 2 and 3 manifests → one limit
    warning; the parsed ones are the shallowest.
15. Determinism: the same tree built twice with a shuffled write order gives an equal `ProjectFacts`.

`test_components.py`
16. Flat layout `pkg/`, `tests/`, `docs/` (md only) → `pkg` SOURCE, `tests` TESTS, `docs` dropped.
17. `src/<pkg>/{cli,core}/` → unwrap to `src/<pkg>/cli`, `src/<pkg>/core` with reason mentioning `src`.
18. `src/{a,b}` (JS style) → `src/a`, `src/b`; single `src/app` with no subdirs → `src/app`.
19. `packages/{a,b}` containers; `modules-x/{a,b}` with a `package.json` each → manifest container,
    and `manifest` field set.
20. Role inheritance: `.github/...` with `.py` → CONFIG; `examples/demo` → EXAMPLES.
21. Flat repo (`main.py` only) → a single `"."` candidate; empty index → `()`.
22. Cap: 5 dirs with `max_candidates=2` → the two largest by code bytes, sorted by path;
    `max_candidates=0` → `ValueError`.
23. Stats: `file_count`, `code_file_count`, `code_bytes`, `languages` ordering, `primary_language`.

`test_parsing_registry.py`
24. An empty registry: `get("python") is None`, `get(None) is None`, `languages() == frozenset()`.
25. Registering a `FakeParser({"typescript","tsx"})` makes `get("tsx")` return it; a duplicate id
    raises `ValueError`; `isinstance(FakeParser(...), LanguageParser)` is True.
26. `default_registry()` is empty and returns a new instance each call.
27. The `queries` dir exists as package data (`importlib.resources.files(...).joinpath("queries")`
    `.is_dir()`).

`test_imports.py`
28. The default registry on a Python fixture → `ImportGraph()` equal to empty, and
    `analyzed_languages == ()`.
29. With `FakeParser`: internal, external and unresolved edges; an INTERNAL target not in the index
    → UNRESOLVED; sorted; `external_modules()`; `analyzed_languages == ("python",)`.
30. A parser raising on one file → `files_failed == 1`, other files still processed.
31. `aggregate_by_component`: a hand-built graph over 3 candidates → weights, self-edges dropped,
    longest-prefix mapping, and `"."` fallback.

`test_interfaces.py`
32. Defaults → an empty `InterfaceReport` with `extractors_run == ()`.
33. A fake extractor that returns unsorted duplicates → sorted and unique; a raising extractor is
    listed in `extractors_failed`, and the other extractor's items are kept.

`test_symbols.py`
34. `build_symbol_index` with the default registry → `len == 0`. With `FakeParser` → symbols from
    all files, sorted.
35. `SymbolIndex.find` by name, qualified name, and kind filter. `at()` returns the innermost
    symbol, or `None` outside all spans.
36. `parse_diff_hunks`: modify, add (`--- /dev/null`), delete (`+++ /dev/null`), rename with
    hunks, `@@ -3 +3 @@` without counts, binary notice, quoted path, empty and garbage input.
37. `symbols_for_hunks`: a hunk inside a method → only the method (not the class); a hunk spanning
    two functions → both; pure deletion (`new_count=0`) → the enclosing symbol; a `deleted_file`
    hunk → skipped; results deduped and sorted.
38. `diff_symbols` end to end on a literal diff string and a hand-built index.

`test_boundaries.py`
39. AST-scan every `src/readme_stack/analysis/**/*.py`. None imports `ai`, `anthropic`, `openai`,
    `httpx`, `requests`, `urllib.request`, `socket`, `subprocess`, `tree_sitter` (T10 only; the
    later step relaxes this to lazy imports inside functions), `readme_stack.infra`,
    `.agents`, `.workflow`, `.publishing`, `.tools`, or `.cli`.
40. Smoke/acceptance: a parametrized test over the tmp fixture repos (python, ts, go, mixed,
    empty) calls all six entry points (`project_facts`, `component_candidates`, `import_graph`,
    `extract_interfaces`, `build_symbol_index`, `diff_symbols("")`) and checks the result types
    with no exception. If `tests/fixtures/repos/*` from T1 exists, those are parametrized too.

Gate: `uv run ruff check && uv run ruff format --check && uv run pytest`.

## 7. Out of scope / deferred to "tools in detail"
- Tree-sitter parsers per language (Python, TS/JS/TSX, Go, Rust, Java first), their `.scm` queries
  in `parsing/queries/<lang>/`, and lazy loading in `default_registry()` with an `ImportError`
  fallback.
- Real `import_graph`: per-language resolution (Python packages/src layout, TS path aliases from
  `tsconfig.json`, Go module path, Rust `mod`), stdlib lists, and populating
  `ComponentCandidate.depends_on`.
- Real `SymbolIndex` build with the content-hash cache, `find_references`, `read_symbol`/
  `code_outline` tool wrappers (`tools/code.py`), and ADDED/DELETED symbol changes by comparing
  old and new trees (including `deleted_file` hunks).
- Interface extractors: argparse/click/typer/commander CLI options, HTTP routes (FastAPI/Flask/
  Express/Go net/http), env vars (`os.environ`, `process.env`, `.env.example`), config files, and
  the public API (`__all__`, TS exports).
- More command sources: Makefile/justfile targets, `[tool.poe.tasks]`, hatch/pdm scripts,
  `Taskfile.yml`, and deno tasks (these need a YAML parser → new dependency decision).
  Dependencies for Cargo/go.mod/Gemfile/etc.
- Using `workspace_patterns` in `component_candidates`, `entry_files` detection, and
  generated/vendored/minified heuristics beyond `IGNORED_SEGMENTS`.
- A precomputed-facts bundle for Preflight/RunContext (e.g. `RepoFacts`) and `{{facts:*}}`
  rendering (publishing).
- `analysis/impact.py` (changed files/symbols → pages): a separate task.

## 8. Open questions
1. Naming: the parent plan lists `interface_extractors` as a precomputed fact. This plan uses
   `extract_interfaces(...) -> InterfaceReport` plus `default_interface_extractors()`. Should the
   literal name be kept as an alias?
2. Placing `ProjectFacts` in a T10-owned `core/models/facts.py` (with a re-export from `repo.py`)
   rather than directly in `repo.py`: acceptable to T1?
3. Is dependency extraction from pyproject/package.json wanted now, or should it be dropped to
   keep T10 minimal? (It costs roughly 40 lines and gives a tech-stack block.)
4. Is it acceptable to implement `parse_diff_hunks`/`symbols_for_hunks` in T10, or should they
   also return `()` until the later step?
5. `IGNORED_SEGMENTS` includes `fixtures`/`testdata`/`build`/`dist`, which can hide a real package
   named `build/`. Is that acceptable, or should only nested manifests be ignored there and not
   the file tree?
6. Invocation style: should Python entry points prefer `uv run <n>` when `uv.lock` exists? README
   readers may expect `pip install . && <n>`. Alternatively, render both.
7. Mixed root (pyproject + package.json): pyproject wins the name. Would the primary language be a
   better tiebreaker?
8. Should `LanguageParser` keep `find_symbol` (it can be derived from `outline`)? It is kept
   because the task requires symbol lookup on the Protocol, so languages can optimize.
