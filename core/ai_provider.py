#core/ai_provider.py
"""Lean, environment-variable-only LLM provider abstraction.

Covers one-shot text completion calls only (dev_agent.py planner/writer,
etc). Does NOT cover the Gemini Live real-time voice session in main.py /
screen_processor.py — that stays hardcoded to Gemini Live.
"""
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


class ProviderConfigError(Exception):
    """Raised when no valid provider credentials are found in the environment."""


@dataclass
class AIResponse:
    text: str


class AIProvider(ABC):
    provider_id: str

    @abstractmethod
    def complete(self, prompt: str, model: str | None = None) -> AIResponse:
        ...


class GeminiProvider(AIProvider):
    provider_id = "gemini"

    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise ProviderConfigError("GEMINI_API_KEY is not set in the environment.")
        from google import genai
        self._client = genai.Client(api_key=api_key)

    def complete(self, prompt: str, model: str | None = None) -> AIResponse:
        response = self._client.models.generate_content(
            model=model or "gemini-2.5-flash",
            contents=prompt,
        )
        return AIResponse(text=response.text)


class OpenAICompatibleProvider(AIProvider):
    provider_id = "openai_compatible"

    def __init__(self):
        api_key  = os.environ.get("LLM_API_KEY", "").strip()
        base_url = os.environ.get("LLM_BASE_URL", "").strip()
        model    = os.environ.get("LLM_MODEL", "").strip()
        if not (api_key and base_url and model):
            raise ProviderConfigError(
                "LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL must all be set for OpenAICompatibleProvider."
            )
        self._model = model
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def complete(self, prompt: str, model: str | None = None) -> AIResponse:
        response = self._client.chat.completions.create(
            model=model or self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        return AIResponse(text=response.choices[0].message.content or "")


def get_provider() -> AIProvider:
    """
    Select the active AI provider from environment variables only.

    Priority:
      1. LLM_API_KEY + LLM_BASE_URL + LLM_MODEL all set -> OpenAICompatibleProvider
      2. GEMINI_API_KEY set                             -> GeminiProvider
      3. otherwise                                       -> ProviderConfigError
    """
    has_custom = all(
        os.environ.get(k, "").strip()
        for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")
    )
    if has_custom:
        return OpenAICompatibleProvider()

    if os.environ.get("GEMINI_API_KEY", "").strip():
        return GeminiProvider()

    raise ProviderConfigError(
        "No AI provider configured. Set either LLM_API_KEY + LLM_BASE_URL + LLM_MODEL "
        "(OpenAI-compatible endpoint), or GEMINI_API_KEY (Gemini)."
    )
