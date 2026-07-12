#core/ai_provider.py
"""Lean, environment-variable-only LLM provider abstraction.

Covers one-shot text completion calls only (dev_agent.py planner/writer,
code_helper.py, investigate.py synthesis, etc). Does NOT cover the Gemini
Live real-time voice session in main.py / screen_processor.py — that stays
hardcoded to Gemini Live and is untouched by anything in this module.
"""
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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


# ---------------------------------------------------------------------------
# Coding/text failover router.
#
# get_provider() above picks exactly ONE provider and is left untouched (it's
# also used by ui.py purely to report text-provider readiness). Everything
# below is additive: a bounded, provider-aware failover chain for coding/text
# completion calls only. It has nothing to do with the Gemini Live voice
# session, which never goes through this module.
# ---------------------------------------------------------------------------

RECOVERABLE_CATEGORIES = frozenset({
    "rate_limit", "payment_required", "capacity", "unavailable", "model_unavailable",
})


def classify_provider_error(error: Exception) -> str:
    """Normalize a provider exception into a diagnostic category.

    Only categories in RECOVERABLE_CATEGORIES should trigger failover to the
    next configured provider. Everything else (bad prompt, auth misconfig,
    a real bug) is "unknown" and must fail fast instead of being silently
    masked by switching providers.
    """
    msg = str(error).lower()

    if any(s in msg for s in ("429", "rate limit", "rate_limit", "too many requests", "quota", "resource_exhausted")):
        return "rate_limit"
    if "402" in msg or "payment" in msg:
        return "payment_required"
    if "capacity" in msg or "overloaded" in msg or "503" in msg:
        return "capacity"
    if "unavailable" in msg or "timeout" in msg or "timed out" in msg or "connection" in msg:
        return "unavailable"
    if "model not found" in msg or "model_not_found" in msg or "does not exist" in msg or "not supported" in msg:
        return "model_unavailable"
    return "unknown"


def is_recoverable_provider_error(error: Exception) -> bool:
    return classify_provider_error(error) in RECOVERABLE_CATEGORIES


@dataclass
class ProviderAttempt:
    provider_id: str
    success: bool
    error_category: str = ""


class AllProvidersFailedError(Exception):
    """Every configured coding/text provider failed with a recoverable error."""

    def __init__(self, attempts: list[ProviderAttempt]):
        self.attempts = attempts
        summary = "; ".join(f"{a.provider_id}={a.error_category}" for a in attempts) or "no providers configured"
        super().__init__(f"All configured coding providers failed ({summary}).")


def build_failover_chain() -> list[AIProvider]:
    """Ordered, bounded list of usable providers for coding/text completion.

    Default order: the OpenAI-compatible gateway PRIMARY (if LLM_API_KEY +
    LLM_BASE_URL + LLM_MODEL are all set) — e.g. a FreeLLM/omni-routing
    gateway — then Gemini as FALLBACK (if GEMINI_API_KEY is set). This
    mirrors the intended architecture: Gemini Live stays the real-time voice
    path (untouched, separate from this module entirely), while coding/text
    calls prefer the omni-routing gateway and only drop to Gemini on a
    recoverable provider error. A provider that fails to construct
    (missing/partial credentials) is skipped, not treated as a failure.
    Raises ProviderConfigError only if nothing at all is configured.
    """
    chain: list[AIProvider] = []

    has_custom = all(
        os.environ.get(k, "").strip()
        for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")
    )
    if has_custom:
        try:
            chain.append(OpenAICompatibleProvider())
        except ProviderConfigError:
            pass

    if os.environ.get("GEMINI_API_KEY", "").strip():
        try:
            chain.append(GeminiProvider())
        except ProviderConfigError:
            pass

    if not chain:
        raise ProviderConfigError(
            "No AI provider configured. Set either LLM_API_KEY + LLM_BASE_URL + LLM_MODEL "
            "(OpenAI-compatible endpoint), or GEMINI_API_KEY (Gemini)."
        )
    return chain


def complete_with_failover(prompt: str, model: str | None = None) -> tuple[AIResponse, list[ProviderAttempt]]:
    """Coding/text completion with bounded provider failover.

    Tries each configured provider in order, preserving the same prompt.
    `model` is ONLY forced onto a Gemini provider — an OpenAI-compatible
    gateway always uses its own configured LLM_MODEL (which may be "auto" /
    an omni route), never a Gemini model name. Provider selection and model
    selection are deliberately separate concerns.

    On a recoverable error (rate limit, payment/quota, capacity, temporary
    unavailability, unsupported model) the router moves to the next
    configured provider. On any other error it raises immediately — a real
    bug or bad request must not be silently masked by switching providers.
    The chain is bounded by the number of configured providers: no infinite
    retries, no retry storm.
    """
    chain = build_failover_chain()
    attempts: list[ProviderAttempt] = []

    for provider in chain:
        try:
            kwargs = {"model": model} if (model and provider.provider_id == "gemini") else {}
            response = provider.complete(prompt, **kwargs)
        except Exception as e:
            category = classify_provider_error(e)
            attempts.append(ProviderAttempt(provider.provider_id, False, category))
            print(f"[AIProvider] {provider.provider_id} -> {category} (attempt {len(attempts)}/{len(chain)})")
            if category not in RECOVERABLE_CATEGORIES:
                raise
            continue
        attempts.append(ProviderAttempt(provider.provider_id, True))
        if len(attempts) > 1:
            print(f"[AIProvider] {provider.provider_id} -> ok (failed over after {len(attempts) - 1} attempt(s))")
        return response, attempts

    raise AllProvidersFailedError(attempts)
