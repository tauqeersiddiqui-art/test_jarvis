import ast
import subprocess
import sys
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core import workspace as ws
from core import coding_task as ct
from core import engineering_memory as em


PROJECTS_DIR         = Path.home() / "Desktop" / "JarvisProjects"
MAX_FIX_ATTEMPTS     = 5
MODEL_PLANNER        = "gemini-2.5-flash"
MODEL_WRITER         = "gemini-2.5-flash"
WRITE_CONCURRENCY    = 2  # bounded — the configured provider may be a rate/capacity-limited gateway
MAX_FIX_TARGET_FILES = 3  # bound on how many files a single fix attempt touches, even with evidence
MAX_EVIDENCE_PROMPT_CHARS = 6000  # bound on evidence context injected into the fix prompt
MAX_FEATURE_TARGET_FILES = 3  # bound on how many files one incremental feature change may touch


def _get_model(model_name: str):
    from core.ai_provider import complete_with_failover

    class _W:
        def generate_content(self, contents):
            # complete_with_failover tries each configured provider in order
            # (e.g. Gemini, then an OpenAI-compatible/FreeLLM gateway) and
            # only forces `model_name` onto Gemini — never onto the gateway,
            # which must keep using its own configured LLM_MODEL (incl. "auto").
            response, _attempts = complete_with_failover(contents, model=model_name)
            return response

    return _W()


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text.strip()


def _is_rate_limit(error: Exception) -> bool:
    msg = str(error).lower()
    return any(s in msg for s in (
        "429", "402", "quota", "resource_exhausted",
        "rate limit", "rate_limit", "too many requests", "capacity",
    ))


def _parse_traceback(output: str, project_files: list[str]) -> tuple[str | None, int | None]:

    pattern = re.compile(r'File ["\']([^"\']+\.py)["\'],\s+line\s+(\d+)', re.IGNORECASE)
    matches = pattern.findall(output)

    for raw_path, line_str in reversed(matches):
        raw_name = Path(raw_path).name
        for pf in project_files:
            if Path(pf).name == raw_name or pf == raw_path or raw_path.endswith(pf):
                return pf, int(line_str)

    return None, None


def _classify_error(output: str) -> str:

    low = output.lower()

    if any(x in low for x in ("no module named", "modulenotfounderror", "importerror")):
        return "dependency_error"

    if "syntaxerror" in low or "invalid syntax" in low:
        return "syntax_error"
    
    if "cannot import" in low or "importerror" in low:
        return "import_error"

    if any(x in low for x in (
        "traceback", "exception", "error:", "nameerror", "typeerror",
        "attributeerror", "valueerror", "keyerror", "indexerror",
        "zerodivisionerror", "filenotfounderror", "permissionerror",
    )):
        return "runtime_error"

    return "none"


def _has_error(output: str, run_command: str) -> bool:
    
    low = output.lower()

    if "timed out" in low:
        return False

    if not output.strip():
        return False

    error_type = _classify_error(output)
    return error_type != "none"

class RateLimitError(Exception):
    pass


def _plan_project(description: str, language: str) -> dict:
    model = _get_model(MODEL_PLANNER)

    prompt = f"""You are a senior software architect. Create a minimal, complete file plan for this project.

Language: {language}
Description: {description}

Return ONLY valid JSON — no markdown, no explanation:
{{
  "project_name": "snake_case_name",
  "entry_point": "main.py",
  "files": [
    {{
      "path": "main.py",
      "description": "Entry point — what it does and which modules it imports",
      "imports": ["utils.helpers", "core.engine"]
    }},
    {{
      "path": "utils/helpers.py",
      "description": "Helper utilities — what functions it exposes",
      "imports": []
    }}
  ],
  "run_command": "python main.py",
  "dependencies": ["requests"]
}}

Critical rules:
1. List files in DEPENDENCY ORDER — files with no imports come first, entry point comes last.
2. The "imports" field must list every other project module this file imports (dot-notation, e.g. "utils.helpers").
3. Keep it minimal — only files truly needed.
4. Entry point must be in the files list.
5. Use relative paths only (e.g. "utils/helpers.py", not absolute paths).
6. Standard library modules (os, sys, json, etc.) do NOT go in "dependencies".

JSON:"""

    try:
        response = model.generate_content(prompt)
        raw = _strip_fences(response.text)
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned invalid JSON: {e}\nRaw: {response.text[:300]}")
    except Exception as e:
        if _is_rate_limit(e):
            raise RateLimitError(str(e))
        raise

def _write_file(
    file_info: dict,
    project_description: str,
    all_files: list[dict],
    language: str,
    project_dir: Path,
    already_written: dict[str, str],
) -> str:
    model = _get_model(MODEL_WRITER)

    file_path = file_info["path"]
    file_desc = file_info.get("description", "")
    file_imports = file_info.get("imports", [])

    file_list = "\n".join(
        f"  [{i+1}] {f['path']}: {f.get('description', '')}"
        for i, f in enumerate(all_files)
    )

    dependency_context = ""
    for dep_dotted in file_imports:
        dep_path = dep_dotted.replace(".", "/") + ".py"
        if dep_path in already_written:
            code_snippet = already_written[dep_path][:2000]
            dependency_context += f"\n\n--- {dep_path} (you must import from this) ---\n{code_snippet}"

    lang_rules = ""
    if language.lower() == "python":
        lang_rules = """
Python-specific rules:
- Use type hints for all function signatures.
- Add docstrings for all public functions and classes.
- Use if __name__ == "__main__": guard in the entry point.
- For relative imports within the project, use: from utils.helpers import foo  (match the project structure exactly).
- Do NOT use implicit relative imports (from . import ...) unless it's a proper package with __init__.py.
- If this is a package subdirectory, create __init__.py files where needed."""
    elif language.lower() in ("javascript", "typescript", "js", "ts"):
        lang_rules = """
JS/TS-specific rules:
- Use ES modules (import/export), not CommonJS (require).
- Add JSDoc comments for all exported functions.
- Handle promise rejections with try/catch in async functions."""

    prompt = f"""You are a senior {language} developer writing production-quality code for a real project.

Project goal: {project_description}

Complete project file structure (in dependency order):
{file_list}

{f"Dependencies this file must import from other project files:{dependency_context}" if dependency_context else ""}

Your task: Write the complete, working code for: {file_path}
Purpose of this file: {file_desc}
{f"This file imports from: {', '.join(file_imports)}" if file_imports else "This file has no project-internal imports."}

{lang_rules}

General rules:
- Output ONLY raw code. Absolutely no explanation, no markdown, no triple backticks.
- Write COMPLETE, RUNNABLE code — no placeholders, no "# TODO", no "pass" stubs.
- Every import must either be from the standard library, listed dependencies, or the project files shown above.
- Match import paths EXACTLY to the file paths in the project structure (e.g. if file is "utils/helpers.py", import as "from utils.helpers import ...").
- Use proper error handling (try/except) where I/O or network calls are made.
- The code must work correctly when the project entry point is run from the project root directory.

Code for {file_path}:"""

    try:
        response = model.generate_content(prompt)
        code = _strip_fences(response.text)

        full_path = project_dir / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(code, encoding="utf-8")

        print(f"[DevAgent] ✅ Written: {file_path} ({len(code)} chars)")
        return code

    except Exception as e:
        if _is_rate_limit(e):
            raise RateLimitError(str(e))
        raise

