#actions/codebase_search.py
"""
Read-only codebase search & discovery.

No write operations exist in this module. No confirmation gate is required
(mirrors git_control's read-only actions: status/diff/log fall through
immediately with no gate).
"""
from __future__ import annotations

import ast
import fnmatch
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core import workspace as ws

MAX_OUTPUT_CHARS = 8000
MAX_FILE_READ_CHARS = 4000
DEFAULT_MAX_RESULTS = 100

# Binary-ish extensions — never attempted for text/content search.
_BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".flac",
    ".zip", ".tar", ".gz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".pyd", ".pyc", ".class", ".jar",
    ".pdf", ".woff", ".woff2", ".ttf", ".eot",
    ".db", ".sqlite", ".sqlite3",
})

REDACTED = "[REDACTED: sensitive file — content not accessible]"


@dataclass
class SearchResult:
    file: str
    line: int | None
    match_type: str   # filename | literal | regex | class_def | function_def | import | reference | structure | config | entry_point | test
    matched: str
    snippet: str
    score: float = 0.0

    def format(self) -> str:
        loc = f"{self.file}:{self.line}" if self.line else self.file
        snip = self.snippet.strip()
        head = f"[{self.match_type}] {loc}  ({self.matched})"
        return f"{head}\n    {snip}" if snip else head


def _trim(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... output truncated ..."


def _is_text_searchable(path: Path) -> bool:
    if path.suffix.lower() in _BINARY_EXTENSIONS:
        return False
    if ws.is_sensitive(path):
        return False
    return True


def _read_text(path: Path, max_chars: int = MAX_FILE_READ_CHARS) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > 2_000_000:
            return None
        data = path.read_bytes()
        if b"\x00" in data[:2048]:
            return None
        return data.decode("utf-8", errors="replace")[:max_chars]
    except Exception:
        return None


# ── Filename search ──────────────────────────────────────────────────────
def search_filenames(workspace: Path, query: str, mode: str = "partial") -> list[SearchResult]:
    """mode: exact | partial | extension | glob"""
    query = (query or "").strip()
    if not query:
        return []
    results: list[SearchResult] = []
    q_low = query.lower()
    for rel in ws.list_files(workspace):
        name = Path(rel).name
        hit = False
        if mode == "exact":
            hit = name.lower() == q_low
        elif mode == "extension":
            ext = query if query.startswith(".") else f".{query}"
            hit = name.lower().endswith(ext.lower())
        elif mode == "glob":
            hit = fnmatch.fnmatch(rel, query) or fnmatch.fnmatch(name, query)
        else:
            hit = q_low in name.lower()
        if hit:
            score = 1.0 if mode == "exact" else (0.8 if name.lower().startswith(q_low) else 0.5)
            results.append(SearchResult(rel, None, "filename", query, "", score))
    results.sort(key=lambda r: -r.score)
    return results


# ── Text search: literal + regex, ripgrep-first with Python fallback ────
def _rg_binary() -> str | None:
    return shutil.which("rg")


def _search_with_rg(
    rg_bin: str, workspace: Path, query: str, regex: bool,
    case_sensitive: bool, max_results: int,
) -> list[SearchResult] | None:
    cmd = [
        rg_bin, "--line-number", "--with-filename", "--color", "never",
        "--no-messages", "--max-count", str(max(1, max_results)),
    ]
    if not regex:
        cmd.append("--fixed-strings")
    if not case_sensitive:
        cmd.append("-i")
    for d in sorted(ws.IGNORED_DIRS):
        cmd.extend(["-g", f"!{d}/**"])
    cmd.extend(["-g", "!*.env*", "-g", "!api_keys.json", "-g", "!*.pem", "-g", "!*.key"])
    cmd.append(query)
    cmd.append(str(workspace))
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=8, check=False,
        )
    except Exception:
        return None
    if completed.returncode not in (0, 1):
        return None

    line_re = re.compile(r"^(.*?):(\d+):(.*)$")
    results: list[SearchResult] = []
    for line in completed.stdout.splitlines():
        m = line_re.match(line)
        if not m:
            continue
        raw_path, lineno, content = m.group(1), int(m.group(2)), m.group(3)
        try:
            rel = Path(raw_path).resolve().relative_to(workspace.resolve()).as_posix()
        except Exception:
            continue
        if ws.is_sensitive(workspace / rel):
            continue  # extra guard beyond the -g excludes above
        results.append(SearchResult(
            rel, lineno, "regex" if regex else "literal", query,
            content.strip()[:300], 1.0,
        ))
        if len(results) >= max_results:
            break
    return results


