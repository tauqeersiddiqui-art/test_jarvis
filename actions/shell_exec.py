#shell_exec.py
"""
General-purpose shell/Python execution tool, ported from a sibling project.

Runs PowerShell commands or workspace-relative Python files. Every command is
classified by actions/command_safety.py first — "safe" commands run
immediately, anything "workspace" or "dangerous" asks the model to re-call
with confirmed=yes, mirroring the confirmation pattern already used for
restart/shutdown in actions/computer_settings.py.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from actions.command_safety import DANGEROUS, SAFE, WORKSPACE, assess_shell_command

MAX_OUTPUT_CHARS = 6000
DEFAULT_TIMEOUT = 120

_CONFIRM_VALUES = {"yes", "true", "1", "confirm"}


def _get_base_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _trim(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n... output truncated ..."


def _run(args: list[str], cwd: Path, timeout: int) -> str:
    try:
        completed = subprocess.run(
            args, cwd=str(cwd), capture_output=True, text=True,
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
        return f"Error: command not found - {e}"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def _resolve_workspace(params: dict) -> Path:
    raw = str(params.get("path", "") or "").strip()
    if not raw:
        return _get_base_dir()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = _get_base_dir() / candidate
    return candidate if candidate.is_dir() else _get_base_dir()


def shell_exec(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    params = parameters or {}
    action = str(params.get("action", "run_command")).strip().lower() or "run_command"
    confirmed = str(params.get("confirmed", "")).lower() in _CONFIRM_VALUES
    workspace = _resolve_workspace(params)

    if player:
        player.write_log(f"[Shell] {action}")

    if action == "run_python":
        file = str(params.get("file", "")).strip()
        if not file:
            return "No Python file specified."
        target = (workspace / file).resolve()
        try:
            target.relative_to(workspace.resolve())
        except ValueError:
            return f"Error: {file} is outside the workspace."
        if not target.is_file():
            return f"Error: {file} not found."
        if not confirmed:
            return (
                f"This will run '{file}' with the Python interpreter. "
                f"Please confirm by calling again with confirmed=yes."
            )
        args = params.get("args") or []
        if isinstance(args, str):
            args = args.split()
        cmd = [sys.executable, str(target), *[str(a) for a in args]]
        return _run(cmd, workspace, int(params.get("timeout", DEFAULT_TIMEOUT)))

    if action != "run_command":
        return f"Unknown shell_exec action: '{action}'."

    command = str(params.get("command", "")).strip()
    if not command:
        return "No command specified."

    level, reason = assess_shell_command(command, workspace)
    if level in (DANGEROUS, WORKSPACE) and not confirmed:
        qualifier = "is potentially destructive" if level == DANGEROUS else "may change the workspace"
        return (
            f"This command {qualifier} ({reason}): '{command}'. "
            f"Please confirm by calling again with confirmed=yes."
        )

    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
    return _run(cmd, workspace, int(params.get("timeout", DEFAULT_TIMEOUT)))
