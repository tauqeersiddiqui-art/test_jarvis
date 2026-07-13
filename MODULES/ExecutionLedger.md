# Module: ExecutionLedger

**Location:** `core/execution_ledger.py`
**Layer:** Core Services
**Added:** 2026-07-14

## Purpose

A deterministic, append-only internal engineering log of every coding operation
routed by `core/coding_orchestrator.py` and executed by `actions/dev_agent.py`. Exists
so a future engineer (human or AI) can answer "what actually happened, in what order,
with what result" without re-deriving it from scattered logs.

## What this module explicitly is NOT

Not user-facing memory — nothing in this module is surfaced to the end user. Not a
decision-maker — it never influences `dev_agent.py`'s behavior. Not a duplicate of
`core/engineering_memory.py` — that module records each individual fix/feature attempt
to inform *future* attempts (recall/scoring); this module records one entry per whole
`dev_agent()` call (one routed coding operation), for observability only.

## Responsibilities

- Record one `LedgerEntry` per `dev_agent()` call, capturing: timestamp, task ID,
  operation type, routing decision, action performed, files touched, duration, result,
  and an optional next-decision hint.
- Provide a deterministic read side (`entries_for_task()`) — no LLM involved anywhere
  in this module.
- Bound retention (`MAX_ENTRIES = 500`), pruning oldest entries first.

## Public Interface

- `record(task_id, operation_type, routing_decision, action_performed, files_touched, duration_seconds, result, next_decision="") -> LedgerEntry`
- `entries_for_task(task_id: str) -> list[LedgerEntry]` — oldest first.
- `LedgerEntry` dataclass — `entry_id`, `timestamp`, `task_id`, `operation_type`,
  `routing_decision`, `action_performed`, `files_touched`, `duration_seconds`,
  `result`, `next_decision`.
- `Result` — result constants: `SUCCESS`, `FAILURE`, `ROLLBACK`.

## Field Semantics

- `operation_type`: `"build"` (new project), `"runtime_fix"` (continue-fix),
  `"feature_change"` (continue-feature) — deliberately reuses
  `core/engineering_memory.py`'s existing `runtime_fix`/`feature_change` vocabulary
  where the concepts line up, rather than inventing a parallel taxonomy.
- `routing_decision`: the exact `core.coding_orchestrator.Route` value for this call.
- `action_performed`: the name of the `actions/dev_agent.py` pipeline function that
  ran (`_build_project`, `_continue_fix_loop_for_task`, `_run_incremental_feature_change`).
- `result`: derived from `CodingTask.status` immediately after the pipeline call —
  see `DECISIONS/ADR-007.md` for why `FAILED` maps to `ROLLBACK` rather than
  `FAILURE`, and why this doesn't require new instrumentation inside `dev_agent.py`'s
  pipelines.
- `next_decision`: optional, populated only when a follow-up decision is already
  known at recording time. Not currently populated by any caller — see Limitations.

## Dependencies

- Standard library only (`json`, `dataclasses`, `uuid`, `tempfile`, `datetime`). No AI
  provider call anywhere in this module. No OS-specific code — pure business logic,
  already platform-independent by construction.

## Persistence

Single JSON file at `config/state/execution_ledger.json` (gitignored), atomic write
via temp-file + `os.replace` — the same convention as `core/coding_task.py` and
`core/engineering_memory.py` (see `DECISIONS/ADR-002.md`). No new persistence
mechanism was introduced.

## Consumers

- `actions/dev_agent.py`'s `dev_agent()` — via the private `_log_ledger_entry()`
  helper, called once per dispatch branch (`CONTINUE_FIX`, `CONTINUE_FEATURE`,
  `NEW_PROJECT`), wrapped in `try/finally` so an entry is recorded even if the pipeline
  raises. `MISSING_DESCRIPTION`/`NEEDS_CLARIFICATION` routes log nothing (no task, no
  operation performed).

## Limitations

- `next_decision` is not populated by any current caller — `dev_agent()` resolves
  each request within a single call today (e.g. a feature-change that fails
  validation hands off to the fix loop *internally*, still within one call), so there
  is no genuine "next decision" hand-off point yet at the orchestrator level. The
  field exists for forward compatibility, not as a currently-exercised capability.
- Persistence failures are swallowed (matches `core/engineering_memory.py`'s
  `record_outcome()` convention) — a ledger-write problem never interrupts the coding
  operation itself, but this means a caller cannot rely on `record()` raising to
  detect a persistence failure; it must be monitored via logs if that matters.
- One entry per top-level `dev_agent()` call, not per internal fix attempt — for
  per-attempt detail (each fix/feature attempt's own outcome), see
  `core/engineering_memory.py` instead.

## Future Direction

- Intended as the landing point for future observability work (e.g. an Execution
  Ledger viewer/report). No such consumer exists yet — this is the recording side
  only.
- `next_decision` may become populated once a genuine multi-step orchestration
  hand-off exists at the `core/coding_orchestrator.py` level (not yet built).