def _install_dependencies(dependencies: list[str], project_dir: Path) -> str:
    if not dependencies:
        return "No external dependencies."

    to_install = []
    for dep in dependencies:
        pkg_name = re.split(r"[>=<!]", dep)[0].strip()
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", pkg_name],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            to_install.append(dep)
        else:
            print(f"[DevAgent] ✓ Already installed: {pkg_name}")

    if not to_install:
        return f"All dependencies already installed: {', '.join(dependencies)}"

    print(f"[DevAgent] 📦 Installing: {to_install}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install"] + to_install,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120, cwd=str(project_dir)
        )
        if result.returncode == 0:
            return f"Installed: {', '.join(to_install)}"
        return f"Install warning (non-fatal): {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return "Dependency install timed out (non-fatal)."
    except Exception as e:
        return f"Install error (non-fatal): {e}"

def _open_vscode(project_dir: Path) -> bool:
    vscode_candidates = [
        "code",
        rf"C:\Users\{Path.home().name}\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd",
        r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
    ]
    for cmd in vscode_candidates:
        try:
            subprocess.Popen(
                [cmd, str(project_dir)],
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.5)
            print(f"[DevAgent] 💻 VSCode opened: {project_dir}")
            return True
        except Exception:
            continue
    return False

def _run_project(run_command: str, project_dir: Path, timeout: int = 30) -> str:
    print(f"[DevAgent] 🚀 Running: {run_command}")
    try:
        parts = run_command.split()
        if parts[0].lower() == "python":
            parts[0] = sys.executable

        result = subprocess.run(
            parts,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
            cwd=str(project_dir)
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        combined_parts = []
        if stdout:
            combined_parts.append(f"STDOUT:\n{stdout}")
        if stderr:
            combined_parts.append(f"STDERR:\n{stderr}")

        return "\n\n".join(combined_parts) if combined_parts else "Ran with no output."

    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s — long-running app (server/GUI) is likely working."
    except FileNotFoundError as e:
        return f"Command not found: {e}"
    except Exception as e:
        return f"Run error: {e}"

def _try_auto_install(error_output: str, project_dir: Path) -> bool:
    """ModuleNotFoundError varsa eksik paketi otomatik kurmaya çalışır."""
    pattern = re.compile(
        r"No module named ['\"]([a-zA-Z0-9_\-\.]+)['\"]", re.IGNORECASE
    )
    match = pattern.search(error_output)
    if not match:
        return False

    pkg = match.group(1).replace("_", "-").split(".")[0]
    print(f"[DevAgent] 🔧 Auto-installing missing package: {pkg}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=60, cwd=str(project_dir)
        )
        return result.returncode == 0
    except Exception:
        return False

_SYMBOL_ERROR_PATTERNS = [
    re.compile(r"name '([A-Za-z_][A-Za-z0-9_]*)' is not defined"),
    re.compile(r"cannot import name '([A-Za-z_][A-Za-z0-9_]*)'"),
    re.compile(r"No module named ['\"]([A-Za-z0-9_.]+)['\"]"),
    re.compile(r"has no attribute '([A-Za-z_][A-Za-z0-9_]*)'"),
]


def _evidence_query_from_error(error_output: str, error_file: str | None) -> str:
    """Build a short, identifier-rich query from a runtime error/traceback
    for bounded evidence search. The raw traceback is too noisy for keyword
    extraction — the final exception line plus any named symbols
    (NameError/ImportError/AttributeError/ModuleNotFoundError) carry almost
    all of the real signal."""
    lines = [l.strip() for l in error_output.strip().splitlines() if l.strip()]
    exception_line = lines[-1] if lines else ""

    symbols: list[str] = []
    for pattern in _SYMBOL_ERROR_PATTERNS:
        symbols.extend(pattern.findall(error_output))

    parts = [exception_line] + symbols
    if error_file:
        parts.append(Path(error_file).stem)
    return " ".join(p for p in parts if p)[:400]


def _gather_project_evidence(
    project_dir: Path, error_output: str, error_file: str | None
) -> tuple[list[dict], str]:
    """Bounded, relevance-ranked evidence from the FAILED PROJECT's own
    directory only — never Mark's own repo, never the global active
    workspace. Reuses investigate.py's search/evidence primitives directly
    (_gather_evidence / _assemble_bounded_context), skipping its top-level
    investigate() entry point entirely so no extra LLM synthesis call is
    made — those primitives already take an explicit `workspace` argument
    and never touch core.workspace's persisted active-workspace state.
    Returns ([], "") if nothing useful is found, signalling the caller to
    fall back to the existing traceback-based fix behavior."""
    from actions import investigate as inv

    query = _evidence_query_from_error(error_output, error_file)
    if not query.strip():
        return [], ""
    try:
        evidence, _notes = inv._gather_evidence(project_dir, query)
    except Exception as e:
        print(f"[DevAgent] Evidence gathering failed (non-fatal): {e}")
        return [], ""
    if not evidence:
        return [], ""
    return evidence, inv._assemble_bounded_context(evidence)


def _plan_fix_targets(
    error_output: str,
    file_codes: dict[str, str],
    all_files: list[dict],
    entry_point: str,
    project_dir: Path,
) -> tuple[list[str], str]:
    """Single, deterministic computation of (a) which files a fix attempt
    will target and (b) the evidence context for the fix prompt. Called
    once per attempt so the pre-attempt snapshot set and the actual write
    set are always identical by construction — never computed twice."""
    error_file, _error_line = _parse_traceback(error_output, list(file_codes.keys()))
    error_type = _classify_error(error_output)

    files_to_fix: list[str] = []
    if error_file:
        files_to_fix.append(error_file)
        if error_type == "import_error":
            for fi in all_files:
                if error_file.replace("/", ".").replace(".py", "") in fi.get("imports", []):
                    p = fi["path"]
                    if p not in files_to_fix:
                        files_to_fix.append(p)
    else:
        files_to_fix.append(entry_point)

    evidence, evidence_context = _gather_project_evidence(project_dir, error_output, error_file)
    if evidence:
        # Evidence-driven: pull in files where the failing symbol is
        # actually defined/referenced, not just where the traceback pointed —
        # bounded so a single fix attempt never touches the whole project.
        for e in evidence:
            fp = e.get("file")
            if fp and fp in file_codes and fp not in files_to_fix and len(files_to_fix) < MAX_FIX_TARGET_FILES:
                files_to_fix.append(fp)

    return files_to_fix, evidence_context


def _fix_files(
    error_output: str,
    project_description: str,
    all_files: list[dict],
    file_codes: dict[str, str],
    language: str,
    project_dir: Path,
    entry_point: str,
    files_to_fix: list[str],
    evidence_context: str = "",
    memory_note: str = "",
) -> dict[str, str]:

    model = _get_model(MODEL_PLANNER)

    error_file, error_line = _parse_traceback(error_output, list(file_codes.keys()))
    error_type = _classify_error(error_output)

    updated_codes: dict[str, str] = {}

    for fix_path in files_to_fix:
        current_code = file_codes.get(fix_path, "")

        if evidence_context:
            # Evidence-driven context: relevance-ranked file:line blocks from
            # a real bounded search of this project only (see
            # _gather_project_evidence), not a raw truncated file dump.
            other_ctx = evidence_context[:MAX_EVIDENCE_PROMPT_CHARS]
            context_label = "Evidence gathered from this project (file:line, relevance-ranked)"
        else:
            # Fallback: no useful evidence found — preserve the original
            # traceback-only context (raw truncated dump of other files).
            other_ctx = ""
            for fp, code in file_codes.items():
                if fp != fix_path and code:
                    snippet = code[:1500] + ("..." if len(code) > 1500 else "")
                    other_ctx += f"\n--- {fp} ---\n{snippet}\n"
            other_ctx = other_ctx[:3500]
            context_label = "Other files for context (read-only — fix only the target file)"

        line_hint = f"\nError appears to be near line {error_line} in this file." if (
            error_line and fix_path == error_file
        ) else ""

        prompt = f"""You are an expert {language} debugger. Fix the broken file below.

Project goal: {project_description}

All project files:
{chr(10).join(f"  - {f['path']}: {f.get('description', '')}" for f in all_files)}

{context_label}:
{other_ctx}

File to fix: {fix_path}{line_hint}
Error type: {error_type}

Error output:
{error_output[:2500]}

Current (broken) code:
{current_code}
{memory_note}
Rules:
- Output ONLY the complete fixed code. No explanation, no markdown, no backticks.
- Fix ALL errors visible in the error output.
- Keep all existing correct logic — do not remove working features.
- Ensure import paths match the actual project file structure exactly.
- Do NOT introduce new bugs or remove error handling.

Fixed code for {fix_path}:"""

        try:
            response = model.generate_content(prompt)
            fixed = _strip_fences(response.text)

            full_path = project_dir / fix_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(fixed, encoding="utf-8")

            updated_codes[fix_path] = fixed
            print(f"[DevAgent] 🔧 Fixed: {fix_path}")

        except Exception as e:
            if _is_rate_limit(e):
                raise RateLimitError(str(e))
            print(f"[DevAgent] ⚠️ Could not fix {fix_path}: {e}")

    return updated_codes


def _snapshot_files(project_dir: Path, relative_paths: list[str]) -> dict[str, bytes | None]:
    """Exact-byte snapshot of the given files (relative to project_dir),
    taken BEFORE a fix attempt writes anything. A value of None means the
    file did not exist yet — rollback must then delete it if the attempt
    creates it. Strictly bounded to project_dir: any path that would
    resolve outside it is skipped, never touched, never snapshotted."""
    snapshot: dict[str, bytes | None] = {}
    for rel in relative_paths:
        try:
            full_path = ws.resolve_in_workspace(rel, project_dir)
        except ws.PathEscapeError:
            continue
        snapshot[rel] = full_path.read_bytes() if full_path.is_file() else None
    return snapshot


def _rollback_snapshot(
    project_dir: Path,
    snapshot: dict[str, bytes | None],
    file_codes: dict[str, str] | None = None,
) -> None:
    """Restore exact pre-attempt bytes for every snapshotted file — local to
    project_dir only, local to this one fix attempt. A file that did not
    exist before (None) is deleted if the attempt created it; any existing
    file (including one the attempt deleted) is restored to its exact
    original bytes. If `file_codes` is given, it is kept consistent with
    the restored disk state (entry removed for files that didn't exist,
    original text restored otherwise)."""
    for rel, original in snapshot.items():
        try:
            full_path = ws.resolve_in_workspace(rel, project_dir)
        except ws.PathEscapeError:
            continue

        if original is None:
            if full_path.exists():
                try:
                    full_path.unlink()
                except Exception:
                    pass
            if file_codes is not None:
                file_codes.pop(rel, None)
        else:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(original)
            if file_codes is not None:
                try:
                    file_codes[rel] = original.decode("utf-8")
                except UnicodeDecodeError:
                    file_codes.pop(rel, None)


_TRACEBACK_FRAME_RE = re.compile(r'File ["\'][^"\']+["\'],\s+line\s+\d+', re.IGNORECASE)
_EXCEPTION_NAME_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning))\b")


