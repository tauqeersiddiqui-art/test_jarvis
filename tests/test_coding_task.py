import json
import os

import pytest

import core.coding_task as ct


@pytest.fixture(autouse=True)
def isolated_state_file(tmp_path, monkeypatch):
    """Every test in this file gets its own state file location — never
    touches Mark's real config/state/coding_task.json."""
    state_file = tmp_path / "config" / "state" / "coding_task.json"
    monkeypatch.setattr(ct, "STATE_FILE", state_file)
    return state_file


# ---------------------------------------------------------------------------
# Task creation / persistence / reload ("simulated restart")
# ---------------------------------------------------------------------------

def test_start_task_creates_a_new_active_task(isolated_state_file):
    task = ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )

    assert task.task_id
    assert task.original_goal == "Build me a calculator app"
    assert task.current_goal == "Build me a calculator app"
    assert task.project_name == "calculator_app"
    assert task.phase == ct.Phase.PLANNING
    assert task.status == ct.Status.ACTIVE
    assert isolated_state_file.exists()


def test_task_state_persists_and_reloads_after_simulated_restart(isolated_state_file):
    task = ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )
    ct.set_phase(task, ct.Phase.BUILDING)

    # Simulate a full app restart: nothing in memory, only the file on disk.
    reloaded = ct.load_active_task()

    assert reloaded is not None
    assert reloaded.task_id == task.task_id
    assert reloaded.project_root == "/tmp/JarvisProjects/calculator_app"
    assert reloaded.phase == ct.Phase.BUILDING


def test_no_state_file_means_no_active_task(isolated_state_file):
    assert not isolated_state_file.exists()
    assert ct.load_active_task() is None


def test_corrupt_state_file_is_treated_as_no_active_task(isolated_state_file):
    isolated_state_file.parent.mkdir(parents=True, exist_ok=True)
    isolated_state_file.write_text("{not valid json", encoding="utf-8")
    assert ct.load_active_task() is None


# ---------------------------------------------------------------------------
# Continuation semantics
# ---------------------------------------------------------------------------

def test_continue_task_updates_goal_and_preserves_identity(isolated_state_file):
    task = ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )
    original_id = task.task_id
    original_root = task.project_root

    ct.continue_task(task, "Add calculation history")

    assert task.task_id == original_id
    assert task.project_root == original_root
    assert task.original_goal == "Build me a calculator app"
    assert task.current_goal == "Add calculation history"


def test_completed_task_reopens_to_active_on_continuation(isolated_state_file):
    task = ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )
    ct.mark_completed(task)
    assert task.status == ct.Status.COMPLETED

    ct.continue_task(task, "Add calculation history")

    assert task.status == ct.Status.ACTIVE
    assert task.phase == ct.Phase.PLANNING
    assert task.current_goal == "Add calculation history"


def test_archived_task_is_not_returned_as_active(isolated_state_file):
    task = ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )
    assert ct.load_active_task() is not None

    ct.archive_active_task()

    assert ct.load_active_task() is None
    # but the record itself is not deleted from disk
    assert isolated_state_file.exists()
    raw = json.loads(isolated_state_file.read_text(encoding="utf-8"))
    assert raw["status"] == ct.Status.ARCHIVED


def test_clear_active_task_removes_state_file(isolated_state_file):
    ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )
    assert isolated_state_file.exists()

    ct.clear_active_task()

    assert not isolated_state_file.exists()
    assert ct.load_active_task() is None


# ---------------------------------------------------------------------------
# Continuation-intent classification (deterministic, concept-based)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "continue the app",
    "continue the project",
    "continue coding",
    "resume the project",
    "keep going",
    "fix it",
    "fix this",
    "fix the current error",
    "fix that bug",
])
def test_looks_like_fix_continuation_true(phrase):
    assert ct.looks_like_fix_continuation(phrase)


@pytest.mark.parametrize("phrase", [
    "build me a calculator app",
    "create a todo list web app",
    "add calculation history",
])
def test_looks_like_fix_continuation_false(phrase):
    assert not ct.looks_like_fix_continuation(phrase)


@pytest.mark.parametrize("phrase", [
    "add a feature to the app",
    "update the current project",
    "change the existing project",
    "add this to the project",
])
def test_looks_like_feature_continuation_true(phrase):
    assert ct.looks_like_feature_continuation(phrase)


def test_looks_like_new_project_request():
    assert ct.looks_like_new_project_request("build me a new web app")
    assert ct.looks_like_new_project_request("start another project")
    assert not ct.looks_like_new_project_request("build me a calculator app")
    assert not ct.looks_like_new_project_request("add calculation history")


# ---------------------------------------------------------------------------
# Bounded, atomic, no-secrets persistence
# ---------------------------------------------------------------------------

def test_atomic_write_uses_temp_file_then_replace(isolated_state_file, monkeypatch):
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((src, dst))
        return real_replace(src, dst)
    monkeypatch.setattr(os, "replace", spy_replace)

    ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )

    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == isolated_state_file
    assert src != str(isolated_state_file)  # a distinct temp file, not an in-place write


def test_atomic_write_leaves_original_untouched_on_failure(isolated_state_file, monkeypatch):
    task = ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )
    before = isolated_state_file.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("simulated crash mid-write")
    monkeypatch.setattr(json, "dump", boom)

    with pytest.raises(RuntimeError):
        ct.save_task(task)

    after = isolated_state_file.read_text(encoding="utf-8")
    assert after == before  # untouched — no half-written state
    leftover_tmp = list(isolated_state_file.parent.glob(".coding_task_*"))
    assert leftover_tmp == []  # temp file cleaned up, not left behind


def test_state_never_contains_source_contents_or_credentials(isolated_state_file):
    task = ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )
    task.touch_files(["main.py", "utils.py"])
    task.record_error(
        "Traceback...\nNameError: name 'helper' is not defined",
        signature="NameError:main.py:3",
        evidence_summary="main.py:3, utils.py:1",
    )
    ct.save_task(task)

    raw = isolated_state_file.read_text(encoding="utf-8")
    forbidden = ["api_key", "API_KEY", "sk-", "def helper", "import ", "BEGIN PRIVATE KEY"]
    for token in forbidden:
        assert token not in raw
    # files_touched holds paths only, never file content
    assert json.loads(raw)["files_touched"] == ["main.py", "utils.py"]


def test_goal_and_error_fields_are_bounded():
    huge = "x" * 10_000
    task = ct.CodingTask(
        task_id="abc", original_goal=huge, current_goal=huge,
        project_name="p", project_root="/tmp/p",
    )
    task.record_error(huge, signature="x", evidence_summary=huge)

    assert len(task.last_runtime_error) <= ct.MAX_ERROR_CHARS
    assert len(task.last_evidence_summary) <= ct.MAX_EVIDENCE_SUMMARY_CHARS
