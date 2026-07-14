import inspect
import io
import json
import os
import tokenize

import pytest

import core.learning_task as lt
import core.learning_source_planner as lsp


def _code_without_strings_and_comments(source: str) -> str:
    """Strips string literals (incl. docstrings) and comments via tokenize,
    so source-inspection tests check actual executable code, not prose that
    documents a constraint (e.g. a docstring saying 'never calls learn()'
    must not itself trip a substring check meant to verify no CALL to
    learn() exists)."""
    out = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok_type, tok_string, *_ in tokens:
            if tok_type in (tokenize.STRING, tokenize.COMMENT):
                continue
            out.append(tok_string)
    except tokenize.TokenizeError:
        return source
    return " ".join(out)


@pytest.fixture(autouse=True)
def isolated_state_file(tmp_path, monkeypatch):
    state_file = tmp_path / "config" / "state" / "learning_source_plans.json"
    monkeypatch.setattr(lsp, "STATE_FILE", state_file)
    return state_file


def _task(
    status,
    task_id="t1",
    requested_task="analyze property RERA before buying, check land registry status",
    missing_capability="rera property investment government registry",
):
    return lt.LearningTask(
        task_id=task_id,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        requested_task=requested_task,
        missing_capability=missing_capability,
        gap_reason="No registered capability overlapped.",
        source=lt.SOURCE_DETECTION,
        priority=1,
        status=status,
        occurrence_count=1,
        last_seen_at="2026-01-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Status gating: only APPROVED tasks produce a plan.
# ---------------------------------------------------------------------------

def test_approved_learning_task_creates_a_plan():
    plan = lsp.create_plan_from_task(_task(lt.Status.APPROVED))
    assert plan is not None
    assert plan.learning_task_id == "t1"
    assert plan.status == lsp.Status.DRAFT


def test_pending_task_creates_no_plan():
    assert lsp.create_plan_from_task(_task(lt.Status.PENDING)) is None
    assert lsp.list_plans() == []


def test_learning_task_creates_no_new_plan():
    assert lsp.create_plan_from_task(_task(lt.Status.LEARNING)) is None
    assert lsp.list_plans() == []


def test_completed_task_creates_no_plan():
    assert lsp.create_plan_from_task(_task(lt.Status.COMPLETED)) is None
    assert lsp.list_plans() == []


def test_failed_task_creates_no_plan():
    assert lsp.create_plan_from_task(_task(lt.Status.FAILED)) is None
    assert lsp.list_plans() == []


def test_rejected_task_creates_no_plan():
    assert lsp.create_plan_from_task(_task(lt.Status.REJECTED)) is None
    assert lsp.list_plans() == []


def test_none_task_creates_no_plan():
    assert lsp.create_plan_from_task(None) is None


# ---------------------------------------------------------------------------
# Domain classification -> source policy
# ---------------------------------------------------------------------------

def test_property_government_capability_requires_authoritative_government_sources():
    plan = lsp.create_plan_from_task(_task(
        lt.Status.APPROVED,
        requested_task="analyze this property before I buy it, check RERA and land registry",
        missing_capability="rera property government registry investment",
    ))
    assert plan.domain == lsp.DOMAIN_GOVERNMENT_PROPERTY
    assert plan.required_authority == lsp.AUTHORITY_AUTHORITATIVE
    assert "government_source" in plan.source_categories


def test_legal_regulatory_requires_high_authority_sources():
    plan = lsp.create_plan_from_task(_task(
        lt.Status.APPROVED,
        requested_task="check legal regulatory compliance and filing requirements",
        missing_capability="legal compliance regulatory filing requirements",
    ))
    assert plan.domain == lsp.DOMAIN_LEGAL_REGULATORY
    assert plan.required_authority in (lsp.AUTHORITY_AUTHORITATIVE, lsp.AUTHORITY_PRIMARY)


def test_api_capability_prefers_official_documentation_and_api_reference():
    plan = lsp.create_plan_from_task(_task(
        lt.Status.APPROVED,
        requested_task="integrate with a REST API using OAuth",
        missing_capability="api oauth rest integration endpoint",
    ))
    assert plan.domain == lsp.DOMAIN_SOFTWARE_API
    assert set(plan.preferred_source_types) == {"official_documentation", "api_reference"}


def test_repository_capability_prefers_local_repository_and_project_documentation():
    plan = lsp.create_plan_from_task(_task(
        lt.Status.APPROVED,
        requested_task="understand Mark's existing architecture and repository module layout",
        missing_capability="architecture repository module codebase",
    ))
    assert plan.domain == lsp.DOMAIN_SOFTWARE_REPOSITORY
    assert set(plan.preferred_source_types) == {"local_repository", "project_documentation"}
    assert plan.required_authority == lsp.AUTHORITY_LOCAL_PROJECT


def test_unknown_domain_remains_unknown_for_empty_task():
    plan = lsp.create_plan_from_task(_task(
        lt.Status.APPROVED, requested_task="", missing_capability="",
    ))
    assert plan.domain == lsp.DOMAIN_UNKNOWN


def test_unknown_domain_remains_unknown_for_tied_indicators():
    classification = lsp.classify_domain("api integration legal regulatory compliance")
    # api(strong)+integration(weak) = score 3; legal(weak)+regulatory(strong)+compliance(strong) = score 5
    # Use a genuinely tied construction instead:
    tied = lsp.classify_domain("rera regulatory")  # one strong hit each in two different domains -> tie at score 2
    assert tied.domain == lsp.DOMAIN_UNKNOWN


def test_ambiguous_terms_do_not_falsely_classify_high_stakes_domain():
    """A single incidental weak term ('license') must not be enough on its
    own to falsely classify an ordinary software question as legal_regulatory."""
    plan = lsp.create_plan_from_task(_task(
        lt.Status.APPROVED,
        requested_task="check the license terms of this software library",
        missing_capability="license software library terms",
    ))
    assert plan.domain != lsp.DOMAIN_LEGAL_REGULATORY
    assert plan.domain == lsp.DOMAIN_GENERAL_KNOWLEDGE


def test_general_knowledge_for_ordinary_non_technical_task():
    plan = lsp.create_plan_from_task(_task(
        lt.Status.APPROVED,
        requested_task="learn how to bake sourdough bread",
        missing_capability="bake sourdough bread",
    ))
    assert plan.domain == lsp.DOMAIN_GENERAL_KNOWLEDGE
    assert plan.required_authority == lsp.AUTHORITY_SUPPLEMENTARY


def test_hardware_device_domain_classification():
    plan = lsp.create_plan_from_task(_task(
        lt.Status.APPROVED,
        requested_task="read a firmware datasheet for an embedded GPIO controller",
        missing_capability="firmware gpio embedded microcontroller",
    ))
    assert plan.domain == lsp.DOMAIN_HARDWARE_DEVICE


# ---------------------------------------------------------------------------
# Fixed vocabulary enforcement
# ---------------------------------------------------------------------------

def test_fixed_source_vocabulary_is_enforced_across_all_domains():
    for domain in lsp.DOMAIN_CLASSES:
        policy = lsp._DOMAIN_POLICY[domain]
        for key in ("source_categories", "preferred_source_types", "disallowed_source_types"):
            for value in policy[key]:
                assert value in lsp.SOURCE_CATEGORIES, f"{domain}.{key} contains non-vocabulary value {value!r}"


def test_fixed_authority_vocabulary_is_enforced_across_all_domains():
    for domain in lsp.DOMAIN_CLASSES:
        policy = lsp._DOMAIN_POLICY[domain]
        assert policy["required_authority"] in lsp.AUTHORITY_LEVELS


def test_high_stakes_domains_require_authoritative_or_primary():
    for domain in lsp.HIGH_STAKES_DOMAINS:
        policy = lsp._DOMAIN_POLICY[domain]
        assert policy["required_authority"] in (lsp.AUTHORITY_AUTHORITATIVE, lsp.AUTHORITY_PRIMARY)


def test_bounded_categories_drops_out_of_vocabulary_values():
    filtered = lsp._bounded_categories(["official_documentation", "totally_made_up_category", "government_source"])
    assert filtered == ["official_documentation", "government_source"]


def test_bounded_categories_respects_max_categories_cap():
    many = list(lsp.SOURCE_CATEGORIES) * 3
    filtered = lsp._bounded_categories(many)
    assert len(filtered) <= lsp.MAX_CATEGORIES


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_duplicate_planning_preserves_plan_id():
    task = _task(lt.Status.APPROVED)
    first = lsp.create_plan_from_task(task)
    second = lsp.create_plan_from_task(task)
    assert first.plan_id == second.plan_id
    assert len(lsp.list_plans()) == 1


def test_repeated_planning_refreshes_updated_at():
    task = _task(lt.Status.APPROVED)
    first = lsp.create_plan_from_task(task)
    second = lsp.create_plan_from_task(task)
    assert second.updated_at >= first.updated_at


def test_different_task_ids_create_separate_plans():
    lsp.create_plan_from_task(_task(lt.Status.APPROVED, task_id="t1"))
    other = lsp.create_plan_from_task(_task(lt.Status.APPROVED, task_id="t2"))
    assert other is not None
    assert len(lsp.list_plans()) == 2


def test_find_by_task_locates_existing_plan():
    task = _task(lt.Status.APPROVED)
    created = lsp.create_plan_from_task(task)
    found = lsp.find_by_task(task.task_id)
    assert found is not None
    assert found.plan_id == created.plan_id


# ---------------------------------------------------------------------------
# Bounded fields
# ---------------------------------------------------------------------------

def test_bounded_fields():
    huge = "property government " * 2000
    task = _task(lt.Status.APPROVED, requested_task=huge, missing_capability=huge)
    plan = lsp.create_plan_from_task(task)
    assert plan is not None
    assert len(plan.missing_capability) <= lsp.MAX_CAPABILITY_CHARS
    assert len(plan.rationale) <= lsp.MAX_RATIONALE_CHARS
    assert len(plan.source_categories) <= lsp.MAX_CATEGORIES
    assert len(plan.preferred_source_types) <= lsp.MAX_CATEGORIES
    assert len(plan.disallowed_source_types) <= lsp.MAX_CATEGORIES


def test_bounded_retention_prunes_oldest_updated_first(monkeypatch):
    monkeypatch.setattr(lsp, "MAX_PLANS", 5)
    for i in range(20):
        lsp.create_plan_from_task(_task(
            lt.Status.APPROVED, task_id=f"task-{i}",
            requested_task=f"unique task about widget number {i}",
            missing_capability=f"widget task {i}",
        ))
    assert len(lsp.list_plans()) <= 5


# ---------------------------------------------------------------------------
# Persistence: atomic writes, corruption/failure fail-safety
# ---------------------------------------------------------------------------

def test_no_state_file_means_no_plans(isolated_state_file):
    assert not isolated_state_file.exists()
    assert lsp.list_plans() == []


def test_corrupt_state_file_fails_safe(isolated_state_file):
    isolated_state_file.parent.mkdir(parents=True, exist_ok=True)
    isolated_state_file.write_text("{not valid json at all", encoding="utf-8")
    assert lsp._load_all() == []
    plan = lsp.create_plan_from_task(_task(lt.Status.APPROVED))
    assert plan is not None


def test_corrupt_plan_entries_are_skipped_not_fatal(isolated_state_file):
    isolated_state_file.parent.mkdir(parents=True, exist_ok=True)
    isolated_state_file.write_text(json.dumps({"plans": [{"garbage": 1}, "not a dict"]}), encoding="utf-8")
    assert lsp._load_all() == []


def test_atomic_write_uses_temp_file_then_replace(isolated_state_file, monkeypatch):
    calls = []
    real_replace = os.replace

    def spy(src, dst):
        calls.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    lsp.create_plan_from_task(_task(lt.Status.APPROVED))

    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == isolated_state_file
    assert src != str(isolated_state_file)


def test_interrupted_write_preserves_previous_state(isolated_state_file, monkeypatch):
    lsp.create_plan_from_task(_task(lt.Status.APPROVED, task_id="t1"))
    before = isolated_state_file.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("simulated crash")
    monkeypatch.setattr(json, "dump", boom)

    lsp.create_plan_from_task(_task(lt.Status.APPROVED, task_id="t2"))  # must not raise

    after = isolated_state_file.read_text(encoding="utf-8")
    assert after == before
    assert list(isolated_state_file.parent.glob(".learning_source_plan_*")) == []


# ---------------------------------------------------------------------------
# Never learn(), never AI provider, never web search/network/shell/exec,
# never installs anything, never mutates the LearningTask's status.
# ---------------------------------------------------------------------------

def test_module_source_has_no_research_or_execution_path():
    code = _code_without_strings_and_comments(inspect.getsource(lsp))
    forbidden_substrings = (
        "learning_engine", ".learn(", "ai_provider", "complete_with_failover",
        "web_search", "subprocess", "os.system", "importlib",
        "requests.get", "requests.post", "urllib", "webbrowser", "socket",
    )
    for forbidden in forbidden_substrings:
        assert forbidden not in code, f"unexpected path in learning_source_planner.py: {forbidden}"


def test_module_source_has_no_code_generation_or_install_path():
    code = _code_without_strings_and_comments(inspect.getsource(lsp))
    for forbidden in ("exec(", "eval(", "install", "pip.main"):
        assert forbidden not in code, f"unexpected install/codegen path in learning_source_planner.py: {forbidden}"


def test_module_source_never_calls_update_status():
    code = _code_without_strings_and_comments(inspect.getsource(lsp))
    assert "update_status(" not in code


def test_create_plan_from_task_does_not_import_actions_or_network_modules():
    import sys
    before = {m for m in sys.modules if m.startswith("actions.")}
    lsp.create_plan_from_task(_task(lt.Status.APPROVED))
    after = {m for m in sys.modules if m.startswith("actions.")}
    assert after == before


def test_learning_task_status_never_changed_automatically(tmp_path, monkeypatch):
    """Exercise the REAL core/learning_task.py persistence path (not just a
    detached dataclass instance) to prove planning never mutates the
    underlying stored task's status."""
    monkeypatch.setattr(lt, "STATE_FILE", tmp_path / "learning_tasks.json")

    import core.capability_gap as cg
    gap = cg.GapResult(
        requested_task="analyze this property, check RERA and land registry",
        required_capability="rera property government registry",
        matched_capabilities=[], missing_capability=True, gap_detected=True,
        confidence=cg.CONFIDENCE_NONE, evidence="no match", background_knowledge=[],
    )
    created = lt.create_from_gap(gap)
    lt.update_status(created.task_id, lt.Status.APPROVED)
    approved_task = lt.get_task(created.task_id)
    assert approved_task.status == lt.Status.APPROVED

    lsp.create_plan_from_task(approved_task)

    unchanged = lt.get_task(created.task_id)
    assert unchanged.status == lt.Status.APPROVED


def test_gap_result_confidence_none_end_to_end_produces_plan(tmp_path, monkeypatch):
    """Full pipeline sanity check: capability_gap -> learning_task -> planner,
    without any of the intermediate modules performing research/execution."""
    monkeypatch.setattr(lt, "STATE_FILE", tmp_path / "learning_tasks.json")
    import core.capability_gap as cg

    gap = cg.detect_gap("walk my dog around the neighborhood park", consult_knowledge=False)
    assert gap.confidence == cg.CONFIDENCE_NONE

    task = lt.create_from_gap(gap)
    assert task is not None
    lt.update_status(task.task_id, lt.Status.APPROVED)
    approved = lt.get_task(task.task_id)

    plan = lsp.create_plan_from_task(approved)
    assert plan is not None
    assert plan.learning_task_id == task.task_id
