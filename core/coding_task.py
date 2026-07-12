#core/coding_task.py
"""
Coding-task continuity layer: lets a JARVIS-driven coding project (built via
actions/dev_agent.py) survive across conversation turns and app restarts.

This module is orchestration and continuity ONLY — it never plans, writes,
runs, or fixes code itself. actions/dev_agent.py's existing build/run/fix
pipeline remains the single execution engine; this module just tracks which
project is "the current one" and a small, bounded summary of its state.

Persisted as a single small JSON file under config/state/ (gitignored,
single-slot — this app is single-user/single-session, one active coding
task is all that's ever needed, same rationale as core/pending_action.py).

Never stores: API keys, full source file contents, full prompts,
credentials, or raw screenshots — only short bounded summaries, file paths,
and small operational strings (entry point, run command, language).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

MAX_GOAL_CHARS             = 500
MAX_ERROR_CHARS            = 1500
MAX_EVIDENCE_SUMMARY_CHARS = 500
MAX_FILES_TOUCHED          = 50


class Phase:
    PLANNING         = "PLANNING"
    BUILDING         = "BUILDING"
    VALIDATING       = "VALIDATING"
    INVESTIGATING    = "INVESTIGATING"
    FIXING           = "FIXING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    COMPLETED        = "COMPLETED"
    FAILED           = "FAILED"


class Status:
    ACTIVE    = "active"
    COMPLETED = "completed"
    FAILED    = "failed"
    ARCHIVED  = "archived"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STATE_DIR  = _base_dir() / "config" / "state"
STATE_FILE = STATE_DIR / "coding_task.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass
class CodingTask:
    task_id: str
    original_goal: str
    current_goal: str
    project_name: str
    project_root: str
    phase: str = Phase.PLANNING
    status: str = Status.ACTIVE
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    last_runtime_error: str = ""
    last_error_signature: str = ""
    last_evidence_summary: str = ""
    files_touched: list = field(default_factory=list)
    fix_attempt_count: int = 0
    last_successful_step: str = ""
    # Small operational metadata (not prompts/source/secrets) needed to
    # resume the fix loop without re-planning after a restart.
    entry_point: str = "main.py"
    run_command: str = ""
    language: str = "python"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "CodingTask":
        known = set(CodingTask.__dataclass_fields__)
        return CodingTask(**{k: v for k, v in d.items() if k in known})

    def touch_files(self, paths) -> None:
        for p in paths:
            if p and p not in self.files_touched:
                self.files_touched.append(p)
        if len(self.files_touched) > MAX_FILES_TOUCHED:
            self.files_touched = self.files_touched[-MAX_FILES_TOUCHED:]

    def record_error(self, error_output: str, signature: str = "", evidence_summary: str = "") -> None:
        self.last_runtime_error = _truncate(error_output, MAX_ERROR_CHARS)
        self.last_error_signature = (signature or "")[:200]
        if evidence_summary:
            self.last_evidence_summary = _truncate(evidence_summary, MAX_EVIDENCE_SUMMARY_CHARS)

    def clear_error(self) -> None:
        self.last_runtime_error = ""
        self.last_error_signature = ""
        self.last_evidence_summary = ""


# ---------------------------------------------------------------------------
# Persistence — single small JSON file, atomic writes (temp file + os.replace,
# which is atomic on both POSIX and Windows when source/dest share a volume).
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".coding_task_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def load_active_task() -> CodingTask | None:
    """The single most-recent coding task, or None if there isn't one, the
    file is unreadable/corrupt, or the task was explicitly archived
    (archiving means: don't auto-resume this one)."""
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not data:
        return None
    try:
        task = CodingTask.from_dict(data)
    except Exception:
        return None
    if task.status == Status.ARCHIVED:
        return None
    return task


def save_task(task: CodingTask) -> None:
    task.updated_at = _now()
    _atomic_write_json(STATE_FILE, task.to_dict())


def archive_active_task() -> bool:
    """Marks the current task archived (kept on disk, but no longer
    auto-resumed by load_active_task()) instead of deleting it outright."""
    task = load_active_task()
    if not task:
        return False
    task.status = Status.ARCHIVED
    save_task(task)
    return True


def clear_active_task() -> None:
    """Removes the state file entirely. Never touches the generated
    project's own files on disk — only the continuity tracking record."""
    try:
        STATE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def start_task(
    original_goal: str,
    project_name: str,
    project_root: str,
    entry_point: str = "main.py",
    run_command: str = "",
    language: str = "python",
) -> CodingTask:
    task = CodingTask(
        task_id=uuid.uuid4().hex[:12],
        original_goal=_truncate(original_goal, MAX_GOAL_CHARS),
        current_goal=_truncate(original_goal, MAX_GOAL_CHARS),
        project_name=project_name,
        project_root=str(project_root),
        entry_point=entry_point,
        run_command=run_command or f"python {entry_point}",
        language=language,
    )
    save_task(task)
    return task


def continue_task(task: CodingTask, new_goal: str) -> CodingTask:
    """Update current_goal for a continuation request, without losing
    task_id/original_goal/history. Reopens a COMPLETED/FAILED task back to
    ACTIVE (per spec: a completed project + a new feature request means
    reopen/continue the same project, not start a fresh one)."""
    task.current_goal = _truncate(new_goal, MAX_GOAL_CHARS)
    if task.status in (Status.COMPLETED, Status.FAILED):
        task.status = Status.ACTIVE
        task.phase = Phase.PLANNING
    save_task(task)
    return task


def set_phase(task: CodingTask, phase: str) -> None:
    task.phase = phase
    save_task(task)


def mark_completed(task: CodingTask, last_step: str = "run") -> None:
    task.phase = Phase.COMPLETED
    task.status = Status.COMPLETED
    task.last_successful_step = last_step
    task.clear_error()
    save_task(task)


def mark_failed(task: CodingTask, last_step: str = "") -> None:
    task.phase = Phase.FAILED
    task.status = Status.FAILED
    if last_step:
        task.last_successful_step = last_step
    save_task(task)


def describe_task(task: CodingTask) -> str:
    """Short, human-readable status summary — bounded, no source contents."""
    lines = [
        f"Project: {task.project_name} ({task.project_root})",
        f"Goal: {task.current_goal}",
        f"Phase: {task.phase} | Status: {task.status}",
        f"Fix attempts so far: {task.fix_attempt_count}",
    ]
    if task.last_error_signature:
        lines.append(f"Last error: {task.last_error_signature}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Continuation-intent classification — deterministic, concept-based (not
# exact-phrase matching). Used by dev_agent.py to decide whether an incoming
# request should continue the active coding task or start a new one.
# ---------------------------------------------------------------------------

_CONTINUE_VERBS   = {"continue", "resume"}
_KEEP_PHRASES     = ("keep going", "keep building", "keep coding", "keep working")
_FIX_TERMS        = {"fix", "solve", "debug", "resolve"}
_ERROR_TERMS      = {"error", "bug", "issue", "problem", "crash", "exception", "broken"}
_REFERRING_TERMS  = {"current", "this", "that", "existing", "same", "it"}
_ADD_TERMS        = {"add", "include", "implement"}
_UPDATE_TERMS     = {"update", "change", "modify", "improve", "enhance", "tweak"}
_PROJECT_TERMS    = {"app", "project", "coding", "code"}
_NEW_TERMS        = {"new", "another", "different", "fresh", "separate", "second"}
_NEW_ACTION_VERBS = {"build", "create", "make", "start"}


def _words(text: str) -> set:
    return set(re.findall(r"[a-z']+", (text or "").lower()))


def looks_like_fix_continuation(description: str) -> bool:
    """Refers to resuming/fixing whatever the active project is currently
    doing, without describing new functionality — routes to the existing
    (lighter) fix loop rather than a full replan."""
    low = (description or "").lower()
    words = _words(description)

    if words & _CONTINUE_VERBS:
        return True
    if any(p in low for p in _KEEP_PHRASES):
        return True
    if "fix it" in low or "fix that" in low or "fix this" in low:
        return True
    if (words & _FIX_TERMS) and (words & _ERROR_TERMS or words & _REFERRING_TERMS):
        return True
    return False


def looks_like_feature_continuation(description: str) -> bool:
    """Refers to adding/changing functionality in the existing project —
    routes to the full build pipeline (replan + write), but targeting the
    SAME project directory rather than creating a new one."""
    words = _words(description)
    if (words & _ADD_TERMS or words & _UPDATE_TERMS) and (words & _REFERRING_TERMS or words & _PROJECT_TERMS):
        return True
    return False


def looks_like_continuation_request(description: str) -> bool:
    """Either kind of continuation — used to decide whether "no active task"
    should trigger a clarifying question instead of guessing a new project."""
    return looks_like_fix_continuation(description) or looks_like_feature_continuation(description)


def looks_like_new_project_request(description: str) -> bool:
    """Explicitly signals a brand-new, separate project (e.g. "build me a
    NEW app", "start ANOTHER project") — overrides any active coding task."""
    words = _words(description)
    return bool((words & _NEW_ACTION_VERBS) and (words & _NEW_TERMS))
