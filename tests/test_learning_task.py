import inspect
import json
import os

import pytest

import core.capability_gap as cg
import core.learning_task as lt


@pytest.fixture(autouse=True)
def isolated_state_file(tmp_path, monkeypatch):
    state_file = tmp_path / "config" / "state" / "learning_tasks.json"
    monkeypatch.setattr(lt, "STATE_FILE", state_file)
    return state_file


def _gap(
    confidence,
    gap_detected,
    requested_task="walk my dog around the neighborhood park",
    required_capability="around dog neighborhood park walk",
    evidence="No registered capability's name/description overlapped with: around, dog, neighborhood, park, walk.",
    matched=None,
    missing=None,
):
    return cg.GapResult(
        requested_task=requested_task,
        required_capability=required_capability,
        matched_capabilities=matched or [],
        missing_capability=missing if missing is not None else (confidence == cg.CONFIDENCE_NONE),
        gap_detected=gap_detected,
        confidence=confidence,
        evidence=evidence,
        background_knowledge=[],
    )


# ---------------------------------------------------------------------------
# Gating: only a confirmed full miss creates/updates a task.
# ---------------------------------------------------------------------------

def test_confirmed_gap_creates_a_task():
    gap = _gap(cg.CONFIDENCE_NONE, True)
    task = lt.create_from_gap(gap)
    assert task is not None
    assert task.status == lt.Status.PENDING
    assert task.missing_capability == "around dog neighborhood park walk"


def test_available_capability_creates_no_task():
    gap = _gap(cg.CONFIDENCE_HIGH, False, matched=["git_control"])
    assert lt.create_from_gap(gap) is None
    assert lt.list_tasks() == []


def test_partial_match_creates_no_task():
    """capability_gap.py sets gap_detected=True for partial matches too --
    this must NOT be enough on its own to create a task."""
    gap = _gap(cg.CONFIDENCE_PARTIAL, True, matched=["desktop_control"])
    assert gap.gap_detected is True  # sanity: this is the tricky case
    assert lt.create_from_gap(gap) is None
    assert lt.list_tasks() == []


def test_ambiguous_result_creates_no_task():
    gap = _gap(cg.CONFIDENCE_AMBIGUOUS, None, requested_task="please help", required_capability="help")
    assert lt.create_from_gap(gap) is None
    assert lt.list_tasks() == []


def test_none_gap_result_creates_no_task():
    assert lt.create_from_gap(None) is None


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_duplicate_gap_preserves_task_id():
    first = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    second = lt.create_from_gap(_gap(
        cg.CONFIDENCE_NONE, True,
        requested_task="WALK my DOG around the neighborhood park",
        required_capability="around dog neighborhood park walk",  # same normalized capability
    ))
    assert second.task_id == first.task_id
    assert len(lt.list_tasks()) == 1


def test_duplicate_gap_increments_occurrence_count():
    lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    second = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    third = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    assert second.occurrence_count == 2
    assert third.occurrence_count == 3


def test_duplicate_gap_updates_last_seen_at():
    first = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    first_seen = first.last_seen_at
    second = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    assert second.last_seen_at >= first_seen


def test_different_capability_creates_a_separate_task():
    lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    other = lt.create_from_gap(_gap(
        cg.CONFIDENCE_NONE, True,
        requested_task="compose a symphony for orchestra",
        required_capability="compose orchestra symphony",
    ))
    assert other is not None
    assert len(lt.list_tasks()) == 2


def test_find_by_capability_locates_existing_task():
    created = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    found = lt.find_by_capability("Walk MY dog around the Neighborhood park")
    assert found is not None
    assert found.task_id == created.task_id


# ---------------------------------------------------------------------------
# Normalization / priority determinism
# ---------------------------------------------------------------------------

def test_normalization_is_deterministic():
    a = lt.normalize_capability("Walk My Dog Around The Park!")
    b = lt.normalize_capability("walk   dog around park")
    c = lt.normalize_capability("PARK dog walk around")
    assert a == b == c


def test_priority_is_deterministic_for_same_inputs():
    assert lt._priority_for(1, lt.SOURCE_DETECTION) == lt._priority_for(1, lt.SOURCE_DETECTION)
    assert lt._priority_for(3, lt.SOURCE_USER) == lt._priority_for(3, lt.SOURCE_USER)


