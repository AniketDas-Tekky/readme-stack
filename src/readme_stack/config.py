"""Resolve the LLM provider, model and API key from the environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

Provider = Literal["anthropic", "openai"]

KEY_ENV_VARS: dict[Provider, str] = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
DEFAULT_MODELS: dict[Provider, str] = {"anthropic": "claude-sonnet-5", "openai": "gpt-5"}


class ConfigError(Exception):
    """Invalid configuration; the CLI maps it to exit code 2."""


@dataclass(frozen=True)
class LLMConfig:
    provider: Provider
    model: str
    api_key: str = field(repr=False)


def resolve_llm(env: Mapping[str, str] | None = None, model: str | None = None) -> LLMConfig:
    """Pick the provider whose API key is set and the model to use."""
    if env is None:
        env = os.environ
    for provider, var in KEY_ENV_VARS.items():
        api_key = env.get(var, "").strip()
        if api_key:
            chosen = model if model and model.strip() else DEFAULT_MODELS[provider]
            return LLMConfig(provider, chosen, api_key)
    raise ConfigError("no API key found: set ANTHROPIC_API_KEY or OPENAI_API_KEY")
