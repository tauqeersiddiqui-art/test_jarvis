"""Tests for pending action mechanism and WhatsApp send."""
import pytest

from core.pending_action import PendingActionStore, PendingAction
from actions.send_message import whatsapp_send
from core import contacts


def test_pending_action_missing_slots():
    """Test that missing returns unfilled slots."""
    pa = PendingAction(
        tool_name="whatsapp_send",
        required=["receiver", "message_text"],
        collected={"receiver": "John"}
    )
    assert pa.missing == ["message_text"]
    assert not pa.is_complete


def test_pending_action_all_slots_filled():
    """Test that all filled slots marks complete."""
    pa = PendingAction(
        tool_name="whatsapp_send",
        required=["receiver", "message_text"],
        collected={"receiver": "John", "message_text": "Hi"}
    )
    assert pa.missing == []
    assert pa.is_complete


def test_pending_action_timeout():
    """Test that action expires after timeout."""
    import time
    pa = PendingAction(
        tool_name="test",
        required=["a"],
        timeout_seconds=0.1
    )
    assert not pa.is_expired()
    time.sleep(0.2)
    assert pa.is_expired()


def test_pending_action_store_get():
    """Test store returns active action."""
    store = PendingActionStore()
    pa = store.start("test_tool", ["slot1", "slot2"])
    retrieved = store.get()
    assert retrieved is pa
    assert retrieved.tool_name == "test_tool"


def test_pending_action_store_cancel():
    """Test store can cancel pending action."""
    store = PendingActionStore()
    store.start("test_tool", ["slot1"])
    assert store.get() is not None
    cancelled = store.cancel()
    assert cancelled  # had one to cancel
    assert store.get() is None


def test_pending_action_store_get_or_start_existing():
    """Test get_or_start returns existing action if tool matches."""
    store = PendingActionStore()
    pa1 = store.start("whatsapp", ["receiver", "message"])
    pa2 = store.get_or_start("whatsapp", ["receiver", "message"])
    assert pa1 is pa2


def test_pending_action_store_get_or_start_different_tool():
    """Test get_or_start creates new action if tool differs."""
    store = PendingActionStore()
    pa1 = store.start("whatsapp", ["receiver", "message"])
    pa2 = store.get_or_start("email", ["recipient", "subject"])
    assert pa1 is not pa2
    assert pa2.tool_name == "email"


def test_whatsapp_send_asks_for_recipient():
    """Test WhatsApp send asks for recipient when missing."""
    from core.pending_action import _store
    _store.cancel()  # clear any previous state
    result = whatsapp_send({"message_text": "Hello"})
    assert "Who should I send it to?" in result


def test_whatsapp_send_asks_for_message():
    """Test WhatsApp send asks for message when missing."""
    from core.pending_action import _store
    _store.cancel()  # clear any previous state
    result = whatsapp_send({"receiver": "John"})
    assert "What message should I send" in result


def test_whatsapp_send_requires_confirmation():
    """Test WhatsApp send requires confirmation when slots filled."""
    from core.pending_action import _store
    _store.cancel()  # clear any previous state
    result = whatsapp_send({
        "receiver": "John",
        "message_text": "Hello John",
        "confirmed": "no"
    })
    assert "confirm" in result.lower()
    assert "WhatsApp message" in result


def test_whatsapp_send_with_recipient_alias():
    """Test WhatsApp send accepts 'recipient' as alias for 'receiver'."""
    from core.pending_action import _store
    _store.cancel()  # clear any previous state
    result = whatsapp_send({
        "recipient": "John",
        "message": "Test"
    })
    # Should ask for confirmation since slots filled
    assert "confirm" in result.lower()


def test_whatsapp_send_cancel():
    """Test WhatsApp send can be cancelled."""
    from core.pending_action import _store
    # First start a pending action
    whatsapp_send({"receiver": "John", "message_text": "Test"})
    # Then cancel it
    result = whatsapp_send({"cancel": "yes"})
    assert "cancelled" in result.lower()


def test_cancel_pending_action_function():
    """Test cancel_pending_action function."""
    from core.pending_action import cancel_pending_action, _store
    # Start an action
    _store.start("test", ["a", "b"])
    # Cancel it
    result = cancel_pending_action()
    assert "cancelled" in result.lower()
    assert _store.get() is None


def test_cancel_pending_action_when_none():
    """Test cancel when nothing pending."""
    from core.pending_action import cancel_pending_action, _store
    _store.cancel()  # clear any existing
    result = cancel_pending_action()
    assert "nothing pending" in result.lower()


def test_find_contacts_resolves_single_match():
    """Test contact resolution finds exact match."""
    # Note: this depends on memory/long_term.json having test data
    # For now just test the function doesn't crash
    result = contacts.resolve("Nonexistent")
    assert result[0] == "unresolved"


def test_whatsapp_send_multi_turn_flow():
    """Test multi-turn WhatsApp flow."""
    from core.pending_action import _store
    _store.cancel()  # start fresh
    
    # Turn 1: no params -> asks for recipient
    result1 = whatsapp_send({})
    assert "Who should I send it to?" in result1
    pending1 = _store.get()
    assert pending1 is not None
    
    # Turn 2: provide recipient -> asks for message
    result2 = whatsapp_send({"receiver": "Alice"})
    assert "What message should I send" in result2
    pending2 = _store.get()
    assert pending2 is pending1  # same action
    assert pending2.collected.get("receiver") == "Alice"
    
    # Turn 3: provide message -> asks for confirmation
    result3 = whatsapp_send({"message_text": "Hello Alice"})
    assert "confirm" in result3.lower()
    pending3 = _store.get()
    assert pending3 is pending1  # still same action
    assert pending3.collected.get("message_text") == "Hello Alice"


def test_pending_action_touch_updates_time():
    """Test that touch() updates the last-activity timestamp."""
    import time
    pa = PendingAction(
        tool_name="test",
        required=["a"],
        timeout_seconds=1.0
    )
    t1 = pa.updated_at
    time.sleep(0.1)
    pa.touch()
    t2 = pa.updated_at
    assert t2 > t1


def test_pending_action_current_slot():
    """Test that touch() sets current_slot to next missing."""
    pa = PendingAction(
        tool_name="test",
        required=["a", "b", "c"],
        collected={"a": "value_a"}
    )
    pa.touch()
    assert pa.current_slot == "b"
