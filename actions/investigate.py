#actions/investigate.py
"""
Iterative codebase investigation: search -> inspect -> refine -> reason.

Builds a bounded, file:line-grounded evidence context from real search
results (never the whole repository) and hands it to AIProvider exactly
once, with strict instructions not to invent facts beyond the evidence.

Does not implement provider selection or failover itself — this module is a
consumer of core/ai_provider.complete_with_failover(), nothing more.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from core import workspace as ws
from actions import codebase_search as cs

MAX_EVIDENCE_CHARS = 9000
MAX_SNIPPET_CHARS = 1200
MAX_PRIMARY_FILES = 6
MAX_FOLLOWUP_FILES = 4
MAX_KEYWORDS = 6

_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "this", "that", "these", "those",
    "find", "where", "how", "what", "which", "who", "does", "do", "did", "for",
    "trace", "show", "explain", "investigate", "search", "project", "code",
    "of", "in", "on", "at", "to", "and", "or", "it", "its", "with", "from", "related",
    "used", "use", "uses", "function", "file", "files", "all", "direct", "calls",
    "call", "responsible", "error", "bug", "across", "reaches", "implemented",
})

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _extract_keywords(question: str) -> list[str]:
    """Prefer identifier-shaped tokens (snake_case/camelCase/CONST) and
    quoted phrases; fall back to plain words, stopword-filtered."""
    quoted_pairs = re.findall(r'"([^"]+)"|\'([^\']+)\'', question)
    quoted = [q[0] or q[1] for q in quoted_pairs]

    tokens = _IDENT_RE.findall(question)
    scored: list[tuple[str, int]] = []
    seen = set()
    for tok in tokens:
        low = tok.lower()
        if low in _STOPWORDS or len(tok) < 3 or low in seen:
            continue
        seen.add(low)
        weight = 1
        if "_" in tok or (tok != tok.lower() and tok != tok.upper()) or tok.isupper():
            weight = 3  # identifier-shaped token: snake_case / CamelCase / CONST
        scored.append((tok, weight))

    scored.sort(key=lambda t: -t[1])
    keywords = quoted + [t for t, _ in scored]
    out: list[str] = []
    for k in keywords:
        if k.lower() not in {x.lower() for x in out}:
            out.append(k)
    return out[:MAX_KEYWORDS]


def _rank_and_dedupe(results: list[cs.SearchResult]) -> list[cs.SearchResult]:
    best: dict[tuple[str, int | None], cs.SearchResult] = {}
    for r in results:
        key = (r.file, r.line)
        if key not in best or r.score > best[key].score:
            best[key] = r
    return sorted(best.values(), key=lambda r: -r.score)


def _local_imports(text: str) -> list[str]:
    """Plausible local-project import targets from a Python file's source.
    Not stdlib/third-party filtered — the follow-up search naturally no-ops
    on names with no in-workspace definition."""
    names: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module.split(".")[-1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name.split(".")[-1])
    return names


def _gather_evidence(workspace: Path, question: str) -> tuple[list[dict], list[str]]:
    """
    Iterative: search -> inspect strongest matches -> follow imports ->
    refine. Returns (evidence_items, notes); each evidence_item is
    {"file": str, "line": int|None, "kind": str, "content": str}.
    """
    keywords = _extract_keywords(question)
    notes = [f"search terms: {', '.join(keywords)}" if keywords else "no usable search terms extracted"]
    if not keywords:
        return [], notes

    round1: list[cs.SearchResult] = []
    per_kw_files: dict[str, set[str]] = {}
    for kw in keywords:
        # A generous cap here (vs. codebase_search's default) matters: a
        # flat low cap combined with alphabetical file-scan order would
        # silently bias file-relevance ranking toward early-alphabetical
        # files for common keywords, hiding files that only match on a
        # keyword found late in the scan order (observed directly: main.py
        # was invisible to the "client"/"env" keywords at max_results=20
        # purely because 20 earlier-alphabetical files matched first).
        kw_results = cs.search_text(workspace, kw, regex=False, max_results=300)
        kw_results += cs.search_filenames(workspace, kw, mode="partial")
        round1.extend(kw_results)
        per_kw_files[kw] = {r.file for r in kw_results if r.file}
    round1 = _rank_and_dedupe(round1)
    notes.append(f"round 1: {len(round1)} raw hits across {len(keywords)} term(s)")
    if not round1:
        return [], notes

    top = round1

    # File-level relevance: a file matching MORE distinct search terms ranks
    # higher than one matching a single term many times (flat per-line score
    # alone can't tell those apart). Earlier keywords are weighted more —
    # they're the higher-priority tokens from _extract_keywords.
    file_scores: dict[str, float] = {}
    for idx, kw in enumerate(keywords):
        weight = 1.0 / (idx + 1)
        for f in per_kw_files.get(kw, ()):
            file_scores[f] = file_scores.get(f, 0.0) + weight

    primary_files = sorted(file_scores, key=lambda f: -file_scores[f])[:MAX_PRIMARY_FILES]

    evidence: list[dict] = []
    read_texts: dict[str, str] = {}
    for rel in primary_files:
        content = cs.read_files(workspace, [rel], max_chars_each=cs.MAX_FILE_READ_CHARS)[0]["content"]
        read_texts[rel] = content
        for r in [r for r in top if r.file == rel][:3]:
            evidence.append({
                "file": rel, "line": r.line, "kind": r.match_type,
                "content": (r.snippet or content)[:MAX_SNIPPET_CHARS],
            })

    followups = 0
    seen_files = set(primary_files)
    for rel, content in list(read_texts.items()):
        if not rel.endswith(".py") or content == cs.REDACTED or content.startswith("[BLOCKED"):
            continue
        for name in _local_imports(content)[:8]:
            if followups >= MAX_FOLLOWUP_FILES:
                break
            defs = [
                d for d in cs.search_symbol(workspace, name, kind="any", max_results=3)
                if d.match_type in ("class_def", "function_def") and d.file not in seen_files
            ]
            if defs:
                d = defs[0]
                seen_files.add(d.file)
                followups += 1
                evidence.append({
                    "file": d.file, "line": d.line, "kind": f"followup:{d.match_type}",
                    "content": d.snippet,
                })
        if followups >= MAX_FOLLOWUP_FILES:
            break
    notes.append(f"follow-up definitions pulled in: {followups}")
    notes.append(f"files inspected: {len(seen_files)}")

    return evidence, notes


def _assemble_bounded_context(evidence: list[dict], max_chars: int = MAX_EVIDENCE_CHARS) -> str:
    """Highest-relevance-first order (evidence already produced that way);
    trims lowest-priority items first once the char budget is hit."""
    blocks = []
    total = 0
    for i, e in enumerate(evidence, start=1):
        loc = f"{e['file']}:{e['line']}" if e.get("line") else e["file"]
        block = f"[EVIDENCE {i}] {loc}  ({e['kind']})\n{e['content']}\n"
        if total + len(block) > max_chars:
            break
        blocks.append(block)
        total += len(block)
    return "\n".join(blocks)


_SYSTEM_INSTRUCTIONS = """You are a codebase investigation assistant. You will be given EVIDENCE
blocks, each tagged with an exact file path and line number, gathered by
a real search of the project's source files. You will also be given a
question.

