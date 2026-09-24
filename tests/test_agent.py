import asyncio
import inspect
import json
import re
from pathlib import Path

import ai
import pytest

from readme_stack import agent
from readme_stack.agent import (
    TOOL_LIMIT_ERROR,
    TRUNCATION_NOTE,
    AgentError,
    ReadmeRequest,
    build_model,
    build_prompts,
    generate_readme,
    load_prompt,
    make_sdk_tools,
)
from readme_stack.config import LLMConfig
from readme_stack.git import Diff
from readme_stack.tools import RepoTools

API_KEY = "sk-test-secret-key-123"
CFG = LLMConfig(provider="anthropic", model="claude-sonnet-5", api_key=API_KEY)
PLACEHOLDER = re.compile(r"\$\{?[A-Za-z_]")

FILES = {
    "pyproject.toml": '[project]\nname = "demo"\n',
    "src/demo/__init__.py": "",
    "src/demo/cli.py": "def main():\n    return 0\n",
}


@pytest.fixture
def tools(tmp_path: Path) -> RepoTools:
    for rel, content in FILES.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return RepoTools(root=tmp_path, files=tuple(sorted(FILES)))


def update_request(truncated: bool = False) -> ReadmeRequest:
    return ReadmeRequest(
        mode="update",
        repo_name="demo",
        current_readme="# Demo\n\nOld text about the CLI.\n",
        diff=Diff(
            stat=" src/demo/cli.py | 2 +-\n 1 file changed",
            patch="-    return 0\n+    return 1\n",
            truncated=truncated,
        ),
    )


def call(sdk_tool, **kwargs) -> str:
    return asyncio.run(sdk_tool.fn(**kwargs))


# 1
def test_prompts_load_and_system_names_every_section():
    for name in ("system", "create", "update"):
        assert load_prompt(name).strip()
    system = load_prompt("system")
    for section in (
        "Architecture",
        "Key components",
        "Getting started",
        "Development",
        "Project layout",
    ):
        assert section in system
    assert "markdown" in system


# 2
def test_build_prompts_create(tools):
    system, user = build_prompts(ReadmeRequest(mode="create", repo_name="demo"), tools)
    assert system == load_prompt("system")
    assert "demo" in user
    assert tools.list_files("") in user
    assert not PLACEHOLDER.search(user)


# 3
@pytest.mark.parametrize("truncated", [False, True])
def test_build_prompts_update(tools, truncated):
    req = update_request(truncated)
    _, user = build_prompts(req, tools)
    assert req.current_readme in user
    assert req.diff.stat in user
    assert req.diff.patch in user
    assert (TRUNCATION_NOTE in user) is truncated
    assert not PLACEHOLDER.search(user)


# 4
def test_update_without_diff_or_readme_raises(tools):
    with pytest.raises(ValueError):
        build_prompts(ReadmeRequest(mode="update", repo_name="demo", current_readme="# x"), tools)
    with pytest.raises(ValueError):
        build_prompts(
            ReadmeRequest(mode="update", repo_name="demo", diff=update_request().diff), tools
        )


# 5
def test_sdk_tools_delegate_log_and_limit(tools, monkeypatch):
    monkeypatch.setattr(agent, "MAX_TOOL_CALLS", 3)
    logged: list[str] = []
    list_files, read_file = make_sdk_tools(tools, logged.append)

    assert [t.name for t in (list_files, read_file)] == ["list_files", "read_file"]
    assert list_files.tool.spec.description == inspect.getdoc(RepoTools.list_files)
    assert read_file.tool.spec.description == inspect.getdoc(RepoTools.read_file)
    assert set(read_file.tool.spec.params["properties"]) == {"path", "start_line", "max_lines"}

    assert call(list_files, path="") == tools.list_files("")
    assert call(read_file, path="src/demo/cli.py") == tools.read_file("src/demo/cli.py")
    assert call(read_file, path="src/demo/cli.py", start_line=2, max_lines=1) == tools.read_file(
        "src/demo/cli.py", 2, 1
    )
    # The budget is shared across both tools.
    assert call(list_files, path="src") == TOOL_LIMIT_ERROR
    assert call(read_file, path="pyproject.toml") == TOOL_LIMIT_ERROR

    assert logged == [
        "tool: list_files .",
        "tool: read_file src/demo/cli.py",
        "tool: read_file src/demo/cli.py",
        "tool: list_files src",
        "tool: read_file pyproject.toml",
    ]


