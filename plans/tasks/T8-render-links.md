# T8: Rendering and link validation

Parent plan: [`plans/readme-generation.md`](../readme-generation.md) (Pipeline step 7 "Assemble", "Rendered fact blocks & templates",
"Risks: hallucinated links"). Depends on T1 (`core/models/plan.py`). It reuses the T5 `FileIndex` type
(`core/models/repo.py`) and stays compatible with T6 (`publishing/markers.py`, `ManifestEntry.kind`).
Consumer: the Assemble stage (a later task), which runs render → fill facts → validate links →
(repair round) → drop broken links → normalize → `apply_marker` (T6, README only).

## 1. Goal
Deterministic, LLM-free, I/O-free (apart from reading package templates) building blocks that turn
structured writer output into final markdown:
- **render**: `PageDraft` (ordered sections) + `PageSpec`/`DocPlan` → markdown through a template
  for each page kind. Python also renders cross-link blocks, with relative paths computed correctly
  from each page's location.
- **fact blocks**: replace `{{facts:KEY}}` placeholders from a dict of pre-rendered strings. Unknown
  keys are left in place and trigger a warning.
- **links**: extract relative links and inline-code repo paths, resolve them against the page's
  location, validate them against `FileIndex` and `DocPlan` page paths, report broken ones, and drop
  broken links while keeping the link text.
- **normalize**: fence-aware whitespace normalization, idempotent.

Same input gives byte-identical output. No new dependency.

## 2. Files
| File | Action |
|---|---|
| `src/readme_stack/publishing/__init__.py` | create if T1/T6 has not (empty) |
| `src/readme_stack/publishing/_markdown.py` | new, private: code-fence/code-span scanner shared by the three modules, `normalize_markdown` |
| `src/readme_stack/publishing/render.py` | new |
| `src/readme_stack/publishing/fact_blocks.py` | new |
| `src/readme_stack/publishing/links.py` | new |
| `src/readme_stack/publishing/templates/__init__.py` | new, empty (makes `importlib.resources.files(...)` reliable) |
| `src/readme_stack/publishing/templates/readme.md.tmpl` | new placeholder template |
| `src/readme_stack/publishing/templates/overview.md.tmpl` | new placeholder template |
| `src/readme_stack/publishing/templates/component.md.tmpl` | new placeholder template |
| `tests/unit/publishing/test_markdown.py`, `test_render.py`, `test_fact_blocks.py`, `test_links.py` | new |

No `pyproject.toml` change: hatchling already ships non-`.py` files that sit inside the package dir
(test 10 guards this).

**Template mechanism: stdlib `string.Template`.** Templates only handle layout (fixed text plus a few
slots). All loops and conditionals (section list, related-page list, empty-block omission) happen in
Python, where they are typed and testable. Jinja2 would add a dependency, a second logic language
and autoescape/whitespace-control pitfalls, and we don't need what it adds. A custom `{{name}}`
renderer would collide visually with the `{{facts:*}}` syntax. `string.Template` substitutes each
slot once and never re-scans values, so a `$` in writer prose or code samples is safe. A literal
`$` inside a template is written `$$`.

## 3. Public interface

### `publishing/_markdown.py` (private, but tested directly)
```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class Span:
    start: int      # offset into the original text
    end: int        # exclusive

def fenced_spans(text: str) -> list[Span]: ...          # whole fenced blocks incl. fence lines
def code_spans(text: str) -> list[tuple[Span, str]]: ...  # inline `code` spans outside fences; (span, content)
def mask_code(text: str) -> str: ...                     # same length; fence/code-span chars → " " (newlines kept)
def close_unclosed_fence(text: str) -> str: ...          # appends a matching closing fence if needed
def normalize_markdown(text: str) -> str: ...            # see 4.6; idempotent
```

