#core/contacts.py
"""
Lightweight, JARVIS-owned contact resolution — reuses the existing
memory/long_term.json "relationships" section rather than creating a new
store. Provides just enough to satisfy contact-safety requirements
(detect ambiguity among *known* contacts) without ever having real
visibility into WhatsApp's own contact list — the existing WhatsApp
backend has no such visibility either (see actions/send_message.py: it
types a name into WhatsApp's own search box and lets WhatsApp resolve
it). See readme/limitations for what this can and can't guarantee.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _memory_path() -> Path:
    return _base_dir() / "memory" / "long_term.json"


def _load_relationships() -> dict:
    try:
        data = json.loads(_memory_path().read_text(encoding="utf-8"))
        return data.get("relationships", {}) or {}
    except Exception:
        return {}


def find_matches(query: str) -> list:
    """Canonical display names of saved contacts matching query
    (case-insensitive substring, either direction)."""
    query = (query or "").strip().lower()
    if not query:
        return []
    matches = []
    for key, entry in _load_relationships().items():
        canonical = entry.get("value") if isinstance(entry, dict) else str(entry)
        canonical = canonical or key
        key_l, canon_l = str(key).lower(), str(canonical).lower()
        if query in key_l or key_l in query or query in canon_l or canon_l in query:
            if canonical not in matches:
                matches.append(canonical)
    return matches


def resolve(query: str) -> tuple:
    """
    Returns (status, data):
      "resolved"   data = canonical name         -- exactly one known match
      "ambiguous"  data = list[canonical names]   -- 2+ known matches
      "unresolved" data = None                    -- no known contact matches;
                                                      caller decides how to proceed
                                                      (see actions/send_message.py's
                                                      whatsapp_send for the policy used)
    """
    matches = find_matches(query)
    if len(matches) == 1:
        return "resolved", matches[0]
    if len(matches) > 1:
        return "ambiguous", matches
    return "unresolved", None
