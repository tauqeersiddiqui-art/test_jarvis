import inspect

import core.ai_provider as aip
from actions import investigate as inv


class _FakeProvider:
    def __init__(self, text="ok"):
        self.text = text
        self.calls = []

    def complete(self, prompt, model=None):
        self.calls.append(prompt)
        return aip.AIResponse(text=self.text)


def test_extract_keywords_prefers_identifiers():
    kws = inv._extract_keywords("find where helper_func is used")
    assert "helper_func" in kws


def test_extract_keywords_captures_quoted_phrases():
    kws = inv._extract_keywords('search for "GEMINI_API_KEY" handling')
    assert "GEMINI_API_KEY" in kws


def test_gather_evidence_returns_grounded_items(project):
    evidence, notes = inv._gather_evidence(project, "find where helper_func is used")
    assert evidence
    for e in evidence:
        assert e["file"]
        assert isinstance(e["content"], str)


def test_gather_evidence_never_includes_secret_content(project):
    evidence, _ = inv._gather_evidence(project, "find the GEMINI_API_KEY value sk-real-secret-value")
    for e in evidence:
        assert "sk-real-secret-value" not in e["content"]


def test_gather_evidence_empty_for_nonexistent_symbol(project):
    evidence, notes = inv._gather_evidence(project, "find zzz_totally_nonexistent_symbol_9999")
    assert evidence == []


def test_bounded_context_respects_char_budget():
    evidence = [
        {"file": f"f{i}.py", "line": i, "kind": "literal", "content": "x" * 500}
        for i in range(50)
    ]
    ctx = inv._assemble_bounded_context(evidence, max_chars=2000)
    assert len(ctx) <= 2000 + 200  # small slack for the final block's own formatting


def test_bounded_context_prioritises_earlier_higher_ranked_evidence():
    evidence = [{"file": "important.py", "line": 1, "kind": "literal", "content": "IMPORTANT"}] + [
        {"file": f"f{i}.py", "line": i, "kind": "literal", "content": "x" * 800} for i in range(20)
    ]
    ctx = inv._assemble_bounded_context(evidence, max_chars=1000)
    assert "important.py" in ctx


def test_investigate_sends_bounded_evidence_not_whole_repo(project, monkeypatch):
    monkeypatch.setattr(inv.ws, "get_workspace", lambda: project)
    fake = _FakeProvider(text="helper_func is defined in utils/helper.py:1 and called from main.py:5.")
    monkeypatch.setattr(aip, "get_provider", lambda: fake)

    result = inv.investigate({"question": "find where helper_func is used"})

    assert len(fake.calls) == 1
    prompt_sent = fake.calls[0]
    # The prompt must be small and bounded, nowhere near "the whole repo" —
    # a real whole-repo dump of even this tiny fixture project plus a real
    # codebase would be drastically larger than this bound.
    assert len(prompt_sent) < inv.MAX_EVIDENCE_CHARS + 3000
    assert "helper_func" in result
    assert "Evidence used" in result


def test_investigate_skips_llm_call_when_no_evidence(project, monkeypatch):
    monkeypatch.setattr(inv.ws, "get_workspace", lambda: project)
    fake = _FakeProvider(text="should never be returned")
    monkeypatch.setattr(aip, "get_provider", lambda: fake)

    result = inv.investigate({"question": "zzz_totally_nonexistent_symbol_9999"})

    assert fake.calls == []
    assert "No matching evidence" in result


def test_investigate_requires_a_question():
    result = inv.investigate({"question": ""})
    assert "provide a question" in result.lower()


def test_investigate_falls_back_gracefully_when_provider_unavailable(project, monkeypatch):
    monkeypatch.setattr(inv.ws, "get_workspace", lambda: project)

    def _raise():
        raise aip.ProviderConfigError("no credentials configured")
    monkeypatch.setattr(aip, "get_provider", _raise)

    result = inv.investigate({"question": "find where helper_func is used"})
    assert "AIProvider unavailable" in result
    assert "utils/helper.py" in result  # raw evidence still returned


def test_no_write_capable_calls_in_module_source():
    src = inspect.getsource(inv)
    for forbidden in ("write_text(", "write_bytes(", "unlink(", "os.remove", "shutil.rmtree", "shutil.move"):
        assert forbidden not in src, f"unexpected write-capable call in investigate.py: {forbidden}"


def test_investigate_does_not_modify_files(project, monkeypatch):
    monkeypatch.setattr(inv.ws, "get_workspace", lambda: project)
    fake = _FakeProvider(text="ok")
    monkeypatch.setattr(aip, "get_provider", lambda: fake)

    before = {p: p.stat().st_mtime_ns for p in project.rglob("*") if p.is_file()}
    inv.investigate({"question": "find where helper_func is used"})
    after = {p: p.stat().st_mtime_ns for p in project.rglob("*") if p.is_file()}
    assert before == after
