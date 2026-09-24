"""Command-line interface for readme-stack.

Parses arguments, runs the flow in order (config, git, agent, output), maps errors to exit
codes and writes the result. It holds no business logic beyond ordering and I/O.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
import traceback
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Protocol

from readme_stack import __version__
from readme_stack.agent import AgentError, Mode, ReadmeRequest, generate_readme
from readme_stack.config import KEY_ENV_VARS, ConfigError, LLMConfig, resolve_llm
from readme_stack.git import Diff, GitError, diff, repo_files, repo_root
from readme_stack.tools import RepoTools

README = "README.md"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

LogFn = Callable[[str], None]


class GenerateFn(Protocol):
    """Signature of :func:`readme_stack.agent.generate_readme` as used by the CLI."""

    def __call__(
        self,
        cfg: LLMConfig,
        tools: RepoTools,
        req: ReadmeRequest,
        *,
        log: LogFn | None = None,
    ) -> Awaitable[str]: ...


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="readme-stack",
        description="Generate or update a repository README with an LLM agent.",
        epilog=(
            "Uses ANTHROPIC_API_KEY or OPENAI_API_KEY, whichever is set. "
            "Exit codes: 0 ok, 1 runtime/LLM failure, 2 usage or configuration error."
        ),
    )
    parser.add_argument(
        "repo",
        nargs="?",
        default=".",
        metavar="REPO",
        help="path inside the git repository (default: current directory)",
    )
    parser.add_argument(
        "--diff",
        metavar="RANGE",
        help="update mode: revise the existing README.md to reflect this git range "
        "(e.g. HEAD~1..HEAD); without it, a README is created from scratch",
    )
    parser.add_argument(
        "--model", metavar="ID", help="override the default model for the detected provider"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the README to stdout instead of writing README.md",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="log agent tool calls to stderr"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _stderr_log(message: str) -> None:
    print(f"  {message}", file=sys.stderr)


def _mask_keys(message: str, env: Mapping[str, str]) -> str:
    """Replace any configured API key value in ``message`` with ``***``."""
    for var in KEY_ENV_VARS.values():
        key = env.get(var, "").strip()
        if key:
            message = message.replace(key, "***")
    return message


def _write_atomic(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via a temp file in the same directory and ``os.replace``."""
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        if path.is_file():
            shutil.copymode(path, tmp)
        else:
            tmp.chmod(0o644)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _run(args: argparse.Namespace, env: Mapping[str, str], generate: GenerateFn) -> int:
    # 1. Key and model.
    try:
        cfg = resolve_llm(env, args.model)
    except ConfigError as e:
        _err(f"error: {e}")
        return EXIT_USAGE

    # 2. Git root.
    try:
        root = repo_root(Path(args.repo))
    except GitError as e:
        _err(f"error: {e}")
        return EXIT_USAGE

    readme_path = root / README
    mode: Mode = "update" if args.diff is not None else "create"
    current_readme: str | None = None
    changes: Diff | None = None

    # 3. Update mode: README must exist; compute the diff.
    if mode == "update":
        if not readme_path.is_file():
            _err(f"error: --diff requires an existing {README}; run without --diff to create one")
            return EXIT_USAGE
        try:
            changes = diff(root, args.diff)
        except GitError as e:
            _err(f"error: {e}")
            return EXIT_USAGE
        if changes.empty:
            _err(f"no changes in {args.diff}; {README} unchanged")
            return EXIT_OK
        current_readme = readme_path.read_text(encoding="utf-8")

    # 4. Tools and request.
    try:
        files = tuple(repo_files(root))
    except GitError as e:
        _err(f"error: {e}")
        return EXIT_USAGE
    tools = RepoTools(root, files)
    req = ReadmeRequest(mode=mode, repo_name=root.name, current_readme=current_readme, diff=changes)

    # 5. Progress.
    verb = "creating" if mode == "create" else "updating"
    _err(f"readme-stack: {verb} {README} with {cfg.provider}/{cfg.model}…")
    log: LogFn | None = _stderr_log if args.verbose else None

    # 6. Run the agent. Only str(err) is printed for AgentError: its cause may hold provider
    # details, so never show a traceback for it.
    try:
        markdown = asyncio.run(generate(cfg, tools, req, log=log))
    except AgentError as e:
        _err(f"error: {e}")
        return EXIT_FAILURE

    # 7. Output.
    if args.dry_run:
        sys.stdout.write(markdown)
        sys.stdout.flush()
        return EXIT_OK
    if mode == "update" and markdown == current_readme:
        _err(f"{README} already up to date")
        return EXIT_OK
    _write_atomic(readme_path, markdown)
    _err(f"wrote {README} ({len(markdown.splitlines())} lines)")
    return EXIT_OK


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    generate: GenerateFn | None = None,
) -> int:
    """Entry point. Returns the process exit code."""
    args = build_parser().parse_args(argv)
    if env is None:
        env = os.environ
    if generate is None:
        generate = generate_readme
    try:
        return _run(args, env, generate)
    except KeyboardInterrupt:
        _err("interrupted")
        return EXIT_INTERRUPTED
    except Exception as e:
        _err(f"error: unexpected failure: {_mask_keys(str(e), env)}")
        if args.verbose:
            traceback.print_exc(file=sys.stderr)
        return EXIT_FAILURE
