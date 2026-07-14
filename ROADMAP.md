# MARK XLVIII — Implementation Roadmap

Status: **Current implementation authority.** Defines HOW the project evolves —
implementation phases, dependencies, acceptance criteria, migration phases, testing
requirements, execution order.

Authority relationship: `PRODUCT_VISION.md` defines WHY Mark exists and is never an
implementation checklist. This document is where "why" becomes scheduled, testable
work. Where a Product Vision Track and this roadmap appear to conflict, this roadmap
takes precedence until the user explicitly changes that ordering (see
`PRODUCT_VISION.md` Part 3, rule 4).

Created: 2026-07-13. This document did not exist before this session — it is
reconstructed from this repository's actual, verified implementation (`core/`,
`actions/`, `tests/`), not copied from any other project's roadmap. The historical
`mini_agent` reference project has its own separate `PLAN.md`/`ROADMAP.md` that is
never modified or referenced as an authority here (see `PRODUCT_VISION.md` for the
lineage note).

---

## How to read this document

- **Completed** — implemented, has corresponding tests in `tests/`, verified present in
  this session by direct code inspection.
- **In progress** — partially implemented; gaps noted explicitly.
- **Planned** — not started. Belongs to a Product Vision Track (see `PRODUCT_VISION.md`)
  but has no code yet.

Never delete a completed phase. Never rewrite accepted migration work. Never duplicate
an existing implementation under a new name — extend the existing module.

---

## PHASE 0 — Foundation (COMPLETE)

**Product Vision Track:** A (AI Software Engineer) + B (Desktop AI OS), pre-Track era.

Core JARVIS Live-voice assistant loop, established before Track-based planning began.

| Capability | Module | Status |
|---|---|---|
| Real-time voice (Gemini Live) | `main.py` | Complete |
| Desktop control (keyboard/mouse/windows) | `actions/computer_control.py` | Complete |
| OS settings (volume/brightness/WiFi/power) | `actions/computer_settings.py` | Complete |
| Screen/camera vision | `actions/screen_processor.py` | Complete (see Known Issues below) |
| Web search (Gemini Grounded + DDG, parallel) | `actions/web_search.py` | Complete |
| Weather / flight lookup | `actions/weather_report.py`, `actions/flight_finder.py` | Complete |
| Reminders (OS-native scheduling) | `actions/reminder.py` | Complete |
| System monitoring (CPU/RAM/GPU/temp) | `actions/system_monitor.py` | Complete |
| Proactive check-ins | `actions/proactive.py` | Complete |
| File read/summarize | `actions/file_processor.py`, `actions/file_controller.py` | Complete |
| Git control | `actions/git_control.py` | Complete |
| Shell execution (gated) | `actions/shell_exec.py` | Complete |
| Browser control | `actions/browser_control.py` | Complete |
| Messaging (UI automation) | `actions/send_message.py` | Complete, but see `DECISIONS/ADR-004.md` — not a pattern to extend |
| YouTube playback control | `actions/youtube_video.py` | Complete |
| Game update checks | `actions/game_updater.py` | Complete, niche/low-priority |
| Persistent key-value memory | `memory/memory_manager.py`, `memory/config_manager.py` | Complete |
| Voice engines (STT/TTS) | `core/stt.py` (Whisper, Vosk), `core/tts.py` (EdgeTTS, Kokoro, ElevenLabs) | Complete |
| Desktop HUD (waveform, log, interrupt) | `ui.py` | Complete |
| Telegram notifications | `actions/telegram_notify.py` | Complete |
| Image generation (gated) | `actions/image_generator.py` | Complete |

**Notes:** This is the pre-existing JARVIS assistant that Track-based planning was
layered on top of. No acceptance criteria are retrofitted here — this phase predates
this roadmap document.

---

## PHASE 1 — AI Software Engineer Core (COMPLETE)

**Product Vision Track:** A (AI Software Engineer) — current highest priority per
`PRODUCT_VISION.md`.

**Goal:** Give JARVIS a bounded, evidence-driven coding-engineer subsystem: it can
build a project, investigate failures using real evidence, fix them with a rollback
safety net, and remember what it tried across restarts.

