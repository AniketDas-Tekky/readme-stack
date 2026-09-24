"""Command-line interface for readme-stack.

Minimal stub: supports ``--help`` and ``--version`` only. The full flow is added later.
"""

from __future__ import annotations

import argparse
from collections.abc import Awaitable, Callable, Mapping

from readme_stack import __version__

# Narrowed to Callable[[LLMConfig, RepoTools, ReadmeRequest], Awaitable[str]] in the full CLI.
GenerateFn = Callable[..., Awaitable[str]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="readme-stack",
        description="Generate or update a repository README with an LLM agent.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    generate: GenerateFn | None = None,
) -> int:
    """Entry point. Returns the process exit code."""
    # env and generate are unused until the full CLI is implemented.
    build_parser().parse_args(argv)
    return 0
