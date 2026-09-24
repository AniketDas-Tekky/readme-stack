import subprocess
import sys

import pytest

import readme_stack
from readme_stack.cli import build_parser, main


def test_version_is_set():
    assert readme_stack.__version__
    assert readme_stack.__version__[0].isdigit()


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "readme-stack" in capsys.readouterr().out


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"readme-stack {readme_stack.__version__}"


def test_build_parser_prog():
    assert build_parser().prog == "readme-stack"


def test_main_accepts_injected_env_and_generate():
    assert main([], env={}, generate=None) == 0


def test_python_m_version():
    result = subprocess.run(
        [sys.executable, "-m", "readme_stack", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert readme_stack.__version__ in result.stdout
