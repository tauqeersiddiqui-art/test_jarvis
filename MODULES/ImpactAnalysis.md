# Module: ImpactAnalysis

**Location:** `actions/impact_analysis.py`
**Layer:** Capability Layer

## Purpose

Deterministic dependency / reverse-dependency impact analysis for a single generated
project's own directory. Answers "if I change these files, what else is likely
affected, and how risky is that?" before `actions/dev_agent.py` commits to a fix or
feature change.

## What this module explicitly is NOT

No LLM is involved anywhere in this module. Every edge in the dependency graph comes
from a real AST parse or a real search result, never an invented/guessed relationship.

## Responsibilities

- Discover Python files in the project (`_discover_python_files()`).
- Parse imports via `ast` (`_python_imports()`) and build a bidirectional dependency
  graph — direct dependencies and reverse dependents (`build_dependency_graph()`).
- Locate relevant tests for a set of primary files (`_find_relevant_tests()`).
- Produce a bounded `ImpactReport`: primary files, direct dependencies, reverse
  dependents, a bounded union of likely-affected files (primary-first), relevant
  tests, and a `risk_level` (`low`/`medium`/`high`) with a reason.

## Public Interface

- `build_impact_report(project_root: Path, primary_files: list) -> ImpactReport`
  — the main entry point.
- `build_dependency_graph(project_root: Path) -> tuple` — lower-level, for callers
  that need the raw graph rather than a bounded report.
- `ImpactReport` dataclass: `.to_dict()` (structured) and `.summary(max_chars=600)`
  (bounded, human-readable — file paths and counts only, never file contents) for
  injecting into an AI edit prompt.

## Dependencies

- `core/workspace.py` — every path is scoped to one `project_root` via the existing
  workspace boundary primitives.
- `actions/codebase_search.py` — reused for file discovery/search rather than
  reimplemented.
- Standard library `ast` only — no third-party dependency graph library.

## Lineage

Adapts the useful part of the historical `mini_agent` reference project's
`self_engineering.py` (a dependency graph + a bounded `ImpactReport` for a proposed
change) into a small Mark-native primitive — see `DECISIONS/ADR-005.md`.

## Limitations

- Python-only import parsing (`ast`) — dependency edges for non-Python files (JS/TS,
  config-driven references, etc.) are not tracked.
- Bounded by design: `MAX_FILES_SCANNED = 500`, `MAX_AFFECTED_FILES = 12`,
  `MAX_TESTS = 5` — a very large project's full blast radius may not fit in one
  report; this trades completeness for a fast, boundedly-sized prompt injection.
- Risk level (`low`/`medium`/`high`) is heuristic, not a formal static-analysis
  guarantee.

## Future Direction

- `PRODUCT_VISION.md` Track K (Repository Intelligence) extends this module's
  existing dependency graph rather than building a second one: dead-code detection,
  duplicate-logic detection, and circular-import detection are all natural queries
  against the graph this module already constructs (see `ROADMAP.md` Phase 3).
