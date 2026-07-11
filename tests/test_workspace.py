from pathlib import Path

import pytest

from core import workspace as ws


def test_set_and_get_workspace_persists(project, tmp_path, monkeypatch):
    wf = tmp_path / "config_state" / "workspace.json"
    monkeypatch.setattr(ws, "_workspace_file", lambda: wf)
    p = ws.set_workspace(str(project))
    assert p == project.resolve()
    assert ws.get_workspace() == project.resolve()


def test_set_workspace_rejects_nonexistent_path(tmp_path, monkeypatch):
    wf = tmp_path / "config_state" / "workspace.json"
    monkeypatch.setattr(ws, "_workspace_file", lambda: wf)
    with pytest.raises(ws.WorkspaceError):
        ws.set_workspace(str(tmp_path / "does_not_exist"))


def test_boundary_enforcement_blocks_relative_traversal(project):
    with pytest.raises(ws.PathEscapeError):
        ws.resolve_in_workspace("../outside.txt", project)


def test_boundary_enforcement_blocks_nested_traversal(project):
    with pytest.raises(ws.PathEscapeError):
        ws.resolve_in_workspace("utils/../../outside.txt", project)


def test_boundary_enforcement_blocks_absolute_outside(project, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    with pytest.raises(ws.PathEscapeError):
        ws.resolve_in_workspace(str(outside), project)


def test_boundary_allows_path_inside_workspace(project):
    resolved = ws.resolve_in_workspace("main.py", project)
    assert resolved == (project / "main.py").resolve()


def test_gitignore_dir_pattern_excludes_files(project):
    files = ws.list_files(project)
    assert "ignored_dir/secret_data.txt" not in files


def test_gitignore_glob_pattern_excludes_files(project):
    files = ws.list_files(project)
    assert "data.secret" not in files


def test_gitignore_does_not_exclude_unrelated_files(project):
    files = ws.list_files(project)
    assert "config.json" in files
    assert "main.py" in files


def test_ignored_dirs_excluded_even_if_not_gitignored(project):
    # node_modules isn't in this fixture's .gitignore at all — it must still
    # be excluded via the hardcoded IGNORED_DIRS defense-in-depth layer.
    files = ws.list_files(project)
    assert not any("node_modules" in f for f in files)


def test_gitignore_fallback_parser_used_without_git(tmp_path):
    root = tmp_path / "nogit"
    root.mkdir()
    (root / ".gitignore").write_text("skip_me.txt\n", encoding="utf-8")
    (root / "skip_me.txt").write_text("x", encoding="utf-8")
    (root / "keep_me.txt").write_text("x", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")

    files = ws.list_files(root)
    assert "keep_me.txt" in files
    assert "skip_me.txt" not in files
    assert not any("__pycache__" in f for f in files)


def test_is_sensitive_by_name():
    assert ws.is_sensitive(Path(".env"))
    assert ws.is_sensitive(Path("config/api_keys.json"))
    assert not ws.is_sensitive(Path("main.py"))


def test_is_sensitive_by_extension():
    assert ws.is_sensitive(Path("server.pem"))
    assert ws.is_sensitive(Path("private.key"))
    assert not ws.is_sensitive(Path("readme.md"))
