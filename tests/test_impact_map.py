"""Unit tests for AST impact mapping and caller dependency analysis."""

from pathlib import Path

from review.agent.tools.impact_map import (
    SymbolDiscoveryVisitor,
    analyze_impact,
    find_associated_tests,
)


def test_symbol_discovery_visitor() -> None:
    code = """
def standalone_func():
    pass

async def async_worker():
    pass

class DataProcessor:
    def process(self):
        pass
"""
    import ast

    tree = ast.parse(code)
    visitor = SymbolDiscoveryVisitor()
    visitor.visit(tree)

    assert "standalone_func" in visitor.symbols
    assert "async_worker" in visitor.symbols
    assert "DataProcessor" in visitor.symbols
    assert "process" in visitor.symbols


def test_find_associated_tests(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_auth.py").write_text("def test_login(): pass\n", encoding="utf-8")
    (tests_dir / "test_models.py").write_text("def test_user(): pass\n", encoding="utf-8")

    matched = find_associated_tests(tmp_path, "src/auth.py")
    assert "tests/test_auth.py" in matched

    unmatched = find_associated_tests(tmp_path, "src/untested_worker.py")
    assert unmatched == []


def test_analyze_impact_cross_file_callers(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    # 1. Define target service
    (src_dir / "calc.py").write_text(
        "def compute_total(a, b):\n    return a + b\n",
        encoding="utf-8",
    )

    # 2. Call compute_total from consumer
    (src_dir / "consumer.py").write_text(
        "from src.calc import compute_total\n"
        "def run_job():\n"
        "    res = compute_total(10, 20)\n"
        "    return res\n",
        encoding="utf-8",
    )

    # 3. Create test file
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_calc.py").write_text(
        "from src.calc import compute_total\n"
        "def test_compute():\n"
        "    assert compute_total(1, 2) == 3\n",
        encoding="utf-8",
    )

    result = analyze_impact(tmp_path, "src/calc.py")
    assert "Impact & Blast Radius Analysis for `src/calc.py`" in result
    assert "compute_total" in result
    assert "src/consumer.py" in result
    assert "tests/test_calc.py" in result


def test_analyze_impact_specific_symbol(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "utils.py").write_text(
        "def helper_one(): pass\ndef helper_two(): pass\n",
        encoding="utf-8",
    )
    (src_dir / "app.py").write_text(
        "from src.utils import helper_one\nhelper_one()\n",
        encoding="utf-8",
    )

    result = analyze_impact(tmp_path, "src/utils.py", symbol_name="helper_one")
    assert "helper_one" in result
    assert "helper_two" not in result
    assert "src/app.py" in result


def test_analyze_impact_nonexistent_file(tmp_path: Path) -> None:
    result = analyze_impact(tmp_path, "does_not_exist.py")
    assert "Error: Target file 'does_not_exist.py' does not exist" in result


def test_analyze_impact_syntax_error(tmp_path: Path) -> None:
    bad_file = tmp_path / "broken.py"
    bad_file.write_text("def broken(:\n", encoding="utf-8")

    result = analyze_impact(tmp_path, "broken.py")
    assert "Error parsing Python syntax" in result
