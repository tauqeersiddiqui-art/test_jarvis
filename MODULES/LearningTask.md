# Module: LearningTask

**Location:** `core/learning_task.py`
**Layer:** Core Services

## Purpose

Converts a CONFIRMED capability gap (a `core/capability_gap.py` `detect_gap()` result
with `gap_detected == True` AND `confidence == "none"`) into a persistent, bounded,
deduplicated record of "this capability is missing and should be learned about
later." Answers: what capability is missing, why Mark needs it, whether this gap has
already been recorded, and what should be learned later.

## What this module explicitly is NOT

Not autonomous research, not capability installation, not self-modification, and not
background execution. A `LearningTask` is only a recorded need — it grants no
permission to browse the internet, run shell commands, modify source, install
software, access secrets, or use a camera/microphone. Any future learning execution
against a task must go through a separate, explicitly approved workflow that does
not exist yet; nothing in this module researches, generates code, installs anything,
or modifies capability registration.

## Responsibilities

- `create_from_gap()` is the only write path. It accepts a `capability_gap.GapResult`
  and creates/updates a task **only** when the gap is a confirmed full miss:
  `gap_detected is True` AND `confidence == capability_gap.CONFIDENCE_NONE`. Note
  that `detect_gap()` sets `gap_detected = True` for **both** a partial match and a
  full miss — checking `gap_detected` alone is not enough to exclude partial
  matches, so `confidence` is checked explicitly too. Available capabilities
  (`confidence == "high"`), partial matches (`"partial"`), and ambiguous results
  (`"ambiguous"`) never create or touch a task.
- Deterministically normalizes the missing capability (`normalize_capability()`:
  lowercase, tokenize, drop stopwords/single-char tokens, sort, join) and uses that
  normalized, bounded string as the dedup key.
- On a repeat of an already-recorded gap (same normalized `missing_capability`), no
  new task is created: `occurrence_count` increments, `last_seen_at`/`updated_at`
  refresh, `priority` is recomputed, and the original `task_id` is preserved.
- Deterministic priority (`_priority_for()`, no LLM): base 1, +1 per repeated
  occurrence beyond the first (capped at `MAX_OCCURRENCE_BONUS = 5`), +2 if
  `source == SOURCE_USER` (an explicit user-requested gap outranks incidental
  detection).
- A minimal, validated status lifecycle (`update_status()`):
  `pending → approved → learning → {completed | failed}`, `failed → pending`
  (re-queue for a future retry), `rejected` reachable from `pending`/`approved` and
  terminal. Invalid transitions are rejected (task left unchanged), never silently
  applied. `update_status()` only ever records a transition a caller explicitly
  requests — no transition itself performs research, code generation, or
  installation.
- Bounded retention (`MAX_TASKS = 200`): over the cap, lowest-priority tasks are
  pruned first, oldest-updated among ties (mirrors
  `core/engineering_memory.py`'s pruning approach).
- Persists atomically — same single-JSON-file, gitignored, temp-file + `os.replace`
  convention as `core/coding_task.py` / `core/engineering_memory.py` /
  `core/execution_ledger.py`. Persistence failures are swallowed (logged, not
  raised) so a broken queue can never interrupt normal Mark execution.

## Public Interface

- `create_from_gap(gap_result, source: str = SOURCE_DETECTION) -> LearningTask | None`
- `list_tasks(status: str | None = None) -> list[LearningTask]` — highest priority
  first, ties broken oldest-created first.
- `get_task(task_id: str) -> LearningTask | None`
- `find_by_capability(missing_capability: str) -> LearningTask | None` — looks up an
  existing task using the same normalization `create_from_gap()` uses, so a caller
  can ask "has this gap already been recorded?" with any equivalent wording.
- `update_status(task_id: str, new_status: str) -> LearningTask | None`
- `stats() -> dict` — `{"total_tasks": int, "by_status": {status: count}}`
- `normalize_capability(text: str) -> str`
- `LearningTask` fields: `task_id, created_at, updated_at, requested_task,
  missing_capability, gap_reason, source, priority, status, occurrence_count,
  last_seen_at` — exactly the fields required, nothing else.
- `Status`: `PENDING, APPROVED, LEARNING, COMPLETED, FAILED, REJECTED`.
- `SOURCE_DETECTION`, `SOURCE_USER`.

## Dependencies

- `core/capability_gap.py` — only for its `CONFIDENCE_NONE` constant (to gate task
  creation correctly) and the shape of a `GapResult` (duck-typed via `getattr`, not
  an `isinstance` check). This is a **one-directional** dependency:
  `capability_gap.py` has no knowledge of this module — `detect_gap()` remains
  entirely read-only and never persists a task on its own.
- Standard library only otherwise (`json`, `re`, `dataclasses`, `tempfile`,
  `uuid`). No AI provider call anywhere in this module.

## Integration Scope (this session)

Detection and task creation remain separate operations: nothing in
`core/capability_gap.py` calls `create_from_gap()`, and no caller in this session
wires `create_from_gap()` into `main.py`'s tool dispatch, `core/coding_orchestrator.py`,
or any other request-routing path. Normal request routing is unchanged.

## Limitations

- `missing_capability` is derived from `capability_gap.py`'s `required_capability`
  (the sorted token-set of the *entire* task request), not a canonical "capability
  name" — two differently-worded requests for what a human would consider the same
  underlying gap will only dedup together if their normalized token sets are
  identical. This is a known limitation of deterministic, non-semantic matching.
- Status transitions are structural only — nothing in this module or elsewhere
  currently acts on a `learning`/`approved` task; there is no execution engine for
  learning tasks yet (explicitly out of scope for v1, per the Security/Trust Rule
  above).

## Future Direction

- `PRODUCT_VISION.md`'s Capability First Principle ends with "record missing
  capabilities for future learning" — this module is that record. A future,
  separately proposed capability would need its own approved workflow to act on a
  `learning`-status task (research, MCP/plugin installation, etc.); nothing here
  authorizes that in advance.
