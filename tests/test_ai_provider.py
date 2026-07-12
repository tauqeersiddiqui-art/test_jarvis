import pytest

import core.ai_provider as aip


class _FakeProvider:
    def __init__(self, provider_id, outcomes):
        """outcomes: list of either an AIResponse-returning callable outcome
        or an Exception instance to raise, consumed one per call."""
        self.provider_id = provider_id
        self._outcomes = list(outcomes)
        self.calls = []

    def complete(self, prompt, model=None):
        self.calls.append({"prompt": prompt, "model": model})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return aip.AIResponse(text=outcome)


# ---------------------------------------------------------------------------
# classify_provider_error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,expected", [
    ("HTTP 429 Too Many Requests", "rate_limit"),
    ("429 RESOURCE_EXHAUSTED", "rate_limit"),
    ("You exceeded your current quota", "rate_limit"),
    ("402 Payment Required", "payment_required"),
    ("model is currently overloaded, try again", "capacity"),
    ("503 Service Unavailable", "capacity"),
    ("service temporarily unavailable", "unavailable"),
    ("connection timed out", "unavailable"),
    ("model 'gpt-9' does not exist", "model_unavailable"),
    ("SyntaxError: invalid syntax", "unknown"),
])
def test_classify_provider_error(message, expected):
    assert aip.classify_provider_error(Exception(message)) == expected


def test_is_recoverable_provider_error():
    assert aip.is_recoverable_provider_error(Exception("429 rate limit"))
    assert not aip.is_recoverable_provider_error(Exception("SyntaxError: invalid syntax"))


# ---------------------------------------------------------------------------
# complete_with_failover
# ---------------------------------------------------------------------------

def test_failover_moves_to_next_provider_on_recoverable_error(monkeypatch):
    gemini = _FakeProvider("gemini", [Exception("429 RESOURCE_EXHAUSTED")])
    gateway = _FakeProvider("openai_compatible", ["completed via fallback"])
    monkeypatch.setattr(aip, "build_failover_chain", lambda: [gemini, gateway])

    response, attempts = aip.complete_with_failover("build me a calculator app", model="gemini-2.5-flash")

    assert response.text == "completed via fallback"
    assert [a.provider_id for a in attempts] == ["gemini", "openai_compatible"]
    assert attempts[0].success is False and attempts[0].error_category == "rate_limit"
    assert attempts[1].success is True
    # same prompt preserved across the failover
    assert gemini.calls[0]["prompt"] == gateway.calls[0]["prompt"]


def test_failover_never_forces_gemini_model_name_onto_gateway(monkeypatch):
    gemini = _FakeProvider("gemini", [Exception("429 rate limit")])
    gateway = _FakeProvider("openai_compatible", ["ok"])
    monkeypatch.setattr(aip, "build_failover_chain", lambda: [gemini, gateway])

    aip.complete_with_failover("hi", model="gemini-2.5-flash")

    assert gemini.calls[0]["model"] == "gemini-2.5-flash"
    assert gateway.calls[0]["model"] is None  # gateway must use its own configured LLM_MODEL / auto route


def test_failover_does_not_retry_on_non_recoverable_error(monkeypatch):
    gemini = _FakeProvider("gemini", [Exception("SyntaxError: invalid syntax in prompt handling")])
    gateway = _FakeProvider("openai_compatible", ["should never be reached"])
    monkeypatch.setattr(aip, "build_failover_chain", lambda: [gemini, gateway])

    with pytest.raises(Exception, match="SyntaxError"):
        aip.complete_with_failover("hi")

    assert gateway.calls == []  # a real bug must not be masked by failing over


def test_failover_is_bounded_not_a_retry_storm(monkeypatch):
    gemini = _FakeProvider("gemini", [Exception("429 rate limit")])
    gateway = _FakeProvider("openai_compatible", [Exception("402 payment required")])
    monkeypatch.setattr(aip, "build_failover_chain", lambda: [gemini, gateway])

    with pytest.raises(aip.AllProvidersFailedError) as exc_info:
        aip.complete_with_failover("hi")

    assert len(gemini.calls) == 1
    assert len(gateway.calls) == 1
    attempts = exc_info.value.attempts
    assert len(attempts) == 2
    assert {a.provider_id for a in attempts} == {"gemini", "openai_compatible"}


