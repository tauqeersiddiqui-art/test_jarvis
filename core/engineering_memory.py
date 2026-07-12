#core/engineering_memory.py
"""
Project-scoped engineering memory: a small, bounded, searchable record of
past coding outcomes (fixes and feature changes) per generated project —
NOT a generic long-term memory system, and NOT a second coding agent. This
module never plans, writes, runs, or fixes code; it only records and
recalls bounded metadata about attempts actions/dev_agent.py already made.

Adapts the useful idea from mini_agent's missions/engineering_memory.py
(searchable past-fix records informing future attempts) into a Mark-native
primitive that reuses core.coding_task's persistence conventions (single
small JSON file under config/state/, gitignored, atomic writes) and plugs
into dev_agent.py's EXISTING evidence-driven fix loop and incremental
feature-change path — no new execution engine, no LLM calls of its own.

Never stores: API keys, credentials, environment variable values, full
source file contents, full prompts, full model responses, raw screenshots,
or complete tracebacks — only short bounded summaries, file paths, and
small deterministic fingerprints.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

MAX_GOAL_CHARS       = 300
MAX_SUMMARY_CHARS    = 300
MAX_SIGNATURE_CHARS  = 200
MAX_FILES_TOUCHED    = 20
MAX_RECALL_RESULTS   = 3

MAX_RECORDS_PER_PROJECT = 20
MAX_TOTAL_RECORDS       = 200

OUTCOME_SUCCESS      = "success"
OUTCOME_IMPROVED     = "improved"
OUTCOME_FAILED       = "failed"
OUTCOME_ROLLED_BACK  = "rolled_back"
_LOW_VALUE_OUTCOMES  = frozenset({OUTCOME_FAILED, OUTCOME_ROLLED_BACK})


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STATE_DIR  = _base_dir() / "config" / "state"
STATE_FILE = STATE_DIR / "engineering_memory.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def project_key(project_root: str) -> str:
    """Safe, normalized project identifier — a hash of the resolved
    absolute path, never the raw path itself, so records are project-scoped
    without persisting filesystem details beyond what's already in
    CodingTask (which does store project_root; this module only needs an
    opaque, stable key to group records by project)."""
    try:
        resolved = str(Path(project_root).resolve())
    except Exception:
        resolved = str(project_root or "")
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


_FINGERPRINT_STOPWORDS = frozenset({
    "the", "a", "an", "in", "on", "at", "to", "of", "for", "and", "or",
    "is", "are", "this", "that", "it", "its",
})


def _normalize_for_fingerprint(text: str) -> str:
    """Word-set normalization — not exact-string matching. Case-folded,
    stopword-filtered, deduplicated, sorted tokens, bounded, so minor
    rewording of the same underlying attempt (added/removed articles,
    reordered words) still fingerprints identically, while genuinely
    different attempts don't collide."""
    words = {w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if w not in _FINGERPRINT_STOPWORDS}
    return " ".join(sorted(words))[:200]


