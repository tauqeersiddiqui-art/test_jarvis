import json
import os

import pytest

import core.engineering_memory as em


class _FakeTask:
    def __init__(self, task_id="t1", project_root="/tmp/proj"):
        self.task_id = task_id
        self.project_root = project_root


@pytest.fixture(autouse=True)
def isolated_state_file(tmp_path, monkeypatch):
    state_file = tmp_path / "config" / "state" / "engineering_memory.json"
    monkeypatch.setattr(em, "STATE_FILE", state_file)
    return state_file


# ---------------------------------------------------------------------------
# Persistence / reload / fail-safety
# ---------------------------------------------------------------------------

def test_project_scoped_records_persist_and_reload(isolated_state_file):
    task = _FakeTask(project_root="/tmp/JarvisProjects/calculator_app")
    em.record_outcome(
        task, "runtime_fix", "build a calculator", "NameError:main.py:3",
        "main.py:3", "risk: low", ["main.py"], "fix NameError in main.py",
        outcome=em.OUTCOME_SUCCESS,
    )

    records = em._load_all()
    assert len(records) == 1
    assert records[0].project_key == em.project_key(task.project_root)
    assert records[0].outcome == em.OUTCOME_SUCCESS


def test_no_state_file_means_no_records(isolated_state_file):
    assert not isolated_state_file.exists()
    assert em._load_all() == []


def test_corrupt_state_file_fails_safe(isolated_state_file):
    isolated_state_file.parent.mkdir(parents=True, exist_ok=True)
    isolated_state_file.write_text("{not valid json at all", encoding="utf-8")
    assert em._load_all() == []


def test_corrupt_records_entries_are_skipped_not_fatal(isolated_state_file):
    isolated_state_file.parent.mkdir(parents=True, exist_ok=True)
    isolated_state_file.write_text(json.dumps({"records": [{"garbage": 1}, "not a dict"]}), encoding="utf-8")
    assert em._load_all() == []  # skipped, no crash


def test_atomic_write_uses_temp_file_then_replace(isolated_state_file, monkeypatch):
    calls = []
    real_replace = os.replace
    def spy(src, dst):
        calls.append((src, dst))
        return real_replace(src, dst)
    monkeypatch.setattr(os, "replace", spy)

    task = _FakeTask()
    em.record_outcome(
        task, "runtime_fix", "goal", "sig", "ev", "impact", ["a.py"], "attempt",
        outcome=em.OUTCOME_SUCCESS,
    )

    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == isolated_state_file
    assert src != str(isolated_state_file)


def test_atomic_write_leaves_original_untouched_on_failure(isolated_state_file, monkeypatch):
    task = _FakeTask()
    em.record_outcome(task, "runtime_fix", "goal", "sig", "ev", "impact", ["a.py"], "attempt", outcome=em.OUTCOME_SUCCESS)
    before = isolated_state_file.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("simulated crash")
    monkeypatch.setattr(json, "dump", boom)

    # record_outcome swallows persistence errors (fail-safe) — must not raise
    em.record_outcome(task, "runtime_fix", "goal2", "sig2", "ev", "impact", ["b.py"], "attempt2", outcome=em.OUTCOME_FAILED)

    after = isolated_state_file.read_text(encoding="utf-8")
    assert after == before
    assert list(isolated_state_file.parent.glob(".engineering_memory_*")) == []


# ---------------------------------------------------------------------------
# Bounded fields / no secrets or source content
# ---------------------------------------------------------------------------

def test_text_fields_are_bounded(isolated_state_file):
    task = _FakeTask()
    huge = "x" * 5000
    record = em.record_outcome(
        task, "runtime_fix", huge, huge, huge, huge, ["a.py"] * 100, huge,
        outcome=em.OUTCOME_FAILED, rollback_reason=huge,
    )
    assert len(record.goal_summary) <= em.MAX_GOAL_CHARS
    assert len(record.normalized_error_signature) <= em.MAX_SIGNATURE_CHARS
    assert len(record.evidence_summary) <= em.MAX_SUMMARY_CHARS
    assert len(record.impact_summary) <= em.MAX_SUMMARY_CHARS
    assert len(record.attempt_summary) <= em.MAX_SUMMARY_CHARS
    assert len(record.rollback_reason) <= em.MAX_SUMMARY_CHARS
    assert len(record.files_touched) <= em.MAX_FILES_TOUCHED