def _search_python(
    workspace: Path, query: str, regex: bool, case_sensitive: bool,
    max_results: int, path_filter: str | None = None,
) -> list[SearchResult]:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query if regex else re.escape(query), flags)
    except re.error as e:
        return [SearchResult("", None, "error", query, f"Invalid regex: {e}", 0.0)]

    results: list[SearchResult] = []
    for rel in ws.list_files(workspace):
        if path_filter and path_filter not in rel:
            continue
        p = workspace / rel
        if not _is_text_searchable(p):
            continue
        text = _read_text(p, max_chars=200_000)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                results.append(SearchResult(
                    rel, i, "regex" if regex else "literal", query,
                    line.strip()[:300], 1.0,
                ))
                if len(results) >= max_results:
                    return results
    return results


def search_text(
    workspace: Path, query: str, regex: bool = False, case_sensitive: bool = False,
    max_results: int = DEFAULT_MAX_RESULTS, path_filter: str | None = None,
) -> list[SearchResult]:
    query = query or ""
    if not query:
        return []
    rg_bin = _rg_binary()
    if rg_bin and not path_filter:
        results = _search_with_rg(rg_bin, workspace, query, regex, case_sensitive, max_results)
        if results is not None:
            return results
    return _search_python(workspace, query, regex, case_sensitive, max_results, path_filter)


# ── Symbol search: class / function / import / reference ────────────────
def search_symbol(
    workspace: Path, symbol: str, kind: str = "any", max_results: int = DEFAULT_MAX_RESULTS,
) -> list[SearchResult]:
    """kind: class | function | import | reference | any"""
    symbol = (symbol or "").strip()
    if not symbol:
        return []
    results: list[SearchResult] = []

    if kind in ("class", "function", "import", "any"):
        for rel in ws.list_files(workspace):
            if not rel.endswith(".py"):
                continue
            p = workspace / rel
            if not _is_text_searchable(p):
                continue
            text = _read_text(p, max_chars=200_000)
            if text is None:
                continue
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if kind in ("class", "any") and isinstance(node, ast.ClassDef) and node.name == symbol:
                    results.append(SearchResult(rel, node.lineno, "class_def", symbol, f"class {node.name}(...)", 1.0))
                elif kind in ("function", "any") and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
                    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                    results.append(SearchResult(rel, node.lineno, "function_def", symbol, f"{prefix} {node.name}(...)", 1.0))
                elif kind in ("import", "any") and isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == symbol or alias.asname == symbol:
                            suffix = f" as {alias.asname}" if alias.asname else ""
                            results.append(SearchResult(rel, node.lineno, "import", symbol, f"import {alias.name}{suffix}", 0.9))
                elif kind in ("import", "any") and isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == symbol or alias.asname == symbol:
                            results.append(SearchResult(rel, node.lineno, "import", symbol, f"from {node.module or ''} import {alias.name}", 0.9))
            if len(results) >= max_results:
                break

    if kind in ("reference", "any"):
        refs = _search_python(workspace, rf"\b{re.escape(symbol)}\b", regex=True, case_sensitive=True, max_results=max_results)
        for r in refs:
            r.match_type = "reference"
        results.extend(refs)

    results.sort(key=lambda r: -r.score)
    return results[:max_results]


# ── Entry points / config files / related tests / structure ─────────────
_ENTRY_POINT_NAMES = frozenset({
    "main.py", "app.py", "server.py", "index.js", "index.ts",
    "cli.py", "__main__.py", "manage.py",
})
_MAIN_GUARD_RE = re.compile(r'if\s+__name__\s*==\s*[\'"]__main__[\'"]')


def find_entry_points(workspace: Path) -> list[SearchResult]:
    best: dict[str, SearchResult] = {}
    for rel in ws.list_files(workspace):
        p = workspace / rel
        name = Path(rel).name
        if name in _ENTRY_POINT_NAMES:
            best[rel] = SearchResult(rel, None, "entry_point", name, "matched common entry-point filename", 0.7)
        if p.suffix == ".py" and _is_text_searchable(p):
            text = _read_text(p, max_chars=50_000)
            if text and _MAIN_GUARD_RE.search(text):
                if rel not in best or best[rel].score < 0.9:
                    best[rel] = SearchResult(rel, None, "entry_point", "__main__ guard", "contains if __name__ == '__main__':", 0.9)
    return sorted(best.values(), key=lambda r: -r.score)


_CONFIG_NAME_PATTERNS = (
    "*.json", "*.toml", "*.ini", "*.cfg", "*.yaml", "*.yml", ".env*",
    "requirements*.txt", "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "Dockerfile", "docker-compose*.yml", "*.config.js", "*.config.ts",
)


def find_config_files(workspace: Path) -> list[SearchResult]:
    results = []
    for rel in ws.list_files(workspace):
        name = Path(rel).name
        if any(fnmatch.fnmatch(name, pat) for pat in _CONFIG_NAME_PATTERNS):
            results.append(SearchResult(rel, None, "config", name, "", 0.6))
    return results