### `publishing/render.py`
```python
from collections.abc import Mapping
from string import Template

from readme_stack.core.models.plan import DocPlan, PageDraft, PageKind, PageSpec

TEMPLATE_FIELDS: frozenset[str] = frozenset(
    {"title", "summary", "sections", "related", "pages", "readme_link"}
)
REQUIRED_FIELDS: frozenset[str] = frozenset({"title", "sections"})

def load_template(kind: PageKind) -> Template: ...            # functools.cache'd; validates fields
def load_templates() -> dict[PageKind, Template]: ...         # every PageKind member; KeyError-free
def validate_template(tmpl: Template, *, name: str) -> None: ...  # ValueError on bad/unknown fields
def relative_link(from_path: str, to_path: str) -> str: ...  # POSIX repo-relative paths → link dest
def render_sections(draft: PageDraft) -> str: ...
def render_page(
    draft: PageDraft,
    spec: PageSpec,
    plan: DocPlan,
    *,
    templates: Mapping[PageKind, Template] | None = None,   # None → load_templates()
) -> str: ...                                                # normalized markdown, no marker, facts unfilled
```

### `publishing/fact_blocks.py`
```python
from collections.abc import Mapping
from dataclasses import dataclass

FACT_RE: re.Pattern[str]   # r"\{\{\s*facts:(?P<key>[A-Za-z0-9_][A-Za-z0-9_.-]*)\s*\}\}"

@dataclass(frozen=True, slots=True)
class FactFillResult:
    text: str
    filled: tuple[str, ...]    # distinct keys replaced, sorted
    unknown: tuple[str, ...]   # distinct keys left in place, sorted

def find_fact_keys(text: str) -> list[str]: ...   # in order of appearance, outside code; duplicates kept
def fill_facts(text: str, facts: Mapping[str, str], *, page: str = "<page>") -> FactFillResult: ...
```

### `publishing/links.py`
```python
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from readme_stack.core.models.plan import DocPlan
from readme_stack.core.models.repo import FileIndex

class LinkKind(StrEnum):
    INLINE = "inline"            # [text](dest "title")
    REF_DEF = "ref_def"          # [label]: dest "title"
    REF_USE = "ref_use"          # [text][label] / [label][]; never validated on its own
    CODE_PATH = "code_path"      # `src/pkg/mod.py`

class BrokenReason(StrEnum):
    EMPTY = "empty"              # [x]() or empty after stripping fragment/query
    ESCAPES_REPO = "escapes_repo"
    NOT_FOUND = "not_found"
    UNDEFINED_REF = "undefined_ref"   # [text][label] with no definition

@dataclass(frozen=True, slots=True)
class LinkRef:
    kind: LinkKind
    raw: str          # exact source slice text[start:end]
    text: str         # link text / label / code content
    target: str       # raw destination (angle brackets removed); label for REF_USE
    start: int
    end: int
    line: int         # 1-based

@dataclass(frozen=True, slots=True)
class BrokenLink:
    page_path: str
    link: LinkRef
    resolved: str | None        # repo-relative POSIX target, None if EMPTY/ESCAPES/UNDEFINED
    reason: BrokenReason
    suggestion: str | None = None   # corrected dest if the repo-root reading exists (for the repair prompt)

@dataclass(frozen=True, slots=True)
class LinkTargets:
    files: frozenset[str]       # FileIndex paths ∪ DocPlan page paths
    dirs: frozenset[str]        # every ancestor dir of `files`, plus "" (repo root)
    def exists(self, path: str) -> bool: ...    # path in files or path.rstrip("/") in dirs

def build_link_targets(index: FileIndex, plan: DocPlan) -> LinkTargets: ...
def extract_links(markdown: str) -> list[LinkRef]: ...          # sorted by start
def resolve_link(page_path: str, dest: str) -> str | None: ...  # None if it escapes the repo
def validate_links(markdown: str, page_path: str, targets: LinkTargets) -> list[BrokenLink]: ...
def drop_broken_links(markdown: str, broken: Iterable[BrokenLink]) -> str: ...
```

## 4. Detailed behavior and edge cases