def test_memory_never_contains_source_or_credentials(isolated_state_file):
    task = _FakeTask()
    em.record_outcome(
        task, "runtime_fix",
        goal_summary="add calculation history",
        normalized_error_signature="NameError:main.py:3",
        evidence_summary="main.py:3, utils.py:1",
        impact_summary="risk: low",
        files_touched=["main.py", "utils.py"],
        attempt_summary="fix NameError in main.py",
        outcome=em.OUTCOME_SUCCESS,
    )
    raw = isolated_state_file.read_text(encoding="utf-8")
    for token in ("api_key", "API_KEY", "sk-", "BEGIN PRIVATE KEY", "def helper", "import os"):
        assert token not in raw


def test_project_key_never_stores_raw_path(isolated_state_file):
    key = em.project_key("/tmp/JarvisProjects/calculator_app")
    assert "/tmp" not in key
    assert "calculator" not in key
    assert len(key) == 16


# ---------------------------------------------------------------------------
# Retention / bounded growth
# ---------------------------------------------------------------------------

def test_retention_caps_records_per_project(isolated_state_file):
    task = _FakeTask(project_root="/tmp/JarvisProjects/big_project")
    for i in range(em.MAX_RECORDS_PER_PROJECT + 10):
        em.record_outcome(
            task, "runtime_fix", "goal", f"sig-{i}", "ev", "impact", ["a.py"],
            f"attempt {i}", outcome=em.OUTCOME_ROLLED_BACK,
        )
    records = [r for r in em._load_all() if r.project_key == em.project_key(task.project_root)]
    assert len(records) <= em.MAX_RECORDS_PER_PROJECT


def test_retention_caps_total_records_across_projects(isolated_state_file):
    for p in range(30):
        task = _FakeTask(project_root=f"/tmp/JarvisProjects/proj_{p}")
        em.record_outcome(
            task, "runtime_fix", "goal", "sig", "ev", "impact", ["a.py"], "attempt",
            outcome=em.OUTCOME_ROLLED_BACK,
        )
    records = em._load_all()
    assert len(records) <= em.MAX_TOTAL_RECORDS


def test_retention_prunes_low_value_records_first(isolated_state_file):
    task = _FakeTask(project_root="/tmp/JarvisProjects/proj")
    # One valuable success record first...
    em.record_outcome(task, "runtime_fix", "goal", "keeper-sig", "ev", "impact", ["a.py"], "attempt", outcome=em.OUTCOME_SUCCESS)
    # ...then enough rolled_back records to force pruning.
    for i in range(em.MAX_RECORDS_PER_PROJECT + 5):
        em.record_outcome(task, "runtime_fix", "goal", f"noise-{i}", "ev", "impact", ["a.py"], "attempt", outcome=em.OUTCOME_ROLLED_BACK)

    records = [r for r in em._load_all() if r.project_key == em.project_key(task.project_root)]
    sigs = {r.normalized_error_signature for r in records}
    assert "keeper-sig" in sigs  # the success record survives pruning over the noisy failures


# ---------------------------------------------------------------------------
# Deterministic search / ranking
# ---------------------------------------------------------------------------

def test_exact_error_signature_match_ranks_highest(isolated_state_file):
    task = _FakeTask(project_root="/tmp/JarvisProjects/proj")
    em.record_outcome(task, "runtime_fix", "goal", "OtherError:x.py:1", "ev", "impact", ["x.py"], "attempt", outcome=em.OUTCOME_IMPROVED)
    em.record_outcome(task, "runtime_fix", "goal", "NameError:main.py:3", "ev", "impact", ["main.py"], "attempt", outcome=em.OUTCOME_SUCCESS)

    pkey = em.project_key(task.project_root)
    results = em.find_relevant_for_error(pkey, "NameError:main.py:3")

    assert results
    assert results[0].normalized_error_signature == "NameError:main.py:3"


def test_same_project_ranks_above_unrelated_project(isolated_state_file):
    same_task = _FakeTask(project_root="/tmp/JarvisProjects/calculator_app")
    other_task = _FakeTask(project_root="/tmp/JarvisProjects/totally_different_app")

    em.record_outcome(same_task, "runtime_fix", "goal", "NameError:main.py:3", "ev", "impact", ["main.py"], "attempt", outcome=em.OUTCOME_SUCCESS)
    em.record_outcome(other_task, "runtime_fix", "goal", "NameError:main.py:3", "ev", "impact", ["main.py"], "attempt", outcome=em.OUTCOME_SUCCESS)

    pkey = em.project_key(same_task.project_root)
    results = em.find_relevant_for_error(pkey, "NameError:main.py:3")

    assert len(results) == 1
    assert results[0].task_id == same_task.task_id