def _normalize_error_signature(output: str) -> tuple[str, str | None, int | None]:
    """(exception_type, failing_file_name, failing_line) — the last
    traceback frame is where execution actually died; the exception class
    name is the clearest type signal. Purely textual/deterministic — no
    LLM judgement involved, used to detect an exactly-repeated error."""
    lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
    exc_name = None
    if lines:
        m = _EXCEPTION_NAME_RE.match(lines[-1])
        if m:
            exc_name = m.group(1)

    pattern = re.compile(r'File ["\']([^"\']+)["\'],\s+line\s+(\d+)', re.IGNORECASE)
    matches = pattern.findall(output)
    file_name, line_no = None, None
    if matches:
        raw_file, raw_line = matches[-1]
        file_name, line_no = Path(raw_file).name, int(raw_line)

    return exc_name or _classify_error(output), file_name, line_no


def _traceback_depth(output: str) -> int:
    """Number of traceback frames — a deterministic proxy for how far
    execution progressed before failing (more frames = got further before
    dying, fewer = regressed to an earlier failure point)."""
    return len(_TRACEBACK_FRAME_RE.findall(output))


def _compare_error_progress(pre_output: str, post_output: str, run_command: str) -> str:
    """Deterministic (no LLM) comparison of a fix attempt's before/after run
    output. Returns "success" | "improved" | "unchanged" | "worse".

    Conservative by design: if improvement cannot be clearly established
    from the normalized signature and traceback depth, this returns
    "unchanged" so the caller rolls back rather than keeping an unproven fix.
    """
    if not _has_error(post_output, run_command):
        return "success"

    pre_sig = _normalize_error_signature(pre_output)
    post_sig = _normalize_error_signature(post_output)
    if pre_sig == post_sig:
        return "unchanged"  # exact repeat — the fix didn't move the needle

    pre_depth = _traceback_depth(pre_output)
    post_depth = _traceback_depth(post_output)
    if post_depth > pre_depth:
        return "improved"   # execution reached a later point before failing
    if post_depth < pre_depth:
        return "worse"      # regressed to an earlier failure point

    # Same depth, different signature: improvement can't be clearly
    # established — conservative default is to treat it as unchanged.
    return "unchanged"


