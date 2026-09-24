from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import readme_stack
from readme_stack.agent import AgentError
from readme_stack.cli import main

ENV = {"ANTHROPIC_API_KEY": "k"}
MARKDOWN = "# Generated\n\nFake README.\n"


class FakeGenerate:
    """Async stand-in for ``agent.generate_readme`` that records its calls."""

    def __init__(self, result: str = MARKDOWN, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple] = []

    async def __call__(self, cfg, tools, req, *, log=None):
        self.calls.append((cfg, tools, req, log))
        if self.error is not None:
            raise self.error
        return self.result

    @property
    def called(self) -> bool:
        return bool(self.calls)

    @property
    def last(self):
        return self.calls[-1]


@pytest.fixture
def repo(git_repo):
    git_repo.commit({"src/app.py": "print('hi')\n"}, message="initial")
    return git_repo


def run(argv: list[str], gen: FakeGenerate, env: dict[str, str] | None = None) -> int:
    return main(argv, env=ENV if env is None else env, generate=gen)


def test_help_lists_options(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for option in ("--diff", "--model", "--dry-run"):
        assert option in out


def test_no_api_key(repo, capsys):
    gen = FakeGenerate()
    assert run([str(repo)], gen, env={}) == 2
    assert "no API key" in capsys.readouterr().err
    assert not gen.called


def test_repo_outside_git(tmp_path, capsys):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    gen = FakeGenerate()
    assert run([str(outside)], gen) == 2
    assert "not a git repository" in capsys.readouterr().err
    assert not gen.called


def test_create_mode_writes_readme(repo):
    gen = FakeGenerate()
    assert run([str(repo)], gen) == 0
    assert (repo / "README.md").read_text(encoding="utf-8") == MARKDOWN
    cfg, tools, req, log = gen.last
    assert req.mode == "create"
    assert req.repo_name == repo.name
    assert req.current_readme is None and req.diff is None
    assert "src/app.py" in tools.files
    assert cfg.provider == "anthropic"
    assert log is None
    assert not list(repo.glob(".README.md.*.tmp"))


def test_create_mode_from_subdirectory_uses_root(repo):
    gen = FakeGenerate()
    assert run([str(repo / "src")], gen) == 0
    assert (repo / "README.md").read_text(encoding="utf-8") == MARKDOWN
    assert not (repo / "src" / "README.md").exists()


def test_create_mode_overwrites_existing(repo, capsys):
    repo.commit({"README.md": "# Old\n"})
    gen = FakeGenerate()
    assert run([str(repo)], gen) == 0
    assert (repo / "README.md").read_text(encoding="utf-8") == MARKDOWN
    assert "wrote README.md (3 lines)" in capsys.readouterr().err


def test_dry_run_prints_and_writes_nothing(repo, capsys):
    gen = FakeGenerate()
    assert run([str(repo), "--dry-run"], gen) == 0
    assert capsys.readouterr().out == MARKDOWN
    assert not (repo / "README.md").exists()


def test_dry_run_leaves_existing_readme(repo, capsys):
    repo.commit({"README.md": "# Old\n"})
    gen = FakeGenerate()
    assert run([str(repo), "--dry-run"], gen) == 0
    assert capsys.readouterr().out == MARKDOWN
    assert (repo / "README.md").read_text(encoding="utf-8") == "# Old\n"


def test_diff_without_readme(repo, capsys):
    repo.commit({"src/app.py": "print('bye')\n"})
    gen = FakeGenerate()
    assert run([str(repo), "--diff", "HEAD~1..HEAD"], gen) == 2
    assert "--diff requires an existing README.md" in capsys.readouterr().err
    assert not gen.called


def test_empty_diff_is_noop(repo, capsys):
    repo.commit({"README.md": "# Old\n"})
    gen = FakeGenerate()
    assert run([str(repo), "--diff", "HEAD..HEAD"], gen) == 0
    assert "no changes in HEAD..HEAD" in capsys.readouterr().err
    assert not gen.called
    assert (repo / "README.md").read_text(encoding="utf-8") == "# Old\n"


def test_bad_range(repo, capsys):
    repo.commit({"README.md": "# Old\n"})
    gen = FakeGenerate()
    assert run([str(repo), "--diff", "nope..HEAD"], gen) == 2
    assert capsys.readouterr().err.startswith("error:")
    assert not gen.called


def test_update_mode(repo, capsys):
    repo.commit({"README.md": "# Old\n"})
    repo.commit({"src/app.py": "print('bye')\n"})
    gen = FakeGenerate()
    assert run([str(repo), "--diff", "HEAD~1..HEAD"], gen) == 0
    _, _, req, _ = gen.last
    assert req.mode == "update"
    assert req.current_readme == "# Old\n"
    assert req.diff is not None and not req.diff.empty
    assert "src/app.py" in req.diff.patch
    assert (repo / "README.md").read_text(encoding="utf-8") == MARKDOWN
    assert "updating README.md" in capsys.readouterr().err


def test_update_unchanged(repo, capsys):
    repo.commit({"README.md": MARKDOWN})
    repo.commit({"src/app.py": "print('bye')\n"})
    before = (repo / "README.md").stat().st_mtime_ns
    gen = FakeGenerate(result=MARKDOWN)
    assert run([str(repo), "--diff", "HEAD~1..HEAD"], gen) == 0
    assert "README.md already up to date" in capsys.readouterr().err
    assert (repo / "README.md").read_text(encoding="utf-8") == MARKDOWN
    assert (repo / "README.md").stat().st_mtime_ns == before


def test_agent_error(repo, capsys):
    cause = RuntimeError("provider secret detail")
    error = AgentError("LLM run failed: boom")
    error.__cause__ = cause
    gen = FakeGenerate(error=error)
    assert run([str(repo)], gen) == 1
    err = capsys.readouterr().err
    assert "error: LLM run failed: boom" in err
    assert "Traceback" not in err
    assert "provider secret detail" not in err
    assert not (repo / "README.md").exists()


def test_agent_error_verbose_has_no_traceback(repo, capsys):
    gen = FakeGenerate(error=AgentError("boom"))
    assert run([str(repo), "-v"], gen) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_unexpected_error(repo, capsys):
    gen = FakeGenerate(error=ValueError("weird"))
    assert run([str(repo)], gen) == 1
    err = capsys.readouterr().err
    assert "error: unexpected failure: weird" in err
    assert "Traceback" not in err


def test_unexpected_error_verbose_traceback_masks_key(repo, capsys):
    gen = FakeGenerate(error=ValueError("bad key sk-secret"))
    assert run([str(repo), "-v"], gen, env={"OPENAI_API_KEY": "sk-secret"}) == 1
    err = capsys.readouterr().err
    assert "error: unexpected failure: bad key ***" in err
    assert "Traceback" in err
    assert "sk-secret" not in err


@pytest.mark.parametrize("argv", [["--diff", "HEAD~1..HEAD"], []])
def test_symlinked_readme_is_refused(repo, tmp_path, capsys, argv):
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    (repo / "README.md").symlink_to(outside)
    repo.commit({"src/app.py": "print('bye')\n"})
    gen = FakeGenerate()
    assert run([str(repo), *argv], gen) == 2
    assert "README.md is a symlink" in capsys.readouterr().err
    assert not gen.called
    assert (repo / "README.md").is_symlink()
    assert outside.read_text(encoding="utf-8") == "secret\n"


def test_keyboard_interrupt(repo):
    gen = FakeGenerate(error=KeyboardInterrupt())
    assert run([str(repo)], gen) == 130


def test_verbose_passes_log(repo, capsys):
    gen = FakeGenerate()
    assert run([str(repo), "-v", "--dry-run"], gen) == 0
    log = gen.last[3]
    assert callable(log)
    log("read_file(path='x')")
    assert "read_file(path='x')" in capsys.readouterr().err


def test_no_verbose_log_is_none(repo):
    gen = FakeGenerate()
    assert run([str(repo), "--dry-run"], gen) == 0
    assert gen.last[3] is None


def test_model_override(repo, capsys):
    gen = FakeGenerate()
    assert run([str(repo), "--model", "custom", "--dry-run"], gen) == 0
    assert gen.last[0].model == "custom"
    err = capsys.readouterr().err
    assert "anthropic/custom" in err


def test_api_key_never_printed(repo, capsys):
    env = {"ANTHROPIC_API_KEY": "sk-very-secret"}
    gen = FakeGenerate()
    assert run([str(repo), "-v"], gen, env=env) == 0
    captured = capsys.readouterr()
    assert "sk-very-secret" not in captured.out + captured.err


def test_default_env_is_os_environ(repo, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    gen = FakeGenerate()
    assert main([str(repo), "--dry-run"], generate=gen) == 0
    assert gen.last[0].provider == "openai"


def test_python_m_version():
    result = subprocess.run(
        [sys.executable, "-m", "readme_stack", "--version"],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).parent,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"readme-stack {readme_stack.__version__}"
