# T5 — Repo index (`analysis/repo_index.py`)

Parent plan: [`plans/readme-generation.md`](../readme-generation.md) (Preflight step 1, Sandbox paragraph, "Risks: secret leakage").
Depends on: T1 (`core/models/repo.py`). Must not call an LLM, the network or subprocesses.

## 1. Goal
Turn the caller-supplied candidate path list (from `git ls-files -z -co --exclude-standard`) into
an immutable `FileIndex`: the set of files that agents and tools may see and read. The index:
- drops secrets (by name, **before** any filesystem access), binaries, oversized files, symlinks,
  missing/unreadable files, and our own outputs (docs-dir files, including the manifest);
- keeps root `README.md` as input but flags it;
- tags each file with a language id by extension/basename;
- records every exclusion with a reason (for `-vv` logging and tests);
- renders a deterministic, capped tree summary for agent prompts.

`infra/sandbox.py` and `tools/fs.py` reuse `is_denylisted()` and `FileIndex` membership as the
single source of truth for "readable".

## 2. Files
| File | Change |
|---|---|
| `src/readme_stack/analysis/__init__.py` | create if T1 has not (empty) |
| `src/readme_stack/analysis/repo_index.py` | new |
| `tests/unit/analysis/__init__.py` | only if the test layout uses packages (follow T1's choice) |
| `tests/unit/analysis/test_repo_index.py` | new |

No changes to `core/models/repo.py` (owned by T1); any gap is listed in section 5.

## 3. Public interface
```python
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from readme_stack.core.models.repo import ExcludedFile, ExclusionReason, FileEntry, FileIndex

README_PATH: str = "README.md"

@dataclass(frozen=True, slots=True)
class IndexLimits:
    max_file_bytes: int = 1_000_000      # files larger than this are excluded (TOO_LARGE)
    max_files: int = 50_000              # files past this (in sorted order) are excluded (FILE_LIMIT)
    sniff_bytes: int = 8192              # bytes read for binary detection

DEFAULT_LIMITS = IndexLimits()

def build_file_index(
    root: Path,
    paths: Iterable[str],
    *,
    docs_dir: str,
    limits: IndexLimits = DEFAULT_LIMITS,
) -> FileIndex: ...

def normalize_path(path: str) -> str | None: ...          # repo-relative POSIX or None if invalid
def is_denylisted(path: str) -> bool: ...                 # name-only, no I/O
def is_owned_output(path: str, docs_dir: str) -> bool: ...
def has_binary_extension(path: str) -> bool: ...          # name-only, no I/O
def looks_binary(data: bytes) -> bool: ...                # pure, on a sniffed prefix
def detect_language(path: str) -> str | None: ...

def render_tree(
    index: FileIndex,
    *,
    subtree: str | None = None,   # render only below this dir (for component explorers)
    max_depth: int = 4,
    max_lines: int = 300,
    max_children: int = 30,
) -> str: ...
```
All helpers are pure except `build_file_index` (lstat + read of up to `sniff_bytes` per candidate).
Module-level constants (`DENY_BASENAME_GLOBS`, `DENY_ALLOW_BASENAMES`, `DENY_DIR_SEGMENTS`,
`DENY_PATH_SUFFIXES`, `BINARY_EXTENSIONS`, `LANGUAGE_BY_EXTENSION`, `LANGUAGE_BY_BASENAME`) are
`frozenset`/`dict` (wrapped in `types.MappingProxyType`) and importable for tests.

## 4. Detailed behavior and edge cases

### 4.1 `build_file_index` pipeline
For each candidate (input order irrelevant), the **first** matching rule wins:

| # | Check | Reason | I/O |
|---|---|---|---|
| 1 | `normalize_path` returns None | `INVALID_PATH` | no |
| 2 | `is_owned_output(path, docs_dir)` | `OWNED_OUTPUT` | no |
| 3 | `is_denylisted(path)` | `DENYLISTED` | no |
| 4 | `os.lstat(root / path)` raises `FileNotFoundError` | `MISSING` | lstat |
| 5 | `S_ISLNK` | `SYMLINK` | – |
| 6 | not `S_ISREG` (dir, fifo, socket, submodule gitlink dir) | `NOT_REGULAR` | – |
| 7 | `st_size > limits.max_file_bytes` | `TOO_LARGE` | – |
| 8 | `has_binary_extension(path)` | `BINARY` | no |
| 9 | open/read of first `sniff_bytes` raises `OSError` | `UNREADABLE` | read |
| 10 | `looks_binary(prefix)` | `BINARY` | – |
| 11 | more than `max_files` survivors (keep first N in sorted order) | `FILE_LIMIT` | – |

- Denylist and owned-output checks run before any filesystem call, so secret files are never
  stat'ed or opened. Other `OSError`s from `lstat` → `UNREADABLE`.
- Input paths are normalized then de-duplicated (`-co` can list a path once per stage; duplicates
  collapse silently, no exclusion record).
- `FileEntry.is_readme = (path == "README.md")` (root only, case-sensitive). README goes through
  the same size/binary rules; if it is excluded, it is simply absent.
- `files` and `excluded` are sorted by path (plain `str` ordering) → identical output for any input
  permutation.
- `root` must be an existing directory; otherwise raise `NotADirectoryError` (caller bug: Preflight
  already verified the git top level).

### 4.2 `normalize_path`
- Strip one leading `./`; reject empty, absolute (`/x`, `C:/x`), NUL-containing, backslash-
  containing, or any segment equal to `..`, `.` or `""` (i.e. `a//b`, trailing `/`).
- Returns the POSIX string unchanged otherwise. Callers must use `git ls-files -z` so names are
  not C-quoted (documented in the docstring; the git wrapper is not this task).

### 4.3 Owned outputs
- `docs_dir` is repo-relative POSIX (already validated by CLI: not absolute, not `.`, not outside).
  Normalize by stripping trailing `/`; a path is owned iff `path == docs_dir` or
  `path.startswith(docs_dir + "/")`. This covers every generated page and `<docs_dir>/.readme-stack.json`.
- Root `README.md` is **not** owned-excluded (it is input to the Explorer); it is flagged.
- Invalid `docs_dir` (fails `normalize_path`) → `ValueError`.

### 4.4 Secret denylist (`is_denylisted`)
Matching is on lower-cased names with `fnmatch.fnmatchcase` (deterministic, platform-independent).

Basename globs (`DENY_BASENAME_GLOBS`):
```
.env  .env.*  *.env
*.pem  *.key  *.p12  *.pfx  *.jks  *.keystore  *.truststore  *.ppk  *.p8
*.der  *.crt.key  *.gpg  *.pgp  *.asc  *.kdbx  *.ovpn
id_rsa  id_rsa.*  id_dsa  id_dsa.*  id_ecdsa  id_ecdsa.*  id_ecdsa_sk*  id_ed25519  id_ed25519.*  id_ed25519_sk*
.npmrc  .yarnrc.yml  .pypirc  .netrc  _netrc  .git-credentials  .gitcookies  .htpasswd
.pgpass  .my.cnf  .dockercfg  .boto  .s3cfg  .vault-token  .terraformrc  terraform.rc
credentials  credentials.*  *.credentials  client_secret*.json  service-account*.json
service_account*.json  *-sa-key.json  secrets.json  secrets.yml  secrets.yaml  secrets.toml
secrets.ini  secrets.env  *.tfstate  *.tfstate.*  *.tfvars  *.tfvars.json  *.auto.tfvars
*.keytab  *.kubeconfig  kubeconfig
```
Allow-list overriding the basename globs (`DENY_ALLOW_BASENAMES`), because these are committed
templates useful for the env-var fact block:
```
.env.example  .env.sample  .env.template  .env.dist  .env.defaults  example.env  sample.env
```
Directory segments (`DENY_DIR_SEGMENTS`, any path segment except the last): `.ssh`, `.gnupg`,
`.aws`, `.azure`, `.gcloud`, `.git` (defensive; ls-files never lists it).
Path suffixes (`DENY_PATH_SUFFIXES`, full-path lower-case endswith after `/` boundary):
`.docker/config.json`, `.kube/config`, `.config/gh/hosts.yml`.

Note `id_*` from the parent plan is deliberately narrowed to SSH key names so that source files
like `id_utils.py` / `id_generator.go` are not dropped (see Open questions).

### 4.5 Binary detection
- `BINARY_EXTENSIONS` (lower-cased final suffix; fast path, no read): images `.png .jpg .jpeg .gif
  .bmp .ico .icns .webp .tif .tiff .psd .heic .avif`; archives `.zip .tar .gz .tgz .bz2 .xz .zst
  .7z .rar .jar .war .ear .whl .egg .apk .ipa .dmg .iso .deb .rpm`; compiled `.pyc .pyo .so .dylib
  .dll .exe .o .a .lib .obj .class .wasm .bin .dat`; fonts `.ttf .otf .woff .woff2 .eot`; media
  `.mp3 .mp4 .m4a .wav .flac .ogg .avi .mov .mkv .webm`; documents `.pdf .doc .docx .xls .xlsx
  .ppt .pptx .odt`; data `.sqlite .sqlite3 .db .parquet .npy .npz .pkl .pickle .h5 .onnx .pt .ckpt
  .safetensors`. (`.svg` stays text.)
- `looks_binary(data)`: empty → False; contains `b"\x00"` → True; decodes as UTF-8 via
  `codecs.getincrementaldecoder("utf-8")().decode(data, final=False)` (tolerates a multibyte char
  cut at the sniff boundary) → False; otherwise True if more than 30 % of bytes are control bytes
  (`< 0x20` excluding `\t \n \r \f \b` and `0x1b`, plus `0x7f`), else False (Latin-1 text etc.).
- UTF-16/32 text contains NULs and is classified binary (accepted limitation).

### 4.6 Language map (`detect_language`)
Basename first (`LANGUAGE_BY_BASENAME`, exact, case-sensitive): `Dockerfile`→`dockerfile`,
`Containerfile`→`dockerfile`, `Makefile`/`GNUmakefile`/`makefile`→`make`, `CMakeLists.txt`→`cmake`,
`Gemfile`/`Rakefile`/`Podfile`/`Fastfile`→`ruby`, `Jenkinsfile`→`groovy`, `BUILD`/`BUILD.bazel`/
`WORKSPACE`→`starlark`, `Justfile`/`justfile`→`just`. Also `Dockerfile.*` prefix → `dockerfile`.

Then lower-cased final suffix (`LANGUAGE_BY_EXTENSION`):
```
.py .pyi→python   .ipynb→jupyter   .js .mjs .cjs→javascript   .jsx→jsx   .ts .mts .cts→typescript
.tsx→tsx   .go→go   .rs→rust   .java→java   .kt .kts→kotlin   .scala→scala   .groovy .gradle→groovy
.c .h→c   .cc .cpp .cxx .hpp .hh .hxx→cpp   .cs→csharp   .fs .fsx→fsharp   .swift→swift
.m→objc   .mm→objcpp   .rb→ruby   .php→php   .pl .pm→perl   .lua→lua   .r→r   .jl→julia
.dart→dart   .ex .exs→elixir   .erl .hrl→erlang   .hs→haskell   .ml .mli→ocaml   .clj .cljs→clojure
.zig→zig   .nim→nim   .v→verilog   .sv→systemverilog   .vhd .vhdl→vhdl   .sol→solidity
.sh .bash .zsh→shell   .fish→fish   .ps1 .psm1→powershell   .bat .cmd→batch
.sql→sql   .graphql .gql→graphql   .proto→protobuf   .thrift→thrift
.html .htm→html   .css→css   .scss .sass→scss   .less→less   .vue→vue   .svelte→svelte
.md .markdown→markdown   .mdx→mdx   .rst→rst   .adoc→asciidoc   .txt→text
.json .jsonc→json   .yaml .yml→yaml   .toml→toml   .xml→xml   .ini .cfg→ini   .csv→csv
.tf .hcl→hcl   .nix→nix   .cmake→cmake   .mk→make   .dockerfile→dockerfile   .svg→svg
```
Unknown → `None`. Ids are lower-case strings, stable, and intended to be matched to tree-sitter
grammar names by T-parsing later (e.g. `tsx`, `csharp`, `cpp`). `.h` → `c` is a known ambiguity.

### 4.7 Tree summary (`render_tree`)
Input: `index.files` only (excluded files, including denylisted names, never appear).

Algorithm:
1. Build a directory trie of `FileEntry.path` (or of paths under `subtree`, re-rooted; unknown or
   empty subtree → header line only with `(0 files)`).
2. Ordering within a directory: sub-directories first, then files; each group sorted by
   `(name.casefold(), name)`.
3. Chain collapse: a directory whose only child is a single directory is rendered as one node
   `a/b/c/` and counts as one depth level.
4. Line budget (`max_lines`, header excluded): expand breadth-first by level. Level 1 = children of
   the root. A directory at depth `d < max_depth` is expanded only if **all** of its visible lines
   (min(n, `max_children`) children plus one overflow line if n > `max_children`) fit into the
   remaining budget; the first directory at a level that does not fit stops all further expansion
   (it and every later/deeper directory stay collapsed). The root is always expanded; if its own
   lines exceed the budget, the root's child list is cut to fit with the overflow line.
5. Render depth-first in the order of step 2, indent 2 spaces per level.

Line formats:
- header: `./ (N files)` or `<subtree>/ (N files)`
- directory: `name/ (N files)` — N = recursive indexed file count, always shown
- file: `name`
- overflow: `… +K more (D dirs, F files)` (U+2026) for children beyond `max_children`

Guarantees: at most `max_lines + 1` lines; byte-identical output for equal indexes; no trailing
whitespace; trailing `\n`. Validate args: `max_depth >= 1`, `max_lines >= 1`, `max_children >= 1`
else `ValueError`.

Example (this repo after the refactor, `max_depth=2, max_children=4`):
```
./ (61 files)
.github/workflows/ (1 files)
  ci.yml
plans/ (9 files)
  tasks/ (8 files)
  readme-generation.md
src/readme_stack/ (38 files)
  agents/ (7 files)
  analysis/ (9 files)
  cli/ (3 files)
  core/ (8 files)
  … +7 more (5 dirs, 2 files)
tests/ (8 files)
  unit/ (6 files)
  integration/ (2 files)
.gitignore
.python-version
action.yml
… +5 more (0 dirs, 5 files)
```
(Use "file" vs "files" uniformly as `files` to keep the format trivially parseable.)

## 5. Requires from T1 (`src/readme_stack/core/models/repo.py`)
```python
from enum import StrEnum
from pydantic import BaseModel, ConfigDict

class ExclusionReason(StrEnum):
    INVALID_PATH = "invalid_path"
    OWNED_OUTPUT = "owned_output"
    DENYLISTED = "denylisted"
    MISSING = "missing"
    SYMLINK = "symlink"
    NOT_REGULAR = "not_regular"
    TOO_LARGE = "too_large"
    BINARY = "binary"
    UNREADABLE = "unreadable"
    FILE_LIMIT = "file_limit"

class FileEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str                 # repo-relative POSIX, normalized
    size: int                 # bytes (st_size)
    language: str | None      # id from detect_language
    is_readme: bool = False   # True only for root README.md

class ExcludedFile(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str                 # normalized when possible, raw input for INVALID_PATH
    reason: ExclusionReason

class FileIndex(BaseModel):
    model_config = ConfigDict(frozen=True)
    files: tuple[FileEntry, ...]          # sorted by path, unique
    excluded: tuple[ExcludedFile, ...] = ()  # sorted by path
```
Nice-to-have on `FileIndex` (T5 does not depend on them; used by sandbox/assemble):
`paths() -> frozenset[str]`, `get(path) -> FileEntry | None`, `__contains__(path)`,
`readme -> FileEntry | None`. If T1 names differ (e.g. `list` vs `tuple`, `is_readme` vs a
`role` enum), T5 adapts to T1; the field semantics above are what matter. No `root` field: the
index is portable and the root comes from `RunContext`.

## 6. Test plan (`tests/unit/analysis/test_repo_index.py`)
Fixtures build files under `tmp_path`; no git, no subprocess.
1. `normalize_path`: `./a.py`→`a.py`; `/abs`, `C:/x`, `a/../b`, `a//b`, `a/`, `""`, `a\\b`, `a\x00` → None.
2. `is_denylisted` parametrized True: `.env`, `.env.local`, `config/.env.production`, `prod.env`,
   `certs/server.pem`, `tls.KEY`, `a.p12`, `id_rsa`, `id_ed25519.pub`, `.npmrc`, `.pypirc`,
   `.netrc`, `.git-credentials`, `credentials`, `credentials.json`, `client_secret_123.json`,
   `terraform.tfstate`, `prod.tfvars`, `home/.ssh/config`, `.aws/config`, `.docker/config.json`.
3. `is_denylisted` parametrized False: `.env.example`, `.env.sample`, `id_utils.py`,
   `identity.go`, `keys.py`, `secrets.py`, `src/key_manager.ts`, `environment.yml`, `README.md`.
4. `is_owned_output`: `docs/architecture/x.md`, `docs/architecture/.readme-stack.json` True;
   `docs/architecture-old/x.md`, `docs/other.md`, `README.md` False; trailing-slash `docs_dir` works;
   invalid `docs_dir` → ValueError.
5. `looks_binary`: `b""` False; `b"hello\n"` False; NUL anywhere True; valid UTF-8 with a
   multibyte char cut at the end False; Latin-1 text (`"caf\xe9"`) False; random control bytes True.
6. `has_binary_extension`: `.PNG`, `.pyc`, `.woff2` True; `.svg`, `.py`, no-extension False.
7. `detect_language`: table of ~15 extensions incl. upper-case suffix, `Dockerfile`,
   `Dockerfile.dev`, `Makefile`, `CMakeLists.txt`; unknown `.xyz` and `LICENSE` → None.
8. `build_file_index` happy path: mixed tree → `files` sorted, sizes correct, languages set,
   `excluded` empty.
9. Denylisted file is excluded with `DENYLISTED` **and never opened/stat'ed**: monkeypatch
   `os.lstat`/`open` in the module to record calls; assert secret path absent from the record.
10. Binary by NUL content (no extension) and by extension (`logo.png` with text content) → `BINARY`.
11. Size: file of `max_file_bytes` kept, `max_file_bytes + 1` → `TOO_LARGE` (custom small limits).
12. Owned outputs: files under `docs/architecture/` incl. manifest → `OWNED_OUTPUT`; root
    `README.md` kept with `is_readme=True`; `sub/README.md` kept with `is_readme=False`.
13. Missing path in list → `MISSING`; directory path → `NOT_REGULAR`; symlink (skip on platforms
    without symlink support) → `SYMLINK`; invalid path → `INVALID_PATH`.
14. Unreadable file (chmod 000, skipped when running as root/Windows) → `UNREADABLE`.
15. `max_files=2` with 4 valid files → first 2 sorted kept, rest `FILE_LIMIT`.
16. Determinism: shuffled and duplicated input list yields an equal `FileIndex` (`==`).
17. `root` not a directory → `NotADirectoryError`.
18. `render_tree` basic: exact expected string for a small index (dirs first, casefold ordering,
    counts, 2-space indent, trailing newline).
19. `render_tree` chain collapse: `src/pkg/mod/a.py` only → `src/pkg/mod/ (1 files)`.
20. `render_tree` `max_depth=1`: only top-level entries, dirs collapsed with counts.
21. `render_tree` `max_children=2`: overflow line with correct dir/file counts.
22. `render_tree` `max_lines`: output has `<= max_lines + 1` lines for a 1,000-file index;
    top-level entries still present (breadth-first), deeper levels collapsed.
23. `render_tree` determinism: same index built from shuffled inputs → identical string.
24. `render_tree` never contains excluded/denylisted names (`.env` in input → absent).
25. `render_tree(subtree="src")` renders only under `src`; unknown subtree → `src/ (0 files)`-style header.
26. `render_tree` invalid caps (0) → `ValueError`.

## 7. Out of scope / deferred
- Running git (`git ls-files -z -co --exclude-standard`) and path decoding: `infra/git.py`.
- Path confinement / symlink resolution for tool reads: `infra/sandbox.py` (reuses `is_denylisted`).
- Content-based secret scanning (API-key regexes, entropy) — possible later hardening.
- Generated/vendored/minified-file heuristics (`vendor/`, `*.min.js`, lockfiles) and "low value"
  ranking for prompts: facts/components tasks.
- Language statistics, `.gitattributes` (`linguist-generated`, `binary`) support.
- Monorepo subdir roots; case-insensitive README variants (`Readme.md`, `README.rst`) as input.
- `{{facts:tree}}` fact block rendering (publishing) — it may call `render_tree` later.

## 8. Open questions
1. Parent plan says `id_*`; this plan narrows to SSH key names to avoid dropping `id_utils.py`.
   Accept?
2. Allow-listing `.env.example`-style templates (useful for env-var facts) vs. strict `.env*`.
3. Internal symlinks (target inside repo) are excluded outright; should they be followed and
   indexed under the link path instead? Excluding is simpler and removes escape risk.
4. Should `README.md` over the size cap or binary still be surfaced to the Explorer (e.g. via
   DocsState, which reads it separately)? Assumed yes, outside this index.
5. `TOO_LARGE` files are fully excluded; alternative is "listed but not readable" so they still
   appear in the tree (e.g. big schema/SQL dumps). Current choice: excluded, simplest.
6. Default caps (`1_000_000` bytes, 50k files, tree 300 lines / depth 4 / 30 children) — confirm or
   move into `config.py` (`RunConfig`) so the CLI can tune them.