| Deliverable | Module | Tests | Status |
|---|---|---|---|
| Provider abstraction + bounded failover | `core/ai_provider.py` | `tests/test_ai_provider.py` | Complete |
| Workspace boundary + sensitive-file guard | `core/workspace.py` | `tests/test_workspace.py` | Complete |
| Cross-turn/cross-restart coding-task continuity | `core/coding_task.py` | `tests/test_coding_task.py` | Complete |
| Project-scoped engineering memory (past-outcome recall) | `core/engineering_memory.py` | `tests/test_engineering_memory.py` | Complete |
| Read-only codebase search | `actions/codebase_search.py` | `tests/test_codebase_search.py` | Complete |
| Evidence-grounded investigation | `actions/investigate.py` | `tests/test_investigate.py` | Complete |
| Deterministic dependency / impact analysis | `actions/impact_analysis.py` | `tests/test_impact_analysis.py` | Complete |
| Evidence-driven build → fix → validate → rollback loop | `actions/dev_agent.py` | `tests/test_dev_agent.py` | Complete |
| Generic pending-action / slot-filling | `core/pending_action.py` | `tests/test_pending_action.py` | Complete |
| Coding request routing/classification (single entry point for future coding capabilities) | `core/coding_orchestrator.py` | `tests/test_coding_orchestrator.py` | Complete (2026-07-14) |
| Execution Ledger (deterministic internal log of every routed coding operation) | `core/execution_ledger.py` | `tests/test_execution_ledger.py` + integration tests in `tests/test_dev_agent.py` | Complete (2026-07-14) |
| Loop Detection (deterministic stuck-task check before routing to the fix/feature pipeline) | `core/loop_detector.py` | `tests/test_loop_detector.py` + integration tests in `tests/test_coding_orchestrator.py` | Complete (2026-07-14) |
| Learning Engine v1 (deterministic knowledge acquisition from local repository documentation + bounded docstrings) | `core/learning_engine.py` | `tests/test_learning_engine.py` | Complete (2026-07-14) |
| Knowledge-Aware Investigation v1 (first Learning Engine consumer — bounded background knowledge context alongside evidence) | `actions/investigate.py` | `tests/test_investigate.py` | Complete (2026-07-14) |
| Capability Gap Detection v1 (deterministic "does Mark have a capability for this?" check, derived from real `main.py` tool registration) | `core/capability_gap.py` | `tests/test_capability_gap.py` | Complete (2026-07-14) |
| Learning Task Queue v1 (converts a confirmed capability gap into a persistent, deduplicated, bounded-lifecycle learning task — record only, no execution) | `core/learning_task.py` | `tests/test_learning_task.py` | Complete (2026-07-14) |

### Acceptance criteria (met)

- `core/ai_provider.py`: on a recoverable provider error (rate limit, capacity,
  timeout, unsupported model), the failover chain moves to the next configured
  provider; on any other error it raises immediately rather than masking a real bug.
- `core/workspace.py`: `resolve_in_workspace()` rejects any path that resolves outside
  the active workspace after symlink/`..` resolution; `is_sensitive()` blocks content
  access to `.env`, API keys, credentials, and private-key/certificate files by name
  and extension.
- `core/coding_task.py` / `core/engineering_memory.py`: state survives an app restart
  (single small JSON file, atomic write via temp-file + `os.replace`); never persists
  API keys, full source contents, full prompts, or raw screenshots.
- `actions/investigate.py`: evidence context is built only from real search results
  (never the whole repository) and the provider is instructed not to invent facts
  beyond that evidence.
- `actions/impact_analysis.py`: every graph edge comes from a real AST parse or a real
  search result — no LLM involved, no guessed relationships.
- `actions/dev_agent.py`: every fix/feature attempt is snapshotted before writing;
  write failures or rejected fixes trigger a full rollback via
  `_rollback_snapshot()`.

### Coding Orchestrator (added 2026-07-14)

`core/coding_orchestrator.py`'s `decide()` is now the single place that classifies an
incoming coding request (new project / continue-fix / continue-feature / needs
clarification / missing description) and returns a `RoutingDecision`. It was
extracted from logic previously inline in `actions/dev_agent.py`'s `dev_agent()`
entry point — behavior is unchanged (verified: all 218 pre-existing tests still pass
unmodified), and `dev_agent()` still owns dispatching the decision to the existing
pipelines (`_build_project`, `_continue_fix_loop_for_task`,
`_run_incremental_feature_change`), which were not moved or rewritten. Future coding
capabilities should route requests through `decide()` rather than reimplementing
classification or calling `dev_agent.py`'s internal pipelines directly. See
`MODULES/CodingOrchestrator.md`.

### Execution Ledger (added 2026-07-14)

