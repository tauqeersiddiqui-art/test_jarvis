#core/learning_source_planner.py
"""
Learning Source Planner v1: deterministically converts an APPROVED
core/learning_task.py LearningTask into a bounded LearningSourcePlan
describing what KINDS of sources should be acquired before Mark attempts to
learn the missing capability -- what must be learned, what source
categories are appropriate, what authority level those sources should have,
and what source types must be avoided.

This is PLANNING ONLY. It never performs research, never browses the
internet, never fetches a URL, never calls actions/web_search.py, never
calls an AI provider, never generates code, and never installs an MCP,
plugin, package, or tool. A LearningSourcePlan is not evidence, not
knowledge, and not permission to access, browse, or execute anything -- it
only describes what a future, separately approved acquisition/validation
workflow (which does not exist yet) should prefer and avoid once it runs.

Domain and authority classification are pure, fixed, deterministic keyword
rules over the task's own request text -- no LLM, and deliberately no
consultation of core/learning_engine.py at all. This is intentional: Track S
and the Capability First Principle in PRODUCT_VISION.md are product
requirements, not implementation evidence, and this module's classification
must never be influenced by learned documentation or Product Vision text,
which could describe a domain's importance without that description being
authoritative about the real world. Keeping this module free of any
Learning Engine dependency removes that risk by construction rather than by
convention.

Persistence reuses the same single-JSON-file, gitignored, atomic-write
convention already used by core/coding_task.py, core/engineering_memory.py,
core/execution_ledger.py, and core/learning_task.py -- no new persistence
mechanism, and no new orchestrator.
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

from core import learning_task as lt

MAX_CAPABILITY_CHARS = 300
MAX_RATIONALE_CHARS = 500
MAX_CATEGORIES = 8
MAX_PLANS = 200  # bounded retention -- oldest-updated pruned first


# ---------------------------------------------------------------------------
# Fixed vocabularies -- never dynamically invented, never LLM-derived.
# ---------------------------------------------------------------------------

SOURCE_CATEGORIES = frozenset({
    "official_documentation",
    "government_source",
    "api_reference",
    "local_repository",
    "project_documentation",
    "standards_source",
    "approved_mcp",
    "approved_open_source_repository",
})

AUTHORITY_AUTHORITATIVE   = "authoritative"
AUTHORITY_PRIMARY         = "primary"
AUTHORITY_TRUSTED_TECH    = "trusted_technical"
AUTHORITY_LOCAL_PROJECT   = "local_project"
AUTHORITY_SUPPLEMENTARY   = "supplementary"

AUTHORITY_LEVELS = frozenset({
    AUTHORITY_AUTHORITATIVE, AUTHORITY_PRIMARY, AUTHORITY_TRUSTED_TECH,
    AUTHORITY_LOCAL_PROJECT, AUTHORITY_SUPPLEMENTARY,
})

DOMAIN_GOVERNMENT_PROPERTY = "government_property"
DOMAIN_LEGAL_REGULATORY    = "legal_regulatory"
DOMAIN_FINANCIAL           = "financial"
DOMAIN_MEDICAL             = "medical"
DOMAIN_SOFTWARE_API        = "software_api"
DOMAIN_SOFTWARE_REPOSITORY = "software_repository"
DOMAIN_HARDWARE_DEVICE     = "hardware_device"
DOMAIN_GENERAL_KNOWLEDGE   = "general_knowledge"
DOMAIN_UNKNOWN             = "unknown"

DOMAIN_CLASSES = frozenset({
    DOMAIN_GOVERNMENT_PROPERTY, DOMAIN_LEGAL_REGULATORY, DOMAIN_FINANCIAL,
    DOMAIN_MEDICAL, DOMAIN_SOFTWARE_API, DOMAIN_SOFTWARE_REPOSITORY,
    DOMAIN_HARDWARE_DEVICE, DOMAIN_GENERAL_KNOWLEDGE, DOMAIN_UNKNOWN,
})

# High-stakes domains: must prefer authoritative/primary sources (per spec).
HIGH_STAKES_DOMAINS = frozenset({
    DOMAIN_GOVERNMENT_PROPERTY, DOMAIN_LEGAL_REGULATORY, DOMAIN_FINANCIAL, DOMAIN_MEDICAL,
})


class Status:
    DRAFT = "draft"


_VALID_PLAN_STATUSES = frozenset({Status.DRAFT})


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STATE_DIR  = _base_dir() / "config" / "state"
STATE_FILE = STATE_DIR / "learning_source_plans.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Deterministic domain classification -- keyword-set overlap only, no LLM.
#
# Each domain has "strong" indicators (specific enough that ONE match is
# sufficient) and "weak" indicators (common enough that at least TWO matches
# are required). This two-tier design exists specifically so a single
# incidental word (e.g. "license" appearing in an ordinary software
# question) can never, on its own, falsely classify a task into a
# high-stakes domain like legal_regulatory.
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


def _normalize(text: str) -> frozenset:
    tokens = _TOKEN_RE.findall((text or "").lower())
    return frozenset(t for t in tokens if t not in _STOPWORDS and len(t) > 1)


_DOMAIN_KEYWORDS = {
    DOMAIN_GOVERNMENT_PROPERTY: {
        "strong": frozenset({"rera", "cadastre", "zoning", "parcel", "deed", "landrecord", "landrecords"}),
        "weak": frozenset({"property", "land", "government", "registry", "municipal", "plot", "title", "estate"}),
    },
    DOMAIN_LEGAL_REGULATORY: {
        "strong": frozenset({"litigation", "statute", "statutes", "regulatory", "compliance", "jurisdiction", "regulation", "regulations"}),
        "weak": frozenset({"legal", "law", "laws", "contract", "court", "license", "licence"}),
    },
    DOMAIN_FINANCIAL: {
        "strong": frozenset({"investment", "mortgage", "taxation", "brokerage", "securities"}),
        "weak": frozenset({"financial", "finance", "money", "bank", "loan", "tax", "stock", "credit", "accounting"}),
    },
    DOMAIN_MEDICAL: {
        "strong": frozenset({"diagnosis", "clinical", "prescription", "symptom", "symptoms"}),
        "weak": frozenset({"medical", "health", "medicine", "patient", "disease", "doctor", "treatment"}),
    },
    DOMAIN_SOFTWARE_API: {
        "strong": frozenset({"api", "endpoint", "sdk", "webhook", "oauth", "endpoints"}),
        "weak": frozenset({"integration", "rest", "graphql", "authentication"}),
    },
    DOMAIN_SOFTWARE_REPOSITORY: {
        "strong": frozenset({"repository", "repositories", "repo", "codebase"}),
        "weak": frozenset({"architecture", "module", "refactor", "function"}),
    },
    DOMAIN_HARDWARE_DEVICE: {
        "strong": frozenset({"firmware", "microcontroller", "circuit", "gpio", "embedded"}),
        "weak": frozenset({"hardware", "device", "sensor"}),
    },
}


@dataclass
class DomainClassification:
    domain: str
    rationale: str


def classify_domain(text: str) -> DomainClassification:
    """
    Deterministic, bounded domain classification. Never guesses: an empty/
    near-empty task, or a genuine tie between two or more domains' scores,
    both resolve to DOMAIN_UNKNOWN rather than a falsely-confident pick. A
    non-empty task that matches no domain's keyword sets resolves to
    DOMAIN_GENERAL_KNOWLEDGE (some content, but nothing domain-specific).
    """
    tokens = _normalize(text)
    if not tokens:
        return DomainClassification(DOMAIN_UNKNOWN, "No meaningful terms to classify.")

    scored = []
    for domain, kw in _DOMAIN_KEYWORDS.items():
        strong_hits = tokens & kw["strong"]
        weak_hits = tokens & kw["weak"]
        qualifies = len(strong_hits) >= 1 or len(weak_hits) >= 2
        if qualifies:
            score = 2 * len(strong_hits) + len(weak_hits)
            scored.append((score, domain, strong_hits, weak_hits))

    if not scored:
        return DomainClassification(
            DOMAIN_GENERAL_KNOWLEDGE,
            "No domain-specific indicators matched; treated as general knowledge.",
        )

    scored.sort(key=lambda t: -t[0])
    top_score = scored[0][0]
    tied = [s for s in scored if s[0] == top_score]
    if len(tied) > 1:
        tied_domains = sorted(d for _, d, _, _ in tied)
        return DomainClassification(
            DOMAIN_UNKNOWN,
            f"Ambiguous -- tied indicators across multiple domains: {', '.join(tied_domains)}.",
        )

    _, domain, strong_hits, weak_hits = scored[0]
    return DomainClassification(
        domain,
        f"Matched domain indicators: strong={sorted(strong_hits)}, weak={sorted(weak_hits)}.",
    )


# ---------------------------------------------------------------------------
# Fixed per-domain source policy -- never dynamically invented, never
# LLM-derived. High-stakes domains (government_property, legal_regulatory,
# financial, medical) always require AUTHORITY_AUTHORITATIVE or
# AUTHORITY_PRIMARY.
# ---------------------------------------------------------------------------

_DOMAIN_POLICY = {
    DOMAIN_GOVERNMENT_PROPERTY: {
        "source_categories": ["government_source", "official_documentation", "standards_source"],
        "required_authority": AUTHORITY_AUTHORITATIVE,
        "preferred_source_types": ["government_source", "official_documentation"],
        "disallowed_source_types": ["local_repository", "approved_open_source_repository"],
    },
    DOMAIN_LEGAL_REGULATORY: {
        "source_categories": ["government_source", "official_documentation", "standards_source"],
        "required_authority": AUTHORITY_AUTHORITATIVE,
        "preferred_source_types": ["government_source", "official_documentation"],
        "disallowed_source_types": ["local_repository", "approved_open_source_repository"],
    },
    DOMAIN_FINANCIAL: {
        "source_categories": ["official_documentation", "standards_source"],
        "required_authority": AUTHORITY_PRIMARY,
        "preferred_source_types": ["official_documentation", "standards_source"],
        "disallowed_source_types": ["local_repository", "approved_open_source_repository"],
    },
    DOMAIN_MEDICAL: {
        "source_categories": ["official_documentation", "standards_source"],
        "required_authority": AUTHORITY_AUTHORITATIVE,
        "preferred_source_types": ["official_documentation", "standards_source"],
        "disallowed_source_types": ["local_repository", "approved_open_source_repository"],
    },
    DOMAIN_SOFTWARE_API: {
        "source_categories": ["official_documentation", "api_reference"],
        "required_authority": AUTHORITY_TRUSTED_TECH,
        "preferred_source_types": ["official_documentation", "api_reference"],
        "disallowed_source_types": ["government_source"],
    },
    DOMAIN_SOFTWARE_REPOSITORY: {
        "source_categories": ["local_repository", "project_documentation"],
        "required_authority": AUTHORITY_LOCAL_PROJECT,
        "preferred_source_types": ["local_repository", "project_documentation"],
        "disallowed_source_types": ["government_source"],
    },
    DOMAIN_HARDWARE_DEVICE: {
        "source_categories": ["official_documentation", "api_reference", "standards_source"],
        "required_authority": AUTHORITY_PRIMARY,
        "preferred_source_types": ["official_documentation", "standards_source"],
        "disallowed_source_types": ["government_source"],
    },
    DOMAIN_GENERAL_KNOWLEDGE: {
        "source_categories": ["official_documentation"],
        "required_authority": AUTHORITY_SUPPLEMENTARY,
        "preferred_source_types": ["official_documentation"],
        "disallowed_source_types": [],
    },
    DOMAIN_UNKNOWN: {
        "source_categories": ["local_repository", "project_documentation"],
        "required_authority": AUTHORITY_SUPPLEMENTARY,
        "preferred_source_types": ["local_repository", "project_documentation"],
        "disallowed_source_types": [],
    },
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class LearningSourcePlan:
    plan_id: str
    learning_task_id: str
    missing_capability: str
    domain: str
    source_categories: list
    required_authority: str
    preferred_source_types: list
    disallowed_source_types: list
    rationale: str
    status: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "LearningSourcePlan":
        known = set(LearningSourcePlan.__dataclass_fields__)
        return LearningSourcePlan(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# Persistence -- same pattern as core/learning_task.py: one small JSON file,
# atomic writes (temp file + os.replace), fail-safe on missing/corrupt data.
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".learning_source_plan_", suffix=".tmp")
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
    exception -- normal Mark execution must never crash because this planner
    is broken."""
    if not STATE_FILE.exists():
        return []
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    raw_plans = data.get("plans", [])
    if not isinstance(raw_plans, list):
        return []
    plans = []
    for p in raw_plans:
        if not isinstance(p, dict):
            continue
        try:
            plans.append(LearningSourcePlan.from_dict(p))
        except Exception:
            continue
    return plans


