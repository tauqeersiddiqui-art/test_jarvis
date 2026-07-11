#git_control.py
"""
Git operations tool, ported from a sibling project.

Read-only ops (status, diff, log, branch listing) run immediately.
Write/network ops (commit, push, pull, fetch, clone, checkout, branch
create/delete) require confirmed=yes, mirroring the confirmation pattern
already used for restart/shutdown in actions/computer_settings.py.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

MAX_OUTPUT_CHARS = 6000
TIMEOUT_DEFAULT = 30
TIMEOUT_NETWORK = 60
TIMEOUT_CLONE = 120

_CONFIRM_VALUES = {"yes", "true", "1", "confirm"}
_WRITE_ACTIONS = {"commit", "add_all_commit", "push", "pull", "fetch", "clone", "checkout"}


def _get_base_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _trim(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n... output truncated ..."


def _resolve_workspace(params: dict) -> Path:
    raw = str(params.get("path", "") or "").strip()
    if not raw:
        return _get_base_dir()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = _get_base_dir() / candidate
    return candidate if candidate.is_dir() else _get_base_dir()


def _git(workspace: Path, *args: str, timeout: int = TIMEOUT_DEFAULT) -> str:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=str(workspace), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        parts = [f"Exit code: {completed.returncode}"]
        if stdout:
            parts.append(f"stdout:\n{stdout}")
        if stderr:
            parts.append(f"stderr:\n{stderr}")
        return _trim("\n".join(parts))
    except FileNotFoundError as e:
        return f"Error: git not found - {e}"
    except subprocess.TimeoutExpired:
        return f"Error: git command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def git_control(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    params = parameters or {}
    action = str(params.get("action", "status")).strip().lower().replace("-", "_") or "status"
    workspace = _resolve_workspace(params)
    confirmed = str(params.get("confirmed", "")).lower() in _CONFIRM_VALUES

    if player:
        player.write_log(f"[Git] {action}")

    if not (workspace / ".git").exists() and action != "clone":
        return f"'{workspace}' is not a git repository."

    is_branch_write = action == "branch" and (params.get("create") or params.get("delete"))
    if (action in _WRITE_ACTIONS or is_branch_write) and not confirmed:
        return (
            f"This will run 'git {action}', which changes the repository or contacts a remote. "
            f"Please confirm by calling again with confirmed=yes."
        )

    if action == "status":
        return _git(workspace, "status", "--short")

    if action == "diff":
        staged = str(params.get("staged", "")).lower() in _CONFIRM_VALUES
        return _git(workspace, "diff", "--cached") if staged else _git(workspace, "diff")

    if action == "log":
        n = int(params.get("n", 10) or 10)
        return _git(workspace, "log", f"-{n}", "--oneline")

    if action == "commit":
        message = str(params.get("message", "")).strip()
        if not message:
            return "No commit message provided."
        stage_result = _git(workspace, "add", "-u")
        if "Exit code: 0" not in stage_result:
            return f"git add failed:\n{stage_result}"
        return _git(workspace, "commit", "-m", message)

    if action == "add_all_commit":
        message = str(params.get("message", "")).strip()
        if not message:
            return "No commit message provided."
        stage_result = _git(workspace, "add", ".")
        if "Exit code: 0" not in stage_result:
            return f"git add failed:\n{stage_result}"
        return _git(workspace, "commit", "-m", message)

    if action == "push":
        remote = str(params.get("remote", "origin")).strip() or "origin"
        branch = str(params.get("branch", "")).strip()
        args = ["push", remote] + ([branch] if branch else [])
        return _git(workspace, *args, timeout=TIMEOUT_NETWORK)

    if action == "pull":
        remote = str(params.get("remote", "origin")).strip() or "origin"
        branch = str(params.get("branch", "")).strip()
        args = ["pull", remote] + ([branch] if branch else [])
        return _git(workspace, *args, timeout=TIMEOUT_NETWORK)

    if action == "fetch":
        remote = str(params.get("remote", "origin")).strip() or "origin"
        return _git(workspace, "fetch", remote, timeout=TIMEOUT_NETWORK)

    if action == "branch":
        create = str(params.get("create", "")).strip()
        delete = str(params.get("delete", "")).strip()
        if create:
            return _git(workspace, "checkout", "-b", create)
        if delete:
            return _git(workspace, "branch", "-d", delete)
        return _git(workspace, "branch", "-a")

    if action == "checkout":
        branch = str(params.get("branch", "")).strip()
        if not branch:
            return "No branch specified."
        return _git(workspace, "checkout", branch)

    if action == "clone":
        url = str(params.get("url", "")).strip()
        if not url:
            return "No repository URL provided."
        directory = str(params.get("directory", "")).strip()
        args = ["clone", url] + ([directory] if directory else [])
        return _git(workspace, *args, timeout=TIMEOUT_CLONE)

    return f"Unknown git_control action: '{action}'."
