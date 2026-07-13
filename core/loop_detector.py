#core/loop_detector.py
"""
Loop Detector: a deterministic engineering-safety check that decides whether
a coding task is still making measurable progress, or is stuck repeating
itself and should stop consuming further LLM calls.

This is an engineering subsystem, not an AI reasoning system — every check
below is a plain comparison over already-recorded, already-deterministic
data. No LLM call is made anywhere in this module, and this module makes no
decision on anyone's behalf: it returns a LoopCheckResult for the caller
(core/coding_orchestrator.py) to act on. Detecting a loop never
automatically stops or continues an operation.

Reuses, does not duplicate:
- core/execution_ledger.py's entries_for_task() — one entry per routed
  dev_agent() call: routing_decision, files_touched, result, operation_type.
- core/engineering_memory.py's records_for_task() — one record per
  individual fix/feature attempt: normalized_error_signature,
  attempt_fingerprint, files_touched, outcome.

This module has no persistence of its own — it only reads the two
already-persistent, already-isolated-in-tests sources above.
"""
from __future__ import annotations

from dataclasses import dataclass

from core import execution_ledger as led
from core import engineering_memory as em

DEFAULT_WINDOW = 3  # repetitions required before a signal counts as a loop


class Reason:
    REPEATED_ROUTING_DECISION = "repeated_routing_decision"
    REPEATED_ROLLBACK         = "repeated_rollback"
    REPEATED_ERROR_SIGNATURE  = "repeated_error_signature"
    REPEATED_FILES_TOUCHED    = "repeated_files_touched"
    REPEATED_FINGERPRINT      = "repeated_fingerprint"
    NO_MEASURABLE_PROGRESS    = "no_measurable_progress"


_RECOMMENDATIONS = {
    Reason.REPEATED_ROUTING_DECISION: (
        "The same routing decision has repeated with no successful outcome. "
        "Consider asking the user for guidance instead of retrying automatically."
    ),
    Reason.REPEATED_ROLLBACK: (
        "The last attempts were all rolled back. Retrying the same approach is "
        "unlikely to succeed — consider a different fix strategy or asking the user."
    ),
    Reason.REPEATED_ERROR_SIGNATURE: (
        "The same error keeps recurring across attempts. The current approach is "
        "not resolving it — consider gathering more evidence or asking the user."
    ),
    Reason.REPEATED_FILES_TOUCHED: (
        "The same files keep being touched with no progress. Consider a broader "
        "investigation before editing these files again."
    ),
    Reason.REPEATED_FINGERPRINT: (
        "A materially identical attempt has already been tried and did not "
        "succeed. Retrying it again is unlikely to help — try a different approach."
    ),
    Reason.NO_MEASURABLE_PROGRESS: (
        "No measurable progress across recent attempts (same routing decision, "
        "same files, same error). Stop and ask the user before spending another "
        "model call."
    ),
}


@dataclass
class LoopCheckResult:
    loop_detected: bool
    reason: str = ""
    evidence: str = ""
    recommendation: str = ""


def _no_loop() -> LoopCheckResult:
    return LoopCheckResult(loop_detected=False)


def _result(reason: str, evidence: str) -> LoopCheckResult:
    return LoopCheckResult(
        loop_detected=True,
        reason=reason,
        evidence=evidence,
        recommendation=_RECOMMENDATIONS[reason],
    )


def _last_n(items: list, n: int) -> list:
    return items[-n:] if len(items) >= n else []


def _all_equal(values: list) -> bool:
    return bool(values) and all(v == values[0] for v in values) and values[0] not in ("", None)


def check_for_loop(task_id: str, window: int = DEFAULT_WINDOW) -> LoopCheckResult:
    """
    Deterministic loop check for a single coding task. Examines the last
    `window` Execution Ledger entries and the last `window` Engineering
    Memory records for this task_id. Returns the FIRST signal that trips, in
    the order below (compound/strongest-signal-implying checks last).

    With fewer than `window` entries/records available, no check can trip —
    a task with a short history is never flagged as a loop.
    """
    entries  = led.entries_for_task(task_id)
    records  = em.records_for_task(task_id)

    recent_entries = _last_n(entries, window)
    recent_records = _last_n(records, window)

    # Every check below that repeats on a dimension unrelated to outcome
    # (routing decision, files touched, fingerprint, error signature) is
    # deliberately guarded with "and none of these succeeded" — repeating
    # the same route or touching the same file across several *successful*
    # operations (e.g. three consecutive successful feature additions) is
    # normal iterative development, not a loop.
    entries_had_success  = any(e.result == led.Result.SUCCESS for e in recent_entries)
    records_had_success  = any(r.outcome in (em.OUTCOME_SUCCESS, em.OUTCOME_IMPROVED) for r in recent_records)

    # Same rollback occurring repeatedly.
    if recent_entries and _all_equal([e.result for e in recent_entries]) and recent_entries[0].result == led.Result.ROLLBACK:
        evidence = f"Last {window} operations all ended in rollback: " + ", ".join(
            f"{e.operation_type}@{e.timestamp}" for e in recent_entries
        )
        return _result(Reason.REPEATED_ROLLBACK, evidence)

    # Same routing decision repeated multiple times, with no success among them.
    if recent_entries and not entries_had_success and _all_equal([e.routing_decision for e in recent_entries]):
        evidence = f"Last {window} routing decisions were all '{recent_entries[0].routing_decision}' with no success."
        return _result(Reason.REPEATED_ROUTING_DECISION, evidence)

    # Same engineering-memory fingerprint repeating — a materially identical
    # attempt tried again, with no success among them.
    fingerprints = [r.attempt_fingerprint for r in recent_records]
    if not records_had_success and _all_equal(fingerprints):
        evidence = f"Last {window} engineering-memory attempts share fingerprint '{fingerprints[0]}' with no success."
        return _result(Reason.REPEATED_FINGERPRINT, evidence)

    # Same error signature repeating across attempts, with no success among them.
    signatures = [r.normalized_error_signature for r in recent_records]
    if not records_had_success and _all_equal(signatures):
        evidence = f"Last {window} attempts all show error signature '{signatures[0]}' with no success."
        return _result(Reason.REPEATED_ERROR_SIGNATURE, evidence)

    # Same files modified repeatedly (identical non-empty file sets), with no success among them.
    file_sets = [tuple(sorted(e.files_touched)) for e in recent_entries]
    if not entries_had_success and _all_equal(file_sets):
        evidence = f"Last {window} operations all touched exactly: {', '.join(file_sets[0])} with no success."
        return _result(Reason.REPEATED_FILES_TOUCHED, evidence)

    # No measurable progress: a weaker, catch-all signal that only applies
    # once none of the exact-match checks above have already identified a
    # more specific reason — the task has cycled through `window` operations
    # (possibly different routes/files each time) without a single success.
    if len(recent_entries) >= window and not entries_had_success:
        evidence = (
            f"Last {window} operations produced zero successful outcomes "
            f"(results: {', '.join(e.result for e in recent_entries)})."
        )
        return _result(Reason.NO_MEASURABLE_PROGRESS, evidence)

    return _no_loop()