### 4.1 PageDraft section structure (what T8 needs)
- `PageDraft.sections` is an **ordered** list of `Section(heading, body)`. It is flat: every section
  renders as `## heading`, and deeper structure lives inside `body` as markdown (`###`...).
- Page title comes from `PageSpec.title`, not from the draft (the plan is authoritative and
  consistent with the manifest). `PageDraft.summary` is the lead paragraph under the title.
  `PageSpec.summary` (one line from the Outliner) is used in the README page index and in
  related-page lists, so rendering one page never needs another page's draft.
- `render_sections`:
  - heading: collapse all whitespace runs (including newlines) to one space, strip, strip leading
    `#`s and the space after them (the writer may have typed `## Foo`). Empty heading → the section
    body is emitted without a heading line.
  - body: `strip("\n")`, trailing whitespace removed, then `close_unclosed_fence` (logs a warning) so
    a broken fence cannot swallow later sections.
  - a section whose heading **and** body are empty is skipped. A heading with empty body is kept.
  - joined as `"## {heading}\n\n{body}"` with `"\n\n"` between sections.
- `draft.page_id != spec.id` → `ValueError`. `spec.id` not in `plan` → `ValueError`.

### 4.2 Template format
Files `templates/<kind>.md.tmpl`, UTF-8, loaded with `importlib.resources.files(
"readme_stack.publishing.templates").joinpath(f"{kind.value}.md.tmpl").read_text("utf-8")`.
Fields available (`$name` or `${name}`):

| Field | Value |
|---|---|
| `title` | `spec.title`, whitespace-collapsed to one line |
| `summary` | `draft.summary.strip()` (may be `""`) |
| `sections` | `render_sections(draft)` |
| `related` | `""` or `"## Related pages\n\n- [Title](rel) - summary\n..."` built from `spec.links_to` |
| `pages` | `""` or `"## Documentation\n\n- [Title](rel) - summary\n..."`: every non-README page in DocPlan order, with links relative to `spec.path` |
| `readme_link` | `relative_link(spec.path, <README page path>)` (e.g. `../../README.md`) |

Blocks own their heading, so an empty block leaves only blank lines, which normalization collapses.
`related`: ids are de-duplicated (first wins), self-links and unknown ids are skipped with a warning,
and order follows `links_to`. List item format: `- [{title}]({rel})` plus ` - {summary}` when the
summary is non-empty.

`validate_template` (run by `load_template`, error = `ValueError` naming the file):
`tmpl.is_valid()` must be True. `set(tmpl.get_identifiers()) ⊆ TEMPLATE_FIELDS`, and
`REQUIRED_FIELDS ⊆` identifiers. Rendering uses `tmpl.substitute(values)` (strict), then
`normalize_markdown`.

Placeholder templates for this step:
```
readme.md.tmpl                 component.md.tmpl / overview.md.tmpl
--------------                 -------------------------------------
# $title                       # $title

$summary                       [Back to README]($readme_link)

$sections                      $summary

$pages                         $sections

                               $related
```
Example: spec `{id: "cli", path: "docs/architecture/cli.md", kind: component, title: "CLI",
links_to: ["pipeline"]}`, draft `{summary: "Parses flags.", sections: [("Flags", "Uses argparse.")]}`,
and the plan also holds `pipeline` at `docs/architecture/pipeline.md` (summary "Stage order"). Output:
```
# CLI

[Back to README](../../README.md)

Parses flags.

## Flags

Uses argparse.

## Related pages

- [Pipeline](pipeline.md) - Stage order
```

### 4.3 `relative_link(from_path, to_path)`
`posixpath.relpath(to_path, start=posixpath.dirname(from_path) or ".")`. Both inputs are
repo-relative POSIX with no leading `/`. The result is percent-encoded only for space, `(`, `)`,
`<`, `>` (`urllib.parse.quote(rel, safe="/._-~#")`), so it is always a valid inline-link dest.
Examples: `README.md` → `docs/architecture/cli.md` = `docs/architecture/cli.md`; `docs/architecture/cli.md` →
`README.md` = `../../README.md`; `docs/architecture/a.md` → `src/x.py` = `../../src/x.py`.

