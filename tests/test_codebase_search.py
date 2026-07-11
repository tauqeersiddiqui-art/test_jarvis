import inspect

from actions import codebase_search as cs


def test_search_filename_partial(project):
    results = cs.search_filenames(project, "helper", mode="partial")
    assert any(r.file.endswith("helper.py") for r in results)


def test_search_filename_exact(project):
    results = cs.search_filenames(project, "main.py", mode="exact")
    assert len(results) == 1
    assert results[0].file == "main.py"


def test_search_filename_extension(project):
    results = cs.search_filenames(project, "py", mode="extension")
    files = {r.file for r in results}
    assert "main.py" in files
    assert "utils/helper.py" in files


def test_search_filename_glob(project):
    results = cs.search_filenames(project, "utils/*.py", mode="glob")
    assert any(r.file == "utils/helper.py" for r in results)


def test_literal_search_finds_matches_in_multiple_files(project):
    results = cs.search_text(project, "helper_func", regex=False)
    files = {r.file for r in results}
    assert "main.py" in files
    assert "utils/helper.py" in files


def test_literal_search_treats_special_chars_literally(project):
    results = cs.search_text(project, "helper_func(", regex=False)
    assert any("helper_func(" in r.snippet for r in results)


def test_regex_search_matches_pattern(project):
    results = cs.search_text(project, r"def \w+_func", regex=True)
    assert any(r.file == "utils/helper.py" for r in results)


def test_regex_string_not_matched_literally(project):
    # the exact regex text "def \w+_func" doesn't appear verbatim anywhere
    literal_results = cs.search_text(project, r"def \w+_func", regex=False)
    assert not any(r.file == "utils/helper.py" for r in literal_results)


def test_symbol_class_definition_found(project):
    results = cs.search_symbol(project, "Foo", kind="class")
    assert len(results) == 1
    assert results[0].file == "main.py"
    assert results[0].match_type == "class_def"
    assert results[0].line == 3


def test_symbol_function_definition_found(project):
    results = cs.search_symbol(project, "helper_func", kind="function")
    assert any(
        r.file == "utils/helper.py" and r.match_type == "function_def" and r.line == 1
        for r in results
    )


def test_symbol_reference_search_finds_usages(project):
    results = cs.search_symbol(project, "helper_func", kind="reference")
    files = {r.file for r in results}
    assert "main.py" in files
    assert "tests/test_helper.py" in files


def test_symbol_import_search(project):
    results = cs.search_symbol(project, "helper_func", kind="import")
    files = {r.file for r in results}
    assert "main.py" in files
    assert "tests/test_helper.py" in files


def test_line_and_context_evidence_present(project):
    results = cs.search_text(project, "helper_func", regex=False)
    r = next(x for x in results if x.file == "utils/helper.py")
    assert r.line == 1
    assert "helper_func" in r.snippet


def test_secret_content_never_returned_by_text_search(project):
    results = cs.search_text(project, "sk-real-secret-value", regex=False)
    assert results == []


def test_secret_file_content_redacted_on_read(project):
    out = cs.read_files(project, [".env"])
    assert out[0]["content"] == cs.REDACTED
    assert "sk-real-secret" not in out[0]["content"]


def test_secret_file_still_discoverable_by_name(project):
    # per spec: path/name may be surfaced as relevant, only content is blocked
    results = cs.search_filenames(project, ".env", mode="exact")
    assert any(r.file == ".env" for r in results)


def test_read_files_blocks_workspace_escape(project):
    out = cs.read_files(project, ["../outside.txt"])
    assert "[BLOCKED" in out[0]["content"]


def test_ripgrep_fallback_produces_same_results(project, monkeypatch):
    monkeypatch.setattr(cs, "_rg_binary", lambda: None)
    results = cs.search_text(project, "helper_func", regex=False)
    assert any(r.file == "utils/helper.py" for r in results)


def test_ripgrep_invocation_is_arg_list_no_shell(project, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs

        class _R:
            returncode = 1
            stdout = ""
        return _R()

    monkeypatch.setattr(cs.subprocess, "run", fake_run)
    monkeypatch.setattr(cs, "_rg_binary", lambda: "rg")
    cs.search_text(project, "helper_func", regex=False)

    assert isinstance(captured["cmd"], list)
    assert captured["cmd"][0] == "rg"
    assert captured["kwargs"].get("shell") is not True


def test_no_write_capable_calls_in_module_source():
    src = inspect.getsource(cs)
    for forbidden in ("write_text(", "write_bytes(", "unlink(", "os.remove", "shutil.rmtree", "shutil.move"):
        assert forbidden not in src, f"unexpected write-capable call in codebase_search.py: {forbidden}"


def test_search_and_read_do_not_modify_files(project):
    before = {p: p.stat().st_mtime_ns for p in project.rglob("*") if p.is_file()}
    cs.search_text(project, "helper_func", regex=False)
    cs.search_symbol(project, "Foo", kind="class")
    cs.search_filenames(project, "main", mode="partial")
    cs.find_entry_points(project)
    cs.find_config_files(project)
    cs.find_related_tests(project, "helper_func")
    cs.project_structure(project)
    cs.read_files(project, ["main.py", ".env"])
    after = {p: p.stat().st_mtime_ns for p in project.rglob("*") if p.is_file()}
    assert before == after
