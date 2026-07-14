import inspect
import os

import pytest

import core.capability_gap as cg
import core.learning_engine as le


@pytest.fixture(autouse=True)
def isolated_learning_state(tmp_path, monkeypatch):
    """Every test here must be isolated from whatever real Learning Engine
    state happens to exist on disk, so results stay deterministic regardless
    of machine state."""
    state_file = tmp_path / "learning_engine_state.json"
    monkeypatch.setattr(le, "STATE_FILE", state_file)
    return state_file


# A small, controlled fixture standing in for main.py's real structure, so
# most tests don't depend on main.py's evolving real tool list.
_FAKE_MAIN_SOURCE = '''
TOOL_DECLARATIONS = [
    {
        "name": "git_control",
        "description": "Runs git operations: status, diff, log, commit, push, pull, branch, clone.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "weather_report",
        "description": "Reports current weather and forecast for a city.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "declared_only_tool",
        "description": "A tool that is declared but has no dispatch handler below.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
]

def handle(name):
    if name == "git_control":
        return git_control()
    elif name == "weather_report":
        return weather_report()
'''


def test_build_inventory_from_fixture_source():
    inv = cg.build_inventory(main_source=_FAKE_MAIN_SOURCE)
    names = {c.name for c in inv}
    assert names == {"git_control", "weather_report", "declared_only_tool"}


def test_dispatch_cross_check_distinguishes_declared_only():
    inv = cg.build_inventory(main_source=_FAKE_MAIN_SOURCE)
    by_name = {c.name: c for c in inv}
    assert by_name["git_control"].has_dispatch_handler is True
    assert by_name["weather_report"].has_dispatch_handler is True
    assert by_name["declared_only_tool"].has_dispatch_handler is False


def test_inventory_comes_from_real_main_py_registration():
    """The default (no main_source override) path must read the REAL
    main.py file and reflect actual registered tools, not a hand-maintained
    list -- this is the 'prefer deriving from actual registered tools'
    requirement."""
    inv = cg.build_inventory()
    names = {c.name for c in inv}
    for real_tool in ("codebase_search", "investigate", "dev_agent", "git_control", "file_ops"):
        assert real_tool in names
    assert "totally_fake_capability_xyz" not in names
    # main.py's declared tools currently all have real dispatch handlers.
    assert all(c.has_dispatch_handler for c in inv)


def test_normalization_is_deterministic():
    a = cg._normalize("Check Git Status!")
    b = cg._normalize("check   git status")
    c = cg._normalize("STATUS check GIT")
    assert a == b == c == frozenset({"check", "git", "status"})


def test_normalization_drops_stopwords_and_short_tokens():
    tokens = cg._normalize("please do it for me, a")
    assert tokens == frozenset()


# ---------------------------------------------------------------------------
# Classification buckets (against the real, current main.py inventory)
# ---------------------------------------------------------------------------

def test_real_registered_capability_detected_as_available():
    result = cg.detect_gap("check git status", consult_knowledge=False)
    assert result.confidence == cg.CONFIDENCE_HIGH
    assert result.gap_detected is False
    assert result.missing_capability is False
    assert "git_control" in result.matched_capabilities


def test_missing_capability_reported_as_gap():
    result = cg.detect_gap("walk my dog around the neighborhood park", consult_knowledge=False)
    assert result.confidence == cg.CONFIDENCE_NONE
    assert result.gap_detected is True
    assert result.missing_capability is True
    assert result.matched_capabilities == []


def test_partial_match_is_distinguishable():
    result = cg.detect_gap("please organize my photos into folders", consult_knowledge=False)
    assert result.confidence == cg.CONFIDENCE_PARTIAL
    assert result.gap_detected is True
    assert result.missing_capability is False
    assert result.matched_capabilities  # something related was found, just not a strong match


def test_ambiguous_task_not_falsely_classified():
    result = cg.detect_gap("please help", consult_knowledge=False)
    assert result.confidence == cg.CONFIDENCE_AMBIGUOUS
    assert result.gap_detected is None  # neither true nor false -- not a forced classification
    assert result.matched_capabilities == []