Rules -- follow these exactly:
1. Answer using ONLY the EVIDENCE provided. Cite the file:line for every
   factual claim about the code.
2. If the evidence does not fully answer the question, say so explicitly.
   Clearly separate what is VERIFIED BY EVIDENCE from what is INFERENCE
   (a plausible guess not directly shown in the evidence).
3. Never invent file paths, function names, or line numbers that are not
   present in the evidence.
4. If no relevant evidence was found at all, say that directly instead of
   guessing.
5. Be concise. Prefer a direct answer with citations over a long essay.
"""


def investigate(parameters: dict, response=None, player=None, session_memory=None, speak=None) -> str:
    """
    JARVIS tool entry point: accepts a natural-language coding question,
    runs an iterative evidence-gathering search over the active workspace,
    and asks AIProvider to answer using only that bounded evidence.
    """
    params = parameters or {}
    question = (params.get("question") or "").strip()
    if not question:
        return "Please provide a question to investigate."

    workspace = ws.get_workspace()
    if player:
        player.write_log(f"[Investigate] {question[:80]}")

    evidence, notes = _gather_evidence(workspace, question)

    if not evidence:
        return (
            f"No matching evidence found in workspace ({workspace}) for: {question}\n"
            f"({'; '.join(notes)})\n"
            "Try a different search term, or use codebase_search directly to explore."
        )

    context = _assemble_bounded_context(evidence)

    prompt = f"{_SYSTEM_INSTRUCTIONS}\n\nQUESTION: {question}\n\nEVIDENCE:\n{context}"
    try:
        from core.ai_provider import complete_with_failover
        answer = complete_with_failover(prompt)[0].text
    except Exception as e:
        return f"AIProvider unavailable ({e}). Returning raw evidence instead:\n\n{context}"

    evidence_list = "\n".join(
        f"- {e['file']}:{e['line']}" if e.get("line") else f"- {e['file']}"
        for e in evidence
    )
    return f"{answer}\n\n---\nEvidence used ({len(evidence)} item(s)):\n{evidence_list}"
