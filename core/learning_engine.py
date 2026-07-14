#core/learning_engine.py
"""
Learning Engine v1: deterministic knowledge acquisition from this project's own
local repository documentation (README/Markdown docs, ROADMAP.md,
PRODUCT_VISION.md, ARCHITECTURE.md, MODULES/, DECISIONS/, JARVIS_STATE.md, any
other *.md file) and bounded source-code docstrings (module/class/function,
via AST — never full function bodies).

Not a second search engine and not an LLM-driven summarizer: `learn()` is a
deterministic scan (no AI provider call anywhere in this module) that detects
new/changed source documents (whole-file content hash vs. a persisted
manifest), extracts small bounded knowledge units, deduplicates identical
content by hash, and persists atomically (same convention as
core/engineering_memory.py and core/coding_task.py: one small JSON file under
config/state/, gitignored, temp-file + os.replace).

Never stores: full file contents, full function bodies, full docstrings
beyond MAX_SUMMARY_CHARS, API keys, credentials, or anything core/workspace.py
classifies as sensitive.

v1 explicitly does NOT do: autonomous/background learning, scheduled or
file-watch triggers, internet access, self-modification, capability
installation, or wiring into any existing pipeline (actions/dev_agent.py,
core/coding_orchestrator.py). It is a standalone Core Service exposing a small
search/query API for future Mark capabilities to call.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from core import workspace as ws

MAX_SUMMARY_CHARS = 400
MAX_UNITS_PER_FILE = 40
MAX_UNITS_TOTAL = 800

_MD_SUFFIXES = frozenset({".md"})
_PY_SUFFIXES = frozenset({".py"})

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STATE_DIR = _base_dir() / "config" / "state"
STATE_FILE = STATE_DIR / "learning_engine.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeUnit:
    unit_id: str
    content_hash: str
    source_type: str          # "markdown_section" | "docstring"
    source_paths: list = field(default_factory=list)
    section_title: str = ""
    summary: str = ""
    first_seen_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "KnowledgeUnit":
        known = set(KnowledgeUnit.__dataclass_fields__)
        return KnowledgeUnit(**{k: v for k, v in d.items() if k in known})


@dataclass
class LearnReport:
    files_scanned: int = 0
    files_changed: int = 0
    files_removed: int = 0
    units_added: int = 0
    units_deduplicated: int = 0
    units_pruned: int = 0
    total_units: int = 0
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Persistence — same convention as core/engineering_memory.py: one small JSON
# file, atomic writes (temp file + os.replace), fail-safe on missing/corrupt
# data.
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".learning_engine_", suffix=".tmp")
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


def _load_state() -> dict:
    """Fail-safe: a missing or corrupt file yields an empty store, never an
    exception."""
    empty = {"manifest": {}, "units": {}}
    if not STATE_FILE.exists():
        return empty
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return empty
    if not isinstance(data, dict):
        return empty
    manifest = data.get("manifest", {})
    raw_units = data.get("units", {})
    if not isinstance(manifest, dict):
        manifest = {}
    if not isinstance(raw_units, dict):
        raw_units = {}

    units: dict = {}
    for unit_id, d in raw_units.items():
        if not isinstance(d, dict):
            continue
        try:
            units[unit_id] = KnowledgeUnit.from_dict(d)
        except Exception:
            continue
    return {"manifest": manifest, "units": units}


def _save_state(manifest: dict, units: dict) -> None:
    _atomic_write_json(STATE_FILE, {
        "manifest": manifest,
        "units": {uid: u.to_dict() for uid, u in units.items()},
    })


# ---------------------------------------------------------------------------
# Extraction — bounded, deterministic, no LLM call.
# ---------------------------------------------------------------------------

def _read_text(path: Path, max_bytes: int = 2_000_000) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return None
        data = path.read_bytes()
        if b"\x00" in data[:2048]:
            return None
        return data.decode("utf-8", errors="replace")
    except Exception:
        return None


def _extract_markdown_sections(text: str) -> list[tuple[str, str]]:
    """Returns (section_title, section_body) pairs, split on '#'/'##'/'###'
    heading lines. Text before the first heading (if any) becomes one
    "(preamble)" section so short docs without headings still yield a unit."""
    matches = list(_HEADING_RE.finditer(text))
    sections: list[tuple[str, str]] = []

    if not matches:
        body = text.strip()
        if body:
            sections.append(("(preamble)", body))
        return sections

    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append(("(preamble)", preamble))

    for i, m in enumerate(matches):
        title = m.group(2).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        sections.append((title, body))

    return sections


def _extract_docstring_units(text: str, rel_path: str) -> list[tuple[str, str]]:
    """Returns (section_title, docstring_text) pairs for the module docstring
    plus every top-level class/function docstring. Nested symbols and full
    function bodies are never read — only ast.get_docstring() output."""
    try:
        tree = ast.parse(text, filename=rel_path)
    except SyntaxError:
        return []

    units: list[tuple[str, str]] = []
    mod_doc = ast.get_docstring(tree)
    if mod_doc:
        units.append((rel_path, mod_doc))

    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            if doc:
                units.append((f"{rel_path}::{node.name}", doc))

    return units


def _units_for_file(workspace: Path, rel_path: str) -> list[tuple[str, str, str]]:
    """Returns (source_type, section_title, body_text) triples for one
    source file, bounded to MAX_UNITS_PER_FILE."""
    p = workspace / rel_path
    if ws.is_sensitive(p):
        return []
    suffix = Path(rel_path).suffix.lower()
    if suffix not in _MD_SUFFIXES and suffix not in _PY_SUFFIXES:
        return []

    text = _read_text(p)
    if text is None:
        return []

    out: list[tuple[str, str, str]] = []
    if suffix in _MD_SUFFIXES:
        for title, body in _extract_markdown_sections(text):
            if body:
                out.append(("markdown_section", title, body))
    else:
        for title, doc in _extract_docstring_units(text, rel_path):
            out.append(("docstring", title, doc))

    return out[:MAX_UNITS_PER_FILE]


# ---------------------------------------------------------------------------
# learn() — the only write path.
# ---------------------------------------------------------------------------

def _discoverable_files(workspace: Path) -> list[str]:
    return [
        rel for rel in ws.list_files(workspace)
        if Path(rel).suffix.lower() in _MD_SUFFIXES or Path(rel).suffix.lower() in _PY_SUFFIXES
    ]


def _prune_oldest_first(units: dict, keep_n: int) -> tuple[dict, int]:
    if len(units) <= keep_n:
        return units, 0
    ordered = sorted(units.values(), key=lambda u: u.updated_at)
    to_remove = len(units) - keep_n
    remove_ids = {u.unit_id for u in ordered[:to_remove]}
    kept = {uid: u for uid, u in units.items() if uid not in remove_ids}
    return kept, len(remove_ids)


def learn(workspace: Path | None = None) -> LearnReport:
    """Deterministic knowledge-acquisition pass. Scans every discoverable
    *.md and *.py file in the active workspace, re-processing only files
    whose whole-content hash differs from the persisted manifest (or that
    are new). Files no longer present have their units removed. Extracted
    units are deduplicated by content hash across the whole store. No AI
    provider call anywhere in this function."""
    started = time.monotonic()
    workspace = workspace or ws.get_workspace()
    state = _load_state()
    manifest: dict = dict(state["manifest"])
    units: dict = dict(state["units"])

    report = LearnReport()

    current_files = _discoverable_files(workspace)
    current_set = set(current_files)
    report.files_scanned = len(current_files)

    # Removed files: drop their previously recorded units.
    removed_paths = [rel for rel in manifest.keys() if rel not in current_set]
    for rel in removed_paths:
        entry = manifest.pop(rel, None)
        if not entry:
            continue
        for uid in entry.get("unit_ids", []):
            u = units.get(uid)
            if not u:
                continue
            u.source_paths = [p for p in u.source_paths if p != rel]
            if not u.source_paths:
                units.pop(uid, None)
        report.files_removed += 1

    for rel in current_files:
        p = workspace / rel
        text = _read_text(p)
        if text is None:
            continue
        file_hash = _sha256(text)
        prior = manifest.get(rel)
        if prior and prior.get("file_hash") == file_hash:
            continue  # unchanged — skip re-processing

        report.files_changed += 1

        # Drop this file's previously recorded units before re-extracting,
        # so a changed file's stale sections don't linger.
        if prior:
            for uid in prior.get("unit_ids", []):
                u = units.get(uid)
                if not u:
                    continue
                u.source_paths = [sp for sp in u.source_paths if sp != rel]
                if not u.source_paths:
                    units.pop(uid, None)

        new_unit_ids: list[str] = []
        for source_type, title, body in _units_for_file(workspace, rel):
            summary = _truncate(body)
            content_hash = _sha256(f"{source_type}|{summary}")
            unit_id = content_hash[:16]
            now = _now()
            existing = units.get(unit_id)
            if existing:
                if rel not in existing.source_paths:
                    existing.source_paths.append(rel)
                existing.updated_at = now
                report.units_deduplicated += 1
            else:
                units[unit_id] = KnowledgeUnit(
                    unit_id=unit_id,
                    content_hash=content_hash,
                    source_type=source_type,
                    source_paths=[rel],
                    section_title=title,
                    summary=summary,
                    first_seen_at=now,
                    updated_at=now,
                )
                report.units_added += 1
            new_unit_ids.append(unit_id)

        manifest[rel] = {"file_hash": file_hash, "unit_ids": new_unit_ids}

    units, pruned = _prune_oldest_first(units, MAX_UNITS_TOTAL)
    report.units_pruned = pruned
    report.total_units = len(units)

    try:
        _save_state(manifest, units)
    except Exception as e:
        print(f"[LearningEngine] Failed to persist knowledge (non-fatal): {e}")

    report.duration_ms = int((time.monotonic() - started) * 1000)
    return report


# ---------------------------------------------------------------------------
# Deterministic search / recall — no LLM involved.
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set:
    return set(re.findall(r"[a-z0-9']+", (text or "").lower()))


def search(query: str, limit: int = 5) -> list:
    """Deterministic word-overlap search over stored knowledge units, ranked
    by score (title match weighted higher than body match) then most-
    recently-updated. Returns [] for an empty query or empty store."""
    query = (query or "").strip()
    if not query:
        return []
    q_words = _tokenize(query)
    if not q_words:
        return []

    units = list(_load_state()["units"].values())
    scored = []
    for u in units:
        title_overlap = len(q_words & _tokenize(u.section_title))
        body_overlap = len(q_words & _tokenize(u.summary))
        score = 2.0 * title_overlap + body_overlap
        if score > 0:
            scored.append((score, u.updated_at, u))

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [u for _, _, u in scored[:limit]]


def get_unit(unit_id: str):
    return _load_state()["units"].get(unit_id)


def stats() -> dict:
    state = _load_state()
    units = state["units"]
    manifest = state["manifest"]
    last_learned_at = None
    if units:
        last_learned_at = max((u.updated_at for u in units.values()), default=None)
    return {
        "total_units": len(units),
        "total_files_tracked": len(manifest),
        "last_learned_at": last_learned_at,
    }
