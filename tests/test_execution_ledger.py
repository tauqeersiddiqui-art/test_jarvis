import pytest

import core.execution_ledger as led


@pytest.fixture(autouse=True)
def isolated_ledger_state(tmp_path, monkeypatch):
    state_file = tmp_path / "config" / "state" / "execution_ledger.json"
    monkeypatch.setattr(led, "STATE_FILE", state_file)
    return state_file


def test_record_persists_all_required_fields():
    entry = led.record(
        task_id="task-1",
        operation_type="build",
        routing_decision="new_project",
        action_performed="_build_project",
        files_touched=["main.py", "utils.py"],
        duration_seconds=1.2345,
        result=led.Result.SUCCESS,
    )

    assert entry.task_id == "task-1"
    assert entry.operation_type == "build"
    assert entry.routing_decision == "new_project"
    assert entry.action_performed == "_build_project"
    assert entry.files_touched == ["main.py", "utils.py"]
    assert entry.duration_seconds == 1.234 or entry.duration_seconds == 1.235  # rounded to 3 dp
    assert entry.result == led.Result.SUCCESS
    assert entry.next_decision == ""
    assert entry.entry_id
    assert entry.timestamp

    reloaded = led._load_all()
    assert len(reloaded) == 1
    assert reloaded[0].task_id == "task-1"


def test_record_survives_restart_single_json_file():
    led.record(
        task_id="task-2", operation_type="feature_change", routing_decision="continue_feature",
        action_performed="_run_incremental_feature_change", files_touched=["main.py"],
        duration_seconds=0.5, result=led.Result.SUCCESS,
    )

    # Simulate a fresh process reading the same state file.
    reloaded = led._load_all()
    assert len(reloaded) == 1
    assert reloaded[0].operation_type == "feature_change"


def test_entries_for_task_filters_and_orders_oldest_first():
    led.record(task_id="task-a", operation_type="build", routing_decision="new_project",
               action_performed="_build_project", files_touched=[], duration_seconds=1.0,
               result=led.Result.SUCCESS)
    led.record(task_id="task-b", operation_type="runtime_fix", routing_decision="continue_fix",
               action_performed="_continue_fix_loop_for_task", files_touched=[], duration_seconds=2.0,
               result=led.Result.ROLLBACK)
    led.record(task_id="task-a", operation_type="feature_change", routing_decision="continue_feature",
               action_performed="_run_incremental_feature_change", files_touched=[], duration_seconds=3.0,
               result=led.Result.SUCCESS)

    entries = led.entries_for_task("task-a")

    assert len(entries) == 2
    assert [e.operation_type for e in entries] == ["build", "feature_change"]  # insertion order preserved
    assert all(e.task_id == "task-a" for e in entries)


def test_entries_for_task_returns_empty_list_for_unknown_task():
    assert led.entries_for_task("no-such-task") == []


def test_missing_state_file_yields_empty_list_not_an_exception():
    assert led._load_all() == []


def test_corrupt_state_file_fails_safe(tmp_path):
    led.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    led.STATE_FILE.write_text("{ not valid json", encoding="utf-8")

    assert led._load_all() == []


def test_files_touched_is_bounded():
    many_files = [f"file_{i}.py" for i in range(led.MAX_FILES_TOUCHED + 10)]
    entry = led.record(
        task_id="task-3", operation_type="build", routing_decision="new_project",
        action_performed="_build_project", files_touched=many_files,
        duration_seconds=0.1, result=led.Result.SUCCESS,
    )

    assert len(entry.files_touched) == led.MAX_FILES_TOUCHED


def test_total_entries_are_pruned_oldest_first_when_over_budget(monkeypatch):
    monkeypatch.setattr(led, "MAX_ENTRIES", 3)

    for i in range(5):
        led.record(
            task_id=f"task-{i}", operation_type="build", routing_decision="new_project",
            action_performed="_build_project", files_touched=[], duration_seconds=0.1,
            result=led.Result.SUCCESS,
        )

    entries = led._load_all()
    assert len(entries) == 3
    # the three most recently recorded tasks survive
    assert {e.task_id for e in entries} == {"task-2", "task-3", "task-4"}


def test_record_never_raises_when_persistence_fails(monkeypatch):
    def broken_write(path, data):
        raise OSError("disk full")
    monkeypatch.setattr(led, "_atomic_write_json", broken_write)

    entry = led.record(
        task_id="task-x", operation_type="build", routing_decision="new_project",
        action_performed="_build_project", files_touched=[], duration_seconds=0.1,
        result=led.Result.SUCCESS,
    )

    assert entry.task_id == "task-x"  # the in-memory entry is still returned
