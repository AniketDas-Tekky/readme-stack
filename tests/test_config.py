import pytest

from readme_stack.config import ConfigError, LLMConfig, resolve_llm

NO_KEY_MESSAGE = "no API key found: set ANTHROPIC_API_KEY or OPENAI_API_KEY"


def test_anthropic_key_only() -> None:
    cfg = resolve_llm({"ANTHROPIC_API_KEY": "sk-ant"})
    assert cfg == LLMConfig("anthropic", "claude-sonnet-5", "sk-ant")


def test_openai_key_only() -> None:
    cfg = resolve_llm({"OPENAI_API_KEY": "sk-oai"})
    assert cfg == LLMConfig("openai", "gpt-5", "sk-oai")


def test_no_keys_raises() -> None:
    with pytest.raises(ConfigError) as exc:
        resolve_llm({})
    assert str(exc.value) == NO_KEY_MESSAGE


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_blank_keys_treated_as_unset(blank: str) -> None:
    with pytest.raises(ConfigError, match=NO_KEY_MESSAGE):
        resolve_llm({"ANTHROPIC_API_KEY": blank, "OPENAI_API_KEY": blank})


def test_blank_anthropic_key_falls_through_to_openai() -> None:
    cfg = resolve_llm({"ANTHROPIC_API_KEY": "  ", "OPENAI_API_KEY": "sk-oai"})
    assert cfg.provider == "openai"
    assert cfg.api_key == "sk-oai"


def test_key_whitespace_is_stripped() -> None:
    cfg = resolve_llm({"OPENAI_API_KEY": "  sk-oai\n"})
    assert cfg.api_key == "sk-oai"


def test_model_override() -> None:
    cfg = resolve_llm({"ANTHROPIC_API_KEY": "sk-ant"}, model="custom-id")
    assert cfg.model == "custom-id"


@pytest.mark.parametrize("blank", ["", "  "])
def test_blank_model_uses_default(blank: str) -> None:
    cfg = resolve_llm({"OPENAI_API_KEY": "sk-oai"}, model=blank)
    assert cfg.model == "gpt-5"


def test_repr_hides_key() -> None:
    cfg = resolve_llm({"ANTHROPIC_API_KEY": "sk-secret-value"})
    assert "sk-secret-value" not in repr(cfg)


def test_both_keys_prefers_anthropic() -> None:
    cfg = resolve_llm({"ANTHROPIC_API_KEY": "sk-ant", "OPENAI_API_KEY": "sk-oai"})
    assert cfg.provider == "anthropic"
    assert cfg.api_key == "sk-ant"
