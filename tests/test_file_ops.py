"""Tests for file_ops module."""
import tempfile
from pathlib import Path

import pytest

from actions import file_ops
from core import workspace as ws


@pytest.fixture
def test_workspace(tmp_path) -> Path:
    """Create a minimal test workspace."""
    root = tmp_path / "test_ws"
    root.mkdir()
    (root / "hello.txt").write_text("Hello, World!\n", encoding="utf-8")
    (root / "test.py").write_text(
        "def greet(name):\n"
        "    return f'Hello, {name}!'\n",
        encoding="utf-8",
    )
    (root / "subdir").mkdir()
    (root / "subdir" / "nested.txt").write_text("Nested file\n", encoding="utf-8")
    return root


def test_read_file_full(test_workspace):
    """Read entire file."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.read_file(test_workspace, "hello.txt")
    assert "Hello, World!" in result


def test_read_file_line_range(test_workspace):
    """Read file with line range."""
    ws.set_workspace(str(test_workspace))
    content = file_ops.read_file(test_workspace, "test.py", start_line=1, end_line=1)
    assert "def greet" in content
    assert "return" not in content


def test_create_file_requires_confirmation(test_workspace):
    """Create file without confirmation should return gate message."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.create_file(test_workspace, "new.txt", content="test")
    assert "[GATE]" in result
    assert not (test_workspace / "new.txt").exists()


def test_create_file_with_confirmation(test_workspace):
    """Create file with confirmation."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.create_file(
        test_workspace, "new.txt", content="test content", confirmed="yes"
    )
    assert "✅ Created" in result
    assert (test_workspace / "new.txt").read_text() == "test content"


def test_create_file_already_exists(test_workspace):
    """Create file that already exists should fail."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.create_file(
        test_workspace, "hello.txt", content="new", confirmed="yes"
    )
    assert "❌ File already exists" in result


def test_replace_exact_requires_confirmation(test_workspace):
    """Replace text without confirmation should return gate."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.replace_exact(
        test_workspace, "hello.txt", "Hello", "Hi"
    )
    assert "[GATE]" in result
    assert "Hello, World!" in (test_workspace / "hello.txt").read_text()


def test_replace_exact_with_confirmation(test_workspace):
    """Replace text with confirmation."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.replace_exact(
        test_workspace, "hello.txt", "Hello", "Hi", confirmed="yes"
    )
    assert "✅ Replaced" in result
    assert "Hi, World!" in (test_workspace / "hello.txt").read_text()


def test_replace_exact_not_found(test_workspace):
    """Replace nonexistent text should fail."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.replace_exact(
        test_workspace, "hello.txt", "Goodbye", "Hi", confirmed="yes"
    )
    assert "❌ Old text not found" in result


def test_replace_line_range_with_confirmation(test_workspace):
    """Replace lines in file."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.replace_line_range(
        test_workspace, "test.py", 1, 1,
        "def greet(name, greeting='Hello'):\n",
        confirmed="yes"
    )
    assert "✅ Replaced lines" in result
    content = (test_workspace / "test.py").read_text()
    assert "greeting=" in content


def test_append_file_with_confirmation(test_workspace):
    """Append to file."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.append_file(
        test_workspace, "hello.txt", "\nGoodbye!\n", confirmed="yes"
    )
    assert "✅ Appended" in result
    content = (test_workspace / "hello.txt").read_text()
    assert "Hello, World!" in content
    assert "Goodbye!" in content


def test_delete_file_requires_confirmation(test_workspace):
    """Delete without confirmation should return gate."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.delete_file(test_workspace, "hello.txt")
    assert "[GATE]" in result
    assert (test_workspace / "hello.txt").exists()


def test_delete_file_with_confirmation(test_workspace):
    """Delete file with confirmation."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.delete_file(test_workspace, "hello.txt", confirmed="yes")
    assert "✅ Deleted" in result
    assert not (test_workspace / "hello.txt").exists()


def test_rename_file_with_confirmation(test_workspace):
    """Rename file."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.rename_file(
        test_workspace, "hello.txt", "greeting.txt", confirmed="yes"
    )
    assert "✅ Renamed" in result
    assert not (test_workspace / "hello.txt").exists()
    assert (test_workspace / "greeting.txt").exists()


def test_validate_syntax_valid_python(test_workspace):
    """Validate valid Python syntax."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.validate_syntax(test_workspace, "test.py")
    assert "✅ Valid Python" in result


def test_validate_syntax_invalid_python(test_workspace):
    """Validate invalid Python syntax."""
    ws.set_workspace(str(test_workspace))
    (test_workspace / "bad.py").write_text("def broken(\n", encoding="utf-8")
    result = file_ops.validate_syntax(test_workspace, "bad.py")
    assert "❌" in result
    assert "Syntax error" in result


def test_path_escape_protection(test_workspace):
    """Paths outside workspace should be blocked."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.read_file(test_workspace, "../../etc/passwd")
    assert "[BLOCKED" in result or "Path escape" in result


def test_create_nested_file(test_workspace):
    """Create file in nested directory that doesn't exist."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.create_file(
        test_workspace, "new/nested/file.txt", content="nested", confirmed="yes"
    )
    assert "✅ Created" in result
    assert (test_workspace / "new" / "nested" / "file.txt").exists()


def test_file_ops_dispatcher(test_workspace):
    """Test the main dispatcher function."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.file_ops(
        {"action": "read", "path": "hello.txt"}
    )
    assert "Hello, World!" in result


def test_tool_entry_point_with_validation(test_workspace):
    """Test tool entry point with Python validation."""
    ws.set_workspace(str(test_workspace))
    result = file_ops.file_ops(
        {
            "action": "create",
            "path": "validated.py",
            "content": "x = 42\n",
            "validate_py": "true",
            "confirmed": "yes"
        }
    )
    assert "✅ Created" in result
    assert (test_workspace / "validated.py").exists()
