#core/capability_gap.py
"""
Capability Gap Detection v1: deterministically answers, for a given task
request, "does Mark currently expose a capability for this, or is this a
gap?" This is the first foundation for the Product Vision's Capability First
Principle and any future controlled self-learning — NOT autonomous
capability installation, NOT autonomous research, NOT self-modification.

Capability inventory is derived from REAL registration evidence, never a
hand-maintained fantasy list: main.py's TOOL_DECLARATIONS list (the actual
tool-calling metadata JARVIS exposes to the model) is extracted via AST
literal parsing of main.py's own source text, and cross-checked against
main.py's tool-dispatch `elif name == "..."` chain (a second, independent
piece of static evidence that a declared capability has a real runtime
handler, not just a description). main.py itself is never imported/executed
here — it has heavy, side-effecting imports (audio devices, a live GenAI
client, a Qt UI) that have no place in a small deterministic detector.

core/learning_engine.py is consulted only as bounded, clearly-separated
background knowledge (never scored, never proof of capability existence).
Per PRODUCT_VISION.md and this module's own Trust/Authority Rule: Learning
Engine content (which indexes README/ROADMAP/PRODUCT_VISION.md/MODULES/*.md
alongside real docstrings) can describe a FUTURE or VISION capability that
has no executable implementation at all — that must never be reported as an
existing capability. Only main.py's actual registered tool inventory counts
as evidence that a capability exists.

This module never calls learn(), never calls an AI provider, never executes
any capability, and never modifies any file.
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

from core import learning_engine as le

MAX_TASK_CHARS = 500
MAX_EVIDENCE_CHARS = 500
MAX_MATCHED_CAPABILITIES = 10
MAX_BACKGROUND_KNOWLEDGE_ITEMS = 3

NAME_MATCH_WEIGHT = 3.0
DESC_MATCH_WEIGHT = 1.0

STRONG_MATCH_MIN_SCORE = 3.0
STRONG_MATCH_MIN_RATIO = 0.34  # top match must explain >= ~1/3 of the task's own tokens
PARTIAL_MATCH_MIN_SCORE = 1.0
MIN_TASK_TOKENS = 2  # fewer meaningful tokens than this -> ambiguous, never classified

CONFIDENCE_HIGH      = "high"       # capability exists
CONFIDENCE_PARTIAL   = "partial"    # capability partially matches
CONFIDENCE_NONE      = "none"       # capability is missing
CONFIDENCE_AMBIGUOUS = "ambiguous"  # task too ambiguous to classify confidently

CLASSIFICATION_EXISTS    = "capability_exists"
CLASSIFICATION_PARTIAL   = "partial_match"
CLASSIFICATION_MISSING   = "capability_missing"
CLASSIFICATION_AMBIGUOUS = "ambiguous"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


MAIN_MODULE_PATH = _base_dir() / "main.py"

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


def _normalize(text: str) -> frozenset:
    """Deterministic normalization: lowercase, tokenize, drop stopwords and
    single-character tokens. Same input (regardless of surrounding
    whitespace/casing/word order) always yields the same token set."""
    tokens = _TOKEN_RE.findall((text or "").lower())
    return frozenset(t for t in tokens if t not in _STOPWORDS and len(t) > 1)


# ---------------------------------------------------------------------------
# Capability inventory — derived from main.py's real registration metadata.
# ---------------------------------------------------------------------------

@dataclass
class CapabilityRecord:
    name: str
    description: str
    has_dispatch_handler: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_tool_declarations(source: str) -> list:
    """
    AST-parses main.py's source text and extracts the TOOL_DECLARATIONS
    list-literal assignment via ast.literal_eval — the exact structure
    JARVIS's tool-calling layer registers. Never executes main.py. Fails
    safe (returns []) if the file can't be parsed or the assignment isn't a
    plain literal list of dicts.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "TOOL_DECLARATIONS" for t in node.targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except Exception:
            return []
        if isinstance(value, list):
            return [d for d in value if isinstance(d, dict)]
    return []


_DISPATCH_NAME_RE = re.compile(r'(?:if|elif)\s+name\s*==\s*"([^"]+)"')


def _extract_dispatch_names(source: str) -> frozenset:
    """A second, independent static-evidence signal: names actually handled
    by main.py's tool-dispatch chain (`if/elif name == "..."`), not merely
    declared. Regex over source text — no execution."""
    return frozenset(_DISPATCH_NAME_RE.findall(source))


def build_inventory(main_source: str | None = None) -> list:
    """
    Builds the capability inventory from real registration evidence. Reads
    main.py's own source text (never imports/executes it) unless
    `main_source` is supplied directly (used by tests to exercise this
    logic against a small, controlled fixture instead of the evolving real
    file). Fails safe to an empty inventory if the file is missing/unreadable
    or unparsable — this never raises.
    """
    if main_source is None:
        try:
            main_source = MAIN_MODULE_PATH.read_text(encoding="utf-8")
        except Exception:
            return []

    declarations = _extract_tool_declarations(main_source)
    dispatched = _extract_dispatch_names(main_source)

    records = []
    seen_names = set()
    for d in declarations:
        name = str(d.get("name") or "").strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        records.append(CapabilityRecord(
            name=name,
            description=str(d.get("description") or "").strip()[:MAX_EVIDENCE_CHARS],
            has_dispatch_handler=name in dispatched,
        ))
    return records


# ---------------------------------------------------------------------------
# Deterministic matching — no LLM call anywhere in this module.
# ---------------------------------------------------------------------------

def _capability_tokens(cap) -> tuple:
    name_tokens = _normalize(cap.name.replace("_", " "))
    desc_tokens = _normalize(cap.description)
    return name_tokens, desc_tokens


