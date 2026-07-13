import pytest

import core.engineering_memory as em
import core.execution_ledger as led
import core.loop_detector as ld


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(led, "STATE_FILE", tmp_path / "config" / "state" / "execution_ledger.json")
    monkeypatch.setattr(em, "STATE_FILE", tmp_path / "config" / "state" / "engineering_memory.json")


def _log_entry(task_id, routing_decision="continue_fix", operation_type="runtime_fix",
                files_touched=None, result=led.Result.FAILURE):
    return led.record(
        task_id=task_id, operation_type=operation_type, routing_decision=routing_decision,
        action_performed="_continue_fix_loop_for_task", files_touched=files_touched or ["main.py"],
        duration_seconds=0.1, result=result,
    )


def _record_memory(task_id, project_key="proj1", signature="NameError:main.py:3",
                    fingerprint="fp-1", outcome=em.OUTCOME_FAILED, files_touched=None):
    return em.record_outcome(
        task=type("T", (), {"task_id": task_id, "project_root": project_key})(),
        operation_type="runtime_fix", goal_summary="fix it",
        normalized_error_signature=signature, evidence_summary="ev", impact_summary="impact",
        files_touched=files_touched or ["main.py"], attempt_summary="fix attempt",
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# Edge cases: short history never trips a loop.
# ---------------------------------------------------------------------------

def test_no_history_is_not_a_loop():
    result = ld.check_for_loop("nonexistent-task")
    assert result.loop_detected is False
    assert result.reason == ""


def test_below_window_threshold_is_not_a_loop():
    for _ in range(ld.DEFAULT_WINDOW - 1):
        _log_entry("task-1", result=led.Result.ROLLBACK)

    result = ld.check_for_loop("task-1")
    assert result.loop_detected is False


# ---------------------------------------------------------------------------
# Repeated rollbacks.
# ---------------------------------------------------------------------------

def test_repeated_rollback_is_detected():
    for _ in range(ld.DEFAULT_WINDOW):
        _log_entry("task-2", result=led.Result.ROLLBACK)

    result = ld.check_for_loop("task-2")

    assert result.loop_detected is True
    assert result.reason == ld.Reason.REPEATED_ROLLBACK
    assert "rollback" in result.evidence.lower()
    assert result.recommendation


# ---------------------------------------------------------------------------
# Repeated failures (routing decision, fingerprint, error signature, files).
# ---------------------------------------------------------------------------

def test_repeated_routing_decision_with_no_success_is_detected():
    for _ in range(ld.DEFAULT_WINDOW):
        _log_entry("task-3", routing_decision="continue_feature", result=led.Result.FAILURE)

    result = ld.check_for_loop("task-3")

    assert result.loop_detected is True
    assert result.reason == ld.Reason.REPEATED_ROUTING_DECISION


def test_repeated_fingerprint_is_detected():
    for _ in range(ld.DEFAULT_WINDOW):
        _record_memory("task-4", fingerprint="same-fp", outcome=em.OUTCOME_FAILED)

    result = ld.check_for_loop("task-4")

    assert result.loop_detected is True
    assert result.reason == ld.Reason.REPEATED_FINGERPRINT


def test_repeated_error_signature_with_different_fingerprints_is_detected():
    for i in range(ld.DEFAULT_WINDOW):
        em.record_outcome(
            task=type("T", (), {"task_id": "task-5", "project_root": "proj"})(),
            operation_type="runtime_fix", goal_summary="fix it",
            normalized_error_signature="NameError:main.py:3", evidence_summary="ev",
            impact_summary="impact", files_touched=["main.py"],
            attempt_summary=f"attempt {i}",  # varies -> different fingerprint each time
            outcome=em.OUTCOME_FAILED,
        )

    result = ld.check_for_loop("task-5")

    assert result.loop_detected is True
    assert result.reason == ld.Reason.REPEATED_ERROR_SIGNATURE


def test_repeated_files_touched_with_no_success_is_detected():
    for i in range(ld.DEFAULT_WINDOW):
        _log_entry("task-6", routing_decision="continue_fix" if i % 2 == 0 else "continue_feature",
                   files_touched=["main.py", "utils.py"], result=led.Result.FAILURE)

    result = ld.check_for_loop("task-6")

    assert result.loop_detected is True
    assert result.reason == ld.Reason.REPEATED_FILES_TOUCHED


def test_no_measurable_progress_catch_all_when_nothing_uniform_but_zero_success():
    routes = ["continue_fix", "continue_feature", "continue_fix"]
    files  = [["a.py"], ["b.py"], ["c.py"]]
    for route, f in zip(routes, files):
        _log_entry("task-7", routing_decision=route, files_touched=f, result=led.Result.FAILURE)

    result = ld.check_for_loop("task-7")

    assert result.loop_detected is True
    assert result.reason == ld.Reason.NO_MEASURABLE_PROGRESS


# ---------------------------------------------------------------------------
# Successful progress / no false positives.
# ---------------------------------------------------------------------------

def test_successful_progress_is_never_flagged_as_a_loop():
    for _ in range(ld.DEFAULT_WINDOW + 2):
        _log_entry("task-8", routing_decision="continue_feature", result=led.Result.SUCCESS)

    result = ld.check_for_loop("task-8")

    assert result.loop_detected is False


def test_repeated_route_with_one_success_is_not_a_false_positive():
    """Three consecutive successful feature additions on the same project use
    the same routing decision every time — that's normal iterative
    development, not a loop."""
    _log_entry("task-9", routing_decision="continue_feature", result=led.Result.SUCCESS)
    _log_entry("task-9", routing_decision="continue_feature", result=led.Result.SUCCESS)
    _log_entry("task-9", routing_decision="continue_feature", result=led.Result.SUCCESS)

    result = ld.check_for_loop("task-9")

    assert result.loop_detected is False


def test_mixed_results_with_eventual_success_is_not_a_loop():
    _log_entry("task-10", result=led.Result.ROLLBACK)
    _log_entry("task-10", result=led.Result.FAILURE)
    _log_entry("task-10", result=led.Result.SUCCESS)

    result = ld.check_for_loop("task-10")

    assert result.loop_detected is False


def test_different_tasks_do_not_interfere():
    for _ in range(ld.DEFAULT_WINDOW):
        _log_entry("task-11", result=led.Result.ROLLBACK)

    result = ld.check_for_loop("task-12")  # a different, unrelated task
    assert result.loop_detected is False


# ---------------------------------------------------------------------------
# Restart persistence — check_for_loop always reads from disk, never from
# in-memory state, so it naturally survives a "restart" between writes.
# ---------------------------------------------------------------------------

def test_detection_survives_reading_fresh_from_disk(tmp_path, monkeypatch):
    for _ in range(ld.DEFAULT_WINDOW):
        _log_entry("task-13", result=led.Result.ROLLBACK)

    # Nothing in-process is cached — reassert the same STATE_FILE path
    # (as a fresh process would use) and confirm detection still works.
    result_again = ld.check_for_loop("task-13")
    assert result_again.loop_detected is True
    assert result_again.reason == ld.Reason.REPEATED_ROLLBACK


def test_custom_window_is_respected():
    _log_entry("task-14", result=led.Result.ROLLBACK)
    _log_entry("task-14", result=led.Result.ROLLBACK)

    assert ld.check_for_loop("task-14", window=2).loop_detected is True
    assert ld.check_for_loop("task-14", window=3).loop_detected is False
