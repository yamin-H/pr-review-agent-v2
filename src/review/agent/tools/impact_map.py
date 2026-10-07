"""AST-based impact mapping and caller dependency analysis tool."""

import ast
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("review.agent.tools.impact_map")

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    "node_modules",
    ".eggs",
}


@dataclass
class CallReference:
    """Record of a call or reference to a symbol."""

    caller_file: str
    caller_function: str
    line_number: int
    call_snippet: str


@dataclass
class ImpactAnalysisResult:
    """Aggregated impact analysis for target symbols."""

    target_file: str
    target_symbols: list[str]
    callers: dict[str, list[CallReference]] = field(default_factory=lambda: defaultdict(list))
    associated_tests: list[str] = field(default_factory=list)
    total_references: int = 0


class SymbolDiscoveryVisitor(ast.NodeVisitor):
    """Extract top-level and class-level symbol definitions from a Python AST."""

    def __init__(self) -> None:
        self.symbols: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.symbols.append(node.name)
        # Continue visiting inner definitions if any
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.symbols.append(node.name)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.symbols.append(node.name)
        self.generic_visit(node)


class CallerReferenceVisitor(ast.NodeVisitor):
    """Walk an AST to find calls and references to specified target symbols."""

    def __init__(self, target_symbols: set[str], source_lines: list[str], rel_path: str) -> None:
        self.target_symbols = target_symbols
        self.source_lines = source_lines
        self.rel_path = rel_path
        self.current_function: str = "<module>"
        self.references: list[tuple[str, CallReference]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        prev_fn = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = prev_fn

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        prev_fn = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = prev_fn

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        prev_fn = self.current_function
        self.current_function = f"class {node.name}"
        self.generic_visit(node)
        self.current_function = prev_fn

    def visit_Call(self, node: ast.Call) -> None:
        target_name: str | None = None
        if isinstance(node.func, ast.Name):
            target_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            target_name = node.func.attr

        if target_name and target_name in self.target_symbols:
            line_idx = node.lineno - 1
            snippet = (
                self.source_lines[line_idx].strip()
                if 0 <= line_idx < len(self.source_lines)
                else f"{target_name}(...)"
            )
            self.references.append(
                (
                    target_name,
                    CallReference(
                        caller_file=self.rel_path,
                        caller_function=self.current_function,
                        line_number=node.lineno,
                        call_snippet=snippet,
                    ),
                )
            )

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Also capture attribute references not immediately part of a call
        if node.attr in self.target_symbols:
            line_idx = node.lineno - 1
            snippet = (
                self.source_lines[line_idx].strip()
                if 0 <= line_idx < len(self.source_lines)
                else f".{node.attr}"
            )
            self.references.append(
                (
                    node.attr,
                    CallReference(
                        caller_file=self.rel_path,
                        caller_function=self.current_function,
                        line_number=node.lineno,
                        call_snippet=snippet,
                    ),
                )
            )
        self.generic_visit(node)


def find_associated_tests(repo_root: Path, file_path: str) -> list[str]:
    """Identify relevant test files associated with a given source file."""
    src_path = Path(file_path)
    stem = src_path.stem
    if stem.startswith("test_"):
        return [file_path]

    candidates = [
        f"test_{stem}.py",
        f"test_{src_path.name}",
    ]

    matched_tests: list[str] = []
    tests_dir = repo_root / "tests"
    if tests_dir.is_dir():
        for py_file in tests_dir.rglob("*.py"):
            if py_file.name in candidates or stem in py_file.stem:
                matched_tests.append(py_file.relative_to(repo_root).as_posix())

    return sorted(set(matched_tests))


def analyze_impact(
    repo_root: Path,
    file_path: str,
    symbol_name: str | None = None,
) -> str:
    """Analyze the caller impact and blast radius for a file or specific symbol.

    Args:
        repo_root: Path to the root of the target repository.
        file_path: Relative path to the modified file.
        symbol_name: Optional symbol name to restrict analysis to.

    Returns:
        Formatted textual summary of callers, usages, and associated test coverage.
    """
    target_abs = repo_root / file_path
    if not target_abs.is_file():
        return f"Error: Target file '{file_path}' does not exist in repository."

    try:
        content = target_abs.read_text(encoding="utf-8", errors="replace")
        target_ast = ast.parse(content, filename=str(target_abs))
    except Exception as e:
        return f"Error parsing Python syntax in '{file_path}': {e}"

    # Determine symbols to track
    if symbol_name:
        target_symbols = {symbol_name.strip()}
    else:
        discovery = SymbolDiscoveryVisitor()
        discovery.visit(target_ast)
        target_symbols = set(discovery.symbols)

    if not target_symbols:
        return f"No function or class definitions found in '{file_path}' to analyze."

    result = ImpactAnalysisResult(
        target_file=file_path,
        target_symbols=sorted(target_symbols),
        associated_tests=find_associated_tests(repo_root, file_path),
    )

    # Scan python files across repository
    for path in repo_root.rglob("*.py"):
        # Skip excluded dirs
        parts = path.parts
        if any(exc in parts for exc in EXCLUDED_DIRS):
            continue

        rel_path = path.relative_to(repo_root).as_posix()
        try:
            file_code = path.read_text(encoding="utf-8", errors="replace")
            file_lines = file_code.splitlines()
            parsed = ast.parse(file_code, filename=str(path))
            visitor = CallerReferenceVisitor(target_symbols, file_lines, rel_path)
            visitor.visit(parsed)

            for sym, ref in visitor.references:
                # Avoid counting a definition as a caller if caller is itself at the same line
                result.callers[sym].append(ref)
                result.total_references += 1
        except Exception:
            # Skip unparseable files gracefully
            continue

    # Format output report
    lines: list[str] = [
        f"=== Impact & Blast Radius Analysis for `{file_path}` ===",
        f"Target Symbols Analyzed: {', '.join(result.target_symbols)}",
        f"Total Usages Found: {result.total_references}",
    ]

    if result.associated_tests:
        lines.append("\nAssociated Test Files:")
        for t in result.associated_tests:
            lines.append(f"  - `{t}`")
    else:
        lines.append("\nAssociated Test Files: None identified (Warning: verify test coverage)")

    lines.append("\nCaller References by Symbol:")
    for sym in sorted(result.target_symbols):
        refs = result.callers.get(sym, [])
        if not refs:
            lines.append(f"  - `{sym}`: No external callers found (may be private or entry point).")
        else:
            lines.append(f"  - `{sym}` ({len(refs)} references):")
            for ref in refs[:8]:  # Limit output per symbol
                lines.append(
                    f"      * [{ref.caller_file}:{ref.line_number}] "
                    f"in `{ref.caller_function}`: {ref.call_snippet}"
                )
            if len(refs) > 8:
                lines.append(f"      * ... and {len(refs) - 8} more references")

    return "\n".join(lines)