def test_single_incidental_shared_word_does_not_force_high_confidence():
    """A task about property investment happens to share the word 'status'
    with system_status's name -- one coincidental word must not be enough to
    claim system_status covers property analysis."""
    result = cg.detect_gap(
        "analyze this property before I buy it, is it a good investment, check RERA status",
        consult_knowledge=False,
    )
    assert result.confidence != cg.CONFIDENCE_HIGH
    assert result.gap_detected is True


# ---------------------------------------------------------------------------
# Trust / Authority rule: Product Vision and Learning Engine never prove
# capability existence.
# ---------------------------------------------------------------------------

def test_product_vision_text_alone_never_proves_capability_existence(tmp_path):
    """Populate Learning Engine with a rich, detailed description of an
    UNIMPLEMENTED vision capability (mirrors PRODUCT_VISION.md Track F,
    Property Intelligence) and confirm the detector still reports a gap --
    background knowledge is never treated as proof of an existing tool."""
    docs_dir = tmp_path / "vision_src"
    docs_dir.mkdir()
    (docs_dir / "PRODUCT_VISION.md").write_text(
        "# Product Vision\n\n"
        "## Track F — Property Intelligence\n\n"
        "Analyze a property before purchase: collect property details, RERA "
        "information, investment analysis. Output: Buy or Avoid.\n",
        encoding="utf-8",
    )
    le.learn(workspace=docs_dir)

    # Inventory has NO real property-analysis tool -- only the generic fixture.
    inv = cg.build_inventory(main_source=_FAKE_MAIN_SOURCE)
    result = cg.detect_gap(
        "analyze this property before I buy it and check RERA information",
        inventory=inv,
        consult_knowledge=True,
    )

    assert result.background_knowledge  # Learning Engine did surface the vision text
    assert result.confidence != cg.CONFIDENCE_HIGH
    assert result.gap_detected is True
    assert result.missing_capability is True


def test_learning_engine_knowledge_alone_never_proves_capability_existence(tmp_path):
    """Even when Learning Engine strongly matches the task's own wording,
    that must not upgrade the classification -- only the real tool inventory
    can produce CONFIDENCE_HIGH."""
    docs_dir = tmp_path / "docs_src"
    docs_dir.mkdir()
    (docs_dir / "notes.md").write_text(
        "# Notes\n\n## Quantum Widget Synthesis\n\n"
        "This documents the quantum widget synthesis capability in detail.\n",
        encoding="utf-8",
    )
    le.learn(workspace=docs_dir)

    inv = cg.build_inventory(main_source=_FAKE_MAIN_SOURCE)  # no quantum widget tool exists
    result = cg.detect_gap("perform quantum widget synthesis", inventory=inv, consult_knowledge=True)

    assert result.background_knowledge
    assert any("Quantum Widget Synthesis" in bk["section_title"] for bk in result.background_knowledge)
    assert result.confidence == cg.CONFIDENCE_NONE
    assert result.gap_detected is True
    assert result.missing_capability is True


# ---------------------------------------------------------------------------
# Fail-safety: Learning Engine trouble must never break gap detection.
# ---------------------------------------------------------------------------

def test_missing_learning_engine_state_is_non_fatal(isolated_learning_state):
    assert not isolated_learning_state.exists()
    result = cg.detect_gap("check git status")  # consult_knowledge defaults True
    assert result.confidence == cg.CONFIDENCE_HIGH
    assert result.background_knowledge == []


def test_corrupt_learning_engine_state_is_non_fatal(isolated_learning_state):
    isolated_learning_state.parent.mkdir(parents=True, exist_ok=True)
    isolated_learning_state.write_text("{not valid json at all", encoding="utf-8")
    result = cg.detect_gap("check git status")
    assert result.confidence == cg.CONFIDENCE_HIGH
    assert result.background_knowledge == []


