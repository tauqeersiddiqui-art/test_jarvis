import json
import threading
import time
from pathlib import Path

import pytest

import core.ai_provider as aip
import core.coding_task as ct
import core.engineering_memory as em
import core.workspace as ws
from actions import dev_agent


def test_compute_waves_orders_independent_files_before_dependents():
    files = [
        {"path": "main.py", "imports": ["utils.helpers", "core.engine"]},
        {"path": "utils/helpers.py", "imports": []},
        {"path": "core/engine.py", "imports": ["utils.helpers"]},
    ]
    waves = dev_agent._compute_waves(files)
    by_wave = [sorted(f["path"] for f in w) for w in waves]

    assert by_wave[0] == ["utils/helpers.py"]
    engine_wave = next(i for i, w in enumerate(by_wave) if "core/engine.py" in w)
    main_wave = next(i for i, w in enumerate(by_wave) if "main.py" in w)
    assert engine_wave > 0
    assert main_wave > engine_wave


def test_get_model_does_not_force_gemini_model_on_other_providers(monkeypatch):
    """Regression test: previously _get_model always passed
    model="gemini-2.5-flash" to provider.complete(), even when the active
    provider was an OpenAI-compatible gateway configured via LLM_MODEL —
    silently sending the wrong model name to that gateway."""
    calls = []

    class FakeOpenAICompatibleProvider:
        provider_id = "openai_compatible"

        def complete(self, contents, **kwargs):
            calls.append(kwargs)
            class R:
                text = "ok"
            return R()

    monkeypatch.setattr("core.ai_provider.build_failover_chain", lambda: [FakeOpenAICompatibleProvider()])

    model = dev_agent._get_model("gemini-2.5-flash")
    model.generate_content("hi")

    assert calls == [{}]


def test_get_model_forces_model_for_gemini_provider(monkeypatch):
    calls = []

    class FakeGeminiProvider:
        provider_id = "gemini"

        def complete(self, contents, **kwargs):
            calls.append(kwargs)
            class R:
                text = "ok"
            return R()

    monkeypatch.setattr("core.ai_provider.build_failover_chain", lambda: [FakeGeminiProvider()])

    model = dev_agent._get_model("gemini-2.5-flash")
    model.generate_content("hi")

    assert calls == [{"model": "gemini-2.5-flash"}]


def test_is_rate_limit_detects_402_and_capacity_errors():
    assert dev_agent._is_rate_limit(Exception("HTTP 429 Too Many Requests"))
    assert dev_agent._is_rate_limit(Exception("402 Payment Required"))
    assert dev_agent._is_rate_limit(Exception("provider capacity exceeded"))
    assert not dev_agent._is_rate_limit(Exception("SyntaxError: invalid syntax"))