def find_related_tests(workspace: Path, target: str) -> list[SearchResult]:
    """target: a source file path OR a bare symbol/function/class name."""
    target = (target or "").strip()
    if not target:
        return []
    stem = Path(target).stem if ("/" in target or "\\" in target or target.endswith(".py")) else target
    candidates = {f"test_{stem}.py", f"{stem}_test.py", f"test_{stem.lower()}.py"}
    results = []
    for rel in ws.list_files(workspace):
        name = Path(rel).name
        parts = Path(rel).parts
        is_test_path = (
            name.startswith("test_") or name.endswith("_test.py")
            or (len(parts) > 1 and parts[0].lower() in ("test", "tests"))
        )
        if not is_test_path:
            continue
        score = 0.0
        if name in candidates:
            score = 1.0
        else:
            p = workspace / rel
            if _is_text_searchable(p):
                text = _read_text(p, max_chars=50_000) or ""
                if stem and stem in text:
                    score = 0.6
        if score > 0:
            results.append(SearchResult(rel, None, "test", stem, "", score))
    results.sort(key=lambda r: -r.score)
    return results


def project_structure(workspace: Path, max_depth: int = 3, max_entries: int = 300) -> str:
    tree: dict = {}
    for rel in ws.list_files(workspace):
        parts = Path(rel).parts
        if len(parts) > max_depth:
            parts = parts[:max_depth] + ("...",)
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node.setdefault("__files__", []).append(parts[-1])

    lines: list[str] = []

    def _walk(node: dict, prefix: str = ""):
        dirs = sorted(k for k in node.keys() if k != "__files__")
        fnames = sorted(set(node.get("__files__", [])))
        for d in dirs:
            lines.append(f"{prefix}{d}/")
            _walk(node[d], prefix + "  ")
        for f in fnames:
            lines.append(f"{prefix}{f}")

    _walk(tree)
    out = "\n".join(lines[:max_entries])
    if len(lines) > max_entries:
        out += f"\n... ({len(lines) - max_entries} more entries truncated)"
    return out or "(empty workspace)"


def read_files(workspace: Path, paths: list[str], max_chars_each: int = MAX_FILE_READ_CHARS) -> list[dict]:
    """Bounded multi-file read. Sensitive files are redacted, never read."""
    out = []
    for rel in paths:
        try:
            p = ws.resolve_in_workspace(rel, workspace)
        except ws.PathEscapeError as e:
            out.append({"file": rel, "content": f"[BLOCKED: {e}]"})
            continue
        if ws.is_sensitive(p):
            out.append({"file": rel, "content": REDACTED})
            continue
        text = _read_text(p, max_chars=max_chars_each)
        out.append({"file": rel, "content": text if text is not None else "[unreadable or binary]"})
    return out


# ── Tool entry point ──────────────────────────────────────────────────────
def codebase_search(parameters: dict, response=None, player=None, session_memory=None) -> str:
    """
    Read-only codebase search dispatcher. No write operations exist here —
    no confirmation gate required, matching git_control's read-only actions.

    Actions: set_workspace, get_workspace, search_filename, search_text,
    search_symbol, find_entry_points, find_config, find_tests, structure,
    read_files.
    """
    params = parameters or {}
    action = (params.get("action") or "").strip().lower()

    if player:
        player.write_log(f"[CodebaseSearch] {action}")

    try:
        if action == "set_workspace":
            new_ws = ws.set_workspace(params.get("path", ""))
            return f"Workspace set to: {new_ws}"

        if action == "get_workspace":
            return f"Active workspace: {ws.get_workspace()}"

        workspace = ws.get_workspace()

        if action == "search_filename":
            results = search_filenames(workspace, params.get("query", ""), params.get("mode", "partial"))
        elif action == "search_text":
            results = search_text(
                workspace, params.get("query", ""),
                regex=str(params.get("regex", "false")).lower() in ("true", "1", "yes"),
                case_sensitive=str(params.get("case_sensitive", "false")).lower() in ("true", "1", "yes"),
                max_results=int(params.get("max_results", DEFAULT_MAX_RESULTS) or DEFAULT_MAX_RESULTS),
            )
        elif action == "search_symbol":
            results = search_symbol(workspace, params.get("symbol", ""), params.get("kind", "any"))
        elif action == "find_entry_points":
            results = find_entry_points(workspace)
        elif action == "find_config":
            results = find_config_files(workspace)
        elif action == "find_tests":
            results = find_related_tests(workspace, params.get("target", ""))
        elif action == "structure":
            return _trim(project_structure(workspace, max_depth=int(params.get("max_depth", 3) or 3)))
        elif action == "read_files":
            paths = params.get("paths", [])
            if isinstance(paths, str):
                paths = [x.strip() for x in paths.split(",") if x.strip()]
            files = read_files(workspace, paths)
            return _trim("\n\n".join(f"--- {f['file']} ---\n{f['content']}" for f in files))
        else:
            return f"Unknown action: '{action}'"

        if not results:
            return "No matches found."
        return _trim("\n".join(r.format() for r in results[:200]))

    except ws.WorkspaceError as e:
        return f"Workspace error: {e}"
    except Exception as e:
        return f"codebase_search '{action}' failed: {e}"
