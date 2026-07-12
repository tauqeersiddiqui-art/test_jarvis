from pathlib import Path

from actions import impact_analysis as ia


def test_dependency_graph_identifies_direct_imports(tmp_path):
    (tmp_path / "utils.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from utils import helper\nhelper()\n", encoding="utf-8")

    direct, _reverse = ia.build_dependency_graph(tmp_path)

    assert direct["main.py"] == ["utils.py"]
    assert direct["utils.py"] == []


def test_reverse_dependency_graph_identifies_downstream_dependents(tmp_path):
    (tmp_path / "utils.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from utils import helper\nhelper()\n", encoding="utf-8")
    (tmp_path / "cli.py").write_text("from utils import helper\nprint(helper())\n", encoding="utf-8")

    _direct, reverse = ia.build_dependency_graph(tmp_path)

    assert set(reverse["utils.py"]) == {"main.py", "cli.py"}


def test_dependency_graph_excludes_unrelated_files(tmp_path):
    (tmp_path / "utils.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from utils import helper\nhelper()\n", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("def totally_unrelated():\n    pass\n", encoding="utf-8")

    direct, reverse = ia.build_dependency_graph(tmp_path)

    assert direct["unrelated.py"] == []
    assert "unrelated.py" not in reverse.get("utils.py", [])
    assert "unrelated.py" not in reverse.get("main.py", [])


def test_nested_package_import_is_resolved(tmp_path):
    (tmp_path / "utils").mkdir()
    (tmp_path / "utils" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "utils" / "helpers.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from utils.helpers import helper\nhelper()\n", encoding="utf-8")

    direct, reverse = ia.build_dependency_graph(tmp_path)

    assert "utils/helpers.py" in direct["main.py"]
    assert "main.py" in reverse["utils/helpers.py"]


def test_impact_report_is_bounded_and_primary_first(tmp_path):
    (tmp_path / "utils.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from utils import helper\nhelper()\n", encoding="utf-8")
    (tmp_path / "cli.py").write_text("from utils import helper\nprint(helper())\n", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("def totally_unrelated():\n    pass\n", encoding="utf-8")

    report = ia.build_impact_report(tmp_path, ["utils.py"])

    assert report.primary_files == ["utils.py"]
    assert report.likely_affected_files[0] == "utils.py"  # primary-first
    assert set(report.reverse_dependents) == {"main.py", "cli.py"}
    assert "unrelated.py" not in report.likely_affected_files
    assert len(report.likely_affected_files) <= ia.MAX_AFFECTED_FILES
    assert report.risk_level in ("low", "medium", "high")


def test_impact_report_risk_escalates_with_more_reverse_dependents(tmp_path):
    (tmp_path / "shared.py").write_text("def core():\n    return 1\n", encoding="utf-8")
    for i in range(4):
        (tmp_path / f"consumer{i}.py").write_text("from shared import core\ncore()\n", encoding="utf-8")

    report = ia.build_impact_report(tmp_path, ["shared.py"])
    assert report.risk_level == "high"

    (tmp_path / "isolated.py").write_text("x = 1\n", encoding="utf-8")
    isolated_report = ia.build_impact_report(tmp_path, ["isolated.py"])
    assert isolated_report.risk_level == "low"


def test_relevant_tests_are_discovered_when_present(tmp_path):
    (tmp_path / "utils.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_utils.py").write_text(
        "from utils import helper\n\ndef test_helper():\n    assert helper() == 1\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_unrelated.py").write_text(
        "def test_unrelated():\n    assert True\n", encoding="utf-8",
    )

    report = ia.build_impact_report(tmp_path, ["utils.py"])

    assert "tests/test_utils.py" in report.relevant_tests
    assert "tests/test_unrelated.py" not in report.relevant_tests


def test_impact_report_summary_never_includes_file_contents(tmp_path):
    (tmp_path / "utils.py").write_text("SECRET_LOOKING_CONTENT_XYZ = 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from utils import SECRET_LOOKING_CONTENT_XYZ\n", encoding="utf-8")

    report = ia.build_impact_report(tmp_path, ["utils.py"])
    summary = report.summary()

    assert "SECRET_LOOKING_CONTENT_XYZ" not in summary
    assert "utils.py" in summary
