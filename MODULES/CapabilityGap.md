# Module: CapabilityGap

**Location:** `core/capability_gap.py`
**Layer:** Core Services

## Purpose

Deterministically answers, for a given task/request: does Mark currently expose a
capability for this, or is this a gap? This is the first foundation for
`PRODUCT_VISION.md`'s Capability First Principle and any future controlled
self-learning — it answers "what capability does this task require," "what
capability does Mark currently have," and "what specific capability is missing."

## What this module explicitly is NOT

Not autonomous capability installation, not autonomous research, not
self-modification, not a task executor, and not an LLM call. It never runs the
requested task, never calls an AI provider, never calls `learning_engine.learn()`,
and never modifies any file (including `main.py`, which it only reads).

## Responsibilities

- Build a capability inventory from **real registration evidence**, never a
  hand-maintained list: `build_inventory()` reads `main.py`'s own source text and
  extracts its `TOOL_DECLARATIONS` list via `ast.literal_eval` (never imports or
  executes `main.py`, which has heavy side-effecting imports — audio devices, a live
  GenAI client, a Qt UI — with no place in a small deterministic detector).
- Cross-checks each declared tool name against `main.py`'s tool-dispatch
  `if/elif name == "..."` chain (`has_dispatch_handler`) — a second, independent
  piece of static evidence that a declared capability has a real runtime handler,
  not just a description.
- Normalizes task text and capability name/description text deterministically
  (`_normalize()`: lowercase, tokenize, drop stopwords and single-character tokens —
  same input always yields the same token set regardless of casing/whitespace/word
  order).
- Scores each inventory capability against the task via deterministic token overlap
  — capability-name overlap weighted higher (3x) than description-word overlap (1x)
  — and additionally requires the top match to explain a meaningful share
  (`STRONG_MATCH_MIN_RATIO = 0.34`) of the task's own tokens before it can be
  classified as a confident match. This exists specifically to prevent one
  incidental shared word (e.g. a task about property investment happening to
  contain "status," which collides with `system_status`'s name) from being enough
  to falsely claim a capability exists.
- Classifies the result into exactly one of four states (`confidence`):
  `high` (capability exists), `partial` (partially matches), `none` (missing), or
  `ambiguous` (too few distinguishing terms in the task to classify confidently —
  `MIN_TASK_TOKENS = 2`).
- Consults `core/learning_engine.py`'s existing `search()` as bounded, clearly
  separate background knowledge (`background_knowledge`, capped at
  `MAX_BACKGROUND_KNOWLEDGE_ITEMS = 3`) — **never** part of the match score, and
  never able to upgrade a classification. Learning Engine indexes this project's own
  documentation, including `PRODUCT_VISION.md`'s long-term, unimplemented Tracks;
  describing a future/vision capability in prose is not evidence that capability
  exists today.

## Public Interface

- `detect_gap(task: str, inventory: list[CapabilityRecord] | None = None, consult_knowledge: bool = True) -> GapResult`
  — the single entry point. `inventory` can be supplied directly (used by tests and
  any future caller that already has one); otherwise `build_inventory()` is called.
- `build_inventory(main_source: str | None = None) -> list[CapabilityRecord]` —
  reads the real `main.py` file by default; `main_source` lets a caller (or a test)
  supply source text directly instead.
- `CapabilityRecord`: `name, description, has_dispatch_handler`.
- `GapResult`: `requested_task, required_capability, matched_capabilities,
  missing_capability, gap_detected (bool | None — None only when confidence is
  "ambiguous"), confidence ("high" | "partial" | "none" | "ambiguous"), evidence,
  background_knowledge`.

## Dependencies

- `main.py`'s source text only (read, never imported/executed) — the actual
  registered tool-calling metadata (`TOOL_DECLARATIONS`) and dispatch chain.
- `core/learning_engine.py`'s `search()` (read-only) — see Responsibilities above.
  This module never calls `learn()`.
- Standard library only otherwise (`ast`, `re`, `dataclasses`). No AI provider call
  anywhere in this module.

## Trust / Authority Rule

Capability existence must be proven by current registered/callable implementation
evidence (`main.py`'s `TOOL_DECLARATIONS` + dispatch chain), never by documentation
or vision text. Authority order, highest to lowest:

1. Current registered capability / callable implementation (`main.py` inventory)
2. Current source evidence (dispatch cross-check)
3. Learned knowledge (`core/learning_engine.py` — background only)
4. Product Vision or future documentation (never proof of an implemented capability)

If `PRODUCT_VISION.md` describes a Track (e.g. Track F, Property Intelligence) but
no executable capability for it exists in `main.py`'s inventory, `detect_gap()` must
report a gap (`confidence` of `partial` or `none`, `gap_detected=True`) — Learning
Engine surfacing that Track's text as `background_knowledge` never changes this.

## Limitations

- Matching is deterministic word-overlap, not semantic — a capability described in
  very different wording from the task will not be found, and this is a known,
  accepted limitation of v1 (no LLM call is used for classification, per the
  Capability Gap Detection v1 scope).
- Not wired into `main.py`'s tool dispatch, `core/coding_orchestrator.py`, or any
  request-routing path in this session — it is a standalone, callable service.
  Normal request routing is unchanged.
- `partial` vs `none` vs `high` thresholds (`STRONG_MATCH_MIN_SCORE`,
  `STRONG_MATCH_MIN_RATIO`, `PARTIAL_MATCH_MIN_SCORE`) are fixed constants tuned
  against `main.py`'s current tool descriptions; they are not learned or adjusted
  automatically (per the "no self-modification" constraint on this capability).

## Future Direction

- `PRODUCT_VISION.md`'s Capability First Principle describes an execution-fallback
  order (native capabilities → MCP servers → local tools → browser automation →
  official APIs → plugins → project knowledge → user-approved external services)
  and "record missing capabilities for future learning." This module is the first
  building block for that — it identifies a gap, deterministically and
  reviewably — but does not itself implement a fallback order, a learning task
  queue, or any wiring into request routing. Those remain future, separately
  proposed capabilities.
