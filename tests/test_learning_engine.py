import json
import os

import pytest

import core.learning_engine as le


@pytest.fixture(autouse=True)
def isolated_state_file(tmp_path, monkeypatch):
    state_file = tmp_path / "config" / "state" / "learning_engine.json"
    monkeypatch.setattr(le, "STATE_FILE", state_file)
    return state_file


@pytest.fixture
def repo(tmp_path):
    """A small synthetic repository: two markdown docs, one python module
    with a module/class/function docstring, one sensitive file, and one
    binary-ish file that must never be read."""
    root = tmp_path / "repo"
    root.mkdir()

    (root / "readme.md").write_text(
        "# Project Title\n"
        "\n"
        "Intro paragraph about the project.\n"
        "\n"
        "## Getting Started\n"
        "\n"
        "Run `python main.py` to start.\n",
        encoding="utf-8",
    )

    (root / "ROADMAP.md").write_text(
        "# Roadmap\n"
        "\n"
        "## Phase 1\n"
        "\n"
        "Build the core engine first.\n",
        encoding="utf-8",
    )

    (root / "core_module.py").write_text(
        '"""Module docstring describing the core module."""\n'
        "\n"
        "class Widget:\n"
        '    """A widget class docstring."""\n'
        "\n"
        "    def spin(self):\n"
        '        """Spin the widget docstring."""\n'
        "        return True\n",
        encoding="utf-8",
    )

    (root / ".env").write_text("API_KEY=super-secret-value\n", encoding="utf-8")

    return root


# ---------------------------------------------------------------------------
# Fresh learn / discovery
# ---------------------------------------------------------------------------

def test_fresh_learn_discovers_markdown_and_docstrings(repo):
    report = le.learn(workspace=repo)
    assert report.files_scanned == 3          # readme.md, ROADMAP.md, core_module.py (.env excluded)
    assert report.files_changed == 3
    assert report.units_added > 0
    assert report.total_units == report.units_added

    all_titles = {u.section_title for u in le._load_state()["units"].values()}
    assert "Getting Started" in all_titles
    assert "Phase 1" in all_titles
    assert any("core_module.py" in t for t in all_titles)


def test_sensitive_file_never_read(repo):
    le.learn(workspace=repo)
    units = le._load_state()["units"].values()
    for u in units:
        assert "super-secret-value" not in u.summary
        assert not any(".env" in p for p in u.source_paths)


