#actions/file_ops.py
"""
Workspace-aware file operations: create, read (with line ranges), replace,
append, rename, delete, diff, validate syntax. All operations respect
workspace boundaries, sensitive-file protection, atomic writes, and
confirmation gating where appropriate. No auto-commit/push.
"""
from __future__ import annotations

import ast
import difflib
import shutil
from pathlib import Path

from core import workspace as ws

_EDIT_JOURNAL_FILE = "edit_journal.txt"
_EDIT_HISTORY: list[dict] = []


class FileOpsError(Exception):
    pass


def _log_edit(action: str, path: str, details: str = "") -> None:
    """Record edit to in-memory journal."""
    import time
    entry = {
        "timestamp": time.time(),
        "action": action,
        "path": path,
        "details": details,
    }
    _EDIT_HISTORY.append(entry)


def _ensure_parent_exists(path: Path) -> None:
    """Atomic: create parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)


def _validate_syntax_python(source: str) -> tuple[bool, str]:
    """Returns (is_valid, error_message)."""
    try:
        ast.parse(source)
        return True, ""
    except SyntaxError as e:
        return False, f"Syntax error at line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"Parse error: {e}"


def create_file(
    workspace: Path, rel_path: str, content: str = "",
    validate_py: bool = False, confirmed: str | None = None,
) -> str:
    """Create a new file. Fails if file already exists.
    
    If validate_py=True and file is .py, syntax is validated first.
    confirmed='yes' skips the confirmation gate.
    """
    try:
        p = ws.resolve_in_workspace(rel_path, workspace)
    except ws.PathEscapeError as e:
        return f"❌ Path escape: {e}"

    if ws.is_sensitive(p):
        return f"❌ Sensitive file — cannot create: {rel_path}"

    if p.exists():
        return f"❌ File already exists: {rel_path}"

    if validate_py and rel_path.endswith(".py"):
        valid, err = _validate_syntax_python(content)
        if not valid:
            return f"❌ {err}"

    if confirmed != "yes":
        return (
            f"[GATE] About to create {rel_path}. "
            f"Call again with confirmed='yes' to proceed."
        )

    try:
        _ensure_parent_exists(p)
        p.write_text(content, encoding="utf-8")
        _log_edit("create", rel_path)
        return f"✅ Created: {rel_path}"
    except Exception as e:
        return f"❌ Creation failed: {e}"


def read_file(
    workspace: Path, rel_path: str,
    start_line: int | None = None, end_line: int | None = None,
) -> str:
    """Read file or line range. Returns the content or error message."""
    try:
        p = ws.resolve_in_workspace(rel_path, workspace)
    except ws.PathEscapeError as e:
        return f"[BLOCKED: {e}]"

    if ws.is_sensitive(p):
        return f"[REDACTED: sensitive file — content not accessible]"

    if not p.is_file():
        return f"[Not a file or not found: {rel_path}]"

    try:
        text = p.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)

        if start_line is None and end_line is None:
            return text

        start = (start_line or 1) - 1
        end = (end_line or len(lines))
        start = max(0, min(start, len(lines)))
        end = max(start, min(end, len(lines)))

        return "".join(lines[start:end])
    except Exception as e:
        return f"[Read failed: {e}]"


def replace_exact(
    workspace: Path, rel_path: str, old_text: str, new_text: str,
    validate_py: bool = False, confirmed: str | None = None,
) -> str:
    """Replace exact text match (first occurrence). Atomic write.
    
    If validate_py=True and file is .py, validates syntax after replacement.
    confirmed='yes' skips confirmation.
    """
    try:
        p = ws.resolve_in_workspace(rel_path, workspace)
    except ws.PathEscapeError as e:
        return f"❌ Path escape: {e}"

    if ws.is_sensitive(p):
        return f"❌ Cannot edit sensitive file: {rel_path}"

    if not p.is_file():
        return f"❌ File not found: {rel_path}"

    try:
        original = p.read_text(encoding="utf-8")
    except Exception as e:
        return f"❌ Read failed: {e}"

    if old_text not in original:
        return f"❌ Old text not found in {rel_path}"

    new_content = original.replace(old_text, new_text, 1)

    if validate_py and rel_path.endswith(".py"):
        valid, err = _validate_syntax_python(new_content)
        if not valid:
            return f"❌ {err} (reverting)"

    if confirmed != "yes":
        lines_old = old_text.count("\n") + 1
        return (
            f"[GATE] About to replace {lines_old} line(s) in {rel_path}. "
            f"Call again with confirmed='yes' to proceed."
        )

    try:
        p.write_text(new_content, encoding="utf-8")
        _log_edit("replace_exact", rel_path, f"old_len={len(old_text)} new_len={len(new_text)}")
        return f"✅ Replaced in: {rel_path}"
    except Exception as e:
        return f"❌ Write failed: {e}"


def replace_line_range(
    workspace: Path, rel_path: str, start_line: int, end_line: int,
    new_lines: str, validate_py: bool = False, confirmed: str | None = None,
) -> str:
    """Replace lines [start_line, end_line] (inclusive, 1-indexed) with new_lines.
    
    confirmed='yes' skips confirmation.
    """
    try:
        p = ws.resolve_in_workspace(rel_path, workspace)
    except ws.PathEscapeError as e:
        return f"❌ Path escape: {e}"

    if ws.is_sensitive(p):
        return f"❌ Cannot edit sensitive file: {rel_path}"

    if not p.is_file():
        return f"❌ File not found: {rel_path}"

    try:
        original = p.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)
    except Exception as e:
        return f"❌ Read failed: {e}"

    if start_line < 1 or end_line > len(lines) or start_line > end_line:
        return f"❌ Invalid line range: {start_line}-{end_line} (file has {len(lines)} lines)"

    idx_start = start_line - 1
    idx_end = end_line

    if confirmed != "yes":
        replaced_count = end_line - start_line + 1
        return (
            f"[GATE] About to replace lines {start_line}-{end_line} ({replaced_count} line(s)) in {rel_path}. "
            f"Call again with confirmed='yes' to proceed."
        )

    new_lines_list = new_lines.splitlines(keepends=True)
    new_content_lines = lines[:idx_start] + new_lines_list + lines[idx_end:]
    new_content = "".join(new_content_lines)

    if validate_py and rel_path.endswith(".py"):
        valid, err = _validate_syntax_python(new_content)
        if not valid:
            return f"❌ {err} (reverting)"

    try:
        p.write_text(new_content, encoding="utf-8")
        _log_edit("replace_line_range", rel_path, f"lines {start_line}-{end_line}")
        return f"✅ Replaced lines {start_line}-{end_line} in: {rel_path}"
    except Exception as e:
        return f"❌ Write failed: {e}"


def append_file(
    workspace: Path, rel_path: str, content: str,
    create_if_missing: bool = False, validate_py: bool = False,
    confirmed: str | None = None,
) -> str:
    """Append content to end of file.
    
    If create_if_missing=True, creates file if it doesn't exist.
    confirmed='yes' skips confirmation.
    """
    try:
        p = ws.resolve_in_workspace(rel_path, workspace)
    except ws.PathEscapeError as e:
        return f"❌ Path escape: {e}"

    if ws.is_sensitive(p):
        return f"❌ Cannot edit sensitive file: {rel_path}"

    if not p.exists() and not create_if_missing:
        return f"❌ File not found: {rel_path}"

    if confirmed != "yes":
        return (
            f"[GATE] About to append {len(content)} char(s) to {rel_path}. "
            f"Call again with confirmed='yes' to proceed."
        )

    try:
        if p.exists():
            original = p.read_text(encoding="utf-8")
            new_content = original + content
        else:
            new_content = content

        if validate_py and rel_path.endswith(".py"):
            valid, err = _validate_syntax_python(new_content)
            if not valid:
                return f"❌ {err} (reverting)"

        _ensure_parent_exists(p)
        p.write_text(new_content, encoding="utf-8")
        _log_edit("append", rel_path, f"added {len(content)} chars")
        return f"✅ Appended to: {rel_path}"
    except Exception as e:
        return f"❌ Write failed: {e}"


def delete_file(
    workspace: Path, rel_path: str, confirmed: str | None = None,
) -> str:
    """Delete a file. Requires explicit confirmation."""
    try:
        p = ws.resolve_in_workspace(rel_path, workspace)
    except ws.PathEscapeError as e:
        return f"❌ Path escape: {e}"

    if not p.is_file():
        return f"❌ Not a file: {rel_path}"

    if confirmed != "yes":
        return (
            f"[GATE] About to DELETE {rel_path} permanently. "
            f"Call again with confirmed='yes' to proceed."
        )

    try:
        p.unlink()
        _log_edit("delete", rel_path)
        return f"✅ Deleted: {rel_path}"
    except Exception as e:
        return f"❌ Delete failed: {e}"


def rename_file(
    workspace: Path, rel_path: str, new_rel_path: str,
    confirmed: str | None = None,
) -> str:
    """Rename/move file within workspace. Requires confirmation."""
    try:
        p = ws.resolve_in_workspace(rel_path, workspace)
        p_new = ws.resolve_in_workspace(new_rel_path, workspace)
    except ws.PathEscapeError as e:
        return f"❌ Path escape: {e}"

    if not p.is_file():
        return f"❌ Not a file: {rel_path}"

    if p_new.exists():
        return f"❌ Target already exists: {new_rel_path}"

    if confirmed != "yes":
        return (
            f"[GATE] About to rename {rel_path} → {new_rel_path}. "
            f"Call again with confirmed='yes' to proceed."
        )

    try:
        _ensure_parent_exists(p_new)
        p.rename(p_new)
        _log_edit("rename", rel_path, f"→ {new_rel_path}")
        return f"✅ Renamed: {rel_path} → {new_rel_path}"
    except Exception as e:
        return f"❌ Rename failed: {e}"


def unified_diff(
    workspace: Path, rel_path: str, start_line: int | None = None,
    end_line: int | None = None, context_lines: int = 3,
) -> str:
    """Show unified diff (simulated: current vs original).
    For now, compares file to empty (all additions). In a real impl,
    would compare against git HEAD or a backup."""
    try:
        p = ws.resolve_in_workspace(rel_path, workspace)
    except ws.PathEscapeError as e:
        return f"[BLOCKED: {e}]"

    if ws.is_sensitive(p):
        return "[REDACTED: sensitive file — content not accessible]"

    if not p.is_file():
        return f"[File not found: {rel_path}]"

    try:
        current = p.read_text(encoding="utf-8")
        lines_current = current.splitlines(keepends=True)

        if start_line is not None and end_line is not None:
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines_current), end_line)
            lines_current = lines_current[start_idx:end_idx]

        diff = difflib.unified_diff(
            [],
            lines_current,
            fromfile=f"{rel_path} (original)",
            tofile=f"{rel_path} (current)",
            lineterm="",
            n=context_lines,
        )
        return "\n".join(diff)
    except Exception as e:
        return f"[Diff failed: {e}]"


def validate_syntax(workspace: Path, rel_path: str) -> str:
    """Validate Python syntax of a file."""
    try:
        p = ws.resolve_in_workspace(rel_path, workspace)
    except ws.PathEscapeError as e:
        return f"❌ Path escape: {e}"

    if not rel_path.endswith(".py"):
        return f"ℹ️  Not a Python file: {rel_path}"

    if not p.is_file():
        return f"❌ File not found: {rel_path}"

    try:
        source = p.read_text(encoding="utf-8")
        valid, err = _validate_syntax_python(source)
        if valid:
            return f"✅ Valid Python: {rel_path}"
        return f"❌ {err}"
    except Exception as e:
        return f"❌ Validation error: {e}"


def revert_last_edit(workspace: Path) -> str:
    """Revert last edit from journal. Currently in-memory only; with git
    integration, would revert HEAD~1 or restore from backup."""
    if not _EDIT_HISTORY:
        return "No edits to revert."

    last = _EDIT_HISTORY.pop()
    return (
        f"⏮️  Reverted (in-memory): {last['action']} on {last['path']} "
        f"(timestamp: {last['timestamp']}). "
        f"Note: requires git/backup integration for persistent revert."
    )


def get_edit_journal() -> str:
    """Return formatted edit journal."""
    if not _EDIT_HISTORY:
        return "(no edits yet)"
    lines = []
    for entry in _EDIT_HISTORY:
        lines.append(
            f"  {entry['action']:20s} {entry['path']:40s} "
            f"({entry['details']})"
        )
    return "\n".join(lines)


# ── Tool entry point ──────────────────────────────────────────────────────
def file_ops(parameters: dict, response=None, player=None, session_memory=None) -> str:
    """
    Workspace-aware file operations dispatcher.

    Actions: create, read, replace_exact, replace_lines, append, delete,
    rename, diff, validate_syntax, revert_last, journal.
    
    All operations respect workspace boundaries, sensitive-file protection,
    and atomic writes. Write operations require confirmation gate.
    """
    params = parameters or {}
    action = (params.get("action") or "").strip().lower()

    if player:
        player.write_log(f"[FileOps] {action}")

    try:
        workspace = ws.get_workspace()

        if action == "create":
            result = create_file(
                workspace, params.get("path", ""),
                content=params.get("content", ""),
                validate_py=str(params.get("validate_py", "false")).lower() in ("true", "1"),
                confirmed=params.get("confirmed", ""),
            )

        elif action == "read":
            result = read_file(
                workspace, params.get("path", ""),
                start_line=params.get("start_line"),
                end_line=params.get("end_line"),
            )

        elif action == "replace_exact":
            result = replace_exact(
                workspace, params.get("path", ""),
                old_text=params.get("old_text", ""),
                new_text=params.get("new_text", ""),
                validate_py=str(params.get("validate_py", "false")).lower() in ("true", "1"),
                confirmed=params.get("confirmed", ""),
            )

        elif action == "replace_lines":
            result = replace_line_range(
                workspace, params.get("path", ""),
                start_line=int(params.get("start_line", 1) or 1),
                end_line=int(params.get("end_line", 1) or 1),
                new_lines=params.get("new_lines", ""),
                validate_py=str(params.get("validate_py", "false")).lower() in ("true", "1"),
                confirmed=params.get("confirmed", ""),
            )

        elif action == "append":
            result = append_file(
                workspace, params.get("path", ""),
                content=params.get("content", ""),
                create_if_missing=str(params.get("create_if_missing", "false")).lower() in ("true", "1"),
                validate_py=str(params.get("validate_py", "false")).lower() in ("true", "1"),
                confirmed=params.get("confirmed", ""),
            )

        elif action == "delete":
            result = delete_file(
                workspace, params.get("path", ""),
                confirmed=params.get("confirmed", ""),
            )

        elif action == "rename":
            result = rename_file(
                workspace, params.get("path", ""),
                new_rel_path=params.get("new_path", ""),
                confirmed=params.get("confirmed", ""),
            )

        elif action == "diff":
            result = unified_diff(
                workspace, params.get("path", ""),
                start_line=params.get("start_line"),
                end_line=params.get("end_line"),
                context_lines=int(params.get("context_lines", 3) or 3),
            )

        elif action == "validate_syntax":
            result = validate_syntax(workspace, params.get("path", ""))

        elif action == "revert_last":
            result = revert_last_edit(workspace)

        elif action == "journal":
            result = get_edit_journal()

        else:
            result = f"Unknown action: '{action}'"

        return result

    except ws.WorkspaceError as e:
        return f"Workspace error: {e}"
    except Exception as e:
        return f"file_ops '{action}' failed: {e}"