def compute_attempt_fingerprint(
    operation_type: str,
    normalized_error_signature: str,
    files_touched: list,
    attempt_summary: str,
) -> str:
    """Deterministic fingerprint for a PROPOSED attempt (known before the AI
    call — it never depends on model output), used to detect whether the
    same approach on the same error/files was already tried and failed or
    was rolled back."""
    parts = [
        operation_type or "",
        normalized_error_signature or "",
        ",".join(sorted(files_touched or [])),
        _normalize_for_fingerprint(attempt_summary),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass
class EngineeringRecord:
    record_id: str
    task_id: str
    project_key: str
    timestamp: str
    operation_type: str          # "build_fix" | "feature_change" | "runtime_fix"
    goal_summary: str
    normalized_error_signature: str
    evidence_summary: str
    impact_summary: str
    files_touched: list = field(default_factory=list)
    attempt_summary: str = ""
    outcome: str = ""             # success | improved | failed | rolled_back
    rollback_reason: str = ""
    successful_step: str = ""
    failure_category: str = ""
    attempt_fingerprint: str = ""  # computed via compute_attempt_fingerprint

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "EngineeringRecord":
        known = set(EngineeringRecord.__dataclass_fields__)
        return EngineeringRecord(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# Persistence — same pattern as core/coding_task.py: one small JSON file,
# atomic writes (temp file + os.replace), fail-safe on missing/corrupt data.
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".engineering_memory_", suffix=".tmp")
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
    exception — a coding task must never crash because memory is broken."""
    if not STATE_FILE.exists():
        return []
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    raw_records = data.get("records", [])
    if not isinstance(raw_records, list):
        return []
    records = []
    for r in raw_records:
        if not isinstance(r, dict):
            continue
        try:
            records.append(EngineeringRecord.from_dict(r))
        except Exception:
            continue
    return records


def _prune_low_value_first(records: list, keep_n: int) -> list:
    """Bounded retention: keep newest records, dropping the oldest
    failed/rolled_back ones first, then oldest overall if still over
    budget."""
    if len(records) <= keep_n:
        return records
    low  = sorted([r for r in records if r.outcome in _LOW_VALUE_OUTCOMES], key=lambda r: r.timestamp)
    high = sorted([r for r in records if r.outcome not in _LOW_VALUE_OUTCOMES], key=lambda r: r.timestamp)
    to_remove = len(records) - keep_n
    remove_ids = set()
    for r in low + high:  # low-value, oldest-first, considered for removal before any high-value record
        if len(remove_ids) >= to_remove:
            break
        remove_ids.add(r.record_id)
    return [r for r in records if r.record_id not in remove_ids]


def _prune_all(records: list) -> list:
    by_project: dict = {}
    for r in records:
        by_project.setdefault(r.project_key, []).append(r)

    kept: list = []
    for _key, recs in by_project.items():
        kept.extend(_prune_low_value_first(recs, MAX_RECORDS_PER_PROJECT))

    if len(kept) > MAX_TOTAL_RECORDS:
        kept = _prune_low_value_first(kept, MAX_TOTAL_RECORDS)
    return kept


def _save_all(records: list) -> None:
    records = _prune_all(records)
    _atomic_write_json(STATE_FILE, {"records": [r.to_dict() for r in records]})


def record_outcome(
    task,
    operation_type: str,
    goal_summary: str,
    normalized_error_signature: str,
    evidence_summary: str,
    impact_summary: str,
    files_touched: list,
    attempt_summary: str,
    outcome: str,
    rollback_reason: str = "",
    successful_step: str = "",
    failure_category: str = "",
) -> EngineeringRecord:
    """Record one engineering outcome — deterministic, no LLM call. Fails
    safe: if persistence itself fails (e.g. disk issue), the exception is
    swallowed so the coding task's own execution is never interrupted by a
    memory-write problem."""
    files_touched = list(files_touched or [])[:MAX_FILES_TOUCHED]
    record = EngineeringRecord(
        record_id=uuid.uuid4().hex[:12],
        task_id=getattr(task, "task_id", "") or "",
        project_key=project_key(getattr(task, "project_root", "") or ""),
        timestamp=_now(),
        operation_type=operation_type,
        goal_summary=_truncate(goal_summary, MAX_GOAL_CHARS),
        normalized_error_signature=_truncate(normalized_error_signature, MAX_SIGNATURE_CHARS),
        evidence_summary=_truncate(evidence_summary, MAX_SUMMARY_CHARS),
        impact_summary=_truncate(impact_summary, MAX_SUMMARY_CHARS),
        files_touched=files_touched,
        attempt_summary=_truncate(attempt_summary, MAX_SUMMARY_CHARS),
        outcome=outcome,
        rollback_reason=_truncate(rollback_reason, MAX_SUMMARY_CHARS),
        successful_step=_truncate(successful_step, 80),
        failure_category=_truncate(failure_category, 80),
        attempt_fingerprint=compute_attempt_fingerprint(
            operation_type, normalized_error_signature, files_touched, attempt_summary,
        ),
    )
    try:
        records = _load_all()
        records.append(record)
        _save_all(records)
    except Exception as e:
        print(f"[EngineeringMemory] Failed to record outcome (non-fatal): {e}")
    return record


# ---------------------------------------------------------------------------
# Deterministic search / recall — no LLM involved.
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set:
    return set(re.findall(r"[a-z0-9']+", (text or "").lower()))


def find_relevant_for_error(
    pkey: str, normalized_error_signature: str, operation_type: str = "runtime_fix",
    limit: int = MAX_RECALL_RESULTS,
) -> list:
    """Bounded recall for a runtime-fix attempt. Ranking (highest first):
    exact normalized-signature match > same operation_type > recency.
    Only same-project records are ever considered."""
    try:
        records = [r for r in _load_all() if r.project_key == pkey]
    except Exception:
        return []

    scored = []
    for r in records:
        score = 0.0
        if r.normalized_error_signature and r.normalized_error_signature == normalized_error_signature:
            score += 10.0
        if r.operation_type == operation_type:
            score += 1.0
        if score > 0:
            scored.append((score, r.timestamp, r))

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)  # highest score first; ties -> most recent first
    return [r for _, _, r in scored[:limit]]


def find_relevant_for_change(
    pkey: str, goal_summary: str, impacted_files: list, limit: int = MAX_RECALL_RESULTS,
) -> list:
    """Bounded recall for an incremental feature change. Ranking (highest
    first): overlapping impacted files > overlapping goal wording >
    recency. Only same-project, same-operation-type (feature_change)
    records are ever considered."""
    try:
        records = [
            r for r in _load_all()
            if r.project_key == pkey and r.operation_type == "feature_change"
        ]
    except Exception:
        return []

    goal_words = _tokenize(goal_summary)
    target_set = set(impacted_files or [])

    scored = []
    for r in records:
        file_overlap = len(target_set & set(r.files_touched))
        word_overlap = len(goal_words & _tokenize(r.goal_summary))
        score = 2.0 * file_overlap + word_overlap
        if score > 0:
            scored.append((score, r.timestamp, r))

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)  # highest score first; ties -> most recent first
    return [r for _, _, r in scored[:limit]]


def find_matching_failed_attempt(pkey: str, fingerprint: str):
    """Returns the most recent failed/rolled_back record for this project
    whose attempt_fingerprint exactly matches, or None. Used to detect a
    materially identical approach that already failed — not a brittle
    exact-string check, since the fingerprint itself is built from
    normalized (word-set, sorted) metadata."""
    try:
        candidates = [
            r for r in _load_all()
            if r.project_key == pkey
            and r.outcome in _LOW_VALUE_OUTCOMES
            and r.attempt_fingerprint == fingerprint
        ]
    except Exception:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda r: r.timestamp, reverse=True)
    return candidates[0]


def summarize_records(records: list, max_chars: int = 500) -> str:
    """Bounded, human/AI-readable summary of recalled records — file paths,
    signatures, and outcomes only, never source content or prompts."""
    lines = []
    for r in records:
        files = ", ".join(r.files_touched[:5]) or "(none)"
        detail = f"- [{r.operation_type}] {r.normalized_error_signature or r.goal_summary} -> {r.outcome}"
        if r.rollback_reason:
            detail += f" ({r.rollback_reason})"
        detail += f" — files: {files}"
        lines.append(detail)
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"