def test_sdk_tools_without_log(tools):
    list_files, _ = make_sdk_tools(tools)
    assert call(list_files, path="src").startswith("2 files under src")


# 6
def test_generate_readme_with_fake_model(tools):
    req = ReadmeRequest(mode="create", repo_name="demo")
    _, user = build_prompts(req, tools)
    markdown = "# Demo\n\nA demo project.\n\n"
    model = ai.testing.FakeModel(
        [
            ai.user_message(user),
            ai.assistant_message(ai.testing.tool_call("read_file", path="pyproject.toml")),
            ai.assistant_message(json.dumps({"markdown": markdown})),
        ]
    )
    logged: list[str] = []

    result = asyncio.run(generate_readme(CFG, tools, req, model=model, log=logged.append))

    assert result == "# Demo\n\nA demo project.\n"
    assert logged == ["tool: read_file pyproject.toml"]
    assert not model.unused
    # The tool result reached the model, and the system prompt was sent first.
    last_call = model.calls[-1]
    assert last_call[0].role == "system"
    tool_results = [r for m in last_call for r in m.tool_results]
    assert tool_results[0].result == tools.read_file("pyproject.toml")


# 7
def test_generate_readme_blank_markdown_raises(tools):
    req = ReadmeRequest(mode="create", repo_name="demo")
    _, user = build_prompts(req, tools)
    model = ai.testing.FakeModel(
        [ai.user_message(user), ai.assistant_message(json.dumps({"markdown": "  "}))]
    )
    with pytest.raises(AgentError, match="empty README"):
        asyncio.run(generate_readme(CFG, tools, req, model=model))


def test_generate_readme_non_json_output_raises(tools):
    req = ReadmeRequest(mode="create", repo_name="demo")
    _, user = build_prompts(req, tools)
    model = ai.testing.FakeModel([ai.user_message(user), ai.assistant_message("# not json")])
    with pytest.raises(AgentError, match="LLM run failed"):
        asyncio.run(generate_readme(CFG, tools, req, model=model))


# 8
def test_generate_readme_run_failure_raises_without_key(tools, monkeypatch):
    def boom(self, *args, **kwargs):
        raise RuntimeError(f"auth failed for key {API_KEY}")

    monkeypatch.setattr(ai.Agent, "run", boom)
    model = ai.testing.FakeModel([ai.assistant_message("unused")])
    req = ReadmeRequest(mode="create", repo_name="demo")

    with pytest.raises(AgentError) as excinfo:
        asyncio.run(generate_readme(CFG, tools, req, model=model))

    message = str(excinfo.value)
    assert message.startswith("LLM run failed: RuntimeError:")
    assert API_KEY not in message


def test_generate_readme_update_mode_sends_update_prompt(tools):
    req = update_request()
    _, user = build_prompts(req, tools)
    model = ai.testing.FakeModel(
        [ai.user_message(user), ai.assistant_message(json.dumps({"markdown": "# Demo\n"}))]
    )
    assert asyncio.run(generate_readme(CFG, tools, req, model=model)) == "# Demo\n"


# 9
@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_build_model_passes_provider_and_key(monkeypatch, provider):
    real_get_provider = ai.get_provider
    seen: list[tuple[str, str]] = []

    def fake_get_provider(name, *, api_key=None, **kwargs):
        seen.append((name, api_key))
        return real_get_provider(name, api_key=api_key, **kwargs)

    monkeypatch.setattr(ai, "get_provider", fake_get_provider)
    model = build_model(LLMConfig(provider=provider, model="some-model", api_key=API_KEY))

    assert seen == [(provider, API_KEY)]
    assert isinstance(model, ai.Model)
    assert model.id == "some-model"
    assert API_KEY not in repr(model)
