#actions/impact_analysis.py
"""
Deterministic dependency / reverse-dependency impact analysis for a single
generated project's own directory. No LLM involved anywhere in this module
— every edge in the graph comes from a real AST parse or a real search
result, never an invented/guessed relationship.

Adapts the useful part of mini_agent's self_engineering.py (a dependency
graph + a bounded ImpactReport for a proposed change) into a small
Mark-native primitive, strictly scoped to one project_root via
core.workspace's existing boundary primitives, and reusing
actions.codebase_search rather than reimplementing file discovery/search.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from core import workspace as ws
from actions import codebase_search as cs

MAX_FILES_SCANNED  = 500
MAX_AFFECTED_FILES = 12
MAX_TESTS          = 5


@dataclass
class ImpactReport:
    primary_files: list         = field(default_factory=list)
    direct_dependencies: list   = field(default_factory=list)   # files primary_files import
    reverse_dependents: list    = field(default_factory=list)   # files that import primary_files
    likely_affected_files: list = field(default_factory=list)   # bounded union, primary-first
    relevant_tests: list        = field(default_factory=list)
    risk_level: str  = "low"    # "low" | "medium" | "high"
    risk_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "primary_files": list(self.primary_files),
            "direct_dependencies": list(self.direct_dependencies),
            "reverse_dependents": list(self.reverse_dependents),
            "likely_affected_files": list(self.likely_affected_files),
            "relevant_tests": list(self.relevant_tests),
            "risk_level": self.risk_level,
            "risk_reason": self.risk_reason,
        }

    def summary(self, max_chars: int = 600) -> str:
        """Short, bounded, human-readable summary for an AI edit prompt —
        file paths and counts only, never file contents."""
        lines = [
            f"Primary target(s): {', '.join(self.primary_files) or '(none)'}",
            f"Direct dependencies: {', '.join(self.direct_dependencies) or '(none)'}",
            f"Reverse dependents (files that depend on the targets): {', '.join(self.reverse_dependents) or '(none)'}",
            f"Risk: {self.risk_level} — {self.risk_reason}",
        ]
        if self.relevant_tests:
            lines.append(f"Relevant tests: {', '.join(self.relevant_tests)}")
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def _module_name_for(rel_path: str) -> str:
    """'utils/helpers.py' -> 'utils.helpers' (dotted module path)."""
    p = Path(rel_path)
    return ".".join(list(p.parts[:-1]) + [p.stem])


def _python_imports(source: str) -> list:
    """Best-effort AST-based import extraction — dotted module names (e.g.
    'utils.helpers', 'os'). Never raises on unparseable source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names: list = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
    return names


def _discover_python_files(project_root: Path) -> list:
    return [f for f in ws.list_files(project_root) if f.endswith(".py")][:MAX_FILES_SCANNED]


def build_dependency_graph(project_root: Path) -> tuple:
    """(direct, reverse) dependency graphs for Python files under
    project_root, keyed by project-relative path. Bounded to
    MAX_FILES_SCANNED files. Non-Python files are not graphed — there is no
    reliable, dependency-free import parser for JS/TS/etc. here (see real
    limitations); codebase_search's text/reference search is still used for
    those files at the evidence-gathering stage, just not graphed.
    """
    py_files = _discover_python_files(project_root)
    module_to_path = {_module_name_for(f): f for f in py_files}
    stem_to_paths: dict = {}
    for f in py_files:
        stem_to_paths.setdefault(Path(f).stem, []).append(f)

    direct: dict = {f: set() for f in py_files}
    for f in py_files:
        try:
            full_path = ws.resolve_in_workspace(f, project_root)
            source = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for imp in _python_imports(source):
            if imp in module_to_path and module_to_path[imp] != f:
                direct[f].add(module_to_path[imp])
                continue
            # Fallback: "from utils import helpers" (module='utils') or
            # "import utils.helpers as h" — match by first/last dotted
            # segment against known file stems (same heuristic already
            # used by investigate.py's _local_imports follow-up search).
            for segment in (imp.split(".")[0], imp.split(".")[-1]):
                for candidate in stem_to_paths.get(segment, []):
                    if candidate != f:
                        direct[f].add(candidate)

    reverse: dict = {f: set() for f in py_files}
    for f, deps in direct.items():
        for d in deps:
            reverse.setdefault(d, set()).add(f)

    return (
        {k: sorted(v) for k, v in direct.items()},
        {k: sorted(v) for k, v in reverse.items()},
    )


def _find_relevant_tests(project_root: Path, primary_files: list) -> list:
    tests: list = []
    for f in primary_files:
        try:
            results = cs.find_related_tests(project_root, Path(f).stem)
        except Exception:
            results = []
        for r in results:
            if r.file not in tests and r.file not in primary_files:
                tests.append(r.file)
    return tests[:MAX_TESTS]


def build_impact_report(project_root: Path, primary_files: list) -> ImpactReport:
    """Deterministic, bounded impact summary for a proposed change scoped
    to `primary_files`. likely_affected_files is primary-first, then direct
    dependencies, then reverse dependents (the ones most likely to break
    from an unexpected change), bounded to MAX_AFFECTED_FILES.
    """
    primary_files = [f for f in dict.fromkeys(primary_files) if f]  # dedupe, preserve order
    direct_graph, reverse_graph = build_dependency_graph(project_root)

    primary_set = set(primary_files)
    direct_deps: set = set()
    reverse_deps: set = set()
    for f in primary_files:
        direct_deps |= set(direct_graph.get(f, []))
        reverse_deps |= set(reverse_graph.get(f, []))
    direct_deps -= primary_set
    reverse_deps -= primary_set

    likely_affected = list(primary_files)
    for f in sorted(direct_deps) + sorted(reverse_deps):
        if f not in likely_affected and len(likely_affected) < MAX_AFFECTED_FILES:
            likely_affected.append(f)

    if reverse_deps:
        risk_level = "high" if len(reverse_deps) > 2 else "medium"
        risk_reason = f"{len(reverse_deps)} file(s) depend on the target file(s) and could break."
    elif direct_deps:
        risk_level = "medium"
        risk_reason = "Target file(s) depend on other project files; changes may ripple outward."
    else:
        risk_level = "low"
        risk_reason = "No other project files depend on, or are depended on by, the target file(s)."

    return ImpactReport(
        primary_files=primary_files,
        direct_dependencies=sorted(direct_deps),
        reverse_dependents=sorted(reverse_deps),
        likely_affected_files=likely_affected[:MAX_AFFECTED_FILES],
        relevant_tests=_find_relevant_tests(project_root, primary_files),
        risk_level=risk_level,
        risk_reason=risk_reason,
    )
