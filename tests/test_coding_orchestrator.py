import pytest

import core.coding_task as ct
from core import coding_orchestrator as orch


@pytest.fixture(autouse=False)
def isolated_coding_task_state(tmp_path, monkeypatch):
    state_file = tmp_path / "config" / "state" / "coding_task.json"
    monkeypatch.setattr(ct, "STATE_FILE", state_file)
    return state_file


def test_missing_description_asks_for_one(isolated_coding_task_state):
    decision = orch.decide("")

    assert decision.route == orch.Route.MISSING_DESCRIPTION
    assert "describe the project" in decision.message.lower()
    assert decision.task is None
    assert ct.load_active_task() is None  # nothing created


def test_no_active_task_plain_build_routes_to_new_project(isolated_coding_task_state):
    decision = orch.decide("Build me a calculator app")

    assert decision.route == orch.Route.NEW_PROJECT
    assert decision.task is not None
    assert decision.task.original_goal == "Build me a calculator app"

    active = ct.load_active_task()
    assert active is not None
    assert active.task_id == decision.task.task_id


def test_no_active_task_continuation_language_needs_clarification(isolated_coding_task_state):
    decision = orch.decide("Fix the current error")

    assert decision.route == orch.Route.NEEDS_CLARIFICATION
    assert "which project" in decision.message.lower()
    assert decision.task is None
    assert ct.load_active_task() is None  # never guesses a project


def test_active_task_fix_language_routes_to_continue_fix(isolated_coding_task_state):
    existing = ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )
    existing.record_error("NameError: name 'x' is not defined", signature="NameError:main.py:3")
    ct.save_task(existing)

    decision = orch.decide("Fix the current error")

    assert decision.route == orch.Route.CONTINUE_FIX
    assert decision.task.task_id == existing.task_id
    assert decision.task.current_goal == "Fix the current error"
    assert "NameError" in decision.task.last_runtime_error  # preserved, not replanned


def test_active_task_feature_language_routes_to_continue_feature(isolated_coding_task_state):
    existing = ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )
    ct.mark_completed(existing)

    decision = orch.decide("Add calculation history")

    assert decision.route == orch.Route.CONTINUE_FEATURE
    assert decision.task.task_id == existing.task_id
    assert decision.task.project_name == "calculator_app"
    assert decision.task.current_goal == "Add calculation history"
    # continuing a COMPLETED task transparently reopens it
    assert decision.task.status == ct.Status.ACTIVE


def test_explicit_new_project_request_overrides_active_task(isolated_coding_task_state):
    existing = ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )

    decision = orch.decide("Build me a new todo list app")

    assert decision.route == orch.Route.NEW_PROJECT
    assert decision.task.task_id != existing.task_id
    assert decision.task.original_goal == "Build me a new todo list app"

    # the previously active task is left untouched on disk, just no longer current
    active = ct.load_active_task()
    assert active.task_id == decision.task.task_id
