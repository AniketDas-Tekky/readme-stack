"""Run the README agent: the only module that imports the ``ai`` SDK.

It builds the SDK model from an :class:`LLMConfig`, wraps :class:`RepoTools` as SDK tools,
assembles the prompts for the mode, runs one agent loop and returns the README markdown.
"""

from __future__ import annotations

import string
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from typing import Literal

import ai
import pydantic

from .config import LLMConfig
from .git import Diff
from .tools import DEFAULT_MAX_LINES, RepoTools

Mode = Literal["create", "update"]
MAX_TOOL_CALLS = 80

TOOL_LIMIT_ERROR = "error: tool call limit reached; write the README now with what you know"
TRUNCATION_NOTE = (
    "The diff above was truncated. Use read_file to inspect the full contents of the changed "
    "files before editing sections that depend on them."
)


class AgentError(Exception):
    """LLM/agent failure; the CLI maps it to exit code 1."""


class ReadmeOutput(pydantic.BaseModel):
    markdown: str = pydantic.Field(description="The complete README.md content")


@dataclass(frozen=True)
class ReadmeRequest:
    mode: Mode
    repo_name: str
    current_readme: str | None = None  # required for update
    diff: Diff | None = None  # required for update


def load_prompt(name: str) -> str:
    """Load ``readme_stack/prompts/<name>.md`` from the package."""
    return (resources.files("readme_stack") / "prompts" / f"{name}.md").read_text(encoding="utf-8")


def build_prompts(req: ReadmeRequest, tools: RepoTools) -> tuple[str, str]:
    """Return the (system, user) prompts for the request. Pure apart from reading prompts."""
    system = load_prompt("system")
    if req.mode == "create":
        user = string.Template(load_prompt("create")).substitute(
            repo_name=req.repo_name,
            file_tree=tools.list_files(""),
        )
    elif req.mode == "update":
        if req.current_readme is None or req.diff is None:
            raise ValueError("update mode requires current_readme and diff")
        user = string.Template(load_prompt("update")).substitute(
            repo_name=req.repo_name,
            current_readme=req.current_readme,
            diff_stat=req.diff.stat,
            diff_patch=req.diff.patch,
            truncation_note=TRUNCATION_NOTE if req.diff.truncated else "",
        )
    else:
        raise ValueError(f"unknown mode: {req.mode!r}")
    return system, user


def build_model(cfg: LLMConfig) -> ai.Model:
    """Build the SDK model for the configured provider, model id and API key."""
    provider = ai.get_provider(cfg.provider, api_key=cfg.api_key)
    return ai.Model(id=cfg.model, provider=provider)


def make_sdk_tools(tools: RepoTools, log: Callable[[str], None] | None = None) -> list:
    """Wrap ``tools`` as ``list_files`` / ``read_file`` SDK tools sharing one call budget."""
    calls = 0

    def _admit(entry: str) -> bool:
        nonlocal calls
        calls += 1
        if log is not None:
            log(f"tool: {entry}")
        # Read the module global at call time so tests can monkeypatch it.
        return calls <= MAX_TOOL_CALLS

    async def list_files(path: str = "") -> str:
        if not _admit(f"list_files {path or '.'}"):
            return TOOL_LIMIT_ERROR
        return tools.list_files(path)

    async def read_file(path: str, start_line: int = 1, max_lines: int = DEFAULT_MAX_LINES) -> str:
        if not _admit(f"read_file {path}"):
            return TOOL_LIMIT_ERROR
        return tools.read_file(path, start_line, max_lines)

    # The RepoTools docstrings are the tool descriptions the model sees.
    list_files.__doc__ = RepoTools.list_files.__doc__
    read_file.__doc__ = RepoTools.read_file.__doc__
    return [ai.tool(list_files), ai.tool(read_file)]


async def generate_readme(
    cfg: LLMConfig,
    tools: RepoTools,
    req: ReadmeRequest,
    *,
    model: ai.Model | None = None,
    log: Callable[[str], None] | None = None,
) -> str:
    """Run the agent and return the README markdown, ending in exactly one newline."""
    system, user = build_prompts(req, tools)
    messages = [ai.system_message(system), ai.user_message(user)]
    agent = ai.Agent(tools=make_sdk_tools(tools, log))
    try:
        model = model or build_model(cfg)
        async with agent.run(model, messages, output_type=ReadmeOutput) as stream:
            async for _ in stream:
                pass
            output = stream.output
    except Exception as e:
        message = f"LLM run failed: {type(e).__name__}: {e}"
        if cfg.api_key:
            message = message.replace(cfg.api_key, "***")
        raise AgentError(message) from e

    markdown = output.markdown.strip()
    if not markdown:
        raise AgentError("model returned an empty README")
    return markdown + "\n"