def test_build_project_degrades_to_sequential_after_rate_limit(monkeypatch, tmp_path):
    """Bounded (max_workers=2) concurrent writes within a wave; once a
    rate-limit/capacity error is seen, remaining waves must fall back to
    sequential writes instead of continuing to fan out — no retry storm."""

    monkeypatch.setattr(dev_agent, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(dev_agent, "WRITE_CONCURRENCY", 2)

    # Keep short pacing sleeps real (so concurrency is actually observable)
    # but skip the long 20s rate-limit backoff so the test stays fast.
    real_sleep = time.sleep
    def fast_sleep(seconds):
        if seconds >= 1:
            return
        real_sleep(seconds)
    monkeypatch.setattr(time, "sleep", fast_sleep)

    plan = {
        "project_name": "demo",
        "entry_point": "main.py",
        "files": [
            {"path": "a.py", "description": "", "imports": []},
            {"path": "b.py", "description": "", "imports": []},
            {"path": "c.py", "description": "", "imports": ["a"]},
            {"path": "d.py", "description": "", "imports": ["a"]},
        ],
        "run_command": "python main.py",
        "dependencies": [],
    }
    monkeypatch.setattr(dev_agent, "_plan_project", lambda description, language: plan)
    monkeypatch.setattr(dev_agent, "_install_dependencies", lambda deps, project_dir: "no deps")
    monkeypatch.setattr(dev_agent, "_open_vscode", lambda project_dir: True)
    monkeypatch.setattr(dev_agent, "_run_project", lambda *a, **k: "Ran with no output.")

    wave0_files = {"a.py", "b.py"}
    wave1_files = {"c.py", "d.py"}
    active = {"n": 0}
    lock = threading.Lock()
    concurrency_seen = {"wave0_max": 0, "wave1_max": 0}
    dependency_ok = {"value": True}

    def fake_write_file(file_info, project_description, all_files, language, project_dir, already_written):
        path = file_info["path"]
        if path in wave1_files and "a.py" not in already_written:
            dependency_ok["value"] = False

        with lock:
            active["n"] += 1
            bucket = "wave0_max" if path in wave0_files else "wave1_max"
            concurrency_seen[bucket] = max(concurrency_seen[bucket], active["n"])
        time.sleep(0.08)
        with lock:
            active["n"] -= 1

        if path == "b.py":
            raise dev_agent.RateLimitError("429 too many requests")
        return f"# {path}"

    monkeypatch.setattr(dev_agent, "_write_file", fake_write_file)

    speak_messages = []
    result = dev_agent._build_project(
        description="demo project",
        language="python",
        project_name="demo",
        timeout=5,
        speak=lambda msg: speak_messages.append(msg),
        player=None,
    )

    assert dependency_ok["value"], "wave 1 files must see wave 0's completed code"
    assert concurrency_seen["wave0_max"] >= 2, "independent files should write concurrently before any rate limit"
    assert concurrency_seen["wave1_max"] == 1, "must degrade to sequential writes after a rate-limit error"
    assert "demo" in result
    assert any("building your project now" in m.lower() for m in speak_messages), \
        "must speak an immediate acknowledgment before the (potentially long) build starts"


def test_build_project_reports_specific_failure_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(dev_agent, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    plan = {
        "project_name": "brokenproj",
        "entry_point": "main.py",
        "files": [{"path": "main.py", "description": "", "imports": []}],
        "run_command": "python main.py",
        "dependencies": [],
    }
    monkeypatch.setattr(dev_agent, "_plan_project", lambda description, language: plan)

    def always_fail(*a, **k):
        raise RuntimeError("model refused: content policy violation")
    monkeypatch.setattr(dev_agent, "_write_file", always_fail)

    result = dev_agent._build_project(
        description="demo", language="python", project_name="brokenproj",
        timeout=5, speak=None, player=None,
    )

    assert "could not write any project files" in result.lower()
    assert "content policy violation" in result


# ---------------------------------------------------------------------------
# Evidence-driven fixing (investigate.py primitives reused, bounded to the
# generated project directory only).
# ---------------------------------------------------------------------------

def test_evidence_query_from_error_extracts_symbol_and_file_stem():
    error = (
        "Traceback (most recent call last):\n"
        '  File "main.py", line 5, in <module>\n'
        "    helper_func()\n"
        "NameError: name 'helper_func' is not defined"
    )
    query = dev_agent._evidence_query_from_error(error, "main.py")
    assert "helper_func" in query
    assert "main" in query


def test_gather_project_evidence_finds_real_definition_and_excludes_unrelated_file(tmp_path):
    (tmp_path / "utils.py").write_text("def helper_func():\n    return 42\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from utils import helper_func\n\nhelper_func()\n", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text(
        "def totally_unrelated_thing_zzz():\n    pass\n", encoding="utf-8"
    )

    error = 'File "main.py", line 3, in <module>\nNameError: name \'helper_func\' is not defined'
    evidence, context = dev_agent._gather_project_evidence(tmp_path, error, "main.py")

    files_in_evidence = {e["file"] for e in evidence}
    assert "utils.py" in files_in_evidence          # the actual definition site is pulled in
    assert "unrelated.py" not in files_in_evidence  # unrelated file excluded from bounded evidence
    assert "helper_func" in context
    assert "totally_unrelated_thing_zzz" not in context


def test_gather_project_evidence_is_bounded_to_the_generated_project_only(tmp_path):
    """complete_with_failover is a REAL symbol defined in Mark's own
    core/ai_provider.py. If evidence gathering ever leaked into Mark-XLVIII's
    own repo instead of staying bounded to the generated project directory,
    it would incorrectly resolve this reference there. It must not."""
    (tmp_path / "app.py").write_text(
        "from core.ai_provider import complete_with_failover\ncomplete_with_failover('x')\n",
        encoding="utf-8",
    )
    error = "NameError: name 'complete_with_failover' is not defined"
    evidence, _context = dev_agent._gather_project_evidence(tmp_path, error, "app.py")

    seen_files = {e["file"] for e in evidence}
    assert seen_files <= {"app.py"}  # only files that exist inside tmp_path, nothing from Mark's repo
    assert not any("ai_provider" in f for f in seen_files)


def test_gather_project_evidence_does_not_mutate_active_workspace(tmp_path, monkeypatch):
    import json as _json

    fake_base = tmp_path / "mark_repo_copy"
    (fake_base / "config").mkdir(parents=True)
    workspace_file = fake_base / "config" / "workspace.json"
    workspace_file.write_text(_json.dumps({"path": str(fake_base)}), encoding="utf-8")
    monkeypatch.setattr(ws, "_base_dir", lambda: fake_base)

    before_content = workspace_file.read_text(encoding="utf-8")
    before_mtime = workspace_file.stat().st_mtime_ns
    before_active = ws.get_workspace()

    project_dir = tmp_path / "generated_project"
    project_dir.mkdir()
    (project_dir / "main.py").write_text("undefined_symbol_xyz()\n", encoding="utf-8")

    dev_agent._gather_project_evidence(
        project_dir, "NameError: name 'undefined_symbol_xyz' is not defined", "main.py"
    )

    assert workspace_file.read_text(encoding="utf-8") == before_content
    assert workspace_file.stat().st_mtime_ns == before_mtime
    assert ws.get_workspace() == before_active


def test_fix_files_injects_evidence_context_into_prompt(monkeypatch, tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "utils.py").write_text("def helper_func():\n    return 42\n", encoding="utf-8")
    (project_dir / "main.py").write_text("from utils import helper_func\n\nhelper_func()\n", encoding="utf-8")

    file_codes = {
        "utils.py": (project_dir / "utils.py").read_text(encoding="utf-8"),
        "main.py": (project_dir / "main.py").read_text(encoding="utf-8"),
    }
    all_files = [
        {"path": "utils.py", "description": "", "imports": []},
        {"path": "main.py", "description": "", "imports": ["utils"]},
    ]

    captured_prompts = []

    class FakeModel:
        def generate_content(self, prompt):
            captured_prompts.append(prompt)
            class R:
                text = "def helper_func():\n    return 42\n"
            return R()

    monkeypatch.setattr(dev_agent, "_get_model", lambda model_name: FakeModel())

    error = 'File "main.py", line 3, in <module>\nNameError: name \'helper_func\' is not defined'
    files_to_fix, evidence_context = dev_agent._plan_fix_targets(
        error, file_codes, all_files, "main.py", project_dir
    )
    dev_agent._fix_files(
        error_output=error,
        project_description="demo",
        all_files=all_files,
        file_codes=file_codes,
        language="python",
        project_dir=project_dir,
        entry_point="main.py",
        files_to_fix=files_to_fix,
        evidence_context=evidence_context,
    )

    assert captured_prompts, "fix generation must be invoked"
    prompt = captured_prompts[0]
    assert "Evidence gathered from this project" in prompt
    assert "helper_func" in prompt
    assert "utils.py" in prompt


def test_fix_files_falls_back_to_traceback_context_when_no_evidence(monkeypatch, tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "main.py").write_text("raise ValueError('boom')\n", encoding="utf-8")

    file_codes = {"main.py": (project_dir / "main.py").read_text(encoding="utf-8")}
    all_files = [{"path": "main.py", "description": "", "imports": []}]

    captured_prompts = []

    class FakeModel:
        def generate_content(self, prompt):
            captured_prompts.append(prompt)
            class R:
                text = "raise ValueError('fixed')\n"
            return R()

    monkeypatch.setattr(dev_agent, "_get_model", lambda model_name: FakeModel())

    error = 'File "main.py", line 1, in <module>\nValueError: boom'
    # Explicit empty evidence_context, to deterministically exercise the fallback branch.
    dev_agent._fix_files(
        error_output=error, project_description="demo", all_files=all_files,
        file_codes=file_codes, language="python", project_dir=project_dir, entry_point="main.py",
        files_to_fix=["main.py"], evidence_context="",
    )

    prompt = captured_prompts[0]
    assert "Other files for context (read-only" in prompt
    assert "Evidence gathered from this project" not in prompt


def test_fix_files_still_uses_provider_failover(monkeypatch, tmp_path):
    """The evidence-driven prompt change must not bypass the provider
    router: gateway-primary, Gemini-fallback failover must still work
    inside _fix_files exactly as everywhere else in dev_agent."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "main.py").write_text("raise ValueError('boom')\n", encoding="utf-8")
    file_codes = {"main.py": (project_dir / "main.py").read_text(encoding="utf-8")}
    all_files = [{"path": "main.py", "description": "", "imports": []}]

    class FakeGateway:
        provider_id = "openai_compatible"
        def __init__(self):
            self.calls = 0
        def complete(self, prompt, **kwargs):
            self.calls += 1
            raise Exception("429 rate limit")

    class FakeGemini:
        provider_id = "gemini"
        def __init__(self):
            self.calls = 0
        def complete(self, prompt, **kwargs):
            self.calls += 1
            class R:
                text = "raise ValueError('fixed')\n"
            return R()

    gateway = FakeGateway()
    gemini = FakeGemini()
    monkeypatch.setattr(aip, "build_failover_chain", lambda: [gateway, gemini])

    error = 'File "main.py", line 1, in <module>\nValueError: boom'
    result = dev_agent._fix_files(
        error_output=error, project_description="demo", all_files=all_files,
        file_codes=file_codes, language="python", project_dir=project_dir, entry_point="main.py",
        files_to_fix=["main.py"], evidence_context="",
    )

    assert gateway.calls == 1
    assert gemini.calls == 1
    assert result["main.py"] == "raise ValueError('fixed')"


# ---------------------------------------------------------------------------
# Auto-rollback: snapshot/restore mechanics (pure, no _build_project needed).
# ---------------------------------------------------------------------------

def test_snapshot_and_rollback_restores_exact_bytes(tmp_path):
    f = tmp_path / "main.py"
    original = b"raise ValueError('boom')\r\n\xc3\xa9"  # exact bytes, incl. a non-ASCII byte
    f.write_bytes(original)

    snapshot = dev_agent._snapshot_files(tmp_path, ["main.py"])
    f.write_bytes(b"something else entirely")

    dev_agent._rollback_snapshot(tmp_path, snapshot)

    assert f.read_bytes() == original


def test_rollback_deletes_newly_created_file(tmp_path):
    snapshot = dev_agent._snapshot_files(tmp_path, ["new_file.py"])  # doesn't exist yet
    assert snapshot["new_file.py"] is None

    created = tmp_path / "new_file.py"
    created.write_text("def x(): pass\n", encoding="utf-8")
    assert created.exists()

    dev_agent._rollback_snapshot(tmp_path, snapshot)

    assert not created.exists()


def test_rollback_restores_deleted_file(tmp_path):
    f = tmp_path / "utils.py"
    original = b"def helper():\n    return 1\n"
    f.write_bytes(original)

    snapshot = dev_agent._snapshot_files(tmp_path, ["utils.py"])
    f.unlink()
    assert not f.exists()

    dev_agent._rollback_snapshot(tmp_path, snapshot)

    assert f.read_bytes() == original


def test_snapshot_and_rollback_never_touch_files_outside_project_dir(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    outside_file = tmp_path / "outside.py"
    outside_file.write_text("SENTINEL — must never change\n", encoding="utf-8")

    # A path-traversal-shaped target trying to escape project_dir.
    escaping_rel = "../outside.py"
    snapshot = dev_agent._snapshot_files(project_dir, [escaping_rel])
    assert escaping_rel not in snapshot  # skipped entirely, never touched

    # Even if somehow present, rollback must not touch it either.
    dev_agent._rollback_snapshot(project_dir, {escaping_rel: b"malicious content"})
    assert outside_file.read_text(encoding="utf-8") == "SENTINEL — must never change\n"


def test_rollback_keeps_file_codes_in_sync(tmp_path):
    # write_bytes (not write_text) — avoids platform newline translation so
    # the exact-bytes-round-trip assertion below is OS-independent.
    (tmp_path / "kept.py").write_bytes(b"original kept content\n")
    snapshot = dev_agent._snapshot_files(tmp_path, ["kept.py", "new.py"])

    (tmp_path / "kept.py").write_bytes(b"modified content\n")
    (tmp_path / "new.py").write_bytes(b"newly created\n")

    file_codes = {"kept.py": "modified content\n", "new.py": "newly created\n"}
    dev_agent._rollback_snapshot(tmp_path, snapshot, file_codes)

    assert file_codes["kept.py"] == "original kept content\n"
    assert "new.py" not in file_codes


# ---------------------------------------------------------------------------
# Auto-rollback: deterministic error-progress comparison (no LLM judgement).
# ---------------------------------------------------------------------------

_RUN_CMD = "python main.py"


def test_compare_error_progress_success():
    pre = 'File "main.py", line 1, in <module>\nValueError: boom'
    post = "42\n"  # no error at all
    assert dev_agent._compare_error_progress(pre, post, _RUN_CMD) == "success"


def test_compare_error_progress_identical_error_is_unchanged():
    err = (
        'Traceback (most recent call last):\n'
        '  File "main.py", line 3, in <module>\n'
        "    helper_func()\n"
        "NameError: name 'helper_func' is not defined"
    )
    assert dev_agent._compare_error_progress(err, err, _RUN_CMD) == "unchanged"


def test_compare_error_progress_deeper_traceback_is_improved():
    pre = (
        'Traceback (most recent call last):\n'
        '  File "main.py", line 3, in <module>\n'
        "NameError: name 'helper_func' is not defined"
    )
    post = (
        'Traceback (most recent call last):\n'
        '  File "main.py", line 5, in <module>\n'
        '  File "utils.py", line 10, in helper_func\n'
        "TypeError: unsupported operand type(s)"
    )
    assert dev_agent._compare_error_progress(pre, post, _RUN_CMD) == "improved"


def test_compare_error_progress_shallower_traceback_is_worse():
    pre = (
        'Traceback (most recent call last):\n'
        '  File "main.py", line 5, in <module>\n'
        '  File "utils.py", line 10, in helper_func\n'
        "TypeError: unsupported operand type(s)"
    )
    post = (
        'Traceback (most recent call last):\n'
        '  File "main.py", line 1, in <module>\n'
        "SyntaxError: invalid syntax"
    )
    assert dev_agent._compare_error_progress(pre, post, _RUN_CMD) == "worse"


def test_compare_error_progress_same_depth_different_error_is_conservative_unchanged():
    """Same traceback depth but a different exception — improvement can't be
    clearly established, so the conservative default is to treat it as
    unchanged (the caller then rolls back)."""
    pre = (
        'Traceback (most recent call last):\n'
        '  File "main.py", line 3, in <module>\n'
        "NameError: name 'helper_func' is not defined"
    )
    post = (
        'Traceback (most recent call last):\n'
        '  File "main.py", line 3, in <module>\n'
        "TypeError: something else entirely"
    )
    assert dev_agent._compare_error_progress(pre, post, _RUN_CMD) == "unchanged"


# ---------------------------------------------------------------------------
# Auto-rollback: full integration through _build_project's run -> fix loop.
# ---------------------------------------------------------------------------

def _rig_build_project(monkeypatch, tmp_path, initial_files, run_outputs, fix_targets, fix_writer):
    """Shared harness: real _build_project, real disk I/O for the generated
    project, but with planning/writing/run/fix-targeting mocked so the test
    controls the exact scripted sequence of run outputs and fix writes.

    initial_files: dict[relpath] -> initial file content (written for real).
    run_outputs:   list of strings returned by successive _run_project calls.
    fix_targets:   list of relpaths _plan_fix_targets should report per attempt.
    fix_writer:    callable(call_index, files_to_fix, project_dir) -> dict
                   of {relpath: new_content}; a path omitted from the
                   returned dict simulates that file failing to write.
    """
    monkeypatch.setattr(dev_agent, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    entry = next(iter(initial_files))
    plan = {
        "project_name": "rollbackproj",
        "entry_point": entry,
        "files": [{"path": p, "description": "", "imports": []} for p in initial_files],
        "run_command": f"python {entry}",
        "dependencies": [],
    }
    monkeypatch.setattr(dev_agent, "_plan_project", lambda description, language: plan)
    monkeypatch.setattr(dev_agent, "_install_dependencies", lambda deps, project_dir: "no deps")
    monkeypatch.setattr(dev_agent, "_open_vscode", lambda project_dir: True)

    def fake_write_file(file_info, project_description, all_files, language, project_dir, already_written):
        path = file_info["path"]
        content = initial_files[path]
        full = project_dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return content
    monkeypatch.setattr(dev_agent, "_write_file", fake_write_file)

    outputs = list(run_outputs)
    def fake_run_project(run_command, project_dir, timeout=30):
        return outputs.pop(0) if outputs else "Ran with no output."
    monkeypatch.setattr(dev_agent, "_run_project", fake_run_project)

    monkeypatch.setattr(
        dev_agent, "_plan_fix_targets",
        lambda error_output, file_codes, all_files, entry_point, project_dir: (list(fix_targets), ""),
    )

    call_index = {"n": 0}
    def fake_fix_files(**kwargs):
        call_index["n"] += 1
        project_dir = kwargs["project_dir"]
        writes = fix_writer(call_index["n"], kwargs["files_to_fix"], project_dir)
        updated = {}
        for path, content in writes.items():
            full = project_dir / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            updated[path] = content
        return updated
    monkeypatch.setattr(dev_agent, "_fix_files", fake_fix_files)

    result = dev_agent._build_project(
        description="demo", language="python", project_name="rollbackproj",
        timeout=5, speak=None, player=None,
    )
    return result, tmp_path / "rollbackproj"


def test_build_project_keeps_successful_fix(monkeypatch, tmp_path):
    result, project_dir = _rig_build_project(
        monkeypatch, tmp_path,
        initial_files={"main.py": "raise ValueError('boom')\n"},
        run_outputs=[
            'File "main.py", line 1, in <module>\nValueError: boom',  # initial run: fails
            "42\n",  # post-fix run: success
        ],
        fix_targets=["main.py"],
        fix_writer=lambda n, targets, project_dir: {"main.py": "print(42)\n"},
    )

    assert "working" in result.lower()
    assert (project_dir / "main.py").read_text(encoding="utf-8") == "print(42)\n"


def test_build_project_keeps_improved_error_and_continues(monkeypatch, tmp_path):
    result, project_dir = _rig_build_project(
        monkeypatch, tmp_path,
        initial_files={"main.py": "raise ValueError('boom')\n"},
        run_outputs=[
            'File "main.py", line 1, in <module>\nValueError: boom',
            # attempt 1's fix: still errors, but deeper traceback -> improved, kept
            'File "main.py", line 5, in <module>\n  File "main.py", line 1, in <module>\nTypeError: deeper',
            # attempt 2's fix: success
            "done\n",
        ],
        fix_targets=["main.py"],
        fix_writer=lambda n, targets, project_dir: {"main.py": f"attempt {n} fix\n"},
    )

    assert "working" in result.lower()
    # both attempts' writes were kept (no rollback) — final content is attempt 2's
    assert (project_dir / "main.py").read_text(encoding="utf-8") == "attempt 2 fix\n"


def test_build_project_rolls_back_on_unchanged_error(monkeypatch, tmp_path):
    same_error = 'File "main.py", line 1, in <module>\nValueError: boom'
    result, project_dir = _rig_build_project(
        monkeypatch, tmp_path,
        initial_files={"main.py": "raise ValueError('boom')\n"},
        run_outputs=[same_error] * (dev_agent.MAX_FIX_ATTEMPTS + 1),  # never changes
        fix_targets=["main.py"],
        fix_writer=lambda n, targets, project_dir: {"main.py": f"broken attempt {n}\n"},
    )

    assert "couldn't fully fix" in result.lower()
    # every attempt was rolled back — original content survives untouched
    assert (project_dir / "main.py").read_text(encoding="utf-8") == "raise ValueError('boom')\n"


def test_build_project_rolls_back_on_worse_error(monkeypatch, tmp_path):
    result, project_dir = _rig_build_project(
        monkeypatch, tmp_path,
        initial_files={"main.py": "raise ValueError('boom')\n"},
        run_outputs=[
            'File "main.py", line 5, in <module>\n  File "main.py", line 3, in <module>\nTypeError: deep',
            # the "fix" regresses to a shallower failure -> worse -> rollback
            'File "main.py", line 1, in <module>\nSyntaxError: invalid syntax',
        ] + ['File "main.py", line 5, in <module>\n  File "main.py", line 3, in <module>\nTypeError: deep'] * dev_agent.MAX_FIX_ATTEMPTS,
        fix_targets=["main.py"],
        fix_writer=lambda n, targets, project_dir: {"main.py": "broken fix\n"},
    )

    assert "couldn't fully fix" in result.lower()
    assert (project_dir / "main.py").read_text(encoding="utf-8") == "raise ValueError('boom')\n"


def test_build_project_rolls_back_on_partial_write_failure(monkeypatch, tmp_path):
    result, project_dir = _rig_build_project(
        monkeypatch, tmp_path,
        initial_files={
            "main.py": "from utils import helper\nhelper()\n",
            "utils.py": "def helper():\n    return 1\n",
        },
        run_outputs=['File "main.py", line 2, in <module>\nNameError: name \'helper\' is not defined'] * (dev_agent.MAX_FIX_ATTEMPTS + 1),
        fix_targets=["main.py", "utils.py"],
        # Only main.py gets written; utils.py silently fails -> partial failure -> rollback both.
        fix_writer=lambda n, targets, project_dir: {"main.py": f"partially fixed {n}\n"},
    )

    assert "couldn't fully fix" in result.lower()
    assert (project_dir / "main.py").read_text(encoding="utf-8") == "from utils import helper\nhelper()\n"
    assert (project_dir / "utils.py").read_text(encoding="utf-8") == "def helper():\n    return 1\n"


def test_build_project_rollback_never_touches_files_outside_project_dir(monkeypatch, tmp_path):
    sentinel = tmp_path / "outside_sentinel.py"
    sentinel.write_text("must never change\n", encoding="utf-8")

    same_error = 'File "main.py", line 1, in <module>\nValueError: boom'
    result, project_dir = _rig_build_project(
        monkeypatch, tmp_path,
        initial_files={"main.py": "raise ValueError('boom')\n"},
        run_outputs=[same_error] * (dev_agent.MAX_FIX_ATTEMPTS + 1),
        fix_targets=["../outside_sentinel.py"],  # attempted escape
        fix_writer=lambda n, targets, project_dir: {},  # nothing writable anyway (escapes snapshot)
    )

    assert sentinel.read_text(encoding="utf-8") == "must never change\n"


def test_build_project_rollback_does_not_mutate_active_workspace(monkeypatch, tmp_path):
    fake_base = tmp_path / "mark_repo_copy"
    (fake_base / "config").mkdir(parents=True)
    workspace_file = fake_base / "config" / "workspace.json"
    workspace_file.write_text(json.dumps({"path": str(fake_base)}), encoding="utf-8")
    monkeypatch.setattr(ws, "_base_dir", lambda: fake_base)

    before_content = workspace_file.read_text(encoding="utf-8")
    before_mtime = workspace_file.stat().st_mtime_ns

    same_error = 'File "main.py", line 1, in <module>\nValueError: boom'
    _rig_build_project(
        monkeypatch, tmp_path / "projects_root",
        initial_files={"main.py": "raise ValueError('boom')\n"},
        run_outputs=[same_error] * (dev_agent.MAX_FIX_ATTEMPTS + 1),
        fix_targets=["main.py"],
        fix_writer=lambda n, targets, project_dir: {"main.py": f"broken {n}\n"},
    )

    assert workspace_file.read_text(encoding="utf-8") == before_content
    assert workspace_file.stat().st_mtime_ns == before_mtime
    assert ws.get_workspace() == fake_base.resolve()


# ---------------------------------------------------------------------------
# CodingTask continuity — dev_agent() orchestration/routing.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def isolated_coding_task_state(tmp_path, monkeypatch):
    state_file = tmp_path / "config" / "state" / "coding_task.json"
    monkeypatch.setattr(ct, "STATE_FILE", state_file)
    # Engineering memory is wired into the same execution paths as
    # CodingTask — isolate it too so no test ever touches Mark's real
    # config/state/engineering_memory.json.
    memory_file = tmp_path / "config" / "state" / "engineering_memory.json"
    monkeypatch.setattr(em, "STATE_FILE", memory_file)
    return state_file


def test_dev_agent_new_build_creates_a_coding_task(monkeypatch, isolated_coding_task_state):
    captured = {}

    def fake_build_project(**kwargs):
        captured.update(kwargs)
        return "Project 'calculator_app' is working, sir."
    monkeypatch.setattr(dev_agent, "_build_project", fake_build_project)

    result = dev_agent.dev_agent(parameters={"description": "Build me a calculator app"})

    assert "working" in result
    assert captured["task"] is not None
    assert captured["task"].original_goal == "Build me a calculator app"

    active = ct.load_active_task()
    assert active is not None
    assert active.original_goal == "Build me a calculator app"


def test_dev_agent_feature_add_continues_same_project_not_a_new_one(monkeypatch, isolated_coding_task_state):
    existing = ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )
    ct.mark_completed(existing)
    original_id = existing.task_id

    build_called = {"n": 0}
    monkeypatch.setattr(dev_agent, "_build_project", lambda **kw: build_called.update(n=build_called["n"] + 1))

    captured = {}
    def fake_incremental(task, change_request, timeout, speak=None, player=None):
        captured["task"] = task
        captured["change_request"] = change_request
        return "Done, sir — updated 'calculator_app'."
    monkeypatch.setattr(dev_agent, "_run_incremental_feature_change", fake_incremental)

    result = dev_agent.dev_agent(parameters={"description": "Add calculation history"})

    assert "done" in result.lower()
    assert build_called["n"] == 0  # never falls back to the fresh-project pipeline
    # same project, not a new one
    assert captured["task"].project_name == "calculator_app"
    assert captured["task"].task_id == original_id
    assert captured["change_request"] == "Add calculation history"

    active = ct.load_active_task()
    assert active.task_id == original_id
    assert active.current_goal == "Add calculation history"
    assert active.original_goal == "Build me a calculator app"  # preserved


def test_dev_agent_fix_current_error_resumes_without_replanning(monkeypatch, isolated_coding_task_state):
    existing = ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )
    existing.record_error("NameError: name 'x' is not defined", signature="NameError:main.py:3")
    ct.save_task(existing)

    build_called = {"n": 0}
    monkeypatch.setattr(dev_agent, "_build_project", lambda **kw: build_called.update(n=build_called["n"] + 1))

    resume_captured = {}
    def fake_resume(task, timeout, speak=None, player=None):
        resume_captured["task"] = task
        resume_captured["preserved_error"] = task.last_runtime_error
        return "Resumed and fixed, sir."
    monkeypatch.setattr(dev_agent, "_continue_fix_loop_for_task", fake_resume)

    result = dev_agent.dev_agent(parameters={"description": "Fix the current error"})

    assert result == "Resumed and fixed, sir."
    assert build_called["n"] == 0  # no replan/rebuild — the fix loop resumed directly
    assert resume_captured["task"].task_id == existing.task_id
    assert "NameError" in resume_captured["preserved_error"]  # last runtime error/evidence carried into the resume


def test_dev_agent_explicit_new_project_creates_new_task(monkeypatch, isolated_coding_task_state):
    existing = ct.start_task(
        original_goal="Build me a calculator app",
        project_name="calculator_app",
        project_root="/tmp/JarvisProjects/calculator_app",
    )

    captured = {}
    def fake_build_project(**kwargs):
        captured.update(kwargs)
        return "Project is working, sir."
    monkeypatch.setattr(dev_agent, "_build_project", fake_build_project)

    dev_agent.dev_agent(parameters={"description": "Build me a new todo list app"})

    assert captured["task"].task_id != existing.task_id
    assert captured["task"].original_goal == "Build me a new todo list app"


def test_dev_agent_no_active_task_does_not_guess_a_project(monkeypatch, isolated_coding_task_state):
    build_called = {"n": 0}
    monkeypatch.setattr(dev_agent, "_build_project", lambda **kw: build_called.update(n=build_called["n"] + 1))
    resume_called = {"n": 0}
    monkeypatch.setattr(dev_agent, "_continue_fix_loop_for_task", lambda *a, **k: resume_called.update(n=resume_called["n"] + 1))

    result = dev_agent.dev_agent(parameters={"description": "Fix the current error"})

    assert "no active coding project" in result.lower() or "which project" in result.lower()
    assert build_called["n"] == 0
    assert resume_called["n"] == 0
    assert ct.load_active_task() is None  # nothing was created/guessed


def test_dev_agent_plain_new_build_with_no_active_task_still_builds(monkeypatch, isolated_coding_task_state):
    """A normal, non-continuation-shaped build request must still work
    exactly as before when there's no active task at all."""
    captured = {}
    def fake_build_project(**kwargs):
        captured.update(kwargs)
        return "Project is working, sir."
    monkeypatch.setattr(dev_agent, "_build_project", fake_build_project)

    result = dev_agent.dev_agent(parameters={"description": "Build me a calculator app"})

    assert "working" in result
    assert captured["task"] is not None


# ---------------------------------------------------------------------------
# Surgical incremental feature change (dependency/impact-analysis driven).
# ---------------------------------------------------------------------------

def _task_with_project(tmp_path, files: dict):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    for path, content in files.items():
        full = project_dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    task = ct.start_task(
        original_goal="Build a calculator app",
        project_name="proj",
        project_root=str(project_dir),
        entry_point="main.py",
        run_command="python main.py",
    )
    return task, project_dir


def test_incremental_change_edits_only_selected_files_and_uses_impact_summary(
    monkeypatch, tmp_path, isolated_coding_task_state,
):
    task, project_dir = _task_with_project(tmp_path, {
        "main.py": "def add(a, b):\n    return a + b\n",
        "unrelated.py": "def totally_unrelated():\n    pass\n",
    })

    prompts = []
    class FakeModel:
        def generate_content(self, prompt):
            prompts.append(prompt)
            class R:
                text = "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n"
            return R()
    monkeypatch.setattr(dev_agent, "_get_model", lambda model_name: FakeModel())
    monkeypatch.setattr(dev_agent, "_run_project", lambda *a, **k: "42\n")

    result = dev_agent._run_incremental_feature_change(task, "add a subtract function", timeout=5)

    assert "done" in result.lower()
    assert "def subtract" in (project_dir / "main.py").read_text(encoding="utf-8")
    assert (project_dir / "unrelated.py").read_text(encoding="utf-8") == "def totally_unrelated():\n    pass\n"
    assert prompts, "the edit model must have been called"
    assert "Impact summary" in prompts[0]
    assert "main.py" in prompts[0]


def test_incremental_change_snapshots_target_files_before_editing(monkeypatch, tmp_path, isolated_coding_task_state):
    task, project_dir = _task_with_project(tmp_path, {"main.py": "def add(a, b):\n    return a + b\n"})

    snapshot_calls = []
    real_snapshot = dev_agent._snapshot_files
    def spy_snapshot(pdir, paths):
        snapshot_calls.append(list(paths))
        return real_snapshot(pdir, paths)
    monkeypatch.setattr(dev_agent, "_snapshot_files", spy_snapshot)

    class FakeModel:
        def generate_content(self, prompt):
            class R:
                text = "def add(a, b):\n    return a + b + 0\n"
            return R()
    monkeypatch.setattr(dev_agent, "_get_model", lambda model_name: FakeModel())
    monkeypatch.setattr(dev_agent, "_run_project", lambda *a, **k: "42\n")

    dev_agent._run_incremental_feature_change(task, "tweak add function", timeout=5)

    assert snapshot_calls
    assert "main.py" in snapshot_calls[0]


def test_incremental_change_partial_write_failure_rolls_back_all_selected_files(
    monkeypatch, tmp_path, isolated_coding_task_state,
):
    task, project_dir = _task_with_project(tmp_path, {
        "main.py": "from utils import helper\nhelper()\n",
        "utils.py": "def helper():\n    return 1\n",
    })
    original_main = (project_dir / "main.py").read_text(encoding="utf-8")
    original_utils = (project_dir / "utils.py").read_text(encoding="utf-8")

    # Force both files to be selected as targets, but only main.py gets written.
    monkeypatch.setattr(
        dev_agent, "_gather_project_evidence",
        lambda pdir, req, ef: (
            [{"file": "main.py", "line": 1, "kind": "literal", "content": ""},
             {"file": "utils.py", "line": 1, "kind": "literal", "content": ""}],
            "main.py:1\nutils.py:1",
        ),
    )

    def fake_apply(**kwargs):
        target = kwargs["target_files"][0]
        full = kwargs["project_dir"] / target
        full.write_text("BROKEN PARTIAL WRITE\n", encoding="utf-8")
        return {target: "BROKEN PARTIAL WRITE\n"}  # only ONE of the two targets
    monkeypatch.setattr(dev_agent, "_apply_feature_change", fake_apply)

    result = dev_agent._run_incremental_feature_change(task, "helper related change", timeout=5)

    assert "rolled back" in result.lower()
    assert (project_dir / "main.py").read_text(encoding="utf-8") == original_main
    assert (project_dir / "utils.py").read_text(encoding="utf-8") == original_utils


def test_incremental_change_success_updates_coding_task_state(monkeypatch, tmp_path, isolated_coding_task_state):
    task, project_dir = _task_with_project(tmp_path, {"main.py": "def add(a, b):\n    return a + b\n"})

    class FakeModel:
        def generate_content(self, prompt):
            class R:
                text = "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n"
            return R()
    monkeypatch.setattr(dev_agent, "_get_model", lambda model_name: FakeModel())
    monkeypatch.setattr(dev_agent, "_run_project", lambda *a, **k: "42\n")

    dev_agent._run_incremental_feature_change(task, "add a subtract function", timeout=5)

    assert task.status == ct.Status.COMPLETED
    assert task.phase == ct.Phase.COMPLETED
    assert task.last_successful_step == "feature_change"
    assert "main.py" in task.files_touched

    reloaded = ct.load_active_task()
    assert reloaded.status == ct.Status.COMPLETED


def test_incremental_change_runtime_failure_enters_existing_fix_loop(monkeypatch, tmp_path, isolated_coding_task_state):
    task, project_dir = _task_with_project(tmp_path, {"main.py": "def add(a, b):\n    return a + b\n"})

    class FakeModel:
        def generate_content(self, prompt):
            class R:
                # syntactically valid, but references an undefined name — a
                # genuine RUNTIME failure, not a syntax-validation rejection.
                text = "def add(a, b):\n    return a + b + oops\n"
            return R()
    monkeypatch.setattr(dev_agent, "_get_model", lambda model_name: FakeModel())
    monkeypatch.setattr(
        dev_agent, "_run_project",
        lambda *a, **k: 'File "main.py", line 2, in add\nNameError: name \'oops\' is not defined',
    )

    fix_loop_calls = []
    def fake_fix_loop(**kwargs):
        fix_loop_calls.append(kwargs)
        return "handed off to fix loop"
    monkeypatch.setattr(dev_agent, "_run_fix_loop", fake_fix_loop)

    result = dev_agent._run_incremental_feature_change(task, "add a subtract function", timeout=5)

    assert result == "handed off to fix loop"
    assert len(fix_loop_calls) == 1
    assert fix_loop_calls[0]["task"] is task
    assert fix_loop_calls[0]["project_dir"] == project_dir


def test_incremental_change_worsening_fix_attempt_still_rolls_back(monkeypatch, tmp_path, isolated_coding_task_state):
    """End-to-end: the post-edit validation fails, control passes to the
    REAL _run_fix_loop, which must still roll back a worsening fix attempt
    exactly as it does for a fresh build — the feature edit content itself
    (the new baseline) is what's being fixed, not what's rolled back."""
    task, project_dir = _task_with_project(tmp_path, {"main.py": "def add(a, b):\n    return a + b\n"})
    monkeypatch.setattr(time, "sleep", lambda s: None)

    class FakeEditModel:
        def generate_content(self, prompt):
            class R:
                text = "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b + 1\n"  # subtly wrong
            return R()
    monkeypatch.setattr(dev_agent, "_get_model", lambda model_name: FakeEditModel())

    deep_failure = 'File "main.py", line 5, in <module>\n  File "main.py", line 3, in <module>\nTypeError: deep failure'
    shallow_failure = 'File "main.py", line 1, in <module>\nSyntaxError: invalid syntax'
    outputs = [
        deep_failure,  # _run_incremental_feature_change's own post-edit validation
        deep_failure,  # _run_fix_loop's own initial re-validation (same unfixed state)
    ] + [shallow_failure] * dev_agent.MAX_FIX_ATTEMPTS  # each fix attempt regresses -> "worse" -> rollback
    monkeypatch.setattr(dev_agent, "_run_project", lambda *a, **k: outputs.pop(0) if outputs else "ok\n")
    monkeypatch.setattr(dev_agent, "_plan_fix_targets", lambda *a, **k: (["main.py"], ""))
    monkeypatch.setattr(dev_agent, "_fix_files", lambda **kw: {"main.py": "still broken\n"})

    result = dev_agent._run_incremental_feature_change(task, "add a subtract function", timeout=5)

    assert "couldn't fully fix" in result.lower()
    # the fix loop rolled back every fix attempt — main.py holds the feature
    # edit's own content (the baseline being fixed), not the fix attempts.
    assert (project_dir / "main.py").read_text(encoding="utf-8") == FakeEditModel().generate_content("").text.strip()
    assert task.status == ct.Status.FAILED


def test_incremental_change_creates_required_new_file_safely(monkeypatch, tmp_path, isolated_coding_task_state):
    task, project_dir = _task_with_project(tmp_path, {"main.py": "print('calc app')\n"})

    class FakeModel:
        def generate_content(self, prompt):
            class R:
                text = "def record(entry):\n    pass\n"
            return R()
    monkeypatch.setattr(dev_agent, "_get_model", lambda model_name: FakeModel())
    monkeypatch.setattr(dev_agent, "_run_project", lambda *a, **k: "ok\n")
    # No existing evidence match for this brand-new capability.
    monkeypatch.setattr(dev_agent, "_gather_project_evidence", lambda pdir, req, ef: ([], ""))

    result = dev_agent._run_incremental_feature_change(
        task, "add a history.py module that tracks calculations", timeout=5,
    )

    assert "done" in result.lower()
    assert (project_dir / "history.py").exists()
    assert (project_dir / "history.py").read_text(encoding="utf-8") == "def record(entry):\n    pass"


def test_incremental_change_rejects_path_escaping_new_file(monkeypatch, tmp_path, isolated_coding_task_state):
    task, project_dir = _task_with_project(tmp_path, {"main.py": "print('calc app')\n"})
    outside = tmp_path / "evil.py"

    build_called = {"n": 0}
    monkeypatch.setattr(dev_agent, "_apply_feature_change", lambda **kw: build_called.update(n=build_called["n"] + 1))
    monkeypatch.setattr(dev_agent, "_gather_project_evidence", lambda pdir, req, ef: ([], ""))

    result = dev_agent._run_incremental_feature_change(
        task, "add ../evil.py with malicious code", timeout=5,
    )

    assert not outside.exists()
    assert build_called["n"] == 0
    assert "couldn't confidently scope" in result.lower() or "broader changes" in result.lower()


def test_incremental_change_too_broad_returns_clear_message_not_full_rebuild(
    monkeypatch, tmp_path, isolated_coding_task_state,
):
    task, project_dir = _task_with_project(tmp_path, {
        f"file{i}.py": f"x{i} = {i}\n" for i in range(5)
    })

    evidence = [{"file": f"file{i}.py", "line": 1, "kind": "literal", "content": ""} for i in range(5)]
    monkeypatch.setattr(dev_agent, "_gather_project_evidence", lambda pdir, req, ef: (evidence, "big evidence"))

    build_called = {"n": 0}
    apply_called = {"n": 0}
    monkeypatch.setattr(dev_agent, "_build_project", lambda **kw: build_called.update(n=build_called["n"] + 1))
    monkeypatch.setattr(dev_agent, "_apply_feature_change", lambda **kw: apply_called.update(n=apply_called["n"] + 1))

    result = dev_agent._run_incremental_feature_change(task, "rename everything everywhere", timeout=5)

    assert "too broad" in result.lower()
    assert build_called["n"] == 0
    assert apply_called["n"] == 0


def test_extract_explicit_new_file_rejects_path_escape(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    result = dev_agent._extract_explicit_new_file("add ../evil.py please", project_dir, {})
    assert result is None


def test_extract_explicit_new_file_accepts_bounded_new_path(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    result = dev_agent._extract_explicit_new_file("add history.py to track things", project_dir, {})
    assert result == "history.py"


# ---------------------------------------------------------------------------
# Engineering memory integration — runtime-fix loop and incremental change.
# ---------------------------------------------------------------------------

def test_no_memory_case_preserves_existing_fix_behavior(monkeypatch, tmp_path, isolated_coding_task_state):
    """With no prior records at all, the fix prompt must be unaffected —
    no memory note, no behavior change from before memory existed."""
    task, project_dir = _task_with_project(tmp_path, {"main.py": "raise ValueError('boom')\n"})
    error_output = 'File "main.py", line 1, in <module>\nValueError: boom'
    outputs = [error_output, "42\n"]
    monkeypatch.setattr(dev_agent, "_run_project", lambda *a, **k: outputs.pop(0))
    monkeypatch.setattr(dev_agent, "_plan_fix_targets", lambda *a, **k: (["main.py"], ""))

    captured_prompts = []
    class FakeModel:
        def generate_content(self, prompt):
            captured_prompts.append(prompt)
            class R:
                text = "print(42)\n"
            return R()
    monkeypatch.setattr(dev_agent, "_get_model", lambda model_name: FakeModel())

    result = dev_agent._run_fix_loop(
        project_dir=project_dir, run_command="python main.py", description="demo",
        language="python", files=[{"path": "main.py", "description": "", "imports": []}],
        entry_point="main.py", file_codes={"main.py": "raise ValueError('boom')\n"},
        timeout=5, proj_name="proj", task=task, operation_type="build_fix",
    )

    assert "working" in result.lower()
    assert len(captured_prompts) == 1
    assert "IMPORTANT" not in captured_prompts[0]
    assert "Relevant past engineering outcomes" not in captured_prompts[0]


def test_runtime_fix_records_successful_attempt(monkeypatch, tmp_path, isolated_coding_task_state):
    task, project_dir = _task_with_project(tmp_path, {"main.py": "raise ValueError('boom')\n"})
    error_output = 'File "main.py", line 1, in <module>\nValueError: boom'
    outputs = [error_output, "42\n"]
    monkeypatch.setattr(dev_agent, "_run_project", lambda *a, **k: outputs.pop(0))
    monkeypatch.setattr(dev_agent, "_plan_fix_targets", lambda *a, **k: (["main.py"], ""))
    monkeypatch.setattr(dev_agent, "_get_model", lambda model_name: type(
        "M", (), {"generate_content": lambda self, p: type("R", (), {"text": "print(42)\n"})()}
    )())

    dev_agent._run_fix_loop(
        project_dir=project_dir, run_command="python main.py", description="demo",
        language="python", files=[{"path": "main.py", "description": "", "imports": []}],
        entry_point="main.py", file_codes={"main.py": "raise ValueError('boom')\n"},
        timeout=5, proj_name="proj", task=task, operation_type="build_fix",
    )

    records = em._load_all()
    assert any(r.outcome == em.OUTCOME_SUCCESS and r.project_key == em.project_key(str(project_dir)) for r in records)


def test_runtime_fix_records_rolled_back_attempt(monkeypatch, tmp_path, isolated_coding_task_state):
    task, project_dir = _task_with_project(tmp_path, {"main.py": "raise ValueError('boom')\n"})
    same_error = 'File "main.py", line 1, in <module>\nValueError: boom'
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(dev_agent, "_run_project", lambda *a, **k: same_error)  # never improves
    monkeypatch.setattr(dev_agent, "_plan_fix_targets", lambda *a, **k: (["main.py"], ""))
    monkeypatch.setattr(dev_agent, "_fix_files", lambda **kw: {"main.py": "still broken\n"})

    dev_agent._run_fix_loop(
        project_dir=project_dir, run_command="python main.py", description="demo",
        language="python", files=[{"path": "main.py", "description": "", "imports": []}],
        entry_point="main.py", file_codes={"main.py": "raise ValueError('boom')\n"},
        timeout=5, proj_name="proj", task=task, operation_type="build_fix",
    )

    records = em._load_all()
    pkey = em.project_key(str(project_dir))
    rolled_back = [r for r in records if r.project_key == pkey and r.outcome == em.OUTCOME_ROLLED_BACK]
    failed = [r for r in records if r.project_key == pkey and r.outcome == em.OUTCOME_FAILED]
    assert rolled_back  # each unchanged-attempt rollback recorded
    assert failed        # final exhaustion recorded distinctly


def test_relevant_memory_reaches_runtime_fix_prompt_and_blocks_known_failure(
    monkeypatch, tmp_path, isolated_coding_task_state,
):
    task, project_dir = _task_with_project(tmp_path, {"main.py": "raise ValueError('boom')\n"})
    error_output = 'File "main.py", line 1, in <module>\nValueError: boom'

    sig = dev_agent._normalize_error_signature(error_output)
    sig_str = f"{sig[0]}:{sig[1]}:{sig[2]}"
    error_type = dev_agent._classify_error(error_output)
    files_to_fix = ["main.py"]
    attempt_summary = f"fix {error_type} via evidence-driven edit in {', '.join(sorted(files_to_fix))}"
    fingerprint = em.compute_attempt_fingerprint("build_fix", sig_str, files_to_fix, attempt_summary)

    prior = em.EngineeringRecord(
        record_id="prior1", task_id="other-task", project_key=em.project_key(str(project_dir)),
        timestamp=em._now(), operation_type="build_fix", goal_summary="fix boom",
        normalized_error_signature=sig_str, evidence_summary="ev", impact_summary="impact",
        files_touched=files_to_fix, attempt_summary=attempt_summary,
        outcome=em.OUTCOME_ROLLED_BACK, rollback_reason="unchanged",
        attempt_fingerprint=fingerprint,
    )
    em._save_all([prior])

    outputs = [error_output, "42\n"]
    monkeypatch.setattr(dev_agent, "_run_project", lambda *a, **k: outputs.pop(0))
    monkeypatch.setattr(dev_agent, "_plan_fix_targets", lambda *a, **k: (files_to_fix, ""))

    captured_prompts = []
    class FakeModel:
        def generate_content(self, prompt):
            captured_prompts.append(prompt)
            class R:
                text = "print(42)\n"
            return R()
    monkeypatch.setattr(dev_agent, "_get_model", lambda model_name: FakeModel())

    result = dev_agent._run_fix_loop(
        project_dir=project_dir, run_command="python main.py", description="demo",
        language="python", files=[{"path": "main.py", "description": "", "imports": []}],
        entry_point="main.py", file_codes={"main.py": "raise ValueError('boom')\n"},
        timeout=5, proj_name="proj", task=task, operation_type="build_fix",
    )

    assert "working" in result.lower()  # the alternative attempt was allowed to proceed and succeeded
    assert len(captured_prompts) == 1   # exactly one generation call — no infinite/double retry
    assert "Avoid repeating this approach" in captured_prompts[0]
    assert "rolled_back" in captured_prompts[0]
    assert "Relevant past engineering outcomes" in captured_prompts[0]


def test_relevant_memory_reaches_incremental_change_prompt(monkeypatch, tmp_path, isolated_coding_task_state):
    task, project_dir = _task_with_project(tmp_path, {"main.py": "def add(a, b):\n    return a + b\n"})

    prior = em.EngineeringRecord(
        record_id="prior1", task_id="other-task", project_key=em.project_key(str(project_dir)),
        timestamp=em._now(), operation_type="feature_change", goal_summary="add subtract function",
        normalized_error_signature="", evidence_summary="ev", impact_summary="impact",
        files_touched=["main.py"], attempt_summary="add subtract function",
        outcome=em.OUTCOME_SUCCESS,
    )
    em._save_all([prior])

    captured_prompts = []
    class FakeModel:
        def generate_content(self, prompt):
            captured_prompts.append(prompt)
            class R:
                text = "def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n"
            return R()
    monkeypatch.setattr(dev_agent, "_get_model", lambda model_name: FakeModel())
    monkeypatch.setattr(dev_agent, "_run_project", lambda *a, **k: "42\n")

    dev_agent._run_incremental_feature_change(task, "add a multiply function", timeout=5)

    assert captured_prompts
    assert "Relevant past engineering outcomes" in captured_prompts[0]


def test_identical_known_failed_feature_attempt_is_rejected_before_writes(
    monkeypatch, tmp_path, isolated_coding_task_state,
):
    task, project_dir = _task_with_project(tmp_path, {"main.py": "def add(a, b):\n    return a + b\n"})
    change_request = "add a subtract function"
    fingerprint = em.compute_attempt_fingerprint("feature_change", "", ["main.py"], change_request)

    prior = em.EngineeringRecord(
        record_id="prior1", task_id="other-task", project_key=em.project_key(str(project_dir)),
        timestamp=em._now(), operation_type="feature_change", goal_summary=change_request,
        normalized_error_signature="", evidence_summary="ev", impact_summary="impact",
        files_touched=["main.py"], attempt_summary=change_request,
        outcome=em.OUTCOME_ROLLED_BACK, rollback_reason="write_failure",
        attempt_fingerprint=fingerprint,
    )
    em._save_all([prior])

    write_calls = {"n": 0}
    captured_prompts = []
    def fake_apply(**kwargs):
        write_calls["n"] += 1
        captured_prompts.append(kwargs.get("memory_note", ""))
        target = kwargs["target_files"][0]
        (kwargs["project_dir"] / target).write_text("def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n", encoding="utf-8")
        return {target: "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n"}
    monkeypatch.setattr(dev_agent, "_apply_feature_change", fake_apply)
    monkeypatch.setattr(dev_agent, "_run_project", lambda *a, **k: "42\n")

    result = dev_agent._run_incremental_feature_change(task, change_request, timeout=5)

    assert write_calls["n"] == 1  # exactly one generation attempt — the alternative, not a blind repeat
    assert "Avoid repeating this approach" in captured_prompts[0]
    assert "done" in result.lower()  # the alternative attempt was allowed to proceed


def test_incremental_change_records_rolled_back_attempt(monkeypatch, tmp_path, isolated_coding_task_state):
    task, project_dir = _task_with_project(tmp_path, {
        "main.py": "from utils import helper\nhelper()\n",
        "utils.py": "def helper():\n    return 1\n",
    })
    monkeypatch.setattr(
        dev_agent, "_gather_project_evidence",
        lambda pdir, req, ef: (
            [{"file": "main.py", "line": 1, "kind": "literal", "content": ""},
             {"file": "utils.py", "line": 1, "kind": "literal", "content": ""}],
            "ev",
        ),
    )
    def fake_apply(**kwargs):
        target = kwargs["target_files"][0]
        return {target: "partial\n"}  # only one of two targets — partial failure
    monkeypatch.setattr(dev_agent, "_apply_feature_change", fake_apply)

    dev_agent._run_incremental_feature_change(task, "helper related change", timeout=5)

    records = em._load_all()
    pkey = em.project_key(str(project_dir))
    assert any(r.project_key == pkey and r.outcome == em.OUTCOME_ROLLED_BACK for r in records)
