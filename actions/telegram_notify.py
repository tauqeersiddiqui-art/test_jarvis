#telegram_notify.py
"""
Telegram Bot API notifications, ported from a sibling project.

Send-only (no polling/receiving). Reads telegram_bot_token/telegram_chat_id
from config/api_keys.json, falling back to the TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID environment variables. Non-destructive — no confirmation gate.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _get_credential(key: str, env_name: str) -> str:
    try:
        path = _get_base_dir() / "config" / "api_keys.json"
        with open(path, "r", encoding="utf-8") as f:
            value = str(json.load(f).get(key, "") or "").strip()
        if value:
            return value
    except Exception:
        pass
    return str(os.getenv(env_name, "") or "").strip()


def telegram_notify(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    params = parameters or {}
    message = str(params.get("message", "")).strip()
    if not message:
        return "No message provided to send."

    token = _get_credential("telegram_bot_token", "TELEGRAM_BOT_TOKEN")
    chat_id = _get_credential("telegram_chat_id", "TELEGRAM_CHAT_ID")
    if not token:
        return "Telegram is not configured: add 'telegram_bot_token' to config/api_keys.json."
    if not chat_id:
        return "Telegram is not configured: add 'telegram_chat_id' to config/api_keys.json."

    if player:
        player.write_log(f"[Telegram] send: {message[:60]}")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": message}).encode()
    request = Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urlopen(request, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        if result.get("ok"):
            return "Message sent via Telegram."
        return f"Telegram API error: {result}"
    except HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        return f"Telegram HTTP {e.code}: {detail}"
    except URLError as e:
        return f"Telegram connection error: {e.reason}"
    except Exception as e:
        return f"Telegram error: {type(e).__name__}: {e}"
