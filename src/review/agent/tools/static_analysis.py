"""AST-based static analysis engine detecting security vulnerabilities and defect patterns."""

import ast
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("review.agent.tools.static_analysis")

SECRET_KEYWORDS = {"api_key", "secret", "password", "token", "auth_token", "private_key"}


@dataclass
class StaticIssue:
    """Represents an issue detected by AST static analysis."""

    rule_id: str
    category: str  # "security" | "defect" | "reliability"
    severity: str  # "critical" | "high" | "medium" | "low"
    line_number: int
    title: str
    message: str
    snippet: str


class StaticAnalysisVisitor(ast.NodeVisitor):
    """AST visitor implementing deterministic security and antipattern checks."""

    def __init__(self, source_lines: list[str], file_path: str) -> None:
        self.source_lines = source_lines
        self.file_path = file_path
        self.issues: list[StaticIssue] = []
        self._within_with_statement = False

    def _get_snippet(self, lineno: int) -> str:
        idx = lineno - 1
        if 0 <= idx < len(self.source_lines):
            return self.source_lines[idx].strip()
        return ""

    def visit_With(self, node: ast.With) -> None:
        prev = self._within_with_statement
        self._within_with_statement = True
        self.generic_visit(node)
        self._within_with_statement = prev

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        prev = self._within_with_statement
        self._within_with_statement = True
        self.generic_visit(node)
        self._within_with_statement = prev

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Check BUG001: Mutable default arguments
        for default in node.args.defaults + node.args.kw_defaults:
            if default is not None and isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self.issues.append(
                    StaticIssue(
                        rule_id="BUG001_MUTABLE_DEFAULT",
                        category="defect",
                        severity="medium",
                        line_number=node.lineno,
                        title="Mutable Default Argument",
                        message=(
                            f"Function '{node.name}' uses a mutable default argument "
                            f"({type(default).__name__}), which is shared across all calls."
                        ),
                        snippet=self._get_snippet(node.lineno),
                    )
                )
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        for default in node.args.defaults + node.args.kw_defaults:
            if default is not None and isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self.issues.append(
                    StaticIssue(
                        rule_id="BUG001_MUTABLE_DEFAULT",
                        category="defect",
                        severity="medium",
                        line_number=node.lineno,
                        title="Mutable Default Argument",
                        message=(
                            f"Async function '{node.name}' uses a mutable default argument "
                            f"({type(default).__name__})."
                        ),
                        snippet=self._get_snippet(node.lineno),
                    )
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = ""
        attr_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if isinstance(node.func.value, ast.Name):
                func_name = f"{node.func.value.id}.{attr_name}"
            else:
                func_name = attr_name

        # SEC001: SQL Injection via execute formatted strings
        if attr_name in {"execute", "executemany"} and node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.JoinedStr):  # f-string
                self.issues.append(
                    StaticIssue(
                        rule_id="SEC001_SQLI",
                        category="security",
                        severity="high",
                        line_number=node.lineno,
                        title="Potential SQL Injection via Formatted String",
                        message=(
                            f"Call to '{attr_name}' uses an f-string expression. "
                            "Use parameterized query placeholders instead."
                        ),
                        snippet=self._get_snippet(node.lineno),
                    )
                )
            elif isinstance(first_arg, ast.BinOp) and isinstance(
                first_arg.op, (ast.Mod, ast.Add)
            ):
                self.issues.append(
                    StaticIssue(
                        rule_id="SEC001_SQLI",
                        category="security",
                        severity="high",
                        line_number=node.lineno,
                        title="Potential SQL Injection via String Concatenation",
                        message=(
                            f"Call to '{attr_name}' uses string concatenation/interpolation. "
                            "Use parameterized query placeholders instead."
                        ),
                        snippet=self._get_snippet(node.lineno),
                    )
                )

        # SEC002: Command Injection via subprocess shell=True
        if "subprocess" in func_name or attr_name in {"run", "Popen", "check_output", "call"}:
            for kw in node.keywords:
                if (
                    kw.arg == "shell"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    self.issues.append(
                        StaticIssue(
                            rule_id="SEC002_COMMAND_INJECTION",
                            category="security",
                            severity="critical",
                            line_number=node.lineno,
                            title="Subprocess Invocation with shell=True",
                            message=(
                                "Executing shell commands with shell=True poses severe command "
                                "injection risks if untrusted arguments are included."
                            ),
                            snippet=self._get_snippet(node.lineno),
                        )
                    )

        # SEC003: Insecure Deserialization via pickle
        if "pickle.loads" in func_name or "pickle.load" in func_name:
            self.issues.append(
                StaticIssue(
                    rule_id="SEC003_INSECURE_DESERIALIZATION",
                    category="security",
                    severity="high",
                    line_number=node.lineno,
                    title="Insecure Deserialization via Pickle",
                    message=(
                        "Using pickle to deserialize data can lead to arbitrary remote code "
                        "execution if input is untrusted. Use JSON or safe serialization formats."
                    ),
                    snippet=self._get_snippet(node.lineno),
                )
            )

        # SEC004: Insecure eval / exec
        if func_name in {"eval", "exec"}:
            self.issues.append(
                StaticIssue(
                    rule_id="SEC004_INSECURE_EVAL",
                    category="security",
                    severity="critical",
                    line_number=node.lineno,
                    title=f"Dangerous Dynamic Code Execution via {func_name}",
                    message=(
                        f"Avoid dynamic '{func_name}' execution "
                        "as it enables arbitrary code execution."
                    ),
                    snippet=self._get_snippet(node.lineno),
                )
            )

        # BUG002: Unclosed file handles (open called outside with)
        if func_name == "open" and not self._within_with_statement:
            self.issues.append(
                StaticIssue(
                    rule_id="BUG002_UNCLOSED_RESOURCE",
                    category="reliability",
                    severity="low",
                    line_number=node.lineno,
                    title="File Opened Outside With Context Manager",
                    message=(
                        "Calling open() outside a 'with' context manager risks leaking file "
                        "descriptors if an exception occurs before explicit close."
                    ),
                    snippet=self._get_snippet(node.lineno),
                )
            )

        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        # SEC005: Hardcoded credentials
        for target in node.targets:
            target_name = ""
            if isinstance(target, ast.Name):
                target_name = target.id.lower()
            elif isinstance(target, ast.Attribute):
                target_name = target.attr.lower()

            has_secret_kw = any(sec in target_name for sec in SECRET_KEYWORDS)
            is_long_str = (
                isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and len(node.value.value) >= 16
            )
            if has_secret_kw and is_long_str:
                self.issues.append(
                    StaticIssue(
                        rule_id="SEC005_HARDCODED_SECRET",
                        category="security",
                        severity="high",
                        line_number=node.lineno,
                        title="Potential Hardcoded Secret or API Key",
                        message=(
                            f"Variable '{target_name}' appears to contain a hardcoded secret. "
                            "Store credentials in environment variables or a secrets manager."
                        ),
                        snippet=self._get_snippet(node.lineno),
                    )
                )
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        # BUG003: Bare except or except Exception with pass
        is_bare = node.type is None
        is_broad = (
            isinstance(node.type, ast.Name)
            and node.type.id in {"Exception", "BaseException"}
        )

        if (is_bare or is_broad) and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            self.issues.append(
                StaticIssue(
                    rule_id="BUG003_SILENT_EXCEPTION",
                    category="defect",
                    severity="medium",
                    line_number=node.lineno,
                    title="Silent Broad Exception Suppression",
                    message=(
                        "Broad exception catch with 'pass' hides unexpected failures and makes "
                        "debugging impossible. Handle specific exceptions or log the error."
                    ),
                    snippet=self._get_snippet(node.lineno),
                )
            )
        self.generic_visit(node)


def run_static_analysis(repo_root: Path, file_path: str) -> str:
    """Execute AST-based static analysis checks against a Python file.

    Args:
        repo_root: Path to the target repository root.
        file_path: Relative path to the target file.

    Returns:
        Formatted summary of detected security vulnerabilities and defect patterns.
    """
    target_abs = repo_root / file_path
    if not target_abs.is_file():
        return f"Error: File '{file_path}' does not exist in repository."

    if not file_path.endswith(".py"):
        return f"Static analysis only supports Python (.py) files. Skipped '{file_path}'."

    try:
        content = target_abs.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content, filename=str(target_abs))
    except Exception as e:
        return f"Static analysis error: Could not parse syntax in '{file_path}': {e}"

    lines = content.splitlines()
    visitor = StaticAnalysisVisitor(source_lines=lines, file_path=file_path)
    visitor.visit(tree)

    if not visitor.issues:
        return (
            f"=== Static Analysis Report: `{file_path}` ===\n"
            f"Status: PASSED. No security vulnerabilities or defect antipatterns detected."
        )

    # Group issues by category
    sec_issues = [i for i in visitor.issues if i.category == "security"]
    defect_issues = [i for i in visitor.issues if i.category in {"defect", "reliability"}]

    output_lines: list[str] = [
        f"=== Static Analysis Report: `{file_path}` ===",
        f"Total Issues Detected: {len(visitor.issues)} "
        f"({len(sec_issues)} security, {len(defect_issues)} defect/reliability)\n",
    ]

    for idx, issue in enumerate(visitor.issues, start=1):
        output_lines.append(
            f"[{idx}] {issue.rule_id} ({issue.severity.upper()} - {issue.category.upper()})\n"
            f"    Line {issue.line_number}: {issue.title}\n"
            f"    Snippet: `{issue.snippet}`\n"
            f"    Detail:  {issue.message}\n"
        )

    return "\n".join(output_lines)
