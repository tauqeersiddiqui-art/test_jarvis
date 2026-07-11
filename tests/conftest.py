import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def project(tmp_path) -> Path:
    """A small synthetic project: a git repo with a .gitignore, a source
    file importing a helper, a test file, a secret .env, and directories
    that must be excluded both via .gitignore and via the hardcoded
    ignored-dirs list."""
    root = tmp_path / "proj"
    root.mkdir()

    (root / ".gitignore").write_text("ignored_dir/\n*.secret\n", encoding="utf-8")

    (root / "main.py").write_text(
        "from utils.helper import helper_func\n"
        "\n"
        "class Foo:\n"
        "    def bar(self):\n"
        "        return helper_func()\n"
        "\n"
        "def main():\n"
        "    return Foo().bar()\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    main()\n",
        encoding="utf-8",
    )

    (root / "utils").mkdir()
    (root / "utils" / "__init__.py").write_text("", encoding="utf-8")
    (root / "utils" / "helper.py").write_text(
        "def helper_func():\n"
        "    return 42\n",
        encoding="utf-8",
    )

    (root / "tests").mkdir()
    (root / "tests" / "test_helper.py").write_text(
        "from utils.helper import helper_func\n"
        "\n"
        "def test_helper_func():\n"
        "    assert helper_func() == 42\n",
        encoding="utf-8",
    )

    (root / ".env").write_text("GEMINI_API_KEY=sk-real-secret-value-12345\n", encoding="utf-8")

    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("module.exports = {};\n", encoding="utf-8")

    (root / "ignored_dir").mkdir()
    (root / "ignored_dir" / "secret_data.txt").write_text("should not be found\n", encoding="utf-8")

    (root / "data.secret").write_text("gitignored-by-pattern\n", encoding="utf-8")

    (root / "config.json").write_text('{"key": "value"}\n', encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)

    return root