def test_learning_engine_search_failure_is_non_fatal(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("simulated Learning Engine failure")
    monkeypatch.setattr(le, "search", _raise)

    result = cg.detect_gap("check git status")
    assert result.confidence == cg.CONFIDENCE_HIGH
    assert result.background_knowledge == []


# ---------------------------------------------------------------------------
# Never calls learn(), no LLM call, no execution, no file modification.
# ---------------------------------------------------------------------------

def test_detect_gap_never_calls_learn(monkeypatch):
    def _forbidden(*args, **kwargs):
        raise AssertionError("detect_gap() must never call learning_engine.learn()")
    monkeypatch.setattr(le, "learn", _forbidden)

    cg.detect_gap("check git status")  # must not raise


def test_module_source_has_no_ai_provider_or_execution_path():
    import re as _re

    src = inspect.getsource(cg)
    forbidden_substrings = (
        "ai_provider", "complete_with_failover", "subprocess", "os.system", "importlib",
    )
    for forbidden in forbidden_substrings:
        assert forbidden not in src, f"unexpected capability-execution/LLM path in capability_gap.py: {forbidden}"

    # ast.literal_eval() (safe -- only parses literals, never executes code)
    # is expected and used for inventory extraction; a bare eval()/exec()
    # call would not be.
    assert _re.search(r"(?<!literal_)\beval\(", src) is None
    assert _re.search(r"\bexec\(", src) is None


def test_module_source_has_no_write_capable_calls():
    src = inspect.getsource(cg)
    for forbidden in ("write_text(", "write_bytes(", "unlink(", "os.remove", "shutil.rmtree", "shutil.move"):
        assert forbidden not in src, f"unexpected write-capable call in capability_gap.py: {forbidden}"


def test_detect_gap_does_not_modify_main_py():
    main_path = cg.MAIN_MODULE_PATH
    before = main_path.stat().st_mtime_ns
    cg.detect_gap("check git status")
    after = main_path.stat().st_mtime_ns
    assert before == after


def test_detect_gap_does_not_execute_any_capability(monkeypatch):
    """A crude but direct check: nothing in actions/* gets imported or
    invoked as a side effect of detection."""
    import sys
    before_action_modules = {m for m in sys.modules if m.startswith("actions.")}
    cg.detect_gap("run git status and open chrome and send a whatsapp message")
    after_action_modules = {m for m in sys.modules if m.startswith("actions.")}
    assert after_action_modules == before_action_modules


# ---------------------------------------------------------------------------
# Bounded results
# ---------------------------------------------------------------------------

def test_requested_task_is_bounded():
    long_task = "check git status " + ("x" * 5000)
    result = cg.detect_gap(long_task, consult_knowledge=False)
    assert len(result.requested_task) <= cg.MAX_TASK_CHARS


def test_matched_capabilities_are_bounded():
    inv = [
        cg.CapabilityRecord(name=f"tool_{i}", description="handles widget tasks", has_dispatch_handler=True)
        for i in range(50)
    ]
    result = cg.detect_gap("handles widget tasks", inventory=inv, consult_knowledge=False)
    assert len(result.matched_capabilities) <= cg.MAX_MATCHED_CAPABILITIES


def test_evidence_is_bounded():
    result = cg.detect_gap("check git status", consult_knowledge=False)
    assert len(result.evidence) <= cg.MAX_EVIDENCE_CHARS


def test_background_knowledge_is_bounded(tmp_path):
    docs_dir = tmp_path / "many_docs"
    docs_dir.mkdir()
    sections = "\n\n".join(f"## Widget Topic {i}\n\nabout widgets and gadgets number {i}" for i in range(30))
    (docs_dir / "many.md").write_text(f"# Many\n\n{sections}\n", encoding="utf-8")
    le.learn(workspace=docs_dir)

    result = cg.detect_gap("tell me about widget topics and gadgets", consult_knowledge=True)
    assert len(result.background_knowledge) <= cg.MAX_BACKGROUND_KNOWLEDGE_ITEMS


def test_gap_result_to_dict_round_trips():
    result = cg.detect_gap("check git status", consult_knowledge=False)
    d = result.to_dict()
    assert d["requested_task"] == result.requested_task
    assert d["confidence"] == cg.CONFIDENCE_HIGH