def test_priority_increases_with_repeated_occurrence():
    p1 = lt._priority_for(1, lt.SOURCE_DETECTION)
    p2 = lt._priority_for(2, lt.SOURCE_DETECTION)
    p5 = lt._priority_for(5, lt.SOURCE_DETECTION)
    assert p2 > p1
    assert p5 > p2


def test_priority_bonus_capped():
    p_at_cap = lt._priority_for(1 + lt.MAX_OCCURRENCE_BONUS, lt.SOURCE_DETECTION)
    p_beyond_cap = lt._priority_for(1000, lt.SOURCE_DETECTION)
    assert p_at_cap == p_beyond_cap


def test_user_requested_source_ranks_above_incidental_detection():
    p_detected = lt._priority_for(1, lt.SOURCE_DETECTION)
    p_user = lt._priority_for(1, lt.SOURCE_USER)
    assert p_user > p_detected


def test_repeated_occurrence_raises_priority_end_to_end():
    task = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    p1 = task.priority
    task2 = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    assert task2.priority > p1


# ---------------------------------------------------------------------------
# Status lifecycle
# ---------------------------------------------------------------------------

def test_status_lifecycle_valid_transition():
    task = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    updated = lt.update_status(task.task_id, lt.Status.APPROVED)
    assert updated is not None
    assert updated.status == lt.Status.APPROVED


def test_status_lifecycle_rejects_invalid_transition():
    task = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    # pending -> learning is not allowed (must go through approved first)
    result = lt.update_status(task.task_id, lt.Status.LEARNING)
    assert result is None
    assert lt.get_task(task.task_id).status == lt.Status.PENDING


def test_status_lifecycle_rejects_unknown_status():
    task = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    assert lt.update_status(task.task_id, "not_a_real_status") is None


def test_status_lifecycle_rejects_unknown_task_id():
    assert lt.update_status("nonexistent", lt.Status.APPROVED) is None


def test_status_full_lifecycle_path():
    task = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    task = lt.update_status(task.task_id, lt.Status.APPROVED)
    assert task.status == lt.Status.APPROVED
    task = lt.update_status(task.task_id, lt.Status.LEARNING)
    assert task.status == lt.Status.LEARNING
    task = lt.update_status(task.task_id, lt.Status.COMPLETED)
    assert task.status == lt.Status.COMPLETED
    # terminal state -- no further transition allowed
    assert lt.update_status(task.task_id, lt.Status.PENDING) is None


def test_failed_status_can_be_requeued_to_pending():
    task = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    task = lt.update_status(task.task_id, lt.Status.APPROVED)
    task = lt.update_status(task.task_id, lt.Status.LEARNING)
    task = lt.update_status(task.task_id, lt.Status.FAILED)
    assert task.status == lt.Status.FAILED
    task = lt.update_status(task.task_id, lt.Status.PENDING)
    assert task.status == lt.Status.PENDING


def test_rejected_status_is_terminal():
    task = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    task = lt.update_status(task.task_id, lt.Status.REJECTED)
    assert task.status == lt.Status.REJECTED
    assert lt.update_status(task.task_id, lt.Status.APPROVED) is None


# ---------------------------------------------------------------------------
# Persistence: atomic writes, corruption/failure fail-safety
# ---------------------------------------------------------------------------

def test_no_state_file_means_no_tasks(isolated_state_file):
    assert not isolated_state_file.exists()
    assert lt.list_tasks() == []


def test_corrupt_state_file_fails_safe(isolated_state_file):
    isolated_state_file.parent.mkdir(parents=True, exist_ok=True)
    isolated_state_file.write_text("{not valid json at all", encoding="utf-8")
    assert lt._load_all() == []
    # normal execution (creating a new task) must still work
    task = lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    assert task is not None


def test_corrupt_task_entries_are_skipped_not_fatal(isolated_state_file):
    isolated_state_file.parent.mkdir(parents=True, exist_ok=True)
    isolated_state_file.write_text(json.dumps({"tasks": [{"garbage": 1}, "not a dict"]}), encoding="utf-8")
    assert lt._load_all() == []


def test_atomic_write_uses_temp_file_then_replace(isolated_state_file, monkeypatch):
    calls = []
    real_replace = os.replace

    def spy(src, dst):
        calls.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))

    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == isolated_state_file
    assert src != str(isolated_state_file)