def _compute_waves(files: list[dict]) -> list[list[dict]]:
    """Group files into dependency waves: wave 0 has no internal imports,
    wave N depends only on files in waves < N. Files within the same wave
    are independent and safe to write concurrently."""
    path_to_file = {f["path"]: f for f in files if f.get("path")}
    wave_num: dict[str, int] = {}

    def _import_to_path(imp: str) -> str | None:
        p = imp.replace(".", "/") + ".py"
        return p if p in path_to_file else None

    def _compute(path: str, stack: set) -> int:
        if path in wave_num:
            return wave_num[path]
        if path in stack:
            return 0  # circular import guard
        stack = stack | {path}
        fi = path_to_file[path]
        deps = [d for d in (_import_to_path(i) for i in fi.get("imports", [])) if d and d != path]
        w = 0 if not deps else 1 + max(_compute(d, stack) for d in deps)
        wave_num[path] = w
        return w

    for f in files:
        if f.get("path"):
            _compute(f["path"], set())

    waves: dict[int, list[dict]] = {}
    for f in files:
        p = f.get("path")
        if p:
            waves.setdefault(wave_num[p], []).append(f)

    return [waves[k] for k in sorted(waves.keys())]


def _build_project(
    description: str,
    language: str,
    project_name: str,
    timeout: int,
    speak=None,
    player=None,
    task=None,
) -> str:

    def log(msg: str):
        print(f"[DevAgent] {msg}")
        if player:
            player.write_log(f"[DevAgent] {msg}")

    if speak:
        speak("Got it, sir — building your project now. This may take a couple of minutes.")

    log("Planning project structure...")
    if task:
        ct.set_phase(task, ct.Phase.PLANNING)
    try:
        plan = _plan_project(description, language)
    except RateLimitError:
        msg = "Rate limit reached, sir. Please try again in a moment."
        if speak: speak(msg)
        return msg
    except ValueError as e:
        msg = f"Planning failed: {e}"
        if speak: speak(msg)
        return msg

    proj_name    = project_name or plan.get("project_name", "jarvis_project")
    proj_name    = re.sub(r"[^\w\-]", "_", proj_name)
    project_dir  = PROJECTS_DIR / proj_name
    project_dir.mkdir(parents=True, exist_ok=True)

    files        = plan.get("files", [])
    entry_point  = plan.get("entry_point", "main.py")
    run_command  = plan.get("run_command", f"python {entry_point}")
    dependencies = plan.get("dependencies", [])

    log(f"Project: {proj_name} | Files: {len(files)} | Entry: {entry_point}")
    if task:
        # The planner may pick its own project_name when the caller didn't
        # specify one — record the REAL, final project_name/root now that
        # it's known, then persist the operational metadata needed to
        # resume the fix loop later without re-planning.
        task.project_name = proj_name
        task.project_root = str(project_dir)
        task.entry_point = entry_point
        task.run_command = run_command
        ct.set_phase(task, ct.Phase.BUILDING)

    waves = _compute_waves(files)

    file_codes: dict[str, str] = {}
    failed_files: dict[str, str] = {}
    degraded_to_sequential = False  # tripped once a rate/capacity error is seen

    def _write_one(file_info: dict, snapshot: dict[str, str]) -> tuple[str, str | None, str | None]:
        file_path = file_info.get("path", "")
        if not file_path:
            return file_path, None, None

        for attempt in range(2):
            try:
                code = _write_file(
                    file_info=file_info,
                    project_description=description,
                    all_files=files,
                    language=language,
                    project_dir=project_dir,
                    already_written=snapshot,
                )
                return file_path, code, None
            except RateLimitError as e:
                if attempt == 0:
                    log(f"Rate limit on {file_path} — waiting 20s...")
                    time.sleep(20)
                else:
                    return file_path, None, str(e)
            except Exception as e:
                return file_path, None, str(e)
        return file_path, None, "unknown error"

    for wave_idx, wave in enumerate(waves):
        valid = [fi for fi in wave if fi.get("path")]
        if not valid:
            continue

        log(f"Writing wave {wave_idx + 1}/{len(waves)}: {', '.join(fi['path'] for fi in valid)}")
        snapshot = dict(file_codes)
        results: list[tuple[str, str | None, str | None]] = []

        if not degraded_to_sequential and len(valid) > 1:
            with ThreadPoolExecutor(max_workers=min(WRITE_CONCURRENCY, len(valid))) as ex:
                futures = {ex.submit(_write_one, fi, snapshot): fi for fi in valid}
                for fut in as_completed(futures):
                    results.append(fut.result())
        else:
            for fi in valid:
                results.append(_write_one(fi, snapshot))

        for file_path, code, err in results:
            if not file_path:
                continue
            if code is not None:
                file_codes[file_path] = code
                log(f"Written: {file_path} ({len(code)} chars)")
            else:
                failed_files[file_path] = err or "unknown error"
                log(f"Failed to write {file_path}: {err}")
                if err and _is_rate_limit(Exception(err)) and not degraded_to_sequential:
                    degraded_to_sequential = True
                    log("Rate limit / capacity error detected — degrading to sequential writes for remaining files (no retry storm).")

        time.sleep(0.3)

    if failed_files and file_codes:
        log(f"Warning: {len(failed_files)} file(s) failed to write: {', '.join(failed_files.keys())}")

    if not file_codes:
        reason = "; ".join(f"{p}: {e[:120]}" for p, e in list(failed_files.items())[:3]) or "unknown error"
        msg = f"I could not write any project files, sir. Reason: {reason}"
        if speak: speak(msg)
        return msg

    if dependencies:
        install_result = _install_dependencies(dependencies, project_dir)
        log(install_result)

    _open_vscode(project_dir)

    return _run_fix_loop(
        project_dir=project_dir,
        run_command=run_command,
        description=description,
        language=language,
        files=files,
        entry_point=entry_point,
        file_codes=file_codes,
        timeout=timeout,
        proj_name=proj_name,
        speak=speak,
        player=player,
        task=task,
    )


