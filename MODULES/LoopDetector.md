# Module: LoopDetector

**Location:** `core/loop_detector.py`
**Layer:** Core Services
**Added:** 2026-07-14

## Purpose

A deterministic engineering-safety check that decides whether a coding task is still
making measurable progress, or is stuck repeating itself and should stop consuming
further LLM calls. This is an engineering subsystem, not an AI reasoning system —
every check is a plain comparison over already-recorded data.

## What this module explicitly does NOT do

- No LLM call anywhere in this module.
- No decision on anyone's behalf — it returns a `LoopCheckResult` for the caller
  (`core/coding_orchestrator.py`) to act on. It never automatically stops or continues
  an operation.
- No persistence of its own — it only reads `core/execution_ledger.py` and
  `core/engineering_memory.py`, both already persistent and already isolated in tests.

## Responsibilities

`check_for_loop(task_id, window=3)` examines the last `window` Execution Ledger
entries and the last `window` Engineering Memory records for a task, and returns the
first signal (in order) that trips:

1. **Repeated rollback** — the last `window` operations all ended in rollback.
2. **Repeated routing decision** — the last `window` operations used the same route,
   with zero successes among them.
3. **Repeated fingerprint** — the last `window` engineering-memory attempts share an
   identical `attempt_fingerprint`, with zero successes among them.
4. **Repeated error signature** — the last `window` attempts show the same
   `normalized_error_signature`, with zero successes among them.
5. **Repeated files touched** — the last `window` operations touched exactly the same
   file set, with zero successes among them.
6. **No measurable progress** — a catch-all: `window` operations have passed with zero
   successful outcomes, even if no single dimension above repeated exactly.

A task with fewer than `window` entries/records available cannot trip any check — a
short history is never flagged as a loop.

## Public Interface

- `check_for_loop(task_id: str, window: int = DEFAULT_WINDOW) -> LoopCheckResult`
- `LoopCheckResult` dataclass — `loop_detected` (bool), `reason` (str), `evidence`
  (str), `recommendation` (str).
- `Reason` — constants for each of the 6 signals above.
- `DEFAULT_WINDOW = 3`.

## Dependencies

- `core/execution_ledger.py`'s `entries_for_task()`.
- `core/engineering_memory.py`'s `records_for_task()` (a new, small, additive read
  accessor added alongside this capability — mirrors the ledger's own
  `entries_for_task()`; does not change any existing `engineering_memory.py` behavior).
- Standard library only otherwise. No OS-specific code — pure business logic, already
  platform-independent by construction.

## Consumers

- `core/coding_orchestrator.py`'s `decide()` — calls `check_for_loop(active.task_id)`
  immediately after confirming an active task is being continued (before the
  fix-vs-feature classification). If a loop is detected, `decide()` returns
  `Route.LOOP_DETECTED` instead of `CONTINUE_FIX`/`CONTINUE_FEATURE` — no pipeline
  function runs, so no further model call is spent. See `DECISIONS/ADR-008.md`.
- `actions/dev_agent.py`'s `dev_agent()` handles `Route.LOOP_DETECTED` exactly like
  `MISSING_DESCRIPTION`/`NEEDS_CLARIFICATION` — returns `decision.message`, calls no
  pipeline.

## False-Positive Guard

Every repetition-based check (routing decision, fingerprint, error signature, files
touched) requires **zero successful outcomes** among the repeated items. Three
consecutive successful feature additions using the same route, or repeatedly editing
the same file across successful changes, is normal iterative development and is never
flagged.

## Limitations

- Fixed, non-adaptive threshold (`DEFAULT_WINDOW = 3`) — not configurable per-task or
  per-project today.
- Only applies to `CONTINUE_FIX`/`CONTINUE_FEATURE` routes on an existing task —
  `NEW_PROJECT` requests have no prior history to check against and are never
  evaluated.
- Detects repetition, not semantic stagnation — two attempts that are different in
  wording but functionally identical would not trip the fingerprint/signature checks
  unless `core/engineering_memory.py`'s own normalization already treats them as
  equivalent.

## Future Direction

- If a future capability (e.g. a Hard Execution Budget) wants to act on
  `Route.LOOP_DETECTED` beyond returning a message (for example, blocking further
  attempts entirely until explicit user override), that is its own separately-scoped
  capability, not implied by this module.