def _score(task_tokens: frozenset, cap) -> tuple:
    """Returns (score, name_overlap_count, total_overlap_count). Name-token
    overlap (the task referencing the capability's own name) is weighted
    higher than a plain description-word overlap. total_overlap_count (the
    union of matched name+description tokens) is used separately to compute
    a match RATIO -- one incidental shared word (e.g. a task that happens to
    contain "status" among eight other words, matching system_status's name)
    must not, on its own, be enough to claim a capability confidently exists;
    a real match should explain a meaningful share of the task's own words."""
    name_tokens, desc_tokens = _capability_tokens(cap)
    name_overlap = len(task_tokens & name_tokens)
    desc_overlap = len(task_tokens & desc_tokens)
    total_overlap = len(task_tokens & (name_tokens | desc_tokens))
    score = NAME_MATCH_WEIGHT * name_overlap + DESC_MATCH_WEIGHT * desc_overlap
    return score, name_overlap, total_overlap


def _gather_background_knowledge(task: str, limit: int = MAX_BACKGROUND_KNOWLEDGE_ITEMS) -> list:
    """
    Best-effort, read-only, bounded background context from Learning
    Engine's EXISTING knowledge store. Never calls learn(). Any failure
    (missing/corrupt state, search() raising) yields an empty list — a
    Learning Engine problem must never fail gap detection.

    This result is informational ONLY: it is never part of the deterministic
    match score above, and it never changes `confidence`, `gap_detected`, or
    `missing_capability`. Learning Engine indexes this project's own
    documentation, including PRODUCT_VISION.md's long-term, unimplemented
    Tracks — describing a future capability is not evidence that capability
    exists today.
    """
    try:
        units = le.search(task, limit=limit) or []
    except Exception:
        return []
    return [
        {"section_title": u.section_title, "source_paths": list(u.source_paths[:2])}
        for u in units
    ]


# ---------------------------------------------------------------------------
# Result model + public entry point
# ---------------------------------------------------------------------------

@dataclass
class GapResult:
    requested_task: str
    required_capability: str
    matched_capabilities: list = field(default_factory=list)
    missing_capability: bool = False
    gap_detected: object = False   # bool, or None when confidence == "ambiguous"
    confidence: str = CONFIDENCE_NONE
    evidence: str = ""
    background_knowledge: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def detect_gap(task: str, inventory: list | None = None, consult_knowledge: bool = True) -> GapResult:
    """
    The public entry point. Given a natural-language task/request, returns a
    bounded, deterministic GapResult. Does not execute the task, does not
    call learn(), does not call an AI provider, and does not modify any
    capability, file, or configuration.
    """
    task = (task or "").strip()[:MAX_TASK_CHARS]
    task_tokens = _normalize(task)

    if inventory is None:
        inventory = build_inventory()

    background_knowledge = (
        _gather_background_knowledge(task) if (consult_knowledge and task) else []
    )

    if len(task_tokens) < MIN_TASK_TOKENS:
        return GapResult(
            requested_task=task,
            required_capability=" ".join(sorted(task_tokens)),
            matched_capabilities=[],
            missing_capability=False,
            gap_detected=None,
            confidence=CONFIDENCE_AMBIGUOUS,
            evidence="Too few distinguishing terms in the request to classify confidently.",
            background_knowledge=background_knowledge,
        )

    required_capability = " ".join(sorted(task_tokens))

    scored = []
    for cap in inventory:
        s, name_overlap, total_overlap = _score(task_tokens, cap)
        if s > 0:
            scored.append((s, name_overlap, total_overlap, cap))
    scored.sort(key=lambda t: (-t[0], t[3].name))

    if not scored:
        return GapResult(
            requested_task=task,
            required_capability=required_capability,
            matched_capabilities=[],
            missing_capability=True,
            gap_detected=True,
            confidence=CONFIDENCE_NONE,
            evidence=(
                f"No registered capability's name/description overlapped with: "
                f"{', '.join(sorted(task_tokens))}."
            )[:MAX_EVIDENCE_CHARS],
            background_knowledge=background_knowledge,
        )

    top_score, top_name_overlap, top_total_overlap, top_cap = scored[0]
    matched_names = [cap.name for _, _, _, cap in scored[:MAX_MATCHED_CAPABILITIES]]
    match_ratio = top_total_overlap / len(task_tokens) if task_tokens else 0.0

    if (
        top_name_overlap >= 1
        and top_score >= STRONG_MATCH_MIN_SCORE
        and match_ratio >= STRONG_MATCH_MIN_RATIO
    ):
        confidence = CONFIDENCE_HIGH
        gap_detected = False
        missing = False
    elif top_score >= PARTIAL_MATCH_MIN_SCORE:
        confidence = CONFIDENCE_PARTIAL
        gap_detected = True
        missing = False
    else:
        # Unreachable with the current NAME/DESC match weights (any score > 0
        # is already >= PARTIAL_MATCH_MIN_SCORE) -- kept explicit so a future
        # change to those weights can't silently misclassify a very weak,
        # near-zero overlap as a genuine partial match.
        confidence = CONFIDENCE_NONE
        gap_detected = True
        missing = True

    evidence = (
        f"Best match: '{top_cap.name}' (score={top_score:.1f}, name_overlap={top_name_overlap}, "
        f"match_ratio={match_ratio:.2f}, "
        f"registered={'yes, has dispatch handler' if top_cap.has_dispatch_handler else 'declared only, no dispatch handler found'})."
    )[:MAX_EVIDENCE_CHARS]

    return GapResult(
        requested_task=task,
        required_capability=required_capability,
        matched_capabilities=matched_names,
        missing_capability=missing,
        gap_detected=gap_detected,
        confidence=confidence,
        evidence=evidence,
        background_knowledge=background_knowledge,
    )
