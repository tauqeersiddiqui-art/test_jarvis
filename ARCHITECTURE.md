# MARK XLVIII — Architecture

Purpose: overall architecture, subsystem relationships, data flow, high-level
diagrams. **No implementation details** — see `MODULES/*.md` for individual module
internals, and the source files themselves for actual implementation.

Last updated: 2026-07-14

---

## 1. Subsystem Map

```
                        ┌───────────────────────────┐
                        │      Interface Layer       │
                        │   main.py  +  ui.py         │
                        │  (Gemini Live voice loop,   │
                        │   PyQt6 HUD, tool dispatch) │
                        └──────────────┬──────────────┘
                                       │  routes recognized intents/tool calls
                                       ▼
        ┌───────────────────────────────────────────────────────────┐
        │                     Capability Layer                       │
        │                      actions/*.py                          │
        │  (one module per capability — search, investigation,       │
        │   impact analysis, desktop control, vision, browser,        │
        │   web search, reminders, monitoring, messaging, etc.)       │
        └───────┬───────────────────────────────────┬─────────────────┘
                │ uses                              │ uses
                ▼                                   ▼
        ┌───────────────────────┐         ┌───────────────────────────┐
        │     Core Services      │         │     Persistence Layer      │
        │        core/*.py       │◄────────┤        memory/*.py         │
        │ (provider abstraction,  │  reads/  │ (general key-value        │
        │  workspace boundary,    │  writes  │  memory), config/state/   │
        │  coding-task continuity,│         │  (single-slot JSON state:  │
        │  engineering memory,    │         │  coding task, engineering  │
        │  pending actions,       │         │  memory, pending action,   │
        │  STT/TTS engines)       │         │  workspace pointer)        │
        └───────────┬─────────────┘         └───────────────────────────┘
                    │ delegates model calls
                    ▼
        ┌───────────────────────────────────────────┐
        │            AI Provider Layer                │
        │              core/ai_provider.py             │
        │  Gemini / OpenAI-compatible gateway,          │
        │  bounded failover chain (coding/text calls    │
        │  only — Gemini Live voice session is separate │
        │  and untouched by this layer)                 │
        └───────────────────────────────────────────┘

        ┌───────────────────────────────────────────┐
        │         Remote Control Surface (optional)   │
        │              dashboard/server.py             │
        │   FastAPI/uvicorn dashboard, independent of   │
        │   the main voice loop's request path          │
        └───────────────────────────────────────────┘
```

## 2. Layer Responsibilities

- **Interface Layer** (`main.py`, `ui.py`) — owns the live session with the user:
  audio I/O, the HUD, and routing a recognized request to the right capability. This
  is the only layer that talks to the Gemini Live real-time session directly.
- **Capability Layer** (`actions/*.py`) — one focused module per user-facing
  capability. Each module is independently understandable and testable; none of them
  own persistence or model-provider selection themselves — they call into Core
  Services for that.
- **Core Services** (`core/*.py`) — cross-cutting primitives shared by multiple
  capability modules: which AI provider to use (`ai_provider.py`), where the active
  workspace is and what's off-limits inside it (`workspace.py`), what the current
  coding project is and how it got there (`coding_task.py`), what happened the last
  time something like this was attempted (`engineering_memory.py`), and how spoken
  input/output is produced (`stt.py`, `tts.py`).
- **Persistence Layer** (`memory/`, `config/state/`) — general-purpose memory
  (`memory/memory_manager.py`) and the small, single-slot, gitignored JSON state files
  that Core Services read/write for continuity.
- **AI Provider Layer** (`core/ai_provider.py`) — the only place that knows how to
  reach a specific model backend for coding/text completion, and the only place that
  decides to fail over from one provider to another.
- **Remote Control Surface** (`dashboard/`) — an independent, optional surface; it
  does not sit in the critical path of a normal voice/text interaction.

## 3. Data Flow — A Typical Coding Request

```
 User speaks/types a coding request
        │
        ▼
 main.py (Gemini Live session recognizes intent, dispatches tool call)
        │
        ▼
 actions/dev_agent.py (the coding pipeline)
        │
        ├─► core/coding_orchestrator.py — classify the request (new/continue-fix/
        │                                 continue-feature) and route it; delegates
        │                                 back to dev_agent.py's own pipeline
        │                                 functions below for the actual work
        │       └─► core/loop_detector.py — deterministic check: is this task stuck?
        │                                    If so, routing stops here (no pipeline
        │                                    call, no further model call spent)
        ├─► core/coding_task.py       — is there an active task? new vs. continuation?
        ├─► core/workspace.py         — resolve the target path, enforce the boundary
        ├─► actions/codebase_search.py — locate relevant files
        ├─► actions/investigate.py     — gather evidence if this is a fix, not a fresh build
        ├─► actions/impact_analysis.py — what else does this change touch?
        ├─► core/engineering_memory.py — has something like this been tried before?
        ├─► core/ai_provider.py        — one bounded model call (plan/write), with failover
        ├─► (write files, snapshot first)
        ├─► run/validate
        ├─► core/engineering_memory.py — record the outcome (success/failed/rolled_back)
        ├─► on failure/rejection: rollback to the pre-write snapshot
        └─► core/execution_ledger.py    — record one deterministic ledger entry for
                                           this whole call (internal engineering log,
                                           not user-facing memory)
        │
        ▼
 Result narrated back through main.py / ui.py
```

## 4. Data Flow — Vision / Screen Understanding

```
 User asks JARVIS to look at the screen or camera
        │
        ▼
 main.py dispatches to actions/screen_processor.py
        │
        ▼
 A dedicated Gemini Live session captures + describes the image
   (architecturally separate from core/ai_provider.py's coding/text
    failover chain — see ARCHITECTURE §5 and DECISIONS/ADR-006.md)
        │
        ▼
 Description narrated back through main.py / ui.py
```

## 5. Architectural Invariants (must hold across all future work)

- **One coding/text provider chain.** All coding/text completion calls flow through
  `core/ai_provider.py`. No module should construct its own model client for text
  completion.
- **One workspace boundary.** All file path resolution for search/read/write flows
  through `core/workspace.py`'s containment check. No module should resolve paths
  independently.
- **One coding-task record.** `core/coding_task.py` is the single source of truth for
  "what is the current coding project." No module should track this separately.
- **Approval before irreversible action.** Git operations, shell execution, image
  generation (billed), and destructive file operations require explicit user
  confirmation before executing.
- **Gemini Live voice stays singular and separate.** The real-time voice session
  (`main.py`) and the vision session (`actions/screen_processor.py`) are the only
  Gemini Live sessions. A third independent session (e.g. for future pose estimation
  or attention tracking) would violate this invariant — see `DECISIONS/ADR-006.md`.

## 6. Where This Fits Relative to the Documentation Set

- This document describes **structure**, not **status** — for what's done vs. planned,
  see `ROADMAP.md`. For **why** a subsystem exists, see `PRODUCT_VISION.md`. For a
  specific module's public interface, see `MODULES/`. For the reasoning behind a
  specific structural choice, see `DECISIONS/`.
