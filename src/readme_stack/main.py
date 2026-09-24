"""Entry point for the readme-stack GitHub Action."""

import os
import sys
from pathlib import Path


def get_input(name: str, default: str = "") -> str:
    """Read an action input passed via an INPUT_<NAME> environment variable."""
    key = f"INPUT_{name.upper().replace('-', '_')}"
    return os.environ.get(key, default).strip()


def set_output(name: str, value: str) -> None:
    """Write an action output to $GITHUB_OUTPUT (no-op when run locally)."""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if not output_file:
        print(f"[output] {name}={value}")
        return
    with open(output_file, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


def run(readme_path: Path) -> str:
    """Core logic. Replace with the real implementation."""
    if not readme_path.is_file():
        raise FileNotFoundError(f"README not found: {readme_path}")
    line_count = len(readme_path.read_text(encoding="utf-8").splitlines())
    return f"{readme_path} has {line_count} lines"


def main() -> int:
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd()))
    readme_path = workspace / get_input("readme-path", "README.md")

    try:
        result = run(readme_path)
    except Exception as e:
        print(f"::error::{e}")
        return 1

    print(result)
    set_output("result", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