def _run_fix_loop(
    project_dir: Path,
    run_command: str,
    description: str,
    language: str,
    files: list[dict],
    entry_point: str,
    file_codes: dict[str, str],
    timeout: int,
    proj_name: str,
    speak=None,
    player=None,
    task=None,
    operation_type: str = "build_fix",
) -> str:
    """The bounded run -> investigate -> fix -> validate -> rollback loop.
    This is THE execution engine for validating and fixing a project on
    disk — shared by a fresh build (_build_project, after planning/writing)
    and a "continue/fix" resume on an already-existing project. Never
    duplicated: both callers reach this exact same function.

    `task` is an optional core.coding_task.CodingTask — purely an
    orchestration hook (phase/error/evidence/attempt-count bookkeeping);
    this function's actual validate/fix/rollback behavior is identical
    whether a task is provided or not.
    """

    def log(msg: str):
        print(f"[DevAgent] {msg}")
        if player:
            player.write_log(f"[DevAgent] {msg}")

    if task:
        ct.set_phase(task, ct.Phase.VALIDATING)

    last_output   = _run_project(run_command, project_dir, timeout)
    log(f"Output preview: {last_output[:150]}")
    auto_installs = 0

    if not _has_error(last_output, run_command):
        if task:
            ct.mark_completed(task)
        msg = f"Project '{proj_name}' is working, sir. Built in 1 attempt. Saved to: {project_dir}"
        if speak: speak(msg)
        return f"{msg}\n\nOutput:\n{last_output}"

    for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
        pre_output = last_output

        error_type = _classify_error(pre_output)
        if error_type == "dependency_error" and auto_installs < 3:
            installed = _try_auto_install(pre_output, project_dir)
            if installed:
                auto_installs += 1
                log("Missing dependency installed, retrying...")
                time.sleep(1)
                last_output = _run_project(run_command, project_dir, timeout)
                log(f"Output preview: {last_output[:150]}")
                if not _has_error(last_output, run_command):
                    if task:
                        ct.mark_completed(task)
                    msg = (
                        f"Project '{proj_name}' is working, sir. "
                        f"Built in {attempt + 1} attempts. Saved to: {project_dir}"
                    )
                    if speak: speak(msg)
                    return f"{msg}\n\nOutput:\n{last_output}"
                continue

        log(f"Fixing errors (type: {error_type}, attempt {attempt}/{MAX_FIX_ATTEMPTS})...")
        if task:
            ct.set_phase(task, ct.Phase.INVESTIGATING)

        # Snapshot BEFORE writing anything: the snapshot set and the actual
        # write set are the exact same list (_plan_fix_targets is the single
        # source of truth for both), so rollback can always fully undo one
        # attempt regardless of outcome.
        files_to_fix, evidence_context = _plan_fix_targets(
            pre_output, file_codes, files, entry_point, project_dir
        )
        snapshot = _snapshot_files(project_dir, files_to_fix)

        sig = _normalize_error_signature(pre_output)
        sig_str = f"{sig[0]}:{sig[1]}:{sig[2]}"
        attempt_summary = f"fix {error_type} via evidence-driven edit in {', '.join(sorted(files_to_fix))}"
        impact_summary = ""
        memory_note = ""
        pkey = ""

        if task:
            task.fix_attempt_count += 1
            task.touch_files(files_to_fix)
            task.record_error(pre_output, signature=sig_str, evidence_summary=evidence_context[:200] if evidence_context else "")
            ct.set_phase(task, ct.Phase.FIXING)

            from actions import impact_analysis as ia
            impact_summary = ia.build_impact_report(project_dir, files_to_fix).summary()

            pkey = em.project_key(str(project_dir))
            fingerprint = em.compute_attempt_fingerprint(operation_type, sig_str, files_to_fix, attempt_summary)

            relevant_memory = em.find_relevant_for_error(pkey, sig_str, operation_type)
            if relevant_memory:
                memory_note += (
                    "\nRelevant past engineering outcomes for this project (bounded recall — "
                    f"do not blindly repeat a failed approach):\n{em.summarize_records(relevant_memory)}\n"
                )

            prior_failure = em.find_matching_failed_attempt(pkey, fingerprint)
            if prior_failure:
                reason = prior_failure.rollback_reason or prior_failure.failure_category or "no reason recorded"
                memory_note += (
                    f"\nIMPORTANT: Previous attempt on this error was {prior_failure.outcome} ({reason}). "
                    "Avoid repeating this approach — generate a materially different fix.\n"
                    f"Prior attempt summary: {prior_failure.attempt_summary}\n"
                )

        try:
            updated = _fix_files(
                error_output=pre_output,
                project_description=description,
                all_files=files,
                file_codes=file_codes,
                language=language,
                project_dir=project_dir,
                entry_point=entry_point,
                files_to_fix=files_to_fix,
                evidence_context=evidence_context,
                memory_note=memory_note,
            )
        except RateLimitError:
            msg = "Rate limit reached during fix. Project saved, check it manually in VSCode."
            if speak: speak(msg)
            return msg
        except Exception as e:
            log(f"Fix step failed: {e}")
            _rollback_snapshot(project_dir, snapshot, file_codes)
            log(f"[Rollback] attempt {attempt}: snapshotted={files_to_fix} result=write_failure rollback=yes")
            if task:
                em.record_outcome(
                    task, operation_type, description, sig_str, evidence_context, impact_summary,
                    files_to_fix, attempt_summary, outcome=em.OUTCOME_ROLLED_BACK,
                    rollback_reason="write_failure", failure_category=error_type,
                )
            time.sleep(1)
            continue

        # Partial fix write failure: not every targeted file was actually
        # written (e.g. one file's generation call failed). Conservative —
        # roll back the whole attempt rather than leave a half-applied fix.
        missing = [f for f in files_to_fix if f not in updated]
        if missing:
            log(f"Partial fix write failure — {len(missing)}/{len(files_to_fix)} file(s) not written. Rolling back.")
            _rollback_snapshot(project_dir, snapshot, file_codes)
            log(f"[Rollback] attempt {attempt}: snapshotted={files_to_fix} result=write_failure rollback=yes")
            if task:
                em.record_outcome(
                    task, operation_type, description, sig_str, evidence_context, impact_summary,
                    files_to_fix, attempt_summary, outcome=em.OUTCOME_ROLLED_BACK,
                    rollback_reason="write_failure", failure_category=error_type,
                )
            time.sleep(1)
            continue

        post_output = _run_project(run_command, project_dir, timeout)
        log(f"Output preview: {post_output[:150]}")

        decision = _compare_error_progress(pre_output, post_output, run_command)

        if decision == "success":
            file_codes.update(updated)
            log(f"[Rollback] attempt {attempt}: snapshotted={files_to_fix} result=success rollback=no")
            if task:
                em.record_outcome(
                    task, operation_type, description, sig_str, evidence_context, impact_summary,
                    files_to_fix, attempt_summary, outcome=em.OUTCOME_SUCCESS, successful_step="fix",
                )
                ct.mark_completed(task)
            msg = (
                f"Project '{proj_name}' is working, sir. "
                f"Built in {attempt + 1} attempts. Saved to: {project_dir}"
            )
            if speak: speak(msg)
            return f"{msg}\n\nOutput:\n{post_output}"

        if decision == "improved":
            # Keep the fix and continue the next bounded attempt from this
            # improved state.
            file_codes.update(updated)
            last_output = post_output
            log(f"[Rollback] attempt {attempt}: snapshotted={files_to_fix} result=improved rollback=no")
            if task:
                em.record_outcome(
                    task, operation_type, description, sig_str, evidence_context, impact_summary,
                    files_to_fix, attempt_summary, outcome=em.OUTCOME_IMPROVED,
                )
        else:
            # "unchanged" or "worse" — roll back every file this attempt
            # touched and continue from the last better (pre-attempt) state.
            _rollback_snapshot(project_dir, snapshot, file_codes)
            last_output = pre_output
            log(f"[Rollback] attempt {attempt}: snapshotted={files_to_fix} result={decision} rollback=yes")
            if task:
                em.record_outcome(
                    task, operation_type, description, sig_str, evidence_context, impact_summary,
                    files_to_fix, attempt_summary, outcome=em.OUTCOME_ROLLED_BACK,
                    rollback_reason=decision, failure_category=error_type,
                )

        time.sleep(1)

    if task:
        ct.mark_failed(task, last_step="fix_loop_exhausted")
        em.record_outcome(
            task, operation_type, description, "", "", "", [],
            "fix loop exhausted after max attempts", outcome=em.OUTCOME_FAILED,
            failure_category="max_attempts_exhausted",
        )
    msg = (
        f"I couldn't fully fix '{proj_name}' after {MAX_FIX_ATTEMPTS} attempts, sir. "
        f"Project is saved at {project_dir} — open it in VSCode and check manually."
    )
    if speak: speak(msg)
    return f"{msg}\n\nLast error:\n{last_output[:600]}"