def _prune(plans: list) -> list:
    """Bounded retention: over MAX_PLANS, drop oldest-updated first."""
    if len(plans) <= MAX_PLANS:
        return plans
    ranked = sorted(plans, key=lambda p: p.updated_at)  # ascending: oldest first
    to_remove = len(plans) - MAX_PLANS
    remove_ids = {p.plan_id for p in ranked[:to_remove]}
    return [p for p in plans if p.plan_id not in remove_ids]


def _save_all(plans: list) -> None:
    plans = _prune(plans)
    _atomic_write_json(STATE_FILE, {"plans": [p.to_dict() for p in plans]})


def _persist(plans: list) -> None:
    """Fail-safe wrapper: a persistence failure is logged, never raised, so
    a broken plan store can never interrupt normal Mark execution (same
    convention as core/learning_task.py's create_from_gap())."""
    try:
        _save_all(plans)
    except Exception as e:
        print(f"[LearningSourcePlanner] Failed to persist (non-fatal): {e}")


def _bounded_categories(values: list) -> list:
    """Defensive enforcement of the fixed SOURCE_CATEGORIES vocabulary --
    any value outside it is dropped rather than persisted, and the result
    is capped at MAX_CATEGORIES."""
    return [v for v in values if v in SOURCE_CATEGORIES][:MAX_CATEGORIES]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_plan_from_task(learning_task) -> "LearningSourcePlan | None":
    """
    The ONLY write path that creates or updates a LearningSourcePlan.

    Accepts a core/learning_task.py LearningTask (or any duck-typed object
    exposing the same `status` / `task_id` / `requested_task` /
    `missing_capability` attributes).

    Returns None -- no plan created, no existing plan touched -- unless
    `learning_task.status == core.learning_task.Status.APPROVED`. Pending,
    learning, completed, failed, and rejected tasks never produce a plan.
    This function never reads or changes the LearningTask's own status --
    only core/learning_task.py's update_status() may do that, and this
    module never calls it.

    On a repeat call for the same learning_task_id, no new plan is created:
    the existing plan is recomputed in place (domain/policy/rationale are
    deterministic functions of the task's own text, so an unchanged task
    yields identical content) and `updated_at` refreshes, while `plan_id` is
    preserved.
    """
    if learning_task is None:
        return None
    if getattr(learning_task, "status", None) != lt.Status.APPROVED:
        return None

    task_id = getattr(learning_task, "task_id", "") or ""
    if not task_id:
        return None

    missing_capability = _truncate(getattr(learning_task, "missing_capability", "") or "", MAX_CAPABILITY_CHARS)
    requested_task = getattr(learning_task, "requested_task", "") or ""

    classification = classify_domain(f"{requested_task} {missing_capability}")
    policy = _DOMAIN_POLICY[classification.domain]

    source_categories = _bounded_categories(policy["source_categories"])
    preferred = _bounded_categories(policy["preferred_source_types"])
    disallowed = _bounded_categories(policy["disallowed_source_types"])
    required_authority = policy["required_authority"]
    rationale = _truncate(
        f"Domain classified as '{classification.domain}'. {classification.rationale}",
        MAX_RATIONALE_CHARS,
    )

    try:
        plans = _load_all()
    except Exception:
        plans = []

    existing = next((p for p in plans if p.learning_task_id == task_id), None)
    now = _now()

    if existing is not None:
        existing.missing_capability = missing_capability
        existing.domain = classification.domain
        existing.source_categories = source_categories
        existing.required_authority = required_authority
        existing.preferred_source_types = preferred
        existing.disallowed_source_types = disallowed
        existing.rationale = rationale
        existing.updated_at = now
        _persist(plans)
        return existing

    plan = LearningSourcePlan(
        plan_id=uuid.uuid4().hex[:12],
        learning_task_id=task_id,
        missing_capability=missing_capability,
        domain=classification.domain,
        source_categories=source_categories,
        required_authority=required_authority,
        preferred_source_types=preferred,
        disallowed_source_types=disallowed,
        rationale=rationale,
        status=Status.DRAFT,
        created_at=now,
        updated_at=now,
    )
    plans.append(plan)
    _persist(plans)
    return plan


def list_plans(status: str | None = None) -> list:
    plans = _load_all()
    if status:
        plans = [p for p in plans if p.status == status]
    return sorted(plans, key=lambda p: p.created_at)


def get_plan(plan_id: str):
    for p in _load_all():
        if p.plan_id == plan_id:
            return p
    return None


def find_by_task(learning_task_id: str):
    for p in _load_all():
        if p.learning_task_id == learning_task_id:
            return p
    return None


def stats() -> dict:
    plans = _load_all()
    by_domain: dict = {}
    for p in plans:
        by_domain[p.domain] = by_domain.get(p.domain, 0) + 1
    return {"total_plans": len(plans), "by_domain": by_domain}