`core/execution_ledger.py`'s `record()`/`entries_for_task()` give a deterministic,
append-only internal log of every `dev_agent()` call — timestamp, task ID, operation
type, routing decision, action performed, files touched, duration, and a result
(success/failure/rollback derived from `CodingTask.status`, see `DECISIONS/ADR-007.md`).
It is an internal engineering log, not user-facing memory, and does not duplicate
`core/engineering_memory.py`'s per-attempt outcome recall. Wired into
`actions/dev_agent.py`'s `dev_agent()` only (one entry per top-level call); no
existing pipeline logic was moved or rewritten. See `MODULES/ExecutionLedger.md`.

### Loop Detection (added 2026-07-14)

`core/loop_detector.py`'s `check_for_loop()` is a deterministic, no-LLM check for
whether a task is stuck (repeated rollback, routing decision, fingerprint, error
signature, or files touched with zero success, or zero successes at all across a
window of attempts). `core/coding_orchestrator.py`'s `decide()` calls it before
routing a `CONTINUE_FIX`/`CONTINUE_FEATURE` request to the existing pipeline; if a
loop is detected, `decide()` returns `Route.LOOP_DETECTED` and no pipeline runs — no
further model call is spent. Nothing in `actions/dev_agent.py`'s pipelines was
touched. See `MODULES/LoopDetector.md` and `DECISIONS/ADR-008.md`.

### Learning Engine v1 (added 2026-07-14)

`core/learning_engine.py`'s `learn()` is a deterministic, no-LLM knowledge-acquisition
pass over this repository's own local documentation (`readme.md`, `ROADMAP.md`,
`PRODUCT_VISION.md`, `ARCHITECTURE.md`, `MODULES/*.md`, `DECISIONS/*.md`,
`JARVIS_STATE.md`, any other `*.md` file) and bounded source-code docstrings
(module/class/function, via AST — never full function bodies). It detects new/changed
files via a whole-file content-hash manifest, extracts small bounded knowledge units
(never full file contents), deduplicates identical content by hash, and persists
atomically (same convention as `core/engineering_memory.py`: one JSON file under
`config/state/`, gitignored, temp-file + `os.replace`). `search()`, `get_unit()`, and
`stats()` give a small deterministic query API. This is a **standalone Core Service in
v1** — it is not wired into `actions/dev_agent.py` or `core/coding_orchestrator.py`,
and it is a distinct addition, **not** a reinterpretation of Phase 4 (Engineering
Experience Engine, below — still not started, still unrelated in scope). It relates
to `PRODUCT_VISION.md` Track J (Continuous Learning) as a first, deterministic
building block future work could extend, per Track J's standing constraint that
learning must remain deterministic and reviewable. See `MODULES/LearningEngine.md`.

### Knowledge-Aware Investigation v1 (added 2026-07-14)

