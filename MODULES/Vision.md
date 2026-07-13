# Module: Vision

**Location:** `actions/screen_processor.py`
**Layer:** Capability Layer

## Purpose

Screen capture and webcam vision: lets JARVIS look at the user's screen or camera and
describe what it sees, piped into a dedicated Gemini Live session.

## Responsibilities

- Capture the screen (`_capture_screen()`, via `mss` where available) or a webcam
  frame (`_capture_camera()`, via `cv2`), with backend/camera-index auto-detection
  (`_cv2_backend()`, `_detect_camera_index()`, `_get_camera_index()`) and compression
  (`_compress()`) before sending.
- Maintain a `_VisionSession` and ensure it exists before use (`_ensure_session()`),
  with a warmup path (`warmup_session()`).
- `screen_process(...)` — the main entry point invoked by tool dispatch.

## Public Interface

- `screen_process(parameters, ...) -> ...` — primary entry point for a
  screen/camera-look request.
- `warmup_session(player=None) -> None` — pre-warms the vision session.

## Dependencies

- Optional third-party: `cv2` (OpenCV, for camera capture/backend detection), `mss`
  (for screen capture) — both guarded with `try/except ImportError`, so the module
  degrades gracefully (rather than crashing at import time) when either is absent.
- A **dedicated Gemini Live session**, independent of `core/ai_provider.py`'s
  coding/text failover chain.

## Limitations — architecturally significant

This module opens a **second, independent Gemini Live audio session**, separate from
both `main.py`'s primary voice session and `core/ai_provider.py`'s coding/text
failover chain. This is a pre-existing characteristic of the module, not a defect
introduced by this documentation pass, but it is flagged here because:

- It is an exception to the architectural invariant that Gemini Live stays singular
  and separate (`ARCHITECTURE.md` §5).
- Any future capability that adds camera-based analysis on top of vision — Track D
  (pose estimation, skeleton comparison) or Track E (attention/eye-direction
  tracking) — must route through this existing session rather than opening a *third*
  independent one. See `DECISIONS/ADR-006.md` for the full reasoning and the
  constraint this places on future work.
- Vision cooldown and echo-guard logic exists specifically because this session can
  pick up its own narrated description via the microphone and re-trigger itself; this
  is handled with a cooldown window and a busy flag, reset on every new session
  connect.

## Future Direction

- `DECISIONS/ADR-006.md` should be read before starting any Track D or Track E work —
  those tracks are the most likely to be tempted to add a third parallel vision/audio
  session for continuous pose or attention analysis, which this module's existing
  design pattern advises against.
