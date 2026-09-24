from pathlib import Path

import pytest

from readme_stack.tools import MAX_LIST_ENTRIES, MAX_READ_BYTES, RepoTools

FILES = (
    "README.md",
    "pyproject.toml",
    "src/readme_stack/__init__.py",
    "src/readme_stack/cli.py",
    "src/readme_stack/prompts/system.md",
    "tests/test_cli.py",
)


def make_tools(root: Path, files: dict[str, str | bytes]) -> RepoTools:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    return RepoTools(root=root, files=tuple(sorted(files)))


@pytest.fixture
def tools(tmp_path: Path) -> RepoTools:
    return make_tools(tmp_path, {f: f"# {f}\n" for f in FILES})


def numbered(n: int) -> str:
    return "".join(f"line {i}\n" for i in range(1, n + 1))


# 1
def test_list_files_root_tree(tools: RepoTools) -> None:
    assert tools.list_files("") == "\n".join(
        [
            "6 files under .",
            "README.md",
            "pyproject.toml",
            "src/",
            "  readme_stack/",
            "    __init__.py",
            "    cli.py",
            "    prompts/",
            "      system.md",
            "tests/",
            "  test_cli.py",
        ]
    )


def test_list_files_dot_is_root(tools: RepoTools) -> None:
    assert tools.list_files(".") == tools.list_files("")


# 2
def test_list_files_subdirectory(tools: RepoTools) -> None:
    expected = "\n".join(
        [
            "3 files under src",
            "readme_stack/",
            "  __init__.py",
            "  cli.py",
            "  prompts/",
            "    system.md",
        ]
    )
    assert tools.list_files("src") == expected
    assert tools.list_files("./src/") == expected


# 3
def test_list_files_no_match(tools: RepoTools) -> None:
    assert tools.list_files("nope") == "error: no files under 'nope'"


def test_list_files_partial_segment_does_not_match(tools: RepoTools) -> None:
    assert tools.list_files("sr") == "error: no files under 'sr'"


# 4
def test_list_files_caps_entries(tmp_path: Path) -> None:
    files = tuple(f"pkg/f{i:03}.py" for i in range(525))
    tools = RepoTools(root=tmp_path, files=files)
    lines = tools.list_files("pkg").splitlines()
    assert lines[0] == "525 files under pkg"
    entries = lines[1:-1]
    assert len(entries) == MAX_LIST_ENTRIES
    assert entries[0] == "f000.py"
    assert entries[-1] == "f499.py"
    assert lines[-1] == "… 25 more files; call list_files with a narrower path"


# 5
@pytest.mark.parametrize("path", ["../x", "/etc", "a\\b", "src/../README.md"])
def test_list_files_invalid_path(tools: RepoTools, path: str) -> None:
    assert tools.list_files(path) == f"error: invalid path '{path}' (use repo-relative paths)"


# 6
def test_read_file_window(tmp_path: Path) -> None:
    tools = make_tools(tmp_path, {"a.txt": numbered(10)})
    out = tools.read_file("a.txt", max_lines=3)
    assert out.splitlines() == [
        "a.txt (lines 1-3 of 10)",
        "    1  line 1",
        "    2  line 2",
        "    3  line 3",
        "… 7 more lines; call read_file with start_line=4",
    ]


def test_read_file_whole_file_has_no_continuation(tmp_path: Path) -> None:
    tools = make_tools(tmp_path, {"a.txt": numbered(2)})
    assert tools.read_file("./a.txt") == "a.txt (lines 1-2 of 2)\n    1  line 1\n    2  line 2"


# 7
def test_read_file_continuation(tmp_path: Path) -> None:
    tools = make_tools(tmp_path, {"a.txt": numbered(10)})
    out = tools.read_file("a.txt", start_line=3, max_lines=2)
    assert out.splitlines() == [
        "a.txt (lines 3-4 of 10)",
        "    3  line 3",
        "    4  line 4",
        "… 6 more lines; call read_file with start_line=5",
    ]


