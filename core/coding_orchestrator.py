#core/coding_orchestrator.py
"""
Coding Orchestrator: the single place that answers two questions for an
incoming coding request —

    1. What kind of coding task is this?
    2. Where should it go?

It classifies a request as a brand-new project, a continuation of the active
project's fix loop, a continuation as an incremental feature change, or a
request that needs clarification (a continuation-shaped request with no
active project) — and returns that decision as a plain, inspectable
RoutingDecision. It does not build, fix, investigate, analyze impact, record
engineering outcomes, or manage rollback itself. Those remain owned by
actions/dev_agent.py's existing pipelines (_build_project,
_continue_fix_loop_for_task, _run_incremental_feature_change) and the modules
they call in turn (core/engineering_memory.py, actions/investigate.py,
actions/impact_analysis.py, actions/codebase_search.py, core/workspace.py,
core/ai_provider.py).

This module reuses core/coding_task.py's existing active-task state and
deterministic classification helpers (looks_like_new_project_request,
looks_like_fix_continuation, looks_like_feature_continuation,
looks_like_continuation_request) rather than reimplementing routing logic —
the decision tree here is unchanged in behavior from what was previously
inline in actions/dev_agent.py's dev_agent() entry point.

Future coding capabilities should route requests through decide() rather
than reimplementing this classification or calling actions/dev_agent.py's
internal pipelines directly.

Also reuses core/loop_detector.py: before routing a CONTINUE_FIX or
CONTINUE_FEATURE request to the existing fix/feature pipeline, this module
checks whether that task is stuck in a deterministic loop (see
DECISIONS/ADR-008.md for why this check lives here rather than inside
actions/dev_agent.py). If a loop is detected, decide() returns
Route.LOOP_DETECTED instead of routing to the pipeline — no pipeline runs,
so no further LLM call is spent. The decision to stop is the orchestrator's;
nothing here retries, overrides, or auto-continues past a detected loop.
"""
from __future__ import annotations

from dataclasses import dataclass

from core import coding_task as ct
from core import loop_detector as ld


class Route:
    NEW_PROJECT         = "new_project"
    CONTINUE_FIX        = "continue_fix"
    CONTINUE_FEATURE    = "continue_feature"
    NEEDS_CLARIFICATION = "needs_clarification"
    MISSING_DESCRIPTION = "missing_description"
    LOOP_DETECTED       = "loop_detected"


@dataclass
class RoutingDecision:
    route: str
    task: "ct.CodingTask | None" = None   # the active/newly-started task, or None
    message: str = ""                     # set for NEEDS_CLARIFICATION / MISSING_DESCRIPTION / LOOP_DETECTED
    loop_check: "ld.LoopCheckResult | None" = None  # set only for LOOP_DETECTED


def decide(description: str, project_name: str = "", language: str = "python") -> RoutingDecision:
    """
    Pure routing decision — does not build, fix, or investigate anything.

    Mirrors, unchanged in behavior, the decision tree previously inline in
    actions/dev_agent.py's dev_agent():

    - No description                                -> MISSING_DESCRIPTION.
    - Active task + explicit "a NEW/another app"     -> NEW_PROJECT anyway
      language                                          (the active task is
                                                          left as-is on disk,
                                                          just no longer
                                                          tracked as current).
    - Active task + "fix it"/"continue" language      -> CONTINUE_FIX
      (not also feature-shaped)                           (same project).
    - Active task + anything else non-new-project     -> CONTINUE_FEATURE
      (feature-add or generic continuation)               (same project).
    - No active task + continuation-shaped language   -> NEEDS_CLARIFICATION
      (never guesses a project).
    - No active task + a normal build description     -> NEW_PROJECT.
    """
    description = (description or "").strip()
    if not description:
        return RoutingDecision(
            route=Route.MISSING_DESCRIPTION,
            message="Please describe the project you want me to build, sir.",
        )

    active     = ct.load_active_task()
    forces_new = ct.looks_like_new_project_request(description)

    if not forces_new and active:
        loop_check = ld.check_for_loop(active.task_id)
        if loop_check.loop_detected:
            return RoutingDecision(
                route=Route.LOOP_DETECTED,
                task=active,
                message=(
                    f"I've noticed this task isn't making progress, sir — {loop_check.reason.replace('_', ' ')}. "
                    f"{loop_check.recommendation}"
                ),
                loop_check=loop_check,
            )

        if ct.looks_like_fix_continuation(description) and not ct.looks_like_feature_continuation(description):
            ct.continue_task(active, description)
            return RoutingDecision(route=Route.CONTINUE_FIX, task=active)

        # Feature-add or generic continuation: surgical incremental change
        # on the SAME project — never the fresh-project planner/writer.
        ct.continue_task(active, description)
        return RoutingDecision(route=Route.CONTINUE_FEATURE, task=active)

    if not forces_new and ct.looks_like_continuation_request(description) and not active:
        return RoutingDecision(
            route=Route.NEEDS_CLARIFICATION,
            message=(
                "There's no active coding project for me to continue, sir. "
                "Which project did you mean, or would you like me to start a new one?"
            ),
        )

    # Brand-new project. project_root is finalized inside _build_project once
    # the planner picks a name (if the caller didn't specify one) — this task
    # record is updated at that point, exactly as before this module existed.
    task = ct.start_task(
        original_goal = description,
        project_name  = project_name,
        project_root  = "",
        language      = language,
    )
    return RoutingDecision(route=Route.NEW_PROJECT, task=task)
