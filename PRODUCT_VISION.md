# PRODUCT VISION — MARK XLVIII

Status: Long-term product vision. **Not** an implementation roadmap.

Authority: This document does not replace, reorder, delete, or rewrite any existing
implementation work in this repository. It is appended context: what the project is
ultimately for, and how the tracks described here relate to what is already built.
If a future implementation proposal conflicts with the existing, working coding
migration work in this repository (`core/`, `actions/`, `tests/`), the existing
implementation takes precedence until the user explicitly directs a change.

Created: 2026-07-13. Source: user-provided Product Vision brief ("MARK XLVIII —
Long-term AI Operating System"), reconciled against this repository's actual code
(`core/`, `actions/`, `tests/`, `readme.md`) and, for lineage only, the historical
reference project `D:\All Bots\mini_agent` (JARVIS OS). `mini_agent` is a **frozen
historical reference** — its `PLAN.md`, `ROADMAP.md`, `JARVIS_STATE.md`, and
`memory/master_plan.json` are consulted for context below but are not modified, and no
pointers back to them are added to this repository's own files.

---

## PART 1 — THE VISION (as provided)

### Core Philosophy

Mark is NOT a chatbot. Mark is NOT simply an LLM wrapper. Mark is NOT only a coding
assistant.

Mark is an AI Operating System that continuously assists the user across software
engineering, desktop computing, learning, productivity, and selected real-world
workflows.

The primary competitive advantage is not the underlying LLM. The advantage is:

- Persistent state
- Engineering memory
- Continuous learning
- Desktop integration
- Tool orchestration
- Real-world workflows
- Evidence-driven reasoning
- Long-term project knowledge

### Core Principles

- Never sacrifice stability for new features.
- Every major capability must become: Persistent, Testable, Recoverable, Observable,
  Replaceable.
- Every subsystem should remain modular. No giant monolithic architecture.

### Long-Term Product Areas

**Track A — AI Software Engineer** (current highest priority)
Already contains: CodingTask, Engineering Memory, Evidence Driven Fixing, Rollback,
Impact Analysis, Investigation, Workspace, Code Search, Provider Failover. Continue
improving this first.

**Track B — Desktop AI Operating System**
Persistent desktop assistant: desktop/application/window awareness, clipboard
intelligence, downloads management, file intelligence, notification intelligence,
calendar awareness, context awareness, PC health monitoring, long-running background
services.

**Track C — AI Expert Modes**
Instead of one huge assistant, expose Expert Modes (Software Engineer, Research
Analyst, Property Advisor, Yoga Coach, Teacher, Study Coach, Chief of Staff, Finance
Assistant, Travel Planner, Health Assistant). Future expert modes reuse the same
Memory, Planning, Workspace, Reasoning, and Tool System.

**Track D — AI Coaching Platform** (Expert Mode: Yoga Coach)
Instructional video in Center Core; camera-based real-time pose estimation, voice
correction, skeleton comparison, progress tracking, difficulty adaptation, history.
Future: meditation, stretching, senior fitness, kids fitness, rehabilitation.

**Track E — AI Teaching Platform** (Expert Mode: Teacher)
Center Core plays teaching content; camera checks attention, eye direction, writing
activity, participation, answers; homework review, weak-topic analysis, personal
learning roadmap. Subjects modular.

**Track F — Property Intelligence** (Expert Mode: Property Advisor)
Analyze a property before purchase: collect property details, government portals,
public records, RERA information, development authority information, nearby
infrastructure, legal/public status where available, investment analysis. Output:
Buy / Avoid / Investigate Further / Negotiate. Never hallucinate legal information —
every recommendation must cite evidence.

**Track G — Personal Knowledge System**
Personal knowledge graph auto-organizing projects, documents, meetings, videos,
courses, research, coding decisions, architecture. Semantic search.

**Track H — Engineering Experience Engine**
Engineering Memory evolves into an Experience Engine. Every completed task becomes an
experience: goal, decision, outcome, lessons, confidence. Future attempts reuse
experiences.

**Track I — Project Digital Twin**
Every software project gets a persistent brain: architecture, timeline, dependencies,
major decisions, bug history, feature history, design rationale.

**Track J — Continuous Learning** (highest long-term priority)
Mark improves from its own successful work: better planning, better estimates,
recognizing repeated mistakes, improving coding strategy/investigation/reasoning.
Learning must be deterministic, reviewable, never a silent behavior rewrite.

**Track K — Repository Intelligence**
Background health checks: dead code, unused files, duplicate logic, circular imports,
security issues, architecture drift, dependency drift, regression trends.

**Track L — Research Agent**
Evidence collection, citation comparison, multi-source validation, report generation.

**Track M — Decision Intelligence**
Help make decisions (property, frameworks, laptops, vendors) via risk analysis,
trade-offs, evidence summaries — not just answer questions.

**Track N — India Workflow Assistant** (future, optional)
Government workflows: passport, income tax, GST, document preparation, public service
workflows. Remains optional.

**Track O — Marketplace Architecture**
Every Expert Mode becomes installable: Core + Expert Modules + Shared Memory + Shared
Tool Layer.

### MCP Strategy

Do not install every MCP. Design a layered MCP architecture:
- Layer 1 — Core infrastructure
- Layer 2 — Essential MCPs: Filesystem, Git, Browser, Playwright, Python, SQLite, PDF,
  GitHub, Windows
- Layer 3 — Expert MCPs: Property, Research, Office, OCR, Vision, Calendar, Email
- Layer 4 — Experimental, only after validation

### Open Source Strategy

Before implementing any large subsystem: search GitHub for mature, maintained,
production-ready, compatible-license, strong-community projects. Prefer integrating
mature projects over rewriting everything. Every adoption must document benefits,
risks, maintenance, license, and integration complexity.

### Rules (as provided)

- Never delete previous roadmap items. Never reorder completed phases. Never remove
  accepted migration work. Append this vision after the current roadmap. Keep the
  coding migration as the active implementation roadmap. This document is the Product
  Vision roadmap.
- Future implementation proposals should reference BOTH the Coding Migration Roadmap
  (this repository's actual `core/`/`actions/` implementation) and the Product Vision
  Roadmap (this document).
- Before implementing any new subsystem, identify: which roadmap it belongs to,
  dependencies, expected commercial value, estimated implementation effort, required
  tests, acceptance criteria, risk level.
- If there is any conflict between the Product Vision and the Coding Migration
  Roadmap, the Coding Migration Roadmap takes precedence until explicitly changed by
  the user.

---

## PART 2 — RECONCILIATION WITH MARK XLVIII'S ACTUAL IMPLEMENTATION

This section compares the vision above against this repository's code as it exists
today (2026-07-13). It does not change or move any existing module. It exists so
future proposals can locate a track's real starting point instead of re-deriving it.

### 1. Track A — already real, and confirmed present in this repository

Unlike a document reference, every Track A item names a module that is verifiably
implemented and tested here:

| Track A item | Where it lives | Confirmed |
|---|---|---|
| CodingTask | `core/coding_task.py` — cross-turn/cross-restart continuity for a JARVIS-driven coding project; single-slot state under `config/state/`, `Phase`/`Status` state machine | Present |
| Engineering Memory | `core/engineering_memory.py` — bounded, searchable record of past coding outcomes per project, informs future fix attempts | Present |
| Evidence Driven Fixing | `actions/dev_agent.py` — bounded run → investigate → fix → validate → rollback loop | Present |
| Rollback | `actions/dev_agent.py:_rollback_snapshot` — snapshot-before-write, full undo on write failure or rejected fix | Present |
| Impact Analysis | `actions/impact_analysis.py` | Present |
| Investigation | `actions/investigate.py` | Present |
| Workspace | `core/workspace.py` | Present |
| Code Search | `actions/codebase_search.py` | Present |
| Provider Failover | `core/ai_provider.py:complete_with_failover` — bounded, ordered failover chain (OpenAI-compatible gateway primary, Gemini fallback), recoverable-error classification, no infinite retries | Present |

No reconstruction was needed for Track A — it is a direct, accurate list of modules
that already exist and have corresponding tests (`tests/test_coding_task.py`,
`tests/test_dev_agent.py`, `tests/test_impact_analysis.py`, `tests/test_investigate.py`,
`tests/test_codebase_search.py`, `tests/test_engineering_memory.py`,
`tests/test_workspace.py`, `tests/test_ai_provider.py`). "Continue improving this
first" should be read as: extend these modules in place, do not create parallel
versions under new names.

### 2. Lineage note (context only, not a dependency)

`core/coding_task.py` and `core/engineering_memory.py` each document in their own
module docstrings that they adapt ideas from an earlier prototype's
`missions/`/`self_memory.py` design (the historical reference project at
`D:\All Bots\mini_agent`), reworked into Mark-native primitives — single JSON file,
gitignored, atomic writes, no second execution engine, no LLM calls of their own. This
is noted for lineage only: Mark XLVIII's implementation is the current, active,
maintained version. The historical reference project is not a dependency and is not
modified as part of this work.

### 3. Partially present — Track B (Desktop AI Operating System)

This repository already implements a meaningful slice of Track B, unrelated to any
external port:

| Track B item | Where it lives | Status |
|---|---|---|
| Desktop control (windows, taskbar) | `actions/desktop.py`, `actions/computer_control.py` | Present |
| Application awareness / launching | `actions/open_app.py` | Present |
| OS settings (volume, brightness, WiFi, power) | `actions/computer_settings.py` | Present |
| PC health monitoring | `actions/system_monitor.py` — CPU/RAM/GPU/temperature telemetry with voice alerts | Present |
| Screen/camera vision | `actions/screen_processor.py` | Present, but see §5 below — this module has an architectural issue independent of Track B |
| Browser control | `actions/browser_control.py` | Present |
| File intelligence (read/summarize documents) | `actions/file_processor.py`, `actions/file_controller.py` | Present |
| Proactive check-ins | `actions/proactive.py` — silence-triggered, no hardcoded rules | Present |

Not present anywhere in this repository: clipboard intelligence, downloads management,
notification intelligence, calendar awareness, structured "context awareness" beyond
what `main.py`/memory already assemble, and long-running background services beyond
the existing monitor/reminder pattern. These are the genuine Track B gap — everything
else in Track B's list already has a home.

### 4. Nothing in the vision asks to redo completed work

No Track in the vision proposes replacing `core/ai_provider.py`, `core/workspace.py`,
`core/coding_task.py`, `core/engineering_memory.py`, or `actions/dev_agent.py`'s
existing loop. Tracks A, H, and J extend these rather than duplicate them.

### 5. One existing module needs attention independent of this vision

`actions/screen_processor.py` opens a second, independent Gemini Live audio session
(noted separately from Track B/D/E work, since Tracks D and E both add camera-based
analysis on top of vision). Any future pose-estimation (Track D) or
attention-tracking (Track E) work should route through the existing vision/provider
path rather than add a third independent session — this repository's `core/ai_provider.py`
already establishes the pattern of "one coding/text failover chain, Gemini Live kept
separate and singular" (see its module docstring); a third parallel session would
break that invariant, not just add a duplicate.

### 6. Phases that must never be built as originally patterned elsewhere

Two of this repository's own existing modules are worth flagging before Track E or
Track N build on top of them:

- `actions/send_message.py` — blind `pyautogui` UI automation across WhatsApp/Signal/
  Discord/Instagram. Fragile, breaks on any UI change. Relevant to **Track E**
  (student participation/messaging touches) and **Track N** (government portal
  form-filling): do not extend this pattern to new surfaces. Prefer official APIs, or
  Playwright with explicit selectors, matching the more deliberate approach already
  used in `actions/browser_control.py`.
- `actions/game_updater.py` — niche, Windows-only, screen-pixel UI automation
  (`pywinauto`, `winreg`, `schtasks`). No Track references this; no action needed, but
  it should not be used as a template for future OS-automation Tracks.

### 7. Missing tracks that genuinely provide new value

These have no corresponding module anywhere in this repository today:

- **Track D — AI Coaching Platform** (pose estimation, skeleton comparison, voice
  correction). Fully new capability — no existing module covers real-time pose ML
  inference.
- **Track E — AI Teaching Platform** (attention/eye-direction tracking, homework
  review, weak-topic analysis). Fully new domain, same reasoning as Track D.
- **Track F — Property Intelligence** as a domain. The evidence discipline it demands
  ("never hallucinate legal information, every recommendation must cite evidence") has
  no formal enforcement anywhere in this repository yet — `actions/web_search.py` and
  `actions/investigate.py` return results/findings but neither module currently
  requires or verifies citations before a conclusion is presented. This would need to
  be built, not just reused.
- **Track C — AI Expert Modes**, as a formal, installable persona concept, is new.
  Nothing in `core/prompt.txt` or `main.py` currently exposes more than one assistant
  persona; the tool-routing rules in `core/prompt.txt` are a single flat policy, not a
  per-mode configuration.
- **Track G — Personal Knowledge System**, **Track I — Project Digital Twin**: no
  knowledge-graph or per-project persistent-brain structure exists yet.
  `core/engineering_memory.py` is project-scoped but records only bounded fix/feature
  outcomes, not architecture/timeline/dependency/decision history — a real but
  narrower slice of what Track I asks for.
- **Track K — Repository Intelligence**, beyond what `actions/codebase_search.py` and
  `actions/impact_analysis.py` already do (structural search, blast-radius analysis).
  Dead-code detection, duplicate-logic detection, circular-import detection, security
  scanning, dependency drift, and regression trends are not implemented.
- **Track L — Research Agent**: `actions/web_search.py` already does parallel
  multi-source search (Gemini Grounded + DDG) but does not do citation comparison,
  multi-source validation, or structured report generation — those are the actual gap.
- **Track M — Decision Intelligence**: no existing module frames output as a
  buy/avoid/compare decision with trade-offs; `actions/web_search.py`'s `compare` mode
  is the closest existing primitive and is a narrower price-comparison feature, not a
  general decision framework.
- **Track N — India Workflow Assistant**: fully new, explicitly optional domain. No
  existing module references government-workflow automation.
- **Track O — Marketplace Architecture**: no plugin/module installation mechanism
  exists in this repository — every `actions/*.py` module is built-in and hardcoded
  into `main.py`'s tool routing today.
- **MCP Strategy**: this repository has no MCP client of any kind today — all tools
  are Python functions dispatched directly from `main.py`. Introducing MCP would be a
  new integration layer, not a reorganization of an existing one.

### 8. Tracks that are natural extensions of what's already built

| Track | Extends |
|---|---|
| H — Engineering Experience Engine | `core/engineering_memory.py` (already records bounded outcomes per project) |
| J — Continuous Learning | `core/engineering_memory.py` + `actions/dev_agent.py`'s existing evidence-driven fix loop, which already tracks `rollback_reason`/`failure_category` per attempt |

---

## PART 3 — STANDING RULES FOR FUTURE PROPOSALS

Per the vision brief:

1. Every future implementation proposal must state which roadmap it belongs to: the
   Coding Migration Roadmap (this repository's actual `core/`/`actions/`
   implementation) or this Product Vision roadmap — and, per Part 2 above, which
   existing module (if any) it extends.
2. Every proposal must state: dependencies, expected commercial value, estimated
   implementation effort, required tests, acceptance criteria, risk level.
3. Before implementing any large subsystem, search for mature open-source projects
   first (Open Source Strategy, above) and document benefits/risks/maintenance/
   license/integration complexity for any adoption.
4. If a proposal conflicts with existing, working code in this repository, the
   existing implementation wins until the user explicitly says otherwise.
5. This document itself must not be rewritten wholesale in a future session. New
   Tracks or reconciliation notes are appended; existing Track text is not silently
   edited. Material changes to an existing Track require explicit user direction.
6. `D:\All Bots\mini_agent` is a frozen historical reference. It may be read for
   context or lineage, but its documents are never modified, and this repository's own
   files do not add pointers into it.
7. Every future feature proposal must additionally answer: why does it exist, which
   Product Track, commercial value, dependencies, implementation effort, acceptance
   criteria, required tests, risk level, and existing open-source alternatives (see
   Part 6, Open Source Strategy, below).
8. Whenever proposing a new subsystem: (1) search this repository for an existing
   module that already does it or something close, (2) search for mature open-source
   alternatives, (3) search the historical `mini_agent` reference for a prior design
   that solved the same problem, (4) compare all three, (5) recommend the best
   approach. Never assume building from scratch is the best solution.

---

## PART 4 — NORTH STAR

*Added 2026-07-13, extending the vision above per explicit user request. Does not
replace or reword Parts 1–3.*

### What Mark IS

Mark is a persistent, local-first AI Operating System — a long-running assistant that
accumulates state, memory, and engineering judgment over years of use, and applies that
accumulated context across software engineering, desktop computing, learning,
productivity, and selected real-world workflows.

### What Mark is NOT

- Not a generic chatbot — every session builds on persistent state, not a blank
  context window.
- Not simply an LLM wrapper — the Kernel/orchestration/memory/workspace layers are the
  product; the underlying model is a replaceable component (see `core/ai_provider.py`'s
  existing failover chain — this principle is already implemented, not aspirational).
- Not only a coding assistant — Track A is the current priority, not the ceiling.
- Not an unrestricted autonomous agent — every irreversible or high-risk action
  requires human approval; this is already true of the running system
  (`actions/dev_agent.py`'s bounded fix loop with rollback, `actions/git_control.py`'s
  and `actions/shell_exec.py`'s confirmation gates).
- Not cloud-first — local-first where practical (offline STT/TTS options already exist
  in `core/stt.py`/`core/tts.py`; cloud providers are used explicitly and are
  swappable, not hardcoded).
- Not a replacement for every desktop application — it orchestrates and automates,
  it does not reimplement office suites, IDEs, or specialized software.
- Not a reinvention of mature open-source projects — see Open Source Strategy (Part 6).

### 5-Year Vision

Mark grows from a single-user voice/desktop assistant with a working coding-engineer
subsystem (Track A, current state) into a modular platform: a small permanent core
(memory, provider abstraction, workspace boundary, approval gates) surrounded by
swappable Expert Modes (Track C) that each reuse the same memory, planning, and tool
system, with the option to install additional modes from a marketplace (Track O). By
year 5 the model, the interface, and the individual Expert Modes should all be
replaceable without touching what makes Mark *Mark*: its accumulated project knowledge,
its evidence-driven reasoning discipline, and its approval-gated relationship with the
user's system.

### Core Philosophy

(Restated from Part 1 for one-page reference — not a new statement.) Persistent state,
engineering memory, continuous learning, desktop integration, tool orchestration,
real-world workflows, evidence-driven reasoning, and long-term project knowledge are
the actual competitive advantage — never the choice of underlying model.

### Commercial Objective

Build a personal-productivity platform valuable enough, across enough distinct
domains (engineering, coaching, teaching, research, decisions), that individual Expert
Modes can be packaged and distributed independently (see Commercial Vision, Part 4.3)
without fragmenting the underlying core.

### Success Definition

- Track A remains stable and improves measurably (fewer failed fix attempts, higher
  first-attempt success rate, tracked via `core/engineering_memory.py`'s existing
  outcome recording) as the primary, ongoing measure of product health.
- A new Expert Mode can be added without modifying the Kernel/core layer.
- No subsystem regresses existing tests when a new Track is added
  (`tests/` must stay green).
- The user retains full approval authority over irreversible actions at every stage of
  growth — this is a permanent constraint, not a milestone to eventually relax.

---

## PART 4.1 — NON-GOALS

Explicitly, Mark will **not** become:

- A generic, personality-first chatbot competing on conversational charm rather than
  capability.
- An unrestricted autonomous agent that acts without human approval on irreversible or
  high-risk operations (file deletion, purchases, messages sent on the user's behalf,
  system-level changes, git pushes/commits) — this constraint is permanent, matching
  the approval-gate pattern already enforced in `actions/dev_agent.py`,
  `actions/git_control.py`, and `actions/shell_exec.py`.
- Cloud-first by default — cloud services are used when they provide clear, explicit
  value (e.g. ElevenLabs TTS quality, Gemini Live's real-time voice), never as the only
  option where a local-first alternative is practical.
- A replacement for every desktop application — Mark automates and orchestrates
  existing applications and OS features; it does not aim to reimplement a browser, an
  IDE, an office suite, or a video editor.
- A reinvention of mature, well-maintained open-source projects. Where a strong
  existing project covers a need (STT/TTS engines, browser automation via Playwright,
  vector search, OCR), Mark integrates it rather than rebuilding it (see Open Source
  Strategy, Part 6).
- A platform that silently rewrites its own behavior. Continuous learning (Track J)
  must remain deterministic and reviewable — no undocumented behavior changes from
  self-modification.

---

## PART 4.2 — COMMERCIAL VISION

*Directional only — no implementation implied by this section. Every item still
requires its own proposal per the Standing Rules (Part 3).*

### Target Users

- Individual developers who want a persistent engineering assistant with memory across
  projects and sessions (Track A — current primary user).
- Power users who want a desktop-integrated personal assistant (Track B).
- Domain-specific users who adopt a single Expert Mode without needing the rest of the
  platform (Track C/D/E/F).

### Prospective Editions

| Edition | Audience | Scope idea |
|---|---|---|
| Developer Edition | Individual engineers | Track A + core (current default shape of this repository) |
| Professional Edition | Power users / consultants | Developer Edition + Track B (Desktop AI OS) + a small set of Expert Modes |
| Education Edition | Students, tutoring contexts | Track E (Teacher) + Track G (Personal Knowledge System), scoped for learning use |
| Enterprise Edition | Teams | Shared knowledge (Track G/I), stricter audit/approval requirements, multi-project Track A at scale |

### Marketplace Vision

Track O (Marketplace Architecture) — Expert Modes as installable modules on top of a
shared Core + Shared Memory + Shared Tool Layer. No installation mechanism exists in
this repository today (Part 2 §7 already notes this); this is a direction, not a
scheduled phase.

### Subscription Ideas (directional, unvalidated)

- Free/local tier: Core + Track A running entirely on local/offline providers where
  practical.
- Paid tier(s): cloud-provider access (higher-quality TTS/STT, higher-capability
  models), additional Expert Modes, cross-device sync.

These are ideas for future evaluation, not commitments — nothing here should be read
as scheduled work.

---

## PART 4.3 — TECHNOLOGY PRINCIPLES

Restated as explicit, standalone principles (each already demonstrated somewhere in
this repository's actual code, not aspirational):

1. **Local-first where practical.** `core/stt.py` offers offline Whisper/Vosk;
   `core/tts.py` offers offline Kokoro alongside cloud EdgeTTS/ElevenLabs.
2. **Evidence before conclusions.** `actions/investigate.py` builds evidence from real
   search results only and instructs the provider not to invent facts beyond that
   evidence; `actions/impact_analysis.py` builds its dependency graph from real AST
   parses only, never guessed relationships.
3. **Persistent memory.** `memory/memory_manager.py` for general context;
   `core/coding_task.py` and `core/engineering_memory.py` for project-scoped
   engineering continuity.
4. **Human remains in control.** Approval gates in `actions/dev_agent.py` (rollback on
   rejected/failed attempts), `actions/git_control.py`, `actions/shell_exec.py`, and
   `actions/image_generator.py` (confirmation before billed/dangerous actions).
5. **Security first.** `core/workspace.py`'s resolve-then-contain path boundary and
   sensitive-file content blocking (`.env`, API keys, credentials, private keys,
   certificates) — content is blocked even when a path/filename may still be surfaced.
6. **Privacy first.** `core/engineering_memory.py` and `core/coding_task.py` both
   explicitly enumerate what they never store (API keys, credentials, full source
   contents, full prompts, raw screenshots).
7. **Replaceable components.** `core/ai_provider.py`'s `AIProvider` abstract base +
   bounded failover chain — no code outside that module depends on a specific
   provider.
8. **Modular architecture.** Each `actions/*.py` module is independently focused
   (search, investigation, impact analysis, desktop control, vision, voice) rather than
   one monolithic file; `core/` holds only cross-cutting primitives.

---

## PART 5 — NEW PRODUCT IDEAS (FOR EVALUATION ONLY — DO NOT IMPLEMENT)

These are concepts to evaluate in future sessions, not scheduled work. None of these
should be started without their own explicit proposal, dependency analysis, and user
approval, per the Standing Rules (Part 3).

1. **Digital Chief of Staff** — morning briefing, priority planning, daily summary,
   decision support. Overlaps Track C (Expert Mode: Chief of Staff) and Track M
   (Decision Intelligence). Closest existing primitive: the morning-briefing flow
   already in `main.py`/`readme.md`'s "Morning Briefing" feature — this idea extends
   that into planning/decision support, it does not replace it.
2. **AI Life Timeline** — searchable personal timeline across meetings, documents,
   projects, computer activity. Overlaps Track G (Personal Knowledge System) and
   Track B (Desktop AI Operating System, for computer-activity capture).
3. **Experience Engine** — every completed task becomes reusable experience. This is
   Track H, restated; no separate idea, just a reminder that Track H already covers it.
4. **Project Time Machine** — replay architecture evolution, feature history, bug
   history. Overlaps Track I (Project Digital Twin); would need git history plus
   `core/engineering_memory.py` records as its evidence sources, not a new data source.
5. **Personal Knowledge Graph** — relationships between projects, documents, ideas,
   meetings, people, research. This is Track G, restated.
6. **AI Yoga Coach** — interactive pose correction. This is Track D, restated.
7. **AI Tuition Teacher** — interactive learning with camera feedback. This is
   Track E, restated.
8. **Property Intelligence** — evidence-driven property analysis. This is Track F,
   restated.

Items 3, 5, 6, 7, and 8 above are not new — they restate existing Tracks H, G, D, E,
and F from Part 1. They are listed here only because the source brief listed them
separately; no new Track is created for them.

---

## PART 6 — OPEN SOURCE STRATEGY (EXPANDED)

*Expands the Open Source Strategy stated in Part 1 with an explicit classification
scheme. Does not replace the Part 1 statement.*

Before building any large subsystem, every candidate — this repository's own code,
mature open-source projects, and the historical `mini_agent` reference — must be
classified as one of:

| Classification | Meaning |
|---|---|
| **Integrate** | Use the existing project/library directly with minimal wrapping (e.g. `faster-whisper`, `Kokoro`, `Playwright` — already the pattern used by `core/stt.py`/`core/tts.py`) |
| **Adapt** | Take the useful design idea, rebuild it as a Mark-native primitive, no shared runtime (this is exactly what `core/coding_task.py` and `core/engineering_memory.py` already did with ideas from `mini_agent`'s `missions/`/`self_memory.py` — see `DECISIONS/ADR-005.md`) |
| **Reference** | Read for design inspiration only; do not port code or structure |
| **Reject** | Explicitly do not use — document why (e.g. `actions/send_message.py`'s blind `pyautogui` pattern is already flagged as not to be extended — see `DECISIONS/ADR-004.md`) |

Every subsystem proposal must state which classification applies and why, alongside
the existing requirement (Part 1) to document benefits, risks, maintenance, license,
and integration complexity for any adoption.
