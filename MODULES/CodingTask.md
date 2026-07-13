# Module: CodingTask

**Location:** `core/coding_task.py`
**Layer:** Core Services

## Purpose

Cross-turn and cross-restart continuity for a JARVIS-driven coding project. Lets a
conversation about "the current coding project" survive a conversation turn boundary
or an app restart without re-deriving what project is active or what phase it's in.

## Responsibilities

- Track a single active coding task: its goal (original and current, in case of
  continuation), project name/root, phase (`PLANNING`, `BUILDING`, `VALIDATING`,
  `INVESTIGATING`, `FIXING`, `WAITING_FOR_USER`, `COMPLETED`, `FAILED`), and status
  (`active`, `completed`, `failed`, `archived`).
- Classify an incoming request as a fix-continuation, a feature-continuation, or a
  brand-new project request, deterministically (word/phrase matching, not an LLM
  call) — `looks_like_fix_continuation()`, `looks_like_feature_continuation()`,
  `looks_like_new_project_request()`.
- Persist/restore that single task as one small JSON file, atomically written.

## What this module explicitly does NOT do

Orchestration and continuity only — it never plans, writes, runs, or fixes code
itself. `actions/dev_agent.py`'s existing build/run/fix pipeline is the single
execution engine; this module only tracks which project is "the current one" and a
small, bounded summary of its state.

## Public Interface

- `start_task(original_goal, project_name, project_root, entry_point="main.py", run_command="", language="python") -> CodingTask`
- `continue_task(task, new_goal) -> CodingTask` — reopens a `COMPLETED`/`FAILED` task
  back to `ACTIVE` for a continuation request rather than starting fresh.
- `load_active_task() -> CodingTask | None` — returns `None` if no task, the file is
  unreadable/corrupt, or the task was explicitly archived.
- `save_task`, `set_phase`, `mark_completed`, `mark_failed`, `archive_active_task`,
  `clear_active_task`, `describe_task` (bounded, human-readable status summary).
- Classification helpers: `looks_like_fix_continuation`, `looks_like_feature_continuation`,
  `looks_like_continuation_request`, `looks_like_new_project_request`.
- `Phase` and `Status` — the state-machine constants.

## Dependencies

- Standard library only (`json`, `dataclasses`, `pathlib`, `tempfile`, `uuid`,
  `datetime`). No AI provider call anywhere in this module.

## Persistence

Single JSON file at `config/state/coding_task.json` (gitignored), atomic write via
temp-file + `os.replace` (atomic on both POSIX and Windows when source/destination
share a volume). Single-slot by design — this app is single-user/single-session, so
one active coding task is all that is ever needed.

## Limitations

- Never stores API keys, full source file contents, full prompts, credentials, or raw
  screenshots — only short bounded summaries, file paths, and small operational
  strings (entry point, run command, language).
- Single-slot: cannot track two coding tasks concurrently. This matches the
  single-user/single-session design of the whole application; a multi-project variant
  would need a different persistence shape.

## Future Direction

- `PRODUCT_VISION.md` Track H (Engineering Experience Engine) and Track I (Project
  Digital Twin) extend the *idea* of per-project continuity that this module already
  proves out, but neither should be built by widening this module's single-slot
  design — they need their own, separately-scoped persistence (see `ROADMAP.md`
  Phase 4 and Phase 7).