def test_unrelated_project_memory_is_never_returned(isolated_state_file):
    other_task = _FakeTask(project_root="/tmp/JarvisProjects/unrelated_app")
    em.record_outcome(other_task, "runtime_fix", "goal", "NameError:main.py:3", "ev", "impact", ["main.py"], "attempt", outcome=em.OUTCOME_SUCCESS)

    pkey = em.project_key("/tmp/JarvisProjects/some_other_project_entirely")
    results = em.find_relevant_for_error(pkey, "NameError:main.py:3")

    assert results == []


def test_find_relevant_for_change_ranks_by_file_overlap_and_wording(isolated_state_file):
    task = _FakeTask(project_root="/tmp/JarvisProjects/proj")
    em.record_outcome(
        task, "feature_change", "add logging to utils", "", "ev", "impact",
        ["utils.py"], "add logging to utils", outcome=em.OUTCOME_SUCCESS,
    )
    em.record_outcome(
        task, "feature_change", "add calculation history", "", "ev", "impact",
        ["main.py", "history.py"], "add calculation history", outcome=em.OUTCOME_SUCCESS,
    )

    pkey = em.project_key(task.project_root)
    results = em.find_relevant_for_change(pkey, "add calculation history tracking", ["main.py", "history.py"])

    assert results
    assert results[0].goal_summary == "add calculation history"


# ---------------------------------------------------------------------------
# Attempt fingerprint / failed-approach avoidance
# ---------------------------------------------------------------------------

def test_identical_conceptual_attempt_produces_same_fingerprint_despite_rewording():
    fp1 = em.compute_attempt_fingerprint("runtime_fix", "NameError:main.py:3", ["main.py"], "fix NameError in main.py")
    fp2 = em.compute_attempt_fingerprint("runtime_fix", "NameError:main.py:3", ["main.py"], "Fix the NameError in main.py")
    assert fp1 == fp2  # word-set normalized, not brittle exact-string match


def test_different_attempt_produces_different_fingerprint():
    fp1 = em.compute_attempt_fingerprint("runtime_fix", "NameError:main.py:3", ["main.py"], "fix NameError in main.py")
    fp2 = em.compute_attempt_fingerprint("runtime_fix", "NameError:main.py:3", ["main.py"], "rewrite the whole module")
    assert fp1 != fp2


def test_find_matching_failed_attempt_detects_prior_rollback(isolated_state_file):
    task = _FakeTask(project_root="/tmp/JarvisProjects/proj")
    fp = em.compute_attempt_fingerprint("runtime_fix", "NameError:main.py:3", ["main.py"], "fix NameError in main.py")
    record = em.EngineeringRecord(
        record_id="r1", task_id=task.task_id, project_key=em.project_key(task.project_root),
        timestamp=em._now(), operation_type="runtime_fix", goal_summary="goal",
        normalized_error_signature="NameError:main.py:3", evidence_summary="ev", impact_summary="impact",
        files_touched=["main.py"], attempt_summary="fix NameError in main.py",
        outcome=em.OUTCOME_ROLLED_BACK, attempt_fingerprint=fp,
    )
    em._save_all([record])

    match = em.find_matching_failed_attempt(em.project_key(task.project_root), fp)
    assert match is not None
    assert match.record_id == "r1"


def test_find_matching_failed_attempt_ignores_successful_records(isolated_state_file):
    task = _FakeTask(project_root="/tmp/JarvisProjects/proj")
    fp = em.compute_attempt_fingerprint("runtime_fix", "NameError:main.py:3", ["main.py"], "fix NameError in main.py")
    record = em.EngineeringRecord(
        record_id="r1", task_id=task.task_id, project_key=em.project_key(task.project_root),
        timestamp=em._now(), operation_type="runtime_fix", goal_summary="goal",
        normalized_error_signature="NameError:main.py:3", evidence_summary="ev", impact_summary="impact",
        files_touched=["main.py"], attempt_summary="fix NameError in main.py",
        outcome=em.OUTCOME_SUCCESS, attempt_fingerprint=fp,
    )
    em._save_all([record])

    match = em.find_matching_failed_attempt(em.project_key(task.project_root), fp)
    assert match is None


def test_summarize_records_never_includes_source_content():
    records = [em.EngineeringRecord(
        record_id="r1", task_id="t1", project_key="pk", timestamp=em._now(),
        operation_type="runtime_fix", goal_summary="goal",
        normalized_error_signature="NameError:main.py:3", evidence_summary="ev",
        impact_summary="impact", files_touched=["main.py"],
        attempt_summary="SECRET_LOOKING_CODE_XYZ = 1", outcome=em.OUTCOME_ROLLED_BACK,
        rollback_reason="write_failure",
    )]
    summary = em.summarize_records(records)
    assert "main.py" in summary
    assert "rolled_back" in summary
    assert "SECRET_LOOKING_CODE_XYZ" not in summary  # attempt_summary (closest thing to code) never surfaces
