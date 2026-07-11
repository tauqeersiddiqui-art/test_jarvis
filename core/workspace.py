#core/workspace.py
"""
Active-workspace management + security boundary for codebase search/investigation.

Mirrors the confirmation-free, read-only tool pattern already used by
actions/git_control.py (subprocess as an args list, never shell=True) and
the workspace-escape guard already used by actions/shell_exec.py
(Path.resolve() + is_relative_to()).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

IGNORED_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    "coverage", ".pytest_cache", ".mypy_cache", ".tox", ".cache", "cache",
    ".idea", ".vscode",
})

# Exact filenames that must never have their contents searched, read, or
# sent to AIProvider. Presence/path may still be surfaced (e.g. filename
# search, project-structure listing) — only content is blocked.
SENSITIVE_NAMES = frozenset({
    ".env", ".env.local", ".env.production", ".env.staging",
    "-.keys.env", "api_keys.json", "secrets.json", "credentials.json",
    ".netrc", "id_rsa", "id_ed25519",
})
SENSITIVE_EXTENSIONS = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".jks", ".crt", ".cer", ".der",
})

_WORKSPACE_FILE_NAME = "workspace.json"
MAX_FILES = 20_000  # hard safety cap on any single discovery pass


class WorkspaceError(Exception):
    pass


class PathEscapeError(WorkspaceError):
    pass


class SensitiveFileError(WorkspaceError):
    pass


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _workspace_file() -> Path:
    return _base_dir() / "config" / _WORKSPACE_FILE_NAME


def get_workspace() -> Path:
    """Active workspace root. Defaults to the Mark-XLVIII project folder."""
    wf = _workspace_file()
    if wf.exists():
        try:
            data = json.loads(wf.read_text(encoding="utf-8"))
            path = Path(data.get("path", "")).expanduser()
            if path.is_dir():
                return path.resolve()
        except Exception:
            pass
    return _base_dir().resolve()


def set_workspace(path: str) -> Path:
    """Set + persist the active workspace root. Raises WorkspaceError if invalid."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (_base_dir() / p).resolve()
    else:
        p = p.resolve()
    if not p.exists():
        raise WorkspaceError(f"Path does not exist: {p}")
    if not p.is_dir():
        raise WorkspaceError(f"Not a directory: {p}")

    wf = _workspace_file()
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text(json.dumps({"path": str(p)}, indent=2), encoding="utf-8")
    return p


def is_sensitive(path: Path) -> bool:
    """Content-level guard: is this file's content off-limits?"""
    if path.name in SENSITIVE_NAMES:
        return True
    if path.suffix.lower() in SENSITIVE_EXTENSIONS:
        return True
    return False


def is_ignored_dir(name: str) -> bool:
    if name in IGNORED_DIRS:
        return True
    low = name.lower()
    return low.endswith("cache") or low in ("tmp", "temp")


def resolve_in_workspace(rel_or_abs: str, workspace: Path) -> Path:
    """
    The only correct way to turn a user/agent-supplied path into a real
    filesystem path for search/read operations. Resolves symlinks/`..`
    fully before checking containment (resolving first, then checking,
    is what actually defeats traversal — checking on the unresolved path
    would let a `..`-laden string slip through).
    """
    raw = Path(rel_or_abs) if rel_or_abs else Path(".")
    candidate = raw if raw.is_absolute() else (workspace / raw)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError:
        raise PathEscapeError(
            f"Path '{rel_or_abs}' resolves outside the active workspace ({workspace})."
        )
    return resolved


def _git_ls_files(workspace: Path) -> list[str] | None:
    """
    Gitignore-aware file discovery via `git ls-files` (tracked + untracked-
    but-not-ignored). Returns None if this isn't a git repo or git failed,
    signalling the caller to use the pure-Python fallback walk.
    """
    if not (workspace / ".git").exists():
        return None
    if not shutil.which("git"):
        return None
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=str(workspace), capture_output=True, timeout=15, check=False,
        )
        if completed.returncode != 0:
            return None
        raw = completed.stdout.decode("utf-8", errors="replace")
        return [p for p in raw.split("\x00") if p]
    except Exception:
        return None


def _parse_gitignore(workspace: Path) -> list[tuple[str, bool, bool]]:
    """
    Minimal .gitignore parser for the non-git-repo fallback path. Not a
    full gitignore engine — handles the common cases (comments, blank
    lines, dir-only trailing '/', root-anchored leading '/', simple
    negation) via fnmatch, which covers the overwhelming majority of
    real-world .gitignore files without depending on git itself.
    """
    gi = workspace / ".gitignore"
    if not gi.exists():
        return []
    patterns = []
    try:
        for line in gi.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            if negate:
                line = line[1:]
            dir_only = line.endswith("/")
            if dir_only:
                line = line[:-1]
            anchored = line.startswith("/")
            if anchored:
                line = line[1:]
            patterns.append((line, negate, dir_only))
    except Exception:
        return []
    return patterns


def _gitignore_matches(rel_posix: str, patterns: list[tuple[str, bool, bool]]) -> bool:
    import fnmatch
    ignored = False
    parts = rel_posix.split("/")
    for pattern, negate, _dir_only in patterns:
        hit = fnmatch.fnmatch(rel_posix, pattern) or any(
            fnmatch.fnmatch(part, pattern) for part in parts
        ) or fnmatch.fnmatch(rel_posix, f"*/{pattern}")
        if hit:
            ignored = not negate
    return ignored


def _walk_fallback(workspace: Path) -> list[str]:
    """Pure-Python discovery used when this isn't a git repo (or git failed)."""
    import os
    patterns = _parse_gitignore(workspace)
    out: list[str] = []
    for current, dirs, files in os.walk(workspace, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not is_ignored_dir(d))
        cur_path = Path(current)
        for fname in sorted(files):
            fpath = cur_path / fname
            try:
                rel = fpath.relative_to(workspace).as_posix()
            except ValueError:
                continue
            if patterns and _gitignore_matches(rel, patterns):
                continue
            out.append(rel)
            if len(out) >= MAX_FILES:
                return out
    return out


def list_files(workspace: Path) -> list[str]:
    """
    Gitignore-aware, ignored-dir-aware relative file list for the active
    workspace. Prefers `git ls-files` (perfectly correct .gitignore
    semantics, zero reimplementation risk); falls back to a pure-Python
    walk + minimal .gitignore parser for non-git directories.
    """
    files = _git_ls_files(workspace)
    if files is None:
        files = _walk_fallback(workspace)
    else:
        # Defense in depth: git ls-files can still surface files under
        # directories we always want excluded (e.g. a vendored, un-.git
        # -ignored node_modules committed by mistake).
        files = [
            f for f in files
            if not any(is_ignored_dir(part) for part in Path(f).parts[:-1])
        ]
    return files[:MAX_FILES]
