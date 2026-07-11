#image_generator.py
"""
Gemini image generation, ported from a sibling project.

Reuses the existing gemini_api_key from config/api_keys.json. Gemini image
generation isn't free, so this is gated behind the same confirmed=yes
convention already used for restart/shutdown in actions/computer_settings.py.
"""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_CONFIRM_VALUES = {"yes", "true", "1", "confirm"}
_DEFAULT_MODEL = "gemini-3.1-flash-image"


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _load_config() -> dict:
    try:
        path = _get_base_dir() / "config" / "api_keys.json"
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _extension(mime_type: str) -> str:
    return {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(
        mime_type.lower(), ".png"
    )


def _output_path(filename: str, mime_type: str) -> Path:
    out_dir = _get_base_dir() / "generated_images"
    out_dir.mkdir(exist_ok=True)
    extension = _extension(mime_type)
    if filename:
        path = Path(filename).name
        stem = Path(path).stem or "image"
        return out_dir / f"{stem}{extension}"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return out_dir / f"gemini-{stamp}{extension}"


def image_generator(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    params = parameters or {}
    prompt = str(params.get("prompt", "")).strip()
    if not prompt:
        return "No image prompt provided."

    confirmed = str(params.get("confirmed", "")).lower() in _CONFIRM_VALUES
    if not confirmed:
        return (
            "Generating an image with Gemini is a billed API call. "
            "Please confirm by calling again with confirmed=yes."
        )

    config = _load_config()
    api_key = config.get("gemini_api_key", "")
    if not api_key:
        return "gemini_api_key is not configured in config/api_keys.json."
    model = config.get("gemini_image_model") or _DEFAULT_MODEL

    if player:
        player.write_log(f"[Image] generate: {prompt[:60]}")

    endpoint = "https://generativelanguage.googleapis.com/v1beta/interactions"
    payload = {"model": model, "input": [{"type": "text", "text": prompt}]}
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
            "Api-Revision": "2026-05-20",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:1000]
        return f"Gemini image API returned HTTP {e.code}: {detail}"
    except URLError as e:
        return f"Could not connect to the Gemini API: {e.reason}"
    except Exception as e:
        return f"Image generation failed: {type(e).__name__}: {e}"

    output_image = result.get("output_image") or result.get("outputImage")
    if not output_image:
        pending = [result]
        while pending and not output_image:
            value = pending.pop()
            if isinstance(value, dict):
                if value.get("type") == "image" and value.get("data"):
                    output_image = value
                    break
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)

    if output_image and output_image.get("data"):
        mime_type = output_image.get("mime_type") or output_image.get("mimeType") or "image/png"
        try:
            image_bytes = base64.b64decode(output_image["data"], validate=True)
        except Exception as e:
            return f"Gemini returned unreadable image data: {e}"
        out_path = _output_path(str(params.get("filename", "")).strip(), mime_type)
        out_path.write_bytes(image_bytes)
        return f"Generated image saved to {out_path}."

    reason = (
        result.get("prompt_feedback", {}).get("block_reason")
        or result.get("promptFeedback", {}).get("blockReason")
    )
    if reason:
        return f"Gemini blocked the image request: {reason}"
    return "Gemini returned no image data."