_RESUME_SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json"}
MAX_RESUME_FILES      = 40
MAX_RESUME_FILE_CHARS = 20_000


def _reload_file_codes_from_disk(project_dir: Path) -> dict[str, str]:
    """Reconstruct an in-memory file_codes map from the project's CURRENT
    on-disk state — needed when resuming a coding task, since CodingTask
    state never persists full source contents (only file paths)."""
    file_codes: dict[str, str] = {}
    if not project_dir.is_dir():
        return file_codes
    count = 0
    for path in sorted(project_dir.rglob("*")):
        if count >= MAX_RESUME_FILES:
            break
        if not path.is_file() or path.suffix.lower() not in _RESUME_SOURCE_EXTENSIONS:
            continue
        rel = path.relative_to(project_dir)
        if any(ws.is_ignored_dir(part) for part in rel.parts[:-1]):
            continue
        try:
            file_codes[rel.as_posix()] = path.read_text(encoding="utf-8", errors="replace")[:MAX_RESUME_FILE_CHARS]
            count += 1
        except Exception:
            continue
    return file_codes


def _continue_fix_loop_for_task(task: "ct.CodingTask", timeout: int, speak=None, player=None) -> str:
    """Resume the existing project's validate/fix loop without re-planning
    or rewriting from scratch — used for 'fix the current error' / generic
    'continue' requests where the user wants the CURRENT code fixed, not
    regenerated. Reuses _run_fix_loop — the same execution engine a fresh
    build uses, never a second implementation."""
    project_dir = Path(task.project_root)
    if not task.project_root or not project_dir.is_dir():
        return f"I can't find the project folder for '{task.project_name}' anymore, sir. It may have been moved or deleted."

    file_codes = _reload_file_codes_from_disk(project_dir)
    if not file_codes:
        return f"I couldn't find any source files in {project_dir}, sir."

    files = [{"path": p, "description": "", "imports": []} for p in file_codes]
    entry_point = task.entry_point or "main.py"
    run_command = task.run_command or f"python {entry_point}"

    return _run_fix_loop(
        project_dir=project_dir,
        run_command=run_command,
        description=task.current_goal,
        language=task.language,
        files=files,
        entry_point=entry_point,
        file_codes=file_codes,
        timeout=timeout,
        proj_name=task.project_name,
        speak=speak,
        player=player,
        task=task,
        operation_type="runtime_fix",
    )


