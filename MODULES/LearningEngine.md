# Module: LearningEngine

**Location:** `core/learning_engine.py`
**Layer:** Core Services

## Purpose

Deterministic knowledge acquisition from this project's own local repository
documentation (`readme.md`, `ROADMAP.md`, `PRODUCT_VISION.md`, `ARCHITECTURE.md`,
`MODULES/*.md`, `DECISIONS/*.md`, `JARVIS_STATE.md`, any other `*.md` file) and
bounded source-code docstrings (module/class/function, via AST). Gives future Mark
capabilities a small, bounded, queryable knowledge store — "what does Mark already
know about X" — without re-reading the whole repository or spending an AI provider
call.

## What this module explicitly is NOT

Not autonomous or background learning, not a file-watcher or scheduler, not internet
access or web crawling, not self-modification, not a capability-installation
mechanism, and not wired into any existing pipeline (`actions/dev_agent.py`,
`core/coding_orchestrator.py`) — v1 is a standalone Core Service. It never calls an
AI provider and never modifies source files.

## Responsibilities

- Discover every `*.md` and `*.py` file in the active workspace via
  `core/workspace.py`'s existing gitignore-aware `list_files()` (same primitive
  `actions/codebase_search.py` uses).
- Detect new or changed source documents via a whole-file content hash manifest —
  `learn()` only re-processes a file whose hash differs from what was last recorded,
  or that is new.
- Remove knowledge units belonging to files that have since been deleted from the
  workspace, so the store never goes stale.
- Extract small, bounded knowledge units:
  - Markdown files are split into sections on `#`/`##`/`###` headings; each section
    becomes one unit (title + truncated body).
  - Python files yield one unit per module docstring and per top-level
    class/function docstring (via `ast.get_docstring()`) — nested symbols and full
    function bodies are never read.
- Deduplicate identical content by a content hash: if the same extracted text already
  exists as a unit (copied across two docs, or unchanged after a file edit touches an
  unrelated section), the existing unit's `source_paths`/`updated_at` is updated
  instead of creating a duplicate.
- Persist atomically and enforce bounded retention (`MAX_UNITS_PER_FILE = 40`,
  `MAX_UNITS_TOTAL = 800`, oldest-`updated_at`-first pruning over the global cap).
- Expose a deterministic, no-LLM word-overlap `search()` over the stored units.

## Public Interface

- `learn(workspace: Path | None = None) -> LearnReport` — the only write path.
  `LearnReport`: `files_scanned, files_changed, files_removed, units_added,
  units_deduplicated, units_pruned, total_units, duration_ms`.
- `search(query: str, limit: int = 5) -> list[KnowledgeUnit]` — ranked by title-word
  overlap (weighted 2x) plus body-word overlap, then most-recently-updated.
- `get_unit(unit_id: str) -> KnowledgeUnit | None`
- `stats() -> dict` — `{"total_units", "total_files_tracked", "last_learned_at"}`.
- `KnowledgeUnit` fields: `unit_id, content_hash, source_type
  ("markdown_section" | "docstring"), source_paths, section_title, summary
  (truncated to `MAX_SUMMARY_CHARS = 400`), first_seen_at, updated_at`.

## Dependencies

- `core/workspace.py` — `get_workspace()`, `list_files()` (discovery), `is_sensitive()`
  (every candidate file is checked before its content is ever read — `.env`,
  credentials, private keys, etc. are never scanned).
- Standard library only (`ast`, `hashlib`, `json`, `re`, `tempfile`, `dataclasses`).
  No AI provider call anywhere in this module.
- Persistence follows `core/engineering_memory.py`'s exact convention: single JSON
  file under `config/state/` (`learning_engine.json`), atomic writes (temp file in the
  same directory + `fsync` + `os.replace`), fail-safe load (missing/corrupt file or
  corrupt individual entries never raise).

## Limitations

- Never stores full file contents or full function bodies — only bounded, truncated
  section/docstring summaries (`MAX_SUMMARY_CHARS = 400`).
- Change detection is whole-file-hash based, not line-level — editing any part of a
  large document reprocesses the entire file's sections, not just the changed one.
- Search ranking is deterministic word-overlap, not semantic — differently-worded
  content describing the same idea will not rank together unless their words overlap.
- `learn()` must be invoked explicitly; nothing in this repository calls it
  automatically yet (no scheduler, no file-watch, no wiring into `dev_agent.py` or
  `coding_orchestrator.py` — that integration is future work, not part of v1).
- Persistence failures are swallowed (`learn()` logs rather than raising) so a
  knowledge-write problem can never interrupt a caller — this means a caller cannot
  rely on `learn()` raising to detect a persistence failure.

## Future Direction

- `PRODUCT_VISION.md` Track J (Continuous Learning) and Track H (Engineering
  Experience Engine): this module is the first concrete "what does Mark already know"
  primitive either Track could build on. Any future evolution (semantic ranking,
  auto-triggering on file change, wiring into `dev_agent.py`'s planning step) must
  remain deterministic and reviewable, per `PRODUCT_VISION.md`'s standing constraint
  on Track J — no silent behavior rewrites. See `ROADMAP.md` Phase 1's "Learning
  Engine v1" entry for why this is a distinct addition and not a reinterpretation of
  Phase 4 (Engineering Experience Engine, still not started).