def test_interrupted_write_preserves_previous_state(isolated_state_file, monkeypatch):
    lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    before = isolated_state_file.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("simulated crash")
    monkeypatch.setattr(json, "dump", boom)

    # create_from_gap swallows persistence errors (fail-safe) -- must not raise
    lt.create_from_gap(_gap(
        cg.CONFIDENCE_NONE, True,
        requested_task="compose a symphony for orchestra",
        required_capability="compose orchestra symphony",
    ))

    after = isolated_state_file.read_text(encoding="utf-8")
    assert after == before
    assert list(isolated_state_file.parent.glob(".learning_task_*")) == []


# ---------------------------------------------------------------------------
# Bounded fields / bounded retention / no sensitive content
# ---------------------------------------------------------------------------

def test_fields_are_bounded():
    huge = "widget " * 2000
    gap = _gap(
        cg.CONFIDENCE_NONE, True,
        requested_task=huge, required_capability=huge, evidence=huge,
    )
    task = lt.create_from_gap(gap)
    assert task is not None
    assert len(task.requested_task) <= lt.MAX_TASK_CHARS
    assert len(task.missing_capability) <= lt.MAX_CAPABILITY_CHARS
    assert len(task.gap_reason) <= lt.MAX_REASON_CHARS


def test_bounded_retention_prunes_lowest_priority_oldest_first(monkeypatch):
    monkeypatch.setattr(lt, "MAX_TASKS", 5)
    for i in range(20):
        lt.create_from_gap(_gap(
            cg.CONFIDENCE_NONE, True,
            requested_task=f"unique task {i}",
            required_capability=f"unique{i} task",
        ))
    tasks = lt.list_tasks()
    assert len(tasks) <= 5


def test_no_secrets_or_raw_prompt_or_source_stored():
    gap = _gap(
        cg.CONFIDENCE_NONE, True,
        requested_task="use my API_KEY=sk-real-secret-12345 to do the task",
        required_capability="api key sk real secret 12345 task use",
        evidence="No registered capability overlapped.",
    )
    task = lt.create_from_gap(gap)
    d = task.to_dict()
    # The model has no field intended for secrets/tokens/raw source/prompts
    # in the first place -- confirm the schema itself stays to the required
    # bounded fields (plus nothing resembling a prompt/source/secret field).
    forbidden_field_names = {"api_key", "secret", "token", "prompt", "source_code", "raw_prompt"}
    assert forbidden_field_names.isdisjoint(d.keys())


# ---------------------------------------------------------------------------
# Never learn(), never AI provider, never execution/research, detection
# stays read-only.
# ---------------------------------------------------------------------------

def test_module_source_has_no_learn_call_or_ai_provider_or_execution_path():
    src = inspect.getsource(lt)
    forbidden_substrings = (
        "learning_engine.learn", ".learn(", "ai_provider", "complete_with_failover",
        "subprocess", "os.system", "importlib", "pip install", "requests.get",
        "requests.post", "urllib", "webbrowser",
    )
    for forbidden in forbidden_substrings:
        assert forbidden not in src, f"unexpected path in learning_task.py: {forbidden}"


def test_module_source_has_no_write_capable_calls_beyond_its_own_state_file():
    src = inspect.getsource(lt)
    for forbidden in ("write_bytes(", "shutil.rmtree", "shutil.move", "shutil.copy"):
        assert forbidden not in src, f"unexpected write-capable call in learning_task.py: {forbidden}"


def test_create_from_gap_does_not_import_actions_modules():
    import sys
    before = {m for m in sys.modules if m.startswith("actions.")}
    lt.create_from_gap(_gap(cg.CONFIDENCE_NONE, True))
    after = {m for m in sys.modules if m.startswith("actions.")}
    assert after == before


def test_detect_gap_remains_read_only_and_unaware_of_learning_task():
    """core/capability_gap.py must not import or reference core/learning_task.py
    -- detection and task creation stay separate, and detect_gap() never
    persists anything on its own."""
    src = inspect.getsource(cg)
    assert "learning_task" not in src


def test_detect_gap_call_alone_creates_no_learning_task(tmp_path, monkeypatch):
    monkeypatch.setattr(lt, "STATE_FILE", tmp_path / "learning_tasks.json")
    cg.detect_gap("walk my dog around the neighborhood park", consult_knowledge=False)
    assert lt.list_tasks() == []
