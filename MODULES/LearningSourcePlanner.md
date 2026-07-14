# Module: LearningSourcePlanner

**Location:** `core/learning_source_planner.py`
**Layer:** Core Services

## Purpose

Deterministically converts an APPROVED `core/learning_task.py` `LearningTask` into a
bounded `LearningSourcePlan` describing what KINDS of sources should be acquired
before Mark attempts to learn the missing capability — what must be learned, what
source categories are appropriate, what authority level those sources should have,
and what source types must be avoided. This is planning only.

## What this module explicitly is NOT

Not research, not internet browsing, not URL fetching, not code generation, not
capability/MCP/plugin/package installation, and not automatic execution of anything.
A `LearningSourcePlan` is not evidence, not knowledge, and not permission to access,
browse, or execute a source — it only describes what a future, separately approved
acquisition/validation workflow (which does not exist yet) should prefer and avoid
once it runs. Source authority requirements describe a preference for future
acquisition; they do not themselves prove any source is trustworthy.

## Responsibilities

- `create_plan_from_task()` is the only write path. It accepts a `LearningTask` and
  creates/updates a plan **only** when `learning_task.status ==
  core.learning_task.Status.APPROVED`. Pending, learning, completed, failed, and
  rejected tasks never produce a plan. This function never reads the task's status
  for the purpose of changing it, and never calls `core.learning_task.update_status()`
  — a plan's existence never automatically advances the underlying task's lifecycle.
- Deterministic, bounded domain classification (`classify_domain()`) via keyword-set
  overlap only — no LLM. Nine domain classes:
  `government_property, legal_regulatory, financial, medical, software_api,
  software_repository, hardware_device, general_knowledge, unknown`.
  - Each domain has "strong" indicator keywords (specific enough that **one** match
    qualifies, e.g. `rera`, `regulatory`, `firmware`) and "weak" indicator keywords
    (common enough that **two** matches are required, e.g. `property`, `legal`,
    `hardware`). This two-tier design exists specifically so one incidental word
    (e.g. "license" in an ordinary software question) can never, on its own, falsely
    classify a task into a high-stakes domain.
  - An empty/near-empty task classifies as `unknown` ("no meaningful terms").
  - A genuine tie between two or more domains' scores also classifies as `unknown`
    rather than guessing.
  - A non-empty task matching no domain's keywords classifies as `general_knowledge`.
- Fixed per-domain source policy (`_DOMAIN_POLICY`) maps each domain to
  `source_categories`, `required_authority`, `preferred_source_types`, and
  `disallowed_source_types` — never dynamically invented, never LLM-derived. The four
  high-stakes domains (`government_property`, `legal_regulatory`, `financial`,
  `medical`) always require `AUTHORITY_AUTHORITATIVE` or `AUTHORITY_PRIMARY`.
- Fixed, small vocabularies enforced by construction: `SOURCE_CATEGORIES` (8 values:
  `official_documentation, government_source, api_reference, local_repository,
  project_documentation, standards_source, approved_mcp,
  approved_open_source_repository`) and `AUTHORITY_LEVELS` (5 values:
  `authoritative, primary, trusted_technical, local_project, supplementary`).
  `_bounded_categories()` defensively drops any value outside `SOURCE_CATEGORIES`
  before it can ever be persisted.
- Deduplicates plans by `learning_task_id` (`create_plan_from_task()`) — one plan per
  task. A repeat call for the same task recomputes domain/policy/rationale in place
  (deterministic functions of the task's own text, so an unchanged task yields
  identical content) and refreshes `updated_at`, while `plan_id` is preserved.
- Bounded retention (`MAX_PLANS = 200`): over the cap, oldest-updated plans are
  pruned first.
- Persists atomically — same single-JSON-file, gitignored, temp-file + `os.replace`
  convention as `core/coding_task.py` / `core/engineering_memory.py` /
  `core/execution_ledger.py` / `core/learning_task.py`. Persistence failures are
  swallowed (logged, not raised) so a broken plan store can never interrupt normal
  Mark execution.

## Public Interface

- `create_plan_from_task(learning_task) -> LearningSourcePlan | None`
- `classify_domain(text: str) -> DomainClassification` (`domain`, `rationale`)
- `list_plans(status: str | None = None) -> list[LearningSourcePlan]`
- `get_plan(plan_id: str) -> LearningSourcePlan | None`
- `find_by_task(learning_task_id: str) -> LearningSourcePlan | None`
- `stats() -> dict` — `{"total_plans": int, "by_domain": {domain: count}}`
- `LearningSourcePlan` fields: `plan_id, learning_task_id, missing_capability,
  domain, source_categories, required_authority, preferred_source_types,
  disallowed_source_types, rationale, status, created_at, updated_at`.
- `Status`: `DRAFT` (the only status produced in v1 — no acquisition workflow exists
  yet to advance a plan past draft).
- Fixed vocabularies: `SOURCE_CATEGORIES`, `AUTHORITY_LEVELS`, `DOMAIN_CLASSES`,
  `HIGH_STAKES_DOMAINS`.

## Dependencies

- `core/learning_task.py` — only for its `Status` constants (to gate plan creation
  correctly) and the shape of a `LearningTask` (duck-typed via `getattr`, not an
  `isinstance` check). This is a **one-directional** dependency:
  `core/learning_task.py` has no knowledge of this module.
- Standard library only otherwise (`json`, `re`, `dataclasses`, `tempfile`, `uuid`).
  **Deliberately does not import `core/learning_engine.py` at all** — domain and
  authority classification are pure, fixed, deterministic keyword rules over the
  task's own request text, so learned documentation or `PRODUCT_VISION.md` text can
  never influence a classification. No AI provider call anywhere in this module.

## Integration Scope (this session)

Nothing in this session wires `create_plan_from_task()` into `main.py`'s tool
dispatch, `core/coding_orchestrator.py`, or any other request-routing path. Normal
request routing is unchanged. This module is not called by `core/capability_gap.py`
or `core/learning_task.py`.

## Limitations

- Domain classification is deterministic keyword overlap, not semantic — a
  differently-worded high-stakes request whose wording doesn't match the fixed
  keyword sets will classify as `general_knowledge` rather than the correct
  high-stakes domain. This is a known, accepted limitation of v1 (no LLM is used for
  classification, per the Learning Source Planner v1 scope).
- `missing_capability`/`required_capability` text feeding classification comes from
  whatever `core/learning_task.py` recorded, which is itself a token-sorted string
  (see `MODULES/LearningTask.md`) rather than natural-language phrasing — combined
  with the original `requested_task` text to give classification the best available
  signal.
- No acquisition, validation, or execution workflow exists yet to act on a
  `draft`-status plan — that remains explicit future work requiring its own separate
  approval, per the Trust Rule above.

## Future Direction

- `PRODUCT_VISION.md`'s Capability First Principle and Track J (Continuous Learning)
  describe learning what Mark needs and recording it for future work. This module is
  the second stage after `core/learning_task.py` (record the gap → plan what kind of
  sources would be needed) — a future, separately proposed capability would need its
  own approved workflow to actually acquire and validate a source against a plan;
  nothing here authorizes that in advance.