`actions/investigate.py` is the **first consumer** of `core/learning_engine.py` —
every `investigate()` call now also runs a best-effort, read-only `le.search()`
lookup alongside its existing evidence-gathering path, and formats any matches into
a separate, clearly-labeled, bounded `KNOWLEDGE CONTEXT` block (`MAX_KNOWLEDGE_CHARS
= 1500`, well under evidence's `MAX_EVIDENCE_CHARS = 9000`). Evidence remains
authoritative — the system prompt instructs the model that knowledge context is
background only and never overrides evidence, and that neither section is ever an
instruction to obey (a narrow trust-boundary rule scoped to this integration only,
not `PRODUCT_VISION.md`'s unbuilt Track S). This integration never calls `learn()`
— ingestion and consumption stay separate operations in v1 — and is not wired into
`core/coding_orchestrator.py` or `core/engineering_memory.py` in this session. See
`MODULES/Investigation.md`.

### Capability Gap Detection v1 (added 2026-07-14)

`core/capability_gap.py`'s `detect_gap()` is the first foundation for
`PRODUCT_VISION.md`'s Capability First Principle: given a task/request, it
deterministically classifies whether Mark already has a matching capability
(`confidence`: `high` / `partial` / `none` / `ambiguous`), using a capability
inventory built from **real registration evidence** — `main.py`'s own
`TOOL_DECLARATIONS` list and tool-dispatch chain, extracted via AST/regex over
`main.py`'s source text (never imported/executed). `core/learning_engine.py`'s
`search()` is consulted only as bounded, clearly separate background knowledge —
it is never part of the match score and can never upgrade a classification, per
this module's Trust/Authority Rule (Product Vision/documentation text is never
proof of an implemented capability; only `main.py`'s actual registered inventory
counts). Never calls `learn()`, never calls an AI provider, never executes any
capability, never modifies any file. Not wired into `main.py`'s tool dispatch,
`core/coding_orchestrator.py`, or any request-routing path in this session —
normal request routing is unchanged. See `MODULES/CapabilityGap.md`.

### Learning Task Queue v1 (added 2026-07-14)

`core/learning_task.py`'s `create_from_gap()` converts a CONFIRMED capability gap
(`core/capability_gap.py`'s `detect_gap()` result with `gap_detected is True` AND
`confidence == "none"`) into a persistent, bounded, deduplicated `LearningTask` — a
recorded need only, never permission to act. Repeated gaps for the same normalized
missing capability increment `occurrence_count`/`priority` and refresh
`last_seen_at` rather than creating a duplicate task, preserving the original
`task_id`. A minimal, validated status lifecycle (`pending → approved → learning →
completed/failed`, with `failed → pending` re-queue and a terminal `rejected`)
records state transitions only — nothing here executes research, generates code,
or installs anything. Detection and task creation remain strictly separate:
`core/capability_gap.py` has no knowledge of this module, and nothing in this
session wires `create_from_gap()` into `main.py`'s tool dispatch or any
request-routing path. Never calls `learning_engine.learn()`, never calls an AI
provider. See `MODULES/LearningTask.md`.

### Known issues (not blocking, tracked for future work)

- `actions/screen_processor.py` opens a second, independent Gemini Live audio session,
  separate from the coding/text failover chain in `core/ai_provider.py`. This is an
  existing architectural inconsistency, not a regression — flagged in
  `PRODUCT_VISION.md` Part 2 §5 and `DECISIONS/ADR-006.md` as something future
  vision-related work (pose estimation, attention tracking) must not compound by
  adding a third parallel session.

**Continue improving this phase first**, per `PRODUCT_VISION.md` Track A. Extend these
modules in place — do not create parallel implementations under new names.

---

## PHASE 2 — Desktop AI Operating System (IN PROGRESS)

**Product Vision Track:** B (Desktop AI Operating System).

### Already present (built as part of Phase 0, mapped onto Track B)

| Track B capability | Module | Status |
|---|---|---|
| Desktop/window/taskbar control | `actions/desktop.py`, `actions/computer_control.py` | Present |
| Application awareness/launching | `actions/open_app.py` | Present |
| OS settings control | `actions/computer_settings.py` | Present |
| PC health monitoring | `actions/system_monitor.py` | Present |
| Screen/camera vision | `actions/screen_processor.py` | Present (see Phase 1 Known Issues) |
| Browser control | `actions/browser_control.py` | Present |
| File intelligence | `actions/file_processor.py`, `actions/file_controller.py` | Present |
| Proactive, unprompted check-ins | `actions/proactive.py` | Present |

### Genuine gap (not started)

- Clipboard intelligence
- Downloads management
- Notification intelligence
- Calendar awareness
- Structured "context awareness" beyond what `main.py`/memory already assemble at
  request time
- Long-running background services beyond the existing monitor/reminder pattern

### Acceptance criteria (for the gap items, once scoped)

To be defined per-capability when each is individually proposed (see
`PRODUCT_VISION.md` Part 3, Standing Rules). No acceptance criteria are pre-committed
here for unstarted work.

---

## PHASE 3 — Repository Intelligence Extensions (PLANNED)

**Product Vision Track:** K (Repository Intelligence).

**Extends:** `actions/impact_analysis.py`'s existing dependency-graph primitive (which
already does forward/reverse dependency tracing from real AST parses) and
`actions/codebase_search.py`'s existing structural search.

**Deliverables (not started):**
- Dead-code / unused-file detection
- Duplicate-logic detection
- Circular-import detection
- Security-issue scanning
- Dependency-drift tracking
- Regression-trend tracking

**Dependencies:** Phase 1 (Track A core) must remain stable; this phase reads from the
same dependency graph rather than building a second one.

**Acceptance criteria:** To be defined when this phase is explicitly proposed and
scoped, per `PRODUCT_VISION.md` Part 3 rule 7 (every proposal must state dependencies,
effort, tests, acceptance criteria, risk level).

---

## PHASE 4 — Engineering Experience Engine (PLANNED)

**Product Vision Track:** H (Engineering Experience Engine) + J (Continuous Learning).

