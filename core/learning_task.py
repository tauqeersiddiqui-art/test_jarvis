#core/learning_task.py
"""
Learning Task Queue v1: converts a CONFIRMED capability gap (a
core/capability_gap.py detect_gap() result with gap_detected == True) into a
persistent, bounded, deduplicated record of "this capability is missing and
should be learned about later."

This is NOT autonomous research, NOT capability installation, NOT
self-modification, and NOT background execution. A LearningTask is only a
recorded need. Recording one grants no permission to browse the internet,
run shell commands, modify source, install software, access secrets, or use
a camera/microphone -- any future learning execution against a task must go
through a separate, explicitly approved workflow that does not exist yet.
Nothing in this module researches, generates code, installs anything, or
modifies capability registration.

Detection and task creation are explicitly separate operations:
core/capability_gap.py's detect_gap() remains entirely read-only and knows
nothing about this module -- only this module imports/consumes a detect_gap()
result, never the reverse, and nothing here calls detect_gap() automatically.

Persistence reuses the same single-JSON-file, gitignored, atomic-write
convention already used by core/coding_task.py, core/engineering_memory.py,
and core/execution_ledger.py -- no new persistence mechanism. Never calls
core/learning_engine.py's learn(), never calls an AI provider.
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

from core import capability_gap as cg

MAX_TASK_CHARS = 500
MAX_CAPABILITY_CHARS = 300
MAX_REASON_CHARS = 500
MAX_TASKS = 200  # bounded retention -- lowest-priority, oldest pruned first
MAX_OCCURRENCE_BONUS = 5  # repeated-occurrence priority bonus caps out here


class Status:
    PENDING   = "pending"
    APPROVED  = "approved"
    LEARNING  = "learning"
    COMPLETED = "completed"
    FAILED    = "failed"
    REJECTED  = "rejected"


_VALID_STATUSES = frozenset({
    Status.PENDING, Status.APPROVED, Status.LEARNING,
    Status.COMPLETED, Status.FAILED, Status.REJECTED,
})

# Deterministic lifecycle -- update_status() only ever RECORDS a transition a
# caller explicitly requests; nothing in this module triggers one on its own,
# and no transition here starts research, code generation, or installation.
_ALLOWED_TRANSITIONS = {
    Status.PENDING:   frozenset({Status.APPROVED, Status.REJECTED}),
    Status.APPROVED:  frozenset({Status.LEARNING, Status.REJECTED}),
    Status.LEARNING:  frozenset({Status.COMPLETED, Status.FAILED}),
    Status.COMPLETED: frozenset(),
    Status.FAILED:    frozenset({Status.PENDING}),  # allow a future retry to be re-queued
    Status.REJECTED:  frozenset(),
}

SOURCE_DETECTION = "capability_gap_detection"
SOURCE_USER = "user_requested"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STATE_DIR  = _base_dir() / "config" / "state"
STATE_FILE = STATE_DIR / "learning_tasks.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Deterministic normalization -- used both to bound `missing_capability` and
# as the dedup key. Independent of core/capability_gap.py's own tokenizer:
# this module must normalize consistently even for a manually-supplied
# (user_requested) missing-capability phrase that never went through
# detect_gap() at all.
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "this", "that", "these", "those",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their",
    "and", "or", "but", "if", "so", "because",
    "to", "of", "in", "on", "at", "for", "with", "from", "into", "onto", "by", "as",
    "do", "does", "did", "done", "doing", "be", "been", "being",
    "can", "could", "would", "should", "will", "shall", "may", "might", "must",
    "please", "just", "also", "then", "than", "there", "here", "not", "no", "yes",
})
_TOKEN_RE = re.compile(r"[a-z0-9']+")


def normalize_capability(text: str) -> str:
    """Deterministic: lowercase, tokenize, drop stopwords/single-char
    tokens, sort, join. Same input (regardless of casing/whitespace/word
    order) always normalizes to the same string, which is what dedup keys
    off of."""
    tokens = _TOKEN_RE.findall((text or "").lower())
    meaningful = sorted({t for t in tokens if t not in _STOPWORDS and len(t) > 1})
    return " ".join(meaningful)


def _priority_for(occurrence_count: int, source: str) -> int:
    """Deterministic priority policy -- no LLM, no learned weighting:
    base 1, +1 per repeated occurrence beyond the first (capped at
    MAX_OCCURRENCE_BONUS), +2 if the source is an explicit user request
    rather than incidental detection."""
    priority = 1 + min(max(occurrence_count - 1, 0), MAX_OCCURRENCE_BONUS)
    if source == SOURCE_USER:
        priority += 2
    return priority


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class LearningTask:
    task_id: str
    created_at: str
    updated_at: str
    requested_task: str
    missing_capability: str
    gap_reason: str
    source: str
    priority: int
    status: str
    occurrence_count: int = 1
    last_seen_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "LearningTask":
        known = set(LearningTask.__dataclass_fields__)
        return LearningTask(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# Persistence -- same pattern as core/coding_task.py / core/engineering_memory.py
# / core/execution_ledger.py: one small JSON file, atomic writes (temp file +
# os.replace), fail-safe on missing/corrupt data.
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".learning_task_", suffix=".tmp")
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


def _load_all() -> list:
    """Fail-safe: a missing or corrupt file yields an empty list, never an
    exception -- normal Mark execution must never crash because this queue
    is broken."""
    if not STATE_FILE.exists():
        return []
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    raw_tasks = data.get("tasks", [])
    if not isinstance(raw_tasks, list):
        return []
    tasks = []
    for t in raw_tasks:
        if not isinstance(t, dict):
            continue
        try:
            tasks.append(LearningTask.from_dict(t))
        except Exception:
            continue
    return tasks


def _prune(tasks: list) -> list:
    """Bounded retention: over MAX_TASKS, drop lowest-priority tasks first,
    oldest-updated first among ties."""
    if len(tasks) <= MAX_TASKS:
        return tasks
    ranked = sorted(tasks, key=lambda t: (t.priority, t.updated_at))  # ascending: weakest first
    to_remove = len(tasks) - MAX_TASKS
    remove_ids = {t.task_id for t in ranked[:to_remove]}
    return [t for t in tasks if t.task_id not in remove_ids]


def _save_all(tasks: list) -> None:
    tasks = _prune(tasks)
    _atomic_write_json(STATE_FILE, {"tasks": [t.to_dict() for t in tasks]})


def _persist(tasks: list) -> None:
    """Fail-safe wrapper: a persistence failure is logged, never raised, so
    a broken learning-task store can never interrupt normal Mark execution
    (same convention as core/engineering_memory.py's record_outcome())."""
    try:
        _save_all(tasks)
    except Exception as e:
        print(f"[LearningTask] Failed to persist (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_from_gap(gap_result, source: str = SOURCE_DETECTION) -> "LearningTask | None":
    """
    The ONLY write path that creates or updates a learning task.

    Accepts a core/capability_gap.py GapResult (or any object exposing the
    same `gap_detected` / `confidence` / `required_capability` /
    `requested_task` / `evidence` attributes).

    Returns None -- no task created, no existing task touched -- unless the
    gap is a CONFIRMED full miss: `gap_detected is True` AND
    `confidence == core.capability_gap.CONFIDENCE_NONE`. Note that
    core/capability_gap.py's detect_gap() sets `gap_detected = True` for
    BOTH a partial match and a full miss (only "high" and "ambiguous" set it
    to False/None) -- checking `gap_detected` alone is NOT enough to exclude
    partial matches, so `confidence` is checked explicitly too. Available
    capabilities (`confidence == "high"`), partial matches
    (`confidence == "partial"`), and ambiguous results
    (`confidence == "ambiguous"`) never create or update a task.

    On a repeat of an already-recorded gap (same normalized
    missing_capability), no new task is created: occurrence_count is
    incremented, last_seen_at/updated_at/priority are refreshed, and the
    original task_id is preserved.
    """
    if gap_result is None:
        return None
    if getattr(gap_result, "gap_detected", None) is not True:
        return None
    if getattr(gap_result, "confidence", None) != cg.CONFIDENCE_NONE:
        return None

    raw_capability = (
        getattr(gap_result, "required_capability", "")
        or getattr(gap_result, "requested_task", "")
    )
    missing_capability = _truncate(normalize_capability(raw_capability), MAX_CAPABILITY_CHARS)
    if not missing_capability:
        return None

    requested_task = _truncate(getattr(gap_result, "requested_task", "") or "", MAX_TASK_CHARS)
    gap_reason = _truncate(getattr(gap_result, "evidence", "") or "", MAX_REASON_CHARS)

    try:
        tasks = _load_all()
    except Exception:
        tasks = []

    existing = next((t for t in tasks if t.missing_capability == missing_capability), None)
    now = _now()

    if existing is not None:
        existing.occurrence_count += 1
        existing.last_seen_at = now
        existing.updated_at = now
        existing.priority = _priority_for(existing.occurrence_count, existing.source)
        _persist(tasks)
        return existing

    task = LearningTask(
        task_id=uuid.uuid4().hex[:12],
        created_at=now,
        updated_at=now,
        requested_task=requested_task,
        missing_capability=missing_capability,
        gap_reason=gap_reason,
        source=source,
        priority=_priority_for(1, source),
        status=Status.PENDING,
        occurrence_count=1,
        last_seen_at=now,
    )
    tasks.append(task)
    _persist(tasks)
    return task


def list_tasks(status: str | None = None) -> list:
    """All tasks, highest priority first (ties broken oldest-created
    first), optionally filtered to one status."""
    tasks = _load_all()
    if status:
        tasks = [t for t in tasks if t.status == status]
    return sorted(tasks, key=lambda t: (-t.priority, t.created_at))


def get_task(task_id: str):
    for t in _load_all():
        if t.task_id == task_id:
            return t
    return None


def find_by_capability(missing_capability: str):
    """Look up an existing task by the same normalization create_from_gap()
    uses -- lets a caller check "has this gap already been recorded?"
    without needing the exact stored (already-normalized) string."""
    key = _truncate(normalize_capability(missing_capability), MAX_CAPABILITY_CHARS)
    for t in _load_all():
        if t.missing_capability == key:
            return t
    return None


def update_status(task_id: str, new_status: str) -> "LearningTask | None":
    """
    Explicit, caller-requested status transition ONLY -- nothing in this
    module calls this automatically, and calling it never itself performs
    research, code generation, installation, or any other lifecycle action;
    it only records the state a caller says the task is now in.

    Returns None (task left unchanged) for an unknown status value, an
    unknown task_id, or a transition not present in the lifecycle table --
    invalid transitions are rejected rather than silently applied.
    """
    if new_status not in _VALID_STATUSES:
        return None

    tasks = _load_all()
    task = next((t for t in tasks if t.task_id == task_id), None)
    if task is None:
        return None
    if new_status != task.status and new_status not in _ALLOWED_TRANSITIONS.get(task.status, frozenset()):
        return None

    task.status = new_status
    task.updated_at = _now()
    _persist(tasks)
    return task


def stats() -> dict:
    tasks = _load_all()
    by_status: dict = {}
    for t in tasks:
        by_status[t.status] = by_status.get(t.status, 0) + 1
    return {"total_tasks": len(tasks), "by_status": by_status}