_EXPLICIT_FILE_RE = re.compile(
    r"(?:^|\s)((?:\.\.?/)*[A-Za-z0-9_][A-Za-z0-9_/.-]*\.(?:py|js|ts|json|html|css))(?=[\s.,!?]|$)"
)


def _extract_explicit_new_file(change_request: str, project_dir: Path, file_codes: dict[str, str]) -> str | None:
    """If the change request explicitly names a file that doesn't exist yet,
    treat it as a deliberate, bounded new-file target. The path always comes
    from the user's own words, never a model choice, and is validated to
    resolve inside project_root before being accepted — a path-escaping
    name (e.g. "../evil.py") is silently rejected, never touched."""
    for m in _EXPLICIT_FILE_RE.finditer(change_request):
        candidate = m.group(1)
        if candidate in file_codes:
            continue  # already exists — not a "new" file
        try:
            ws.resolve_in_workspace(candidate, project_dir)
        except ws.PathEscapeError:
            continue
        return candidate
    return None


def _apply_feature_change(
    change_request: str,
    task: "ct.CodingTask",
    project_dir: Path,
    target_files: list[str],
    file_codes: dict[str, str],
    evidence_context: str,
    impact,
    language: str,
    memory_note: str = "",
) -> dict[str, str]:
    """Generate and write the incremental edit for EXACTLY target_files —
    never any other file, never outside project_dir. Python output is
    syntax-validated before being written; invalid output is simply not
    written (counts as a write failure for that file, triggering the
    caller's whole-attempt rollback)."""
    model = _get_model(MODEL_WRITER)
    updated: dict[str, str] = {}

    for target in target_files:
        is_new_file = target not in file_codes
        current_code = file_codes.get(target, "")

        others_ctx = ""
        for other in target_files:
            if other != target and other in file_codes:
                others_ctx += f"\n--- {other} ---\n{file_codes[other][:1500]}\n"

        prompt = f"""You are an expert {language} developer making a SMALL, INCREMENTAL change to an
existing, working project. Do not rewrite unrelated code or restructure the project.

Project: {task.project_name}
Original goal: {task.original_goal}
Requested change: {change_request}

Relevant evidence from this project (file:line, relevance-ranked):
{evidence_context[:MAX_EVIDENCE_PROMPT_CHARS] if evidence_context else "(none found)"}

Impact summary (deterministic, bounded — not exhaustive):
{impact.summary()}

You may modify ONLY this file: {target}
{"This file does not exist yet in the project — create it." if is_new_file else "Other file(s) being changed together for this same request (read-only context):"}
{others_ctx}
{"" if is_new_file else f"Current content of {target}:\n{current_code}"}
{memory_note}

Rules:
- Output ONLY the complete file content for {target}. No explanation, no markdown, no backticks.
- Make the SMALLEST change that satisfies the request.
- Keep all existing correct logic and functionality intact — do not remove working features.
- Do not introduce new bugs or remove error handling.

{"New" if is_new_file else "Updated"} file content for {target}:"""

        try:
            response = model.generate_content(prompt)
        except Exception as e:
            if _is_rate_limit(e):
                raise RateLimitError(str(e))
            print(f"[DevAgent] Feature edit failed for {target}: {e}")
            continue

        new_code = _strip_fences(response.text)

        if target.endswith(".py"):
            try:
                ast.parse(new_code)
            except SyntaxError as e:
                print(f"[DevAgent] Feature edit produced invalid syntax for {target}: {e}")
                continue

        try:
            full_path = ws.resolve_in_workspace(target, project_dir)
        except ws.PathEscapeError:
            continue
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(new_code, encoding="utf-8")
        updated[target] = new_code

    return updated