**Extends:** `core/engineering_memory.py`, which already records bounded outcomes
(`success`/`improved`/`failed`/`rolled_back`) per project and already recalls relevant
past attempts by error-signature and file-overlap scoring. This phase is about turning
that existing recall into forward-looking guidance for planning, not building a new
memory store.

**Constraint carried from `PRODUCT_VISION.md`:** learning must remain deterministic
and reviewable — no silent behavior rewrites. Any change to how `dev_agent.py` uses
recalled records must be inspectable and attributable to a specific rule, not an
opaque model adjustment.

**Status:** Not started. No acceptance criteria committed yet.

---

## PHASE 5 — Expert Mode Platform (PLANNED)

**Product Vision Track:** C (AI Expert Modes) + O (Marketplace Architecture).

**Gap identified in `PRODUCT_VISION.md` Part 2 §7:** nothing in `core/prompt.txt` or
`main.py` currently exposes more than one assistant persona; tool-routing rules are a
single flat policy today, not a per-mode configuration. No plugin/module installation
mechanism exists — every `actions/*.py` module is built-in and hardcoded into
`main.py`'s tool routing.

**Status:** Not started. This is a foundational gap that Tracks D, E, F, and M all
depend on before they can be "modes" rather than one-off features bolted onto the
single existing persona.

---

## PHASE 6 — Domain Expert Modes (PLANNED, EACH INDEPENDENTLY SCOPED)

**Product Vision Tracks:** D (Yoga Coach), E (Teacher), F (Property Intelligence),
M (Decision Intelligence), N (India Workflow Assistant, optional).

**Dependency:** Phase 5 (Expert Mode Platform) should exist first, or each of these
risks being built as a one-off bolt-on to the single existing persona rather than a
reusable mode.

**Status:** Not started. Each Track in this phase requires its own proposal,
dependency analysis, and explicit approval before implementation — none are
pre-approved by their presence in this roadmap.

**Explicit warning carried from `PRODUCT_VISION.md` Part 2 §6:** Track E and Track N
must not extend `actions/send_message.py`'s blind UI-automation pattern to new
surfaces (student messaging, government portal form-filling). See
`DECISIONS/ADR-004.md`.

---

## PHASE 7 — Personal Knowledge System & Project Digital Twin (PLANNED)

**Product Vision Tracks:** G (Personal Knowledge System), I (Project Digital Twin).

**Status:** No knowledge-graph or per-project persistent-brain structure exists today.
`core/engineering_memory.py` is project-scoped but records only bounded fix/feature
outcomes — a real but narrower slice of what Track I eventually asks for. Not started.

---

## PHASE 8 — Research Agent & MCP Integration Layer (PLANNED)

**Product Vision Tracks:** L (Research Agent), MCP Strategy (`PRODUCT_VISION.md`
Part 1).

**Status:** `actions/web_search.py` already does parallel multi-source search but does
not do citation comparison, multi-source validation, or structured report generation —
that is the actual gap for Track L. This repository has no MCP client of any kind
today; all tools are Python functions dispatched directly from `main.py`. Introducing
MCP is a new integration layer, not a reorganization of an existing one, and needs its
own architecture decision (see `DECISIONS/` — no ADR yet, to be written when this phase
is scoped) reconciling an MCP client against the current direct-dispatch tool-routing
model in `main.py`.

---

## Testing Requirements (standing, applies to every phase)

- Every new module must ship with a corresponding `tests/test_<module>.py`.
- No phase is complete until `python -m pytest tests/` passes with no regressions in
  previously completed phases.
- Modules that never call an LLM (e.g. `core/workspace.py`, `actions/impact_analysis.py`,
  `actions/codebase_search.py`) must have fully deterministic tests — no live-provider
  calls in the test suite.

## Execution Order

Phases are numbered for reference, not strict sequencing — Phase 1 (Track A) is the
explicit current priority per `PRODUCT_VISION.md`. Phases 2–8 may be reordered by
explicit user direction; reordering must be recorded here as an edit to this section,
never a silent resequencing.

## Roadmap Rules

- Never delete a completed phase.
- Never rewrite accepted migration work.
- Never duplicate an existing implementation under a new module name — extend the
  existing one (see Phase 1's closing note).
- This roadmap remains implementation authority. `PRODUCT_VISION.md` remains strategic
  authority. Where they conflict, this roadmap wins until the user explicitly changes
  that ordering.
