# Module: Investigation

**Location:** `actions/investigate.py`
**Layer:** Capability Layer

## Purpose

Iterative, evidence-grounded codebase investigation: search → inspect → refine →
reason. Answers open-ended questions like "find where X is used," "trace how X
reaches Y," or "find the code responsible for this bug" without letting the model
invent facts beyond what was actually found in the codebase.

## Responsibilities

- Extract search keywords from a natural-language question, preferring
  identifier-shaped tokens (snake_case/camelCase/CONST) and quoted phrases over plain
  stopword-filtered words (`_extract_keywords()`).
- Gather evidence from real search results only — never the whole repository
  (`_gather_evidence()`), then rank and deduplicate results (`_rank_and_dedupe()`).
- Assemble a bounded, file:line-grounded evidence context (`_assemble_bounded_context()`,
  capped at `MAX_EVIDENCE_CHARS = 9000`) and hand it to the AI provider exactly once,
  with strict instructions not to invent facts beyond the evidence.

## Public Interface

- `investigate(parameters: dict, response=None, player=None, session_memory=None, speak=None) -> str`
  — the single entry point invoked by the tool-dispatch layer.

Everything else in this module is a private helper supporting that one entry point.

## Dependencies

- `core/workspace.py` — path resolution/boundary for any file access.
- `actions/codebase_search.py` — the actual search mechanics; this module is a
  consumer, not a reimplementation, of search.
- `core/ai_provider.py`'s `complete_with_failover()` — exactly one bounded call per
  investigation. This module does **not** implement provider selection or failover
  itself.

## Limitations

- Evidence context is bounded (`MAX_EVIDENCE_CHARS = 9000`, `MAX_SNIPPET_CHARS = 1200`,
  `MAX_PRIMARY_FILES = 6`, `MAX_FOLLOWUP_FILES = 4`) — a question whose answer requires
  more context than fits in these bounds will get a partial, best-effort answer rather
  than an exhaustive one.
- Exactly one AI call per investigation — there is no multi-round "ask a follow-up
  question of itself" loop; refinement happens through bounded internal ranking, not
  iterative model calls.
- No citation/multi-source validation — this module answers from local codebase
  evidence only. Cross-referencing external documentation or multiple independent
  sources is out of scope here (see `PRODUCT_VISION.md` Track L, Research Agent, which
  is a separate, unbuilt capability).

## Future Direction

- `PRODUCT_VISION.md` Track F (Property Intelligence) and Track M (Decision
  Intelligence) both require an "evidence before conclusions" discipline; this module
  is the existing, working example of that discipline for the codebase domain and
  should be the reference pattern (not the code itself) for evidence-gathering in
  other domains — see `ROADMAP.md` Phase 6.
- `PRODUCT_VISION.md` Track K (Repository Intelligence) extensions (dead code,
  duplicate logic, circular imports) are a natural growth path built on the same
  search primitives this module already consumes.