def _run_incremental_feature_change(
    task: "ct.CodingTask", change_request: str, timeout: int, speak=None, player=None,
) -> str:
    """
    Surgical incremental change for an EXISTING CodingTask: identify the
    smallest bounded set of files a feature/change request actually
    touches (evidence + dependency/impact analysis), edit ONLY those,
    validate, and hand any runtime failure to the EXISTING evidence-driven
    fix loop. Never calls the fresh-project planner/writer — a full
    project rewrite is never triggered from this path.
    """
    from actions import impact_analysis as ia

    project_dir = Path(task.project_root)
    if not task.project_root or not project_dir.is_dir():
        return f"I can't find the project folder for '{task.project_name}' anymore, sir. It may have been moved or deleted."

    file_codes = _reload_file_codes_from_disk(project_dir)
    if not file_codes:
        return f"I couldn't find any source files in {project_dir}, sir."

    entry_point = task.entry_point or "main.py"
    run_command = task.run_command or f"python {entry_point}"

    if player:
        player.write_log(f"[DevAgent] Scoping incremental change: {change_request[:80]}")

    # Evidence-driven candidate selection — same bounded, project-scoped
    # search evidence-driven fixing already uses, just seeded by the change
    # request instead of a runtime error.
    evidence, evidence_context = _gather_project_evidence(project_dir, change_request, None)

    candidate_files: list[str] = []
    for e in evidence:
        fp = e.get("file")
        if fp and fp in file_codes and fp not in candidate_files:
            candidate_files.append(fp)

    new_file = _extract_explicit_new_file(change_request, project_dir, file_codes)
    if new_file and new_file not in candidate_files:
        candidate_files.append(new_file)

    if not candidate_files:
        return (
            f"I couldn't confidently scope '{change_request}' to specific files in "
            f"'{task.project_name}', sir. This may need broader changes than a safe "
            "incremental edit — tell me which file to change, or ask me to rebuild the project."
        )

    if len(candidate_files) > MAX_FEATURE_TARGET_FILES:
        return (
            f"'{change_request}' would touch {len(candidate_files)} files in "
            f"'{task.project_name}', sir — too broad for a safe incremental change. "
            "Ask me to rebuild the project if you want this applied throughout."
        )

    target_files = candidate_files
    impact = ia.build_impact_report(project_dir, target_files)
    impact_summary = impact.summary()

    # Engineering-memory recall (deterministic, no LLM): prior same-project
    # feature changes with overlapping files/wording, plus a check for an
    # exact prior failed/rolled-back attempt fingerprint.
    attempt_summary = change_request
    pkey = em.project_key(str(project_dir))
    fingerprint = em.compute_attempt_fingerprint("feature_change", "", target_files, attempt_summary)

    memory_note = ""
    relevant_memory = em.find_relevant_for_change(pkey, change_request, target_files)
    if relevant_memory:
        memory_note += (
            "\nRelevant past engineering outcomes for this project (bounded recall — "
            f"do not blindly repeat a failed approach):\n{em.summarize_records(relevant_memory)}\n"
        )

    prior_failure = em.find_matching_failed_attempt(pkey, fingerprint)
    if prior_failure:
        reason = prior_failure.rollback_reason or prior_failure.failure_category or "no reason recorded"
        memory_note += (
            f"\nIMPORTANT: A previous attempt at this exact change was {prior_failure.outcome} ({reason}). "
            "Avoid repeating this approach — generate a materially different change.\n"
            f"Prior attempt summary: {prior_failure.attempt_summary}\n"
        )

    # Snapshot BEFORE writing anything — same rollback primitives the fix
    # loop already uses, local to this one incremental-change attempt.
    snapshot = _snapshot_files(project_dir, target_files)

    try:
        updated = _apply_feature_change(
            change_request=change_request,
            task=task,
            project_dir=project_dir,
            target_files=target_files,
            file_codes=file_codes,
            evidence_context=evidence_context,
            impact=impact,
            language=task.language,
            memory_note=memory_note,
        )
    except RateLimitError:
        msg = "Rate limit reached while making that change, sir. Please try again in a moment."
        if speak: speak(msg)
        return msg

    missing = [f for f in target_files if f not in updated]
    if missing:
        _rollback_snapshot(project_dir, snapshot, file_codes)
        em.record_outcome(
            task, "feature_change", change_request, "", evidence_context, impact_summary,
            target_files, attempt_summary, outcome=em.OUTCOME_ROLLED_BACK,
            rollback_reason="write_failure",
        )
        msg = (
            f"I couldn't safely write all the files needed for that change, sir — "
            f"rolled back to keep '{task.project_name}' working."
        )
        if speak: speak(msg)
        return msg

    file_codes.update(updated)
    task.touch_files(target_files)

    output = _run_project(run_command, project_dir, timeout)

    if not _has_error(output, run_command):
        em.record_outcome(
            task, "feature_change", change_request, "", evidence_context, impact_summary,
            target_files, attempt_summary, outcome=em.OUTCOME_SUCCESS, successful_step="feature_change",
        )
        ct.mark_completed(task, last_step="feature_change")
        msg = f"Done, sir — updated '{task.project_name}'. Saved to: {project_dir}"
        if speak: speak(msg)
        return f"{msg}\n\nOutput:\n{output}"

    # Runtime failure: hand off to the EXISTING evidence-driven fix loop.
    # Its own bounded attempts + rollback take over from here — the
    # feature edit itself is the new baseline being fixed, not reverted.
    files_plan = [{"path": p, "description": "", "imports": []} for p in file_codes]
    return _run_fix_loop(
        project_dir=project_dir,
        run_command=run_command,
        description=task.current_goal,
        language=task.language,
        files=files_plan,
        entry_point=entry_point,
        file_codes=file_codes,
        timeout=timeout,
        proj_name=task.project_name,
        speak=speak,
        player=player,
        task=task,
        operation_type="runtime_fix",
    )


def dev_agent(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    """
    JARVIS tool entry point. Orchestrates coding-task continuity (see
    core/coding_task.py) around the existing build/run/fix pipeline:

    - No active task + a normal build description  -> start a new task.
    - No active task + continuation-shaped language -> ask which project,
      never guess one.
    - Active task + explicit "a NEW/another app" language -> start a new
      task anyway (the active one is left as-is on disk, just no longer
      tracked as "current").
    - Active task + "fix it" / "continue" style language -> resume the
      SAME project's validate/fix loop (_continue_fix_loop_for_task),
      using whatever is currently on disk — no re-plan, no rewrite.
    - Active task + "add X" / feature language (or anything else, as long
      as it's not an explicit new-project request) -> re-run the full
      build pipeline pointed at the SAME project directory/name, which
      also transparently reopens a COMPLETED/FAILED task.
    """
    p            = parameters or {}
    description  = p.get("description", "").strip()
    language     = p.get("language", "python").strip()
    project_name = p.get("project_name", "").strip()
    timeout      = int(p.get("timeout", 30))

    if not description:
        return "Please describe the project you want me to build, sir."

    active     = ct.load_active_task()
    forces_new = ct.looks_like_new_project_request(description)

    if not forces_new and active:
        if ct.looks_like_fix_continuation(description) and not ct.looks_like_feature_continuation(description):
            ct.continue_task(active, description)
            return _continue_fix_loop_for_task(active, timeout, speak=speak, player=player)

        # Feature-add or generic continuation: surgical incremental change
        # on the SAME project — never the fresh-project planner/writer.
        ct.continue_task(active, description)
        return _run_incremental_feature_change(active, description, timeout, speak=speak, player=player)

    if not forces_new and ct.looks_like_continuation_request(description) and not active:
        return (
            "There's no active coding project for me to continue, sir. "
            "Which project did you mean, or would you like me to start a new one?"
        )

    # Brand-new project. project_name/project_root are finalized inside
    # _build_project once the planner picks a name (if the caller didn't
    # specify one) — this task record is updated at that point.
    task = ct.start_task(
        original_goal = description,
        project_name  = project_name,
        project_root  = "",
        language      = language,
    )
    return _build_project(
        description  = description,
        language     = language,
        project_name = project_name,
        timeout      = timeout,
        speak        = speak,
        player       = player,
        task         = task,
    )