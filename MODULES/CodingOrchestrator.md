# Module: CodingOrchestrator

**Location:** `core/coding_orchestrator.py`
**Layer:** Core Services
**Added:** 2026-07-14

## Purpose

The single place that answers two questions for an incoming coding request: what kind
of coding task is this, and where should it go? Classifies a request as a brand-new
project, a continuation of the active project's fix loop, a continuation as an
incremental feature change, or a request that needs clarification — without
performing any of the actual work itself.

## What this module explicitly does NOT do

It does not build, fix, investigate, analyze impact, record engineering outcomes, or
manage rollback. Those remain owned by `actions/dev_agent.py`'s existing pipelines
(`_build_project`, `_continue_fix_loop_for_task`, `_run_incremental_feature_change`)
and the modules they call in turn (`core/engineering_memory.py`,
`actions/investigate.py`, `actions/impact_analysis.py`, `actions/codebase_search.py`,
`core/workspace.py`, `core/ai_provider.py`). This module was extracted from routing
logic previously inline in `actions/dev_agent.py`'s `dev_agent()` — the decision tree
itself is unchanged in behavior, not a rewrite.

## Responsibilities

- Load the active `CodingTask` (if any) via `core/coding_task.py`.
- Apply `core/coding_task.py`'s existing deterministic classifiers
  (`looks_like_new_project_request`, `looks_like_fix_continuation`,
  `looks_like_feature_continuation`, `looks_like_continuation_request`) to decide the
  route.
- Start a new task (`ct.start_task`) or continue the active one
  (`ct.continue_task`) as appropriate to the decision — the same state mutations that
  previously happened inline in `dev_agent()`.
- Return a `RoutingDecision` (route + task + message) for the caller to act on.

## Public Interface

- `decide(description: str, project_name: str = "", language: str = "python") -> RoutingDecision`
  — the single entry point.
- `Route` — routing constants: `NEW_PROJECT`, `CONTINUE_FIX`, `CONTINUE_FEATURE`,
  `NEEDS_CLARIFICATION`, `MISSING_DESCRIPTION`.
- `RoutingDecision` dataclass — `.route` (str), `.task` (`CodingTask | None`),
  `.message` (str, set only for `NEEDS_CLARIFICATION`/`MISSING_DESCRIPTION`).

## Dependencies

- `core/coding_task.py` only. No AI provider call, no file I/O beyond what
  `core/coding_task.py` already performs, no OS-specific code — pure business logic,
  already platform-independent by construction.

## Consumers

- `actions/dev_agent.py`'s `dev_agent()` — calls `decide()` once per request, then
  dispatches to the existing pipeline function matching `decision.route`. The
  dispatch call itself (which pipeline function to invoke) stays in `dev_agent.py`,
  not in this module, so existing tests that monkeypatch `dev_agent._build_project` /
  `dev_agent._continue_fix_loop_for_task` / `dev_agent._run_incremental_feature_change`
  continue to work unchanged.

## Limitations

- Classification is exactly as deterministic (word/phrase-based, not semantic) as
  `core/coding_task.py`'s existing classifiers — this module adds no new
  classification intelligence, only a dedicated place for the decision to live.
- Does not yet expose a way for a future capability to intercept or extend the
  decision (e.g. a plugin-style hook) — it is a direct function call today, per the
  "do not create new architecture" constraint on this initial extraction.

## Future Direction

- This module is intended to become the single entry point for every future coding
  capability, per `PRODUCT_VISION.md` Track A ("continue improving this first"). Two
  named future capabilities — an Execution Ledger and Loop Detection — are expected to
  plug into this orchestrator rather than into `dev_agent.py` directly, but neither
  has been started (each is its own separately-scoped capability, per the
  one-capability-per-session development rule).