def test_read_file_max_lines_zero_clamped_to_one(tmp_path: Path) -> None:
    tools = make_tools(tmp_path, {"a.txt": numbered(3)})
    out = tools.read_file("a.txt", max_lines=0)
    assert out.splitlines()[:2] == ["a.txt (lines 1-1 of 3)", "    1  line 1"]


# 8
def test_read_file_start_past_end(tmp_path: Path) -> None:
    tools = make_tools(tmp_path, {"a.txt": numbered(3)})
    assert (
        tools.read_file("a.txt", start_line=4)
        == "error: start_line 4 is past the end of 'a.txt' (3 lines)"
    )


def test_read_file_start_zero_clamped(tmp_path: Path) -> None:
    tools = make_tools(tmp_path, {"a.txt": numbered(3)})
    assert tools.read_file("a.txt", start_line=0) == tools.read_file("a.txt")
    assert tools.read_file("a.txt", start_line=0).startswith("a.txt (lines 1-3 of 3)")


# 9
def test_read_file_not_in_allow_list(tmp_path: Path) -> None:
    tools = make_tools(tmp_path, {"a.txt": "x\n"})
    (tmp_path / ".env").write_text("SECRET=1\n")
    assert (
        tools.read_file(".env")
        == "error: '.env' is not a readable file; use list_files to find files"
    )


# 10
@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "a\\b", "src/../a.txt"])
def test_read_file_invalid_path(tmp_path: Path, path: str) -> None:
    (tmp_path.parent / "outside.txt").write_text("nope\n")
    tools = make_tools(tmp_path, {"a.txt": "x\n"})
    assert tools.read_file(path) == f"error: invalid path '{path}' (use repo-relative paths)"


# 11
def test_read_file_empty(tmp_path: Path) -> None:
    tools = make_tools(tmp_path, {"empty.txt": ""})
    assert tools.read_file("empty.txt") == "empty.txt (empty file)"


def test_read_file_invalid_utf8_replaced(tmp_path: Path) -> None:
    tools = make_tools(tmp_path, {"bin.txt": b"ok\n\xff\xfe\n"})
    out = tools.read_file("bin.txt")
    assert out.splitlines() == ["bin.txt (lines 1-2 of 2)", "    1  ok", "    2  ��"]


# 12
def test_read_file_byte_cap(tmp_path: Path) -> None:
    tools = make_tools(tmp_path, {"big.txt": ("x" * 199 + "\n") * 1500})
    out = tools.read_file("big.txt", max_lines=2_000)
    lines = out.splitlines()
    body = lines[1:-1]
    assert 0 < len(body) < 1500
    assert sum(len(line.encode()) + 1 for line in body) <= MAX_READ_BYTES
    assert lines[0] == f"big.txt (lines 1-{len(body)} of 1500)"
    assert lines[-1] == (
        f"… {1500 - len(body)} more lines; call read_file with start_line={len(body) + 1}"
    )


def test_read_file_single_oversized_line_truncated(tmp_path: Path) -> None:
    tools = make_tools(tmp_path, {"wide.txt": "y" * (MAX_READ_BYTES * 2) + "\nnext\n"})
    lines = tools.read_file("wide.txt").splitlines()
    assert lines[0] == "wide.txt (lines 1-1 of 2)"
    assert lines[1].startswith("    1  yyy")
    assert len(lines[1].encode()) < MAX_READ_BYTES
    assert lines[2] == "… 1 more lines; call read_file with start_line=2"


# 13
def test_read_file_deleted_after_indexing(tmp_path: Path) -> None:
    tools = make_tools(tmp_path, {"gone.txt": "x\n"})
    (tmp_path / "gone.txt").unlink()
    out = tools.read_file("gone.txt")
    assert out.startswith("error: cannot read 'gone.txt': ")
    assert "No such file" in out
