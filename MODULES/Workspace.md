# Module: Workspace

**Location:** `core/workspace.py`
**Layer:** Core Services

## Purpose

Active-workspace management and the security boundary for every codebase
search/investigation/read operation in this repository. Establishes "which directory
is JARVIS currently allowed to look at" and enforces that no path resolution ever
escapes it.

## Responsibilities

- Track and persist the active workspace root (`get_workspace()`, `set_workspace()`),
  defaulting to the Mark-XLVIII project folder itself.
- Resolve any relative-or-absolute, user/agent-supplied path into a real filesystem
  path safely (`resolve_in_workspace()`), rejecting anything that resolves outside the
  active workspace **after** symlink/`..` resolution — resolving first, then checking
  containment, is what actually defeats traversal.
- Guard sensitive file content: `is_sensitive()` blocks content access to `.env`
  files, API keys, credentials, `.netrc`, private keys, and certificate/key files by
  exact name or extension. Presence/path may still be surfaced (e.g. filename search,
  project-structure listing) — only content is blocked.
- Discover files in the workspace, gitignore-aware (`list_files()`): prefers
  `git ls-files` for perfectly correct `.gitignore` semantics, falls back to a
  pure-Python walk + minimal `.gitignore` parser for non-git directories.

## Public Interface

- `get_workspace() -> Path`, `set_workspace(path: str) -> Path`
- `resolve_in_workspace(rel_or_abs: str, workspace: Path) -> Path` — raises
  `PathEscapeError` on any attempted escape.
- `is_sensitive(path: Path) -> bool`
- `is_ignored_dir(name: str) -> bool`
- `list_files(workspace: Path) -> list[str]` (bounded at `MAX_FILES = 20_000`)
- Exceptions: `WorkspaceError`, `PathEscapeError`, `SensitiveFileError`.

## Dependencies

- Mirrors the confirmation-free, read-only tool pattern already used by
  `actions/git_control.py` (subprocess as an args list, never `shell=True`) and the
  workspace-escape guard already used by `actions/shell_exec.py`
  (`Path.resolve()` + `is_relative_to()`).
- Standard library only, plus `git` as an optional external binary (falls back
  gracefully if absent).

## Consumers

Every module that touches files by path goes through this module first:
`actions/codebase_search.py`, `actions/investigate.py`, `actions/impact_analysis.py`,
`actions/dev_agent.py`.

## Limitations

- `list_files()` is hard-capped at 20,000 files per discovery pass — a safety cap, not
  a completeness guarantee, on very large workspaces.
- The non-git `.gitignore` fallback parser is minimal (comments, blank lines, dir-only
  trailing `/`, root-anchored leading `/`, simple negation via `fnmatch`) — it covers
  the overwhelming majority of real-world `.gitignore` files but is not a full
  gitignore engine.
- `SENSITIVE_NAMES`/`SENSITIVE_EXTENSIONS` are a fixed, hardcoded list — a new secret
  file naming convention not on this list would not be automatically blocked.

## Future Direction

- Every future Expert Mode (Track C) or Repository Intelligence extension (Track K)
  that touches files must route through this module's existing boundary rather than
  resolving paths independently — see `ARCHITECTURE.md` §5 (architectural invariant:
  one workspace boundary) and `DECISIONS/ADR-003.md`.
