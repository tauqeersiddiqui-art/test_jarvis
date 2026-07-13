# Module: Voice

**Location:** `core/stt.py` (speech-to-text), `core/tts.py` (text-to-speech)
**Layer:** Core Services

## Purpose

Speech input/output engines for the assistant, with both offline and cloud options so
the voice path can run local-first where practical (`PRODUCT_VISION.md` Part 4.3,
Technology Principle 1) or use a higher-quality cloud engine when the user chooses.

## Responsibilities

### Speech-to-Text (`core/stt.py`)

- `WhisperSTT` — offline transcription via `faster-whisper`, VAD-buffered. Picks
  CUDA + float16 if available, falls back to CPU + int8. Handles the first-run
  "model not cached yet" case by clearing offline flags and downloading once.
- `VoskSTT` — offline streaming transcription, lighter-weight than Whisper.

### Text-to-Speech (`core/tts.py`)

- `EdgeTTSEngine` — free Microsoft TTS, requires internet, no API key.
- `KokoroTTSEngine` — fully offline neural TTS (~330 MB model).
- `ElevenLabsTTSEngine` — cloud API, requires an API key, best quality.
- `TTSPlayer` — the common playback wrapper used regardless of which engine is
  configured (`create_tts_player(config: dict) -> TTSPlayer`).

## Public Interface

- STT: instantiate `WhisperSTT(model_name="base", language=None)` or `VoskSTT(...)`
  directly; both expose a transcription interface consumed by `main.py`'s audio loop.
- TTS: `create_tts_player(config: dict) -> TTSPlayer` is the single entry point —
  callers do not instantiate a specific engine class directly; the engine choice is
  driven by `config`.

## Dependencies

- STT: `faster-whisper`, `vosk`, `numpy`, optionally `torch` (for CUDA detection).
- TTS: `edge-tts` (async), a Kokoro pipeline import, ElevenLabs SDK, `sounddevice`,
  `numpy`. `USE_TF=0` is set before any transformers import to skip TensorFlow and
  save 4–8s of startup time — `USE_TORCH`/`USE_JAX` are deliberately left unset since
  forcing them breaks `transformers`' lazy-loader on certain versions.

## Limitations

- This module is entirely separate from the Gemini Live real-time voice session in
  `main.py` — that session has its own, different real-time audio path. `core/stt.py`
  / `core/tts.py` are used for other voice-adjacent features (e.g. discrete
  transcription/synthesis needs), not as a replacement for the live session's
  streaming audio.
- Offline engines (Whisper, Vosk, Kokoro) trade some quality/latency for not requiring
  network access or an API key — engine choice is a deliberate quality/privacy/cost
  tradeoff left to configuration, not hardcoded.

## Future Direction

- `PRODUCT_VISION.md` does not currently define a dedicated Voice track distinct from
  the existing capability — this module is considered stable infrastructure that
  other Tracks (D, E) may depend on for spoken correction/feedback, without needing
  changes to this module itself.
