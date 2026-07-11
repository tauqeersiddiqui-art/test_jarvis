#core/pending_action.py
"""
Generic pending-action / slot-filling mechanism. Not hardcoded to any one
tool — any action can start a pending action, collect required arguments
across turns, and complete once every required slot is filled.

Single-slot store: this app is single-user/single-session (one active
Gemini Live conversation at a time), so one in-flight pending action is
all that's ever needed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

DEFAULT_TIMEOUT_SECONDS = 120.0


@dataclass
class PendingAction:
    tool_name: str
    required: list
    collected: dict = field(default_factory=dict)
    current_slot: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @property
    def missing(self) -> list:
        return [f for f in self.required if not self.collected.get(f)]

    @property
    def is_complete(self) -> bool:
        return not self.missing

    def is_expired(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return (now - self.updated_at) > self.timeout_seconds

    def touch(self) -> None:
        self.updated_at = time.time()
        self.current_slot = next(iter(self.missing), None)


class PendingActionStore:
    def __init__(self):
        self._current: PendingAction | None = None

    def get(self) -> PendingAction | None:
        """Returns the active pending action, or None if there isn't one or
        it has expired (expiry is enforced here, on every lookup)."""
        if self._current and self._current.is_expired():
            self._current = None
        return self._current

    def start(self, tool_name: str, required: list, collected: dict | None = None) -> PendingAction:
        pa = PendingAction(tool_name=tool_name, required=list(required), collected=dict(collected or {}))
        pa.touch()
        self._current = pa
        return pa

    def get_or_start(self, tool_name: str, required: list) -> PendingAction:
        pa = self.get()
        if not pa or pa.tool_name != tool_name:
            pa = self.start(tool_name, required)
        return pa

    def cancel(self) -> bool:
        had_one = self._current is not None
        self._current = None
        return had_one


_store = PendingActionStore()


def get_store() -> PendingActionStore:
    return _store


def cancel_pending_action(parameters: dict = None, response=None, player=None, session_memory=None) -> str:
    """
    JARVIS tool entry point. Generic — cancels whatever is currently
    pending, regardless of which tool started it. Call this when the user
    says cancel / never mind / stop / forget it while a pending action
    (a JARVIS follow-up question) is in progress.
    """
    store = get_store()
    if store.cancel():
        if player:
            player.write_log("[PendingAction] cancelled")
        return "Okay, cancelled. I won't proceed with that."
    return "There's nothing pending to cancel."
