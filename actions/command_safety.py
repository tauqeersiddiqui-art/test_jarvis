#command_safety.py
"""
Conservative risk classifier for PowerShell/git commands before JARVIS runs them.

Ported from a sibling project's permission model. Pure stdlib, no framework
dependencies — classifies a command as "safe" (run immediately), "workspace"
(mutates state, ask the user to confirm), or "dangerous" (destructive/system-
level, always ask the user to confirm).
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

SAFE = "safe"
WORKSPACE = "workspace"
DANGEROUS = "dangerous"

_SAFE_COMMANDS = frozenset({
    "cat", "compileall", "dir", "findstr", "gc", "gci", "get-childitem",
    "get-content", "get-date", "get-location", "ipconfig", "ls",
    "measure-object", "netstat", "pwd", "pytest", "rg", "select-object",
    "select-string", "sls", "sort-object", "tasklist", "type", "where",
    "where-object",
})

_WORKSPACE_COMMANDS = frozenset({
    "add-content", "clear-content", "copy", "copy-item", "cp", "git", "md",
    "mkdir", "move", "move-item", "mv", "new-item", "ni", "out-file",
    "pop-location", "push-location", "ren", "rename-item", "set-content",
})

_DANGEROUS_COMMANDS = frozenset({
    "bcdedit", "cmdkey", "del", "diskpart", "erase", "format", "icacls",
    "netsh", "reg", "regedit", "rd", "remove-item", "remove-itemproperty",
    "remove-netipaddress", "remove-netroute", "restart-computer",
    "restart-service", "ri", "rm", "rmdir", "sc", "schtasks", "route",
    "disable-netadapter", "enable-netadapter", "new-itemproperty",
    "new-netipaddress", "new-netroute", "set-dnsclientserveraddress",
    "set-itemproperty", "set-netadapter", "set-netipaddress",
    "set-netipinterface", "set-netroute", "set-service", "setx", "shutdown",
    "stop-computer", "stop-process", "stop-service", "takeown", "taskkill",
})

_SAFE_GIT_SUBCOMMANDS = frozenset({"diff", "fetch", "log", "show", "status"})
_DANGEROUS_GIT_SUBCOMMANDS = frozenset({"clean", "filter-branch", "gc", "rebase", "reflog", "reset"})

_PACKAGE_MANAGERS = frozenset({
    "choco", "npm", "pip", "pip3", "pnpm", "python", "python.exe", "py",
    "scoop", "uv", "winget", "yarn",
})

_PYTHON_EXECUTABLE_RE = re.compile(r"(?:^|[\\/])(?:python|python\d*)(?:\.exe)?$", re.IGNORECASE)


def assess_shell_command(command: str, workspace: Path | None = None) -> tuple[str, str]:
    """Classify a PowerShell command. Returns (level, reason)."""
    command = (command or "").strip()
    if not command:
        return WORKSPACE, "empty shell command"

    if _contains_credential_change(command):
        return DANGEROUS, "credential change"

    segments = [s.strip() for s in re.split(r"\s*(?:;|\|\||&&|\|)\s*", command) if s.strip()]
    if not segments:
        return WORKSPACE, "unparsed shell command"

    levels, reasons = [], []
    for segment in segments:
        level, reason = _assess_segment(segment, workspace)
        if level == DANGEROUS:
            return level, reason
        levels.append(level)
        reasons.append(reason)

    if levels and all(level == SAFE for level in levels):
        return SAFE, "read-only shell command"

    return WORKSPACE, "; ".join(dict.fromkeys(reasons)) or "shell command may change workspace"


def _assess_segment(segment: str, workspace: Path | None) -> tuple[str, str]:
    tokens = _split_command(segment)
    if not tokens:
        return WORKSPACE, "unparsed shell segment"

    command = _normalize_command_token(tokens[0])
    args = [_strip_quotes(t) for t in tokens[1:]]
    has_redirection = ">" in segment or "<" in segment

    if command in _DANGEROUS_COMMANDS:
        return DANGEROUS, f"dangerous command: {command}"
    if _touches_registry(tokens):
        return DANGEROUS, "registry edit"
    if _is_package_uninstall(command, args):
        return DANGEROUS, "package uninstall"
    if command == "git":
        return _assess_git_command(args)
    if _is_python_command(tokens):
        if has_redirection:
            return WORKSPACE, "shell redirection may write output"
        return SAFE, "test or compile command"
    if command in _SAFE_COMMANDS:
        if has_redirection:
            return WORKSPACE, "shell redirection may write output"
        return SAFE, f"safe command: {command}"
    if command in _WORKSPACE_COMMANDS:
        if _has_path_outside_workspace(tokens[1:], workspace):
            return DANGEROUS, "workspace command targets path outside workspace"
        return WORKSPACE, f"workspace command: {command}"
    if _has_path_outside_workspace(tokens[1:], workspace):
        return DANGEROUS, "command references path outside workspace"

    return WORKSPACE, f"unrecognized command: {command or tokens[0]}"


def _assess_git_command(args: list[str]) -> tuple[str, str]:
    visible = [a.lower() for a in args if a and not a.startswith("-c")]
    if not visible:
        return SAFE, "git status by default"
    subcommand = visible[0]

    if subcommand == "remote" and visible[1:] in (["-v"], ["--verbose"]):
        return SAFE, "git remote listing"

    if subcommand == "branch":
        if any(a in {"-d", "-D", "--delete"} for a in visible[1:]):
            return WORKSPACE, "git branch delete"
        if len(visible) == 1 or all(a in {"-a", "--all", "-v", "-vv", "--list"} for a in visible[1:]):
            return SAFE, "git branch listing"
        return WORKSPACE, "git branch mutation"

    if subcommand in _SAFE_GIT_SUBCOMMANDS:
        return SAFE, f"safe git {subcommand}"
    if subcommand in _DANGEROUS_GIT_SUBCOMMANDS:
        return DANGEROUS, f"dangerous git {subcommand}"
    if subcommand == "commit" and any(a == "--amend" for a in visible[1:]):
        return DANGEROUS, "git history rewrite"
    if subcommand == "checkout" and any(a in {"--", "-f", "--force"} for a in visible[1:]):
        return DANGEROUS, "destructive git checkout"
    if subcommand == "push" and any(a in {"--force", "-f", "--force-with-lease"} for a in visible[1:]):
        return DANGEROUS, "force push"
    if subcommand == "push" and any(a in {"--delete", "-d"} or a.startswith(":") for a in visible[1:]):
        return DANGEROUS, "remote ref deletion"
    if subcommand == "config" and any(_looks_like_credential_token(a) for a in visible[1:]):
        return DANGEROUS, "credential change"
    if subcommand == "credential":
        return DANGEROUS, "credential change"

    return WORKSPACE, f"git {subcommand}"


def _split_command(segment: str) -> list[str]:
    try:
        tokens = shlex.split(segment, posix=False)
    except ValueError:
        tokens = segment.split()
    tokens = [_strip_quotes(t) for t in tokens if t.strip()]
    while tokens and tokens[0] in {"&", "call"}:
        tokens = tokens[1:]
    return tokens


def _normalize_command_token(token: str) -> str:
    token = _strip_quotes(token).strip().replace("`", "")
    return token.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _strip_quotes(value: str) -> str:
    return value.strip().strip("'\"")


def _is_python_command(tokens: list[str]) -> bool:
    if not tokens:
        return False
    command = _strip_quotes(tokens[0])
    normalized = _normalize_command_token(command)
    is_python = normalized in {"python", "python.exe", "py"} or bool(_PYTHON_EXECUTABLE_RE.search(command))
    if not is_python:
        return False
    lowered = [_strip_quotes(t).lower() for t in tokens[1:]]
    return len(lowered) >= 2 and lowered[0] == "-m" and lowered[1] in {"pytest", "compileall"}


def _is_package_uninstall(command: str, args: list[str]) -> bool:
    lowered = [a.lower() for a in args]
    if command not in _PACKAGE_MANAGERS:
        return False
    if command in {"python", "python.exe", "py"}:
        return len(lowered) >= 3 and lowered[:2] == ["-m", "pip"] and lowered[2] == "uninstall"
    return any(a in {"uninstall", "remove"} for a in lowered)


def _touches_registry(tokens: list[str]) -> bool:
    lowered = [_strip_quotes(t).lower() for t in tokens]
    if any(t.startswith(("hklm:", "hkcu:", "registry::")) for t in lowered):
        return True
    return bool(lowered) and lowered[0] in {"new-itemproperty", "remove-itemproperty", "set-itemproperty"}


def _contains_credential_change(command: str) -> bool:
    lowered = command.lower()
    change_markers = ("setx", "cmdkey", "git config", "set-itemproperty", "new-itemproperty")
    return _looks_like_credential_token(lowered) and any(m in lowered for m in change_markers)


def _looks_like_credential_token(value: str) -> bool:
    lowered = value.lower()
    credential_markers = (
        "api_key", "apikey", "authorization", "credential", "credentials",
        "password", "secret", "token", "[environment]::setenvironmentvariable",
    )
    return any(marker in lowered for marker in credential_markers)


def _has_path_outside_workspace(tokens: list[str], workspace: Path | None) -> bool:
    if workspace is None:
        return False
    try:
        root = workspace.resolve()
    except OSError:
        return False

    for raw in tokens:
        token = _strip_quotes(raw)
        if not token or token.startswith("-"):
            continue
        try:
            path = Path(token)
        except (OSError, ValueError):
            continue
        if not path.is_absolute():
            if not any(part == ".." for part in path.parts):
                continue
            path = root / path
        try:
            path.resolve().relative_to(root)
        except (OSError, ValueError):
            return True
    return False
