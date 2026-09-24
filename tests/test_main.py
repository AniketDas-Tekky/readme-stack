import pytest

from readme_stack.main import get_input, main, run


def test_get_input(monkeypatch):
    monkeypatch.setenv("INPUT_README_PATH", " docs/README.md ")
    assert get_input("readme-path") == "docs/README.md"


def test_get_input_default(monkeypatch):
    monkeypatch.delenv("INPUT_README_PATH", raising=False)
    assert get_input("readme-path", "README.md") == "README.md"


def test_run_counts_lines(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Title\n\nBody\n")
    assert run(readme) == f"{readme} has 3 lines"


def test_run_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        run(tmp_path / "missing.md")


def test_main_writes_output(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text("hello\n")
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.delenv("INPUT_README_PATH", raising=False)

    assert main() == 0
    assert output.read_text().startswith("result=")