### 4.4 Code-region scanning (`_markdown.py`, shared)
- **Fenced blocks** (CommonMark subset), scanned line by line:
  - opening line: `^ {0,3}(`{3,}|~{3,})` (a backtick fence's info string must not contain a backtick);
  - closing line: `^ {0,3}` followed by the same char repeated at least the opening length, then only
    whitespace;
  - an unclosed fence runs to end of text.
- **Inline code spans**, in non-fence text: a backtick run of length n up to the next run of exactly
  n backticks. It may cross single newlines but not a blank line. An unmatched run is literal.
  Backslash escapes do not apply inside spans, and `` \` `` outside spans is not an opener.
- `mask_code` replaces every non-newline char in fences and code spans (including the backticks)
  with a space. Offsets and line numbers are preserved, so the link/fact regexes run on the masked
  text while slices are taken from the original.
- Indented (4-space) code blocks and raw HTML are **not** detected. They get treated as text (see §7).

### 4.5 Link extraction and validation
Run on `mask_code(markdown)`.
- **Inline link/image** (a leading `!` marks an image):
  `(?<![\\\]])(?P<bang>!?)\[(?P<text>(?:\\.|[^\[\]\\\n]|\[[^\[\]\n]*\])*)\]\((?:\s*)(?P<dest><[^<>\n]*>|[^\s()<>]*(?:\([^\s()<>]*\)[^\s()<>]*)*)(?:\s+(?P<title>"[^"\n]*"|'[^'\n]*'|\([^()\n]*\)))?\s*\)`
  - Link text may contain one level of brackets and code spans (masked, so taken from the original).
  - **Images are skipped**: `bang == "!"` → not returned. FileIndex excludes binaries, so image paths
    would always look broken.
- **Reference definition**: `(?m)^ {0,3}\[(?P<label>[^\]\n]+)\]:[ \t]*(?P<dest><[^<>\n]*>|\S+)(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'|\([^()\n]*\)))?[ \t]*$`.
  Labels match case-insensitively with whitespace collapsed (CommonMark).
- **Reference use**: `\[(?P<text>[^\[\]\n]+)\]\[(?P<label>[^\[\]\n]*)\]`, where an empty label means the
  label is the text. Shortcut `[label]` is not extracted. If its definition is dropped, it renders as
  literal `[label]`, which is harmless.
- **Code paths**: every `code_spans` content `c` (stripped) that matches
  `^(?:\./)?[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)*/?(?::\d+(?:-\d+)?|#L\d+(?:-L?\d+)?)?$`
  **and** contains a `/`. The `:12`, `:12-30` and `#L12` suffix is stripped before validation.
  Skipped: bare names (`pyproject.toml`, `os.path`), anything containing `..`, `*`, `?`, `{`, `<`,
  `~`, `$`, spaces, a leading `/` or `-`, or a scheme. The match is always resolved from the
  **repo root** (writers name repo paths, not page-relative ones), regardless of the page location.
- **Destination classification** for INLINE and REF_DEF (angle brackets removed first):
  1. matches `^[A-Za-z][A-Za-z0-9+.-]*:` (http, https, mailto, ...) or starts with `//` → external,
     not returned;
  2. starts with `#` → in-page anchor, not returned (anchor validation deferred);
  3. otherwise relative: drop `#fragment` and `?query`, apply `urllib.parse.unquote`. An empty result
     → `EMPTY`.
- **`resolve_link(page_path, dest)`**: a leading `/` means repo-root-relative (GitHub semantics), so
  `base = ""`. Otherwise `base = posixpath.dirname(page_path)`. Then
  `p = posixpath.normpath(posixpath.join(base, dest))`. If `p == ".."`, or `p` starts with `../`, or
  `p` is absolute → `None` (`ESCAPES_REPO`). `p == "."` → `""` (repo root, a dir, valid). A trailing
  `/` in `dest` is kept on the result as a directory hint.
- **Validity**: `targets.exists(resolved)`. Files are FileIndex paths ∪ every `PageSpec.path`
  (README included). Generated pages are absent from FileIndex because T5 excludes owned outputs,
  which is why DocPlan paths are required. A page deleted from the plan is therefore broken. Dirs are
  the ancestors of those files, so `[src](../../src/)` and `../../src` are valid. Matching is
  case-sensitive and exact.
- **`suggestion`**: for a NOT_FOUND INLINE/REF_DEF from a page not at the root, if
  `targets.exists(normpath(dest))` (the typical LLM mistake of writing `src/x.py` from
  `docs/architecture/cli.md`), then `suggestion = relative_link(page_path, normpath(dest))`, keeping
  the original fragment.
- REF_USE whose normalized label has no definition in the page → `UNDEFINED_REF`. If its definition
  is broken, the use is reported too (reason copied from the definition), so dropping removes both.
- `validate_links` returns `BrokenLink`s sorted by `link.start`. Valid links are not returned.

### 4.6 Dropping broken links (`drop_broken_links`)
- Apply replacements from the highest `start` to the lowest. For each item, first check that
  `markdown[start:end] == link.raw`; if not → `ValueError` (the caller passed a report for other text).
- INLINE → `link.text` (the inner markdown is kept verbatim, e.g. `` `a.py` ``). An empty text leaves
  nothing.
- REF_USE → `link.text`.
- REF_DEF → remove the whole line including its newline.
- CODE_PATH → **unchanged** (prose cannot be rewritten deterministically). It is only reported and
  warned; see Q1.
- One `logging.warning` per dropped link: `"%s:%d: dropped broken link %r (%s)"`.
- Overlapping spans cannot occur, because extraction never nests links. Duplicate entries are
  de-duplicated by `(start, end)`.
- The result is not re-normalized. Assemble calls `normalize_markdown` last.

### 4.7 Fact blocks (`fill_facts`)
- Match `FACT_RE` on `mask_code(text)`, so placeholders inside fences or inline code stay literal
  (docs can then describe the syntax). Keys are case-sensitive.
- The replacement is `facts[key].strip("\n")`, done in a single `re.sub`-style pass (built from match
  offsets). Values are never re-scanned, so a value containing `{{facts:x}}` or `$` stays literal.
- Unknown key → the placeholder is left untouched. One `logging.warning("%s: unknown fact block
  {{facts:%s}}", page, key)` is logged per distinct key per call.
- Malformed placeholders (`{{facts:}}`, `{{ fact:x }}`) do not match and stay as they are.
- Deterministic: `filled`/`unknown` are sorted and distinct.

### 4.8 Whitespace normalization (`normalize_markdown`)
1. Remove one leading U+FEFF. `\r\n` and `\r` → `\n`.
2. Outside fences: strip trailing spaces and tabs from each line (two-space hard breaks are lost on
   purpose, which keeps output deterministic), and collapse runs of 2+ blank lines into one.
3. Inside fences: content untouched (line endings were already normalized).
4. Strip leading blank lines. The result ends with exactly one `\n`. All-whitespace input → `""`.
5. Idempotent: `normalize_markdown(normalize_markdown(x)) == normalize_markdown(x)`.
Tabs are not expanded, and no line rewrapping, heading or list renumbering happens.

## 5. Requires from T1 (`core/models/plan.py`) and T5 (`core/models/repo.py`)
```python
class PageKind(StrEnum):
    README = "readme"        # must equal T6 ManifestEntry.kind for the README
    OVERVIEW = "overview"
    COMPONENT = "component"

class PageSpec(BaseModel):
    id: str                  # stable page id; README uses "readme" (T6)
    path: str                # repo-relative POSIX: "README.md" or "<docs_dir>/<slug>.md"
    kind: PageKind
    title: str
    summary: str = ""        # one line from the Outliner; used for index/related lists
    source_paths: list[str]  # globs (not used by T8)
    sections: list[str] = [] # planned headings (not used by T8)
    links_to: list[str] = [] # page ids of cross-links

class DocPlan(BaseModel):
    pages: list[PageSpec]    # ordered; exactly one kind=README page (path "README.md")
    # nice-to-have: page(id) -> PageSpec | None, readme -> PageSpec

class Section(BaseModel):
    heading: str
    body: str                # markdown; may contain {{facts:*}}

class PageDraft(BaseModel):
    page_id: str
    summary: str = ""
    sections: list[Section]
```
- T8 adds a template for **every** `PageKind` member. If T1 names the kinds differently (e.g.
  `topic` instead of `overview`), the template files are renamed to match. Test 9 enforces full
  coverage.
- If T1 keeps the README out of `DocPlan.pages`, then `render_page`/`build_link_targets` need a
  `readme_path: str = "README.md"` parameter instead. T8 assumes the README is in `pages`, which
  matches T6 listing the README in manifest `files`.
- From T5: `FileIndex.files: tuple[FileEntry, ...]` with `FileEntry.path` (repo-relative POSIX).
  T8 uses `index.paths()` if T1 provides it, else `{f.path for f in index.files}`.
- pydantic comes from T1. T8 adds no dependency.

## 6. Test plan
Pure unit tests, no filesystem except package resources. Helpers `make_plan()` (README + overview
+ two components under `docs/architecture/`) and `make_index(paths)`.

**`_markdown`**
1. `fenced_spans`: backtick and tilde fences; a longer closing fence closes; a shorter one does not;
   up to 3 leading spaces allowed; an unclosed fence runs to EOF; ``` inside a ~~~ block does not
   close it.
2. `code_spans`: single and double backticks (``` `` a`b `` ```); an unmatched backtick is literal; a
   span does not cross a blank line; spans inside fences are not reported.
3. `mask_code` preserves length and newline positions.
4. `normalize_markdown`: CRLF/CR/BOM handling; trailing whitespace stripped outside fences but kept
   inside; 3 blank lines → 1 outside a fence but kept inside; leading blanks stripped; exactly one
   final `\n`; `""` and `"  \n\n"` → `""`; idempotence (parametrized over all fixtures).
5. `close_unclosed_fence` appends a matching fence (same char and length) and is a no-op otherwise.

**render**
6. The golden example from §4.2 renders byte-exactly. The README page has a `## Documentation`
   index with links relative to root, in DocPlan order.
7. Determinism: two calls give equal output. Values containing `$title`, `${x}`, `$$` and
   `{{facts:k}}` pass through literally.
8. Empty handling: empty `summary`, empty `links_to` and no non-README pages give no stray blank
   runs or headings. A fully empty section is skipped; a heading-only section is kept. `"## Foo\n"`
   as a heading gives `## Foo`. A body with an unclosed fence gets closed, and the next section still
   renders as a heading.
9. `load_templates()` returns every `PageKind` member. Every packaged template passes
   `validate_template`.
10. Templates load through `importlib.resources`, which catches missing package data.
11. `validate_template` rejects an unknown identifier (`$foo`), a missing `$sections`, and an invalid
    `$` usage (`$ 1`).
12. `related`: unknown id and self id are skipped with a warning (`caplog`), and duplicates collapse.
13. `draft.page_id` mismatch or a spec missing from the plan → `ValueError`.
14. `relative_link` table from §4.3, plus same-dir links and a path with a space (`%20`).

**fact_blocks**
15. Known keys are filled, including a multi-line value with surrounding newlines stripped, and
    `{{ facts:commands }}` with inner whitespace. `filled` is sorted and distinct.
16. An unknown key stays verbatim, appears in `unknown`, and logs exactly one warning even when it
    occurs twice.
17. Placeholders inside a fence or an inline code span are untouched.
18. A value containing `{{facts:other}}` is not re-expanded. Malformed placeholders are untouched.
19. `find_fact_keys` returns keys in order with duplicates and ignores code.

**links**
20. Extraction: inline with a title, an angle-bracket dest with a space, a dest with balanced parens,
    link text with nested brackets and a code span, an escaped `\[x\](y)` (not a link), images (not
    returned), links in fences/code spans (not returned), ref def plus ref use, a code path with a
    `:12` suffix. Offsets and line numbers are correct.
21. Skipped as not validatable: `https://`, `mailto:`, `//host`, `#anchor`; bare-name code spans;
    code spans containing glob or `..` characters.
22. `resolve_link` table: from `docs/architecture/cli.md`, `../../src/a.py` → `src/a.py`, `pipeline.md` →
    `docs/architecture/pipeline.md`, `/src/a.py` → `src/a.py`, `../../../x` → None, `../..` → `""`,
    and from `README.md`, `docs/architecture/cli.md` → itself.
23. **Docs-dir → repo file resolves**: `[app](../../src/cli/app.py)` on `docs/architecture/cli.md` is
    valid when `src/cli/app.py` is in the index. `[app](src/cli/app.py)` on the same page is broken
    (`NOT_FOUND`) with `suggestion == "../../src/cli/app.py"`.
24. Page-to-page links resolve through DocPlan paths even though the index has no docs files. A link
    to a page not in the plan is broken.
25. Directory links (`../../src/`, `../../src`) are valid. A missing dir is broken. Fragment and query
    are stripped before lookup (`cli.md#flags` is valid). Percent-encoded names are decoded.
26. Reasons: `[x]()` → EMPTY; escaping → ESCAPES_REPO; `[t][nolabel]` → UNDEFINED_REF; a broken
    ref def reports both the def and its use.
27. Code path `src/missing.py` → NOT_FOUND. `src/cli/app.py:10-20` is valid.
28. `drop_broken_links`: inline → text kept (code span in the text preserved); ref def line removed
    and its use → text; code path unchanged; valid links and external URLs untouched. One warning is
    logged per drop. Re-validating the result gives only CODE_PATH items.
29. `drop_broken_links` with a stale report (text mismatch) → `ValueError`. A duplicate report is
    applied once.
30. End-to-end acceptance: `render_page` → `fill_facts` → `validate_links` → `drop_broken_links` →
    `normalize_markdown` on a fixture page with good and bad links and one known plus one unknown
    fact. The output equals a golden string, and running it twice gives identical bytes.

## 7. Out of scope / deferred
- Assemble stage orchestration, the LLM repair round, and marker injection (T6 `apply_marker`).
- Real template content and wording (later "prompts and templates" step). Templates with an
  optional per-section layout (named slots).
- Anchor/heading-slug validation (`#section`, `page.md#section`).
- Validation of raw HTML (`<a href>`, `<img>`), images, autolinks, indented code blocks, and
  shortcut reference links.
- Auto-rewriting root-relative mistakes (only `suggestion` is provided; see Q2).
- Symbol validation in code spans (`verify` tool task).
- Computing fact values (`analysis/facts.py`); T8 only substitutes pre-rendered strings.

## 8. Open questions
1. **Broken code paths**: currently reported and warned but left in the text. Alternatives: remove
   the backticks, or have Assemble fail/repair only. Which one?
2. **Auto-fix**: should `drop_broken_links` (or a new `fix_links`) apply `suggestion` automatically
   instead of dropping? It is deterministic and would remove most LLM path mistakes without a repair
   round.
3. **Leading-`/` links**: accepted as repo-root-relative (GitHub behavior) but they break on other
   renderers. Should they be rewritten to page-relative form, or treated as broken?
4. **Page kinds**: is `readme` / `overview` / `component` the final set for T1? Is `overview` a
   separate kind or just a component page?
5. **Files outside FileIndex** (large or binary files, `LICENSE` if excluded): links to them are
   reported broken. Should `build_link_targets` accept the raw `git ls-files` list as an extra
   allowlist for existence checks, while FileIndex still governs readability?
6. **Stripping trailing whitespace** removes markdown two-space hard breaks. Is that acceptable, or
   should exactly-two-space endings be preserved?
