# Module: EngineeringMemory

**Location:** `core/engineering_memory.py`
**Layer:** Core Services

## Purpose

A small, bounded, searchable record of past coding outcomes (fixes and feature
changes) per generated project. Lets `actions/dev_agent.py` check "has something like
this already been tried, and what happened" before attempting a fix or change.

## What this module explicitly is NOT

Not a generic long-term memory system, and not a second coding agent. It never plans,
writes, runs, or fixes code — it only records and recalls bounded metadata about
attempts `actions/dev_agent.py` already made.

## Responsibilities

- Record one outcome per attempt (`record_outcome()`): operation type
  (`build_fix` / `feature_change` / `runtime_fix`), goal summary, normalized error
  signature, evidence summary, impact summary, files touched, outcome
  (`success` / `improved` / `failed` / `rolled_back`), rollback reason, failure
  category, and a deterministic attempt fingerprint.
- Recall relevant past records for a new attempt, scored deterministically (no LLM):
  - `find_relevant_for_error()` — ranks by exact error-signature match, then
    operation-type match, then recency.
  - `find_relevant_for_change()` — ranks by overlapping impacted files, then
    overlapping goal wording, then recency.
  - `find_matching_failed_attempt()` — exact fingerprint match against prior
    failed/rolled-back attempts, to detect a materially identical approach that
    already failed.
- Bound retention per project and in total (`MAX_RECORDS_PER_PROJECT = 20`,
  `MAX_TOTAL_RECORDS = 200`), pruning low-value (`failed`/`rolled_back`) records first,
  oldest first.

## Public Interface

- `project_key(project_root: str) -> str` — opaque, stable per-project identifier
  (SHA-256 of the resolved absolute path, truncated), so records are project-scoped
  without persisting the raw path beyond what `CodingTask` already stores.
- `compute_attempt_fingerprint(operation_type, normalized_error_signature, files_touched, attempt_summary) -> str`
  — deterministic, computable before any AI call.
- `record_outcome(task, operation_type, goal_summary, normalized_error_signature, evidence_summary, impact_summary, files_touched, attempt_summary, outcome, rollback_reason="", successful_step="", failure_category="") -> EngineeringRecord`
- `find_relevant_for_error`, `find_relevant_for_change`, `find_matching_failed_attempt`,
  `summarize_records(records, max_chars=500) -> str` (bounded, human/AI-readable —
  file paths, signatures, outcomes only, never source content or prompts).

## Dependencies

- Reuses `core/coding_task.py`'s persistence conventions (single small JSON file
  under `config/state/`, gitignored, atomic writes).
- Standard library only (`hashlib`, `json`, `re`, `dataclasses`, `tempfile`). No AI
  provider call anywhere in this module.

## Lineage

Adapts the useful idea from the historical `mini_agent` reference project's
`missions/engineering_memory.py` (searchable past-fix records informing future
attempts) into a Mark-native primitive — see `DECISIONS/ADR-005.md` for why this was
an *adapt*, not an *integrate* or a direct port.

## Limitations

- Never stores API keys, credentials, environment variable values, full source file
  contents, full prompts, full model responses, raw screenshots, or complete
  tracebacks — only short bounded summaries, file paths, and small deterministic
  fingerprints.
- Persistence failures are swallowed (`record_outcome()` catches and logs rather than
  raising) so a memory-write problem can never interrupt the coding task's own
  execution — this means a caller cannot rely on `record_outcome()` raising to detect
  a persistence failure; it must be monitored via logs if that matters.
- Recall scoring is deterministic and file/word-overlap based, not semantic — two
  attempts describing the same fix in very different wording will not be linked unless
  their normalized error signatures match exactly.

## Future Direction

- `PRODUCT_VISION.md` Track H (Engineering Experience Engine): "Engineering Memory
  evolves into an Experience Engine... every completed task should become an
  experience... future attempts should reuse experiences." This module's existing
  `find_relevant_for_error`/`find_relevant_for_change` recall is the mechanism Track H
  would extend — the constraint carried forward is that any such evolution must
  remain deterministic and reviewable, never a silent behavior rewrite (`ROADMAP.md`
  Phase 4).
