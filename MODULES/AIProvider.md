# Module: AIProvider

**Location:** `core/ai_provider.py`
**Layer:** Core Services / AI Provider Layer (see `ARCHITECTURE.md`)

## Purpose

Lean, environment-variable-only abstraction over coding/text-completion model
backends, with a bounded, ordered failover chain. Exists so that no other module in
this repository needs to know which model backend is active or how to recover from a
transient provider failure.

## Responsibilities

- Select a single active provider from environment variables
  (`get_provider()`) — used for readiness reporting (e.g. `ui.py`).
- Build and run a bounded, ordered failover chain for coding/text completion calls
  (`build_failover_chain()`, `complete_with_failover()`).
- Classify provider errors into recoverable categories (`rate_limit`,
  `payment_required`, `capacity`, `unavailable`, `model_unavailable`) vs. everything
  else, which is treated as `unknown` and must fail fast rather than be masked by
  switching providers (`classify_provider_error()`).

## Public Interface

- `get_provider() -> AIProvider` — single-provider selection (priority: OpenAI-compatible
  gateway if `LLM_API_KEY`+`LLM_BASE_URL`+`LLM_MODEL` are all set, else Gemini if
  `GEMINI_API_KEY` is set, else raises `ProviderConfigError`).
- `complete_with_failover(prompt: str, model: str | None = None) -> tuple[AIResponse, list[ProviderAttempt]]`
  — the primary entry point for coding/text completion. Tries each configured
  provider in order; `model` is only ever forced onto a Gemini provider, never onto an
  OpenAI-compatible gateway (which keeps its own configured model, including "auto"
  routing).
- `build_failover_chain() -> list[AIProvider]` — ordered, bounded provider list;
  raises `ProviderConfigError` only if nothing at all is configured.
- `classify_provider_error(error: Exception) -> str` / `is_recoverable_provider_error(error) -> bool`
  — error categorization used both internally and by callers that want to reason
  about a failure without invoking the full chain.
- Exceptions: `ProviderConfigError`, `AllProvidersFailedError` (raised only when every
  configured provider fails with a recoverable error).

## Dependencies

- `google-genai` SDK (Gemini).
- `openai` SDK (OpenAI-compatible gateway).
- Environment variables only — no config file, no hardcoded credentials.

## Consumers

- `actions/dev_agent.py` (planner/writer calls), `actions/code_helper.py`,
  `actions/investigate.py` (evidence synthesis) — all coding/text completion callers
  in this repository go through `complete_with_failover()`, never construct a client
  directly.

## Limitations

- Explicitly does **not** cover the Gemini Live real-time voice session in `main.py` /
  `actions/screen_processor.py` — that session is hardcoded to Gemini Live by design
  and untouched by this module. See `ARCHITECTURE.md` §5 (invariant: Gemini Live stays
  singular and separate) and `DECISIONS/ADR-006.md`.
- The failover chain is bounded by the number of configured providers — no infinite
  retries, no retry storm. If both providers are configured but both fail
  recoverably, the caller gets `AllProvidersFailedError` with a per-attempt summary,
  not a silent hang.
- Model selection and provider selection are deliberately separate concerns; a
  gateway's own configured model cannot be overridden per-call.

## Future Direction

- Track A (`PRODUCT_VISION.md`): "Continue improving this first" — extend this module
  in place (e.g. additional recoverable-error categories, additional providers) rather
  than introducing a second provider abstraction.
- Any future subsystem that needs model access (Expert Modes, Research Agent, Decision
  Intelligence) should consume this module's existing interface rather than building
  its own client.