def test_build_failover_chain_raises_when_nothing_configured(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    with pytest.raises(aip.ProviderConfigError):
        aip.build_failover_chain()


def test_build_failover_chain_orders_gateway_before_gemini(monkeypatch):
    """Intended architecture: the OpenAI-compatible/FreeLLM omni gateway is
    PRIMARY for coding/text calls; Gemini is the fallback. Gemini Live
    (real-time voice) is a completely separate path and never goes through
    this chain."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.setenv("LLM_API_KEY", "fake-gateway-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("LLM_MODEL", "auto")

    # Avoid constructing real SDK clients.
    monkeypatch.setattr(aip, "GeminiProvider", lambda: _FakeProvider("gemini", []))
    monkeypatch.setattr(aip, "OpenAICompatibleProvider", lambda: _FakeProvider("openai_compatible", []))

    chain = aip.build_failover_chain()

    assert [p.provider_id for p in chain] == ["openai_compatible", "gemini"]


def test_build_failover_chain_gateway_only_when_gemini_not_configured(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "fake-gateway-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("LLM_MODEL", "auto")
    monkeypatch.setattr(aip, "OpenAICompatibleProvider", lambda: _FakeProvider("openai_compatible", []))

    chain = aip.build_failover_chain()

    assert [p.provider_id for p in chain] == ["openai_compatible"]


def test_gateway_success_means_gemini_is_never_called(monkeypatch):
    gateway = _FakeProvider("openai_compatible", ["done via FreeLLM"])
    gemini = _FakeProvider("gemini", ["should never be reached"])
    monkeypatch.setattr(aip, "build_failover_chain", lambda: [gateway, gemini])

    response, attempts = aip.complete_with_failover("build me a calculator app", model="gemini-2.5-flash")

    assert response.text == "done via FreeLLM"
    assert [a.provider_id for a in attempts] == ["openai_compatible"]
    assert gemini.calls == []


def test_gateway_recoverable_failure_falls_back_to_gemini(monkeypatch):
    gateway = _FakeProvider("openai_compatible", [Exception("429 rate limit — capacity exceeded")])
    gemini = _FakeProvider("gemini", ["continuing via gemini fallback"])
    monkeypatch.setattr(aip, "build_failover_chain", lambda: [gateway, gemini])

    response, attempts = aip.complete_with_failover("build me a calculator app", model="gemini-2.5-flash")

    assert response.text == "continuing via gemini fallback"
    assert [a.provider_id for a in attempts] == ["openai_compatible", "gemini"]
    assert attempts[0].success is False and attempts[0].error_category == "rate_limit"
    assert attempts[1].success is True
    # same task/prompt continues into the fallback, not repeated by the user
    assert gateway.calls[0]["prompt"] == gemini.calls[0]["prompt"]


def test_gateway_preserves_auto_omni_model_route(monkeypatch):
    """The gateway must use its own configured LLM_MODEL (e.g. "auto" / an
    omni route) — the router must never override it with a Gemini model
    name, and never pass a model kwarg to the gateway at all."""
    gateway = _FakeProvider("openai_compatible", ["ok"])
    monkeypatch.setattr(aip, "build_failover_chain", lambda: [gateway])

    aip.complete_with_failover("hi", model="gemini-2.5-flash")

    assert gateway.calls[0]["model"] is None


def test_gemini_live_module_is_not_touched_by_the_router():
    """Architectural guardrail: core/ai_provider.py must never import or
    reference the Gemini Live real-time voice session — that stays hardcoded
    in main.py / screen_processor.py, fully separate from coding/text
    failover."""
    import inspect
    src = inspect.getsource(aip)
    for forbidden in ("live.connect", "LiveConnectConfig", "response_modalities", "session_resumption"):
        assert forbidden not in src
