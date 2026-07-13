#core/execution_ledger.py
"""
Execution Ledger: a deterministic, append-only internal engineering log of
every coding operation routed by core/coding_orchestrator.py and executed by
actions/dev_agent.py.

This is an internal engineering log, not user-facing memory — it exists so a
future engineer (human or AI) can answer "what actually happened, in what
order, with what result" without re-deriving it from scattered logs. It does
not decide anything and does not influence dev_agent.py's behavior. It also
does not duplicate core/engineering_memory.py's per-attempt fix/change
outcome recall — that module records each individual fix/feature attempt to
inform future attempts; this module records one entry per whole dev_agent()
call (one routed coding operation), for observability, not decision-making.

Persistence reuses the same single-JSON-file, gitignored, atomic-write
convention already used by core/coding_task.py and core/engineering_memory.py
(see DECISIONS/ADR-002.md) — no new persistence mechanism.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

MAX_FILES_TOUCHED = 50
MAX_ENTRIES        = 500  # bounded retention — oldest entries pruned first


class Result:
    SUCCESS  = "success"
    FAILURE  = "failure"
    ROLLBACK = "rollback"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STATE_DIR  = _base_dir() / "config" / "state"
STATE_FILE = STATE_DIR / "execution_ledger.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LedgerEntry:
    entry_id: str
    timestamp: str
    task_id: str
    operation_type: str        # "build" | "runtime_fix" | "feature_change"
    routing_decision: str      # core.coding_orchestrator.Route value
    action_performed: str      # the dev_agent.py pipeline function that ran
    files_touched: list = field(default_factory=list)
    duration_seconds: float = 0.0
    result: str = ""           # Result.SUCCESS | Result.FAILURE | Result.ROLLBACK
    next_decision: str = ""    # optional — populated only when already known

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "LedgerEntry":
        known = set(LedgerEntry.__dataclass_fields__)
        return LedgerEntry(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# Persistence — same pattern as core/coding_task.py / core/engineering_memory.py:
# one small JSON file, atomic writes (temp file + os.replace), fail-safe on
# missing/corrupt data.
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".execution_ledger_", suffix=".tmp")
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
    exception — a coding operation must never crash because the ledger is
    broken."""
    if not STATE_FILE.exists():
        return []
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    raw_entries = data.get("entries", [])
    if not isinstance(raw_entries, list):
        return []
    entries = []
    for e in raw_entries:
        if not isinstance(e, dict):
            continue
        try:
            entries.append(LedgerEntry.from_dict(e))
        except Exception:
            continue
    return entries


def _save_all(entries: list) -> None:
    if len(entries) > MAX_ENTRIES:
        entries = sorted(entries, key=lambda e: e.timestamp)[-MAX_ENTRIES:]
    _atomic_write_json(STATE_FILE, {"entries": [e.to_dict() for e in entries]})


def record(
    task_id: str,
    operation_type: str,
    routing_decision: str,
    action_performed: str,
    files_touched: list,
    duration_seconds: float,
    result: str,
    next_decision: str = "",
) -> LedgerEntry:
    """Record one execution-ledger entry — deterministic, no LLM call. Fails
    safe: if persistence itself fails (e.g. disk issue), the exception is
    swallowed so the coding operation's own execution is never interrupted by
    a ledger-write problem (same convention as
    core/engineering_memory.py's record_outcome())."""
    entry = LedgerEntry(
        entry_id=uuid.uuid4().hex[:12],
        timestamp=_now(),
        task_id=task_id or "",
        operation_type=operation_type or "",
        routing_decision=routing_decision or "",
        action_performed=action_performed or "",
        files_touched=list(files_touched or [])[:MAX_FILES_TOUCHED],
        duration_seconds=round(duration_seconds, 3),
        result=result or "",
        next_decision=next_decision or "",
    )
    try:
        entries = _load_all()
        entries.append(entry)
        _save_all(entries)
    except Exception as e:
        print(f"[ExecutionLedger] Failed to record entry (non-fatal): {e}")
    return entry


def entries_for_task(task_id: str) -> list:
    """All ledger entries for a given task, oldest first — the deterministic
    read side of this internal log."""
    return sorted((e for e in _load_all() if e.task_id == task_id), key=lambda e: e.timestamp)