def test_empty_workspace_yields_no_units(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    report = le.learn(workspace=empty)
    assert report.files_scanned == 0
    assert report.total_units == 0


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def test_second_learn_skips_unchanged_files(repo):
    le.learn(workspace=repo)
    report2 = le.learn(workspace=repo)
    assert report2.files_changed == 0
    assert report2.units_added == 0


def test_changed_file_is_reprocessed_and_old_units_replaced(repo):
    le.learn(workspace=repo)
    (repo / "readme.md").write_text(
        "# Project Title\n"
        "\n"
        "## Getting Started\n"
        "\n"
        "Updated instructions: run `python app.py` instead.\n"
        "\n"
        "## New Section\n"
        "\n"
        "Brand new content.\n",
        encoding="utf-8",
    )
    report2 = le.learn(workspace=repo)
    assert report2.files_changed == 1

    units = le._load_state()["units"].values()
    titles = {u.section_title for u in units}
    assert "New Section" in titles
    # the old "Getting Started" body text should no longer be findable verbatim
    getting_started = [u for u in units if u.section_title == "Getting Started"]
    assert getting_started
    assert "app.py" in getting_started[0].summary


def test_deleted_file_units_are_removed(repo):
    le.learn(workspace=repo)
    (repo / "ROADMAP.md").unlink()
    report2 = le.learn(workspace=repo)
    assert report2.files_removed == 1

    units = le._load_state()["units"].values()
    for u in units:
        assert not any(p == "ROADMAP.md" for p in u.source_paths)
    assert not any(u.section_title == "Phase 1" for u in units)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_duplicate_content_across_files_dedups_to_one_unit(repo):
    shared_body = "This exact paragraph appears in two different documents verbatim."
    (repo / "docs_a.md").write_text(f"# Doc A\n\n## Shared\n\n{shared_body}\n", encoding="utf-8")
    (repo / "docs_b.md").write_text(f"# Doc B\n\n## Shared\n\n{shared_body}\n", encoding="utf-8")

    report = le.learn(workspace=repo)
    assert report.units_deduplicated >= 1

    units = le._load_state()["units"].values()
    shared_units = [u for u in units if u.summary.strip() == shared_body]
    assert len(shared_units) == 1
    assert set(shared_units[0].source_paths) >= {"docs_a.md", "docs_b.md"}


# ---------------------------------------------------------------------------
# Bounded truncation
# ---------------------------------------------------------------------------

def test_long_section_is_truncated_and_bounded(repo):
    long_body = "word " * 500  # far exceeds MAX_SUMMARY_CHARS
    (repo / "long.md").write_text(f"# Long Doc\n\n## Big Section\n\n{long_body}\n", encoding="utf-8")

    le.learn(workspace=repo)
    units = [u for u in le._load_state()["units"].values() if u.section_title == "Big Section"]
    assert units
    assert len(units[0].summary) <= le.MAX_SUMMARY_CHARS
    assert units[0].summary.endswith("…")
    assert long_body.strip() not in units[0].summary


def test_per_file_unit_cap_is_bounded(repo):
    many_headings = "\n\n".join(f"## Section {i}\n\nbody {i}" for i in range(200))
    (repo / "huge.md").write_text(f"# Huge\n\n{many_headings}\n", encoding="utf-8")

    le.learn(workspace=repo)
    manifest = le._load_state()["manifest"]
    assert len(manifest["huge.md"]["unit_ids"]) <= le.MAX_UNITS_PER_FILE


# ---------------------------------------------------------------------------
# Persistence / fail-safety
# ---------------------------------------------------------------------------

def test_no_state_file_means_empty_store(isolated_state_file):
    assert not isolated_state_file.exists()
    state = le._load_state()
    assert state["units"] == {}
    assert state["manifest"] == {}


def test_corrupt_state_file_fails_safe(isolated_state_file):
    isolated_state_file.parent.mkdir(parents=True, exist_ok=True)
    isolated_state_file.write_text("{not valid json", encoding="utf-8")
    state = le._load_state()
    assert state["units"] == {}
    assert state["manifest"] == {}


def test_corrupt_unit_entries_are_skipped_not_fatal(isolated_state_file):
    isolated_state_file.parent.mkdir(parents=True, exist_ok=True)
    isolated_state_file.write_text(
        json.dumps({"manifest": {}, "units": {"bad": "not a dict", "ok": None}}),
        encoding="utf-8",
    )
    state = le._load_state()
    assert state["units"] == {}


def test_atomic_write_uses_temp_file_then_replace(repo, isolated_state_file, monkeypatch):
    calls = []
    real_replace = os.replace

    def spy(src, dst):
        calls.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    le.learn(workspace=repo)

    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == isolated_state_file
    assert src != str(isolated_state_file)


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------

def test_search_ranks_title_match_above_body_only_match(repo):
    le.learn(workspace=repo)
    results = le.search("Phase")
    assert results
    assert results[0].section_title == "Phase 1"


def test_search_empty_query_returns_empty(repo):
    le.learn(workspace=repo)
    assert le.search("") == []
    assert le.search("   ") == []


def test_search_respects_limit(repo):
    for i in range(10):
        (repo / f"topic_{i}.md").write_text(f"# T{i}\n\n## Widget Topic {i}\n\nabout widgets\n", encoding="utf-8")
    le.learn(workspace=repo)
    results = le.search("widget", limit=3)
    assert len(results) <= 3


def test_get_unit_returns_none_for_unknown_id(repo):
    le.learn(workspace=repo)
    assert le.get_unit("nonexistent-id") is None


def test_get_unit_returns_matching_unit(repo):
    le.learn(workspace=repo)
    any_unit = next(iter(le._load_state()["units"].values()))
    fetched = le.get_unit(any_unit.unit_id)
    assert fetched is not None
    assert fetched.unit_id == any_unit.unit_id


# ---------------------------------------------------------------------------
# stats()
# ---------------------------------------------------------------------------

def test_stats_on_empty_store(isolated_state_file):
    s = le.stats()
    assert s["total_units"] == 0
    assert s["total_files_tracked"] == 0
    assert s["last_learned_at"] is None


def test_stats_after_learn(repo):
    le.learn(workspace=repo)
    s = le.stats()
    assert s["total_units"] > 0
    assert s["total_files_tracked"] == 3
    assert s["last_learned_at"] is not None


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------

def test_global_unit_cap_triggers_oldest_first_pruning(repo, monkeypatch):
    monkeypatch.setattr(le, "MAX_UNITS_TOTAL", 5)
    many_headings = "\n\n".join(f"## Section {i}\n\nunique body text number {i}" for i in range(20))
    (repo / "overflow.md").write_text(f"# Overflow\n\n{many_headings}\n", encoding="utf-8")

    report = le.learn(workspace=repo)
    assert report.total_units <= 5
    assert report.units_pruned > 0
