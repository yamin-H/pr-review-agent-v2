"""Tool definitions, schemas, and dispatcher for the autonomous review agent."""

from pathlib import Path
from typing import Any

from review.agent.state import AgentState
from review.agent.tools.diff_tools import get_diff, list_changed_files
from review.agent.tools.findings_tools import (
    add_finding,
    run_reproduction_test,
    verify_finding,
)
from review.agent.tools.impact_map import analyze_impact
from review.agent.tools.memory_tools import search_precedents
from review.agent.tools.repo_tools import get_blame, read_file, search_code
from review.agent.tools.static_analysis import run_static_analysis
from review.agent.tools.subagent import delegate_subagent
from review.agent.tools.submit import submit_review
from review.memory.store import MemoryStore
from review.sandbox.prover import ProofEngine
from review.verifier import FindingVerifier

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_changed_files",
            "description": "Get the list of reviewable changed files in the pull request diff.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_diff",
            "description": "Get the numbered unified diff for a specific changed file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative file path (e.g. 'src/app.py')",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents in the repository around a line range for context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path to file in repository",
                    },
                    "start_line": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                        "description": "Optional 1-indexed starting line number",
                    },
                    "end_line": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                        "description": "Optional 1-indexed ending line number",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Search for symbols, function definitions, or strings across the repository."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text query or symbol name to search for",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_blame",
            "description": (
                "Inspect git blame commit history for a range of lines to understand past changes, "
                "authors, commit messages, and distinguish legacy code from new regressions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path to the file in repository",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "1-indexed starting line number",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "1-indexed ending line number",
                    },
                },
                "required": ["file_path", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_impact",
            "description": (
                "Perform AST blast radius analysis to find all callers, external usages, and "
                "associated test files for modified functions, classes, or files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path to modified Python file",
                    },
                    "symbol_name": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Optional specific function or class name to trace",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_static_analysis",
            "description": (
                "Execute deterministic AST static analysis to detect security vulnerabilities "
                "(SQLi, command injection, insecure eval/pickle, hardcoded secrets) and defect "
                "patterns (mutable defaults, unclosed handles, silent exception suppression)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path to Python file to analyze",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_subagent",
            "description": (
                "Spawn a specialized, bounded subagent to perform an in-depth focused "
                "investigation (e.g., security_audit, impact_investigation, test_coverage)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_type": {
                        "type": "string",
                        "enum": [
                            "security_audit",
                            "impact_investigation",
                            "test_coverage",
                            "general",
                        ],
                        "description": "Specialized role and focus area for the subagent",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "Specific instruction explaining what to investigate",
                    },
                    "focus_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of file paths to prioritize",
                    },
                },
                "required": ["task_type", "instruction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_precedents",
            "description": (
                "Search repository memory for historical precedents, past bug fixes, "
                "reverted commits, or team conventions relevant to the code under review."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords or pattern describing the issue or design",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Optional file path to filter relevant precedents",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_finding",
            "description": (
                "Propose a concrete defect or security finding. Line number MUST exist "
                "in the added lines of the diff for that file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "File path where issue is located",
                    },
                    "line": {
                        "type": "integer",
                        "description": "1-indexed new-file line number from the diff",
                    },
                    "title": {
                        "type": "string",
                        "description": "Concise summary title of the issue",
                    },
                    "body": {
                        "type": "string",
                        "description": "Clear explanation of the bug and suggested fix",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                        "description": "Severity level of the finding",
                    },
                    "citation": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Optional precedent citation (e.g. 'PR #42')",
                    },
                },
                "required": ["file", "line", "title", "body", "severity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_finding",
            "description": (
                "Subject a proposed finding to adversarial Devil's Advocate review "
                "to confirm or refute it. Can be called at any point after add_finding."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "finding_index": {
                        "type": "integer",
                        "description": "1-indexed number of the finding to verify",
                    },
                },
                "required": ["finding_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_reproduction_test",
            "description": (
                "Execute a reproduction unit test in an isolated sandbox subprocess to prove "
                "whether a candidate defect is a real reproducible failure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "finding_index": {
                        "type": "integer",
                        "description": "1-indexed number of the finding to reproduce",
                    },
                    "test_code": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Optional custom python test code to execute",
                    },
                },
                "required": ["finding_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_review",
            "description": "Complete the review by providing an overall summary comment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Overall summary review comment for the pull request",
                    },
                },
                "required": ["summary"],
            },
        },
    },
]


def execute_tool(
    tool_name: str,
    arguments: dict[str, Any],
    state: AgentState,
    repo_root: Path,
    verifier: FindingVerifier | None = None,
    proof_engine: ProofEngine | None = None,
    memory_store: MemoryStore | None = None,
    reviewer: Any | None = None,
    llm_client: Any | None = None,
) -> str:
    """Safely dispatch and execute a tool call requested by the agent."""
    try:
        if tool_name == "list_changed_files":
            files = list_changed_files(state.diff)
            return f"Changed reviewable files ({len(files)}):\n" + "\n".join(files)

        elif tool_name == "get_diff":
            return get_diff(state.diff, arguments.get("file_path", ""))

        elif tool_name == "read_file":
            return read_file(
                repo_root=repo_root,
                file_path=arguments.get("file_path", ""),
                start_line=arguments.get("start_line"),
                end_line=arguments.get("end_line"),
            )

        elif tool_name == "search_code":
            return search_code(repo_root=repo_root, query=arguments.get("query", ""))

        elif tool_name == "get_blame":
            return get_blame(
                repo_root=repo_root,
                file_path=arguments.get("file_path", ""),
                start_line=int(arguments.get("start_line", 1)),
                end_line=int(arguments.get("end_line", 1)),
            )

        elif tool_name == "analyze_impact":
            return analyze_impact(
                repo_root=repo_root,
                file_path=arguments.get("file_path", ""),
                symbol_name=arguments.get("symbol_name"),
            )

        elif tool_name == "run_static_analysis":
            return run_static_analysis(
                repo_root=repo_root,
                file_path=arguments.get("file_path", ""),
            )

        elif tool_name == "delegate_subagent":
            return delegate_subagent(
                state=state,
                task_type=arguments.get("task_type", "general"),
                instruction=arguments.get("instruction", ""),
                focus_files=arguments.get("focus_files"),
                repo_root=repo_root,
                reviewer=reviewer,
                llm_client=llm_client,
            )

        elif tool_name == "search_precedents":
            return search_precedents(
                state=state,
                query=arguments.get("query", ""),
                file_path=arguments.get("file_path", ""),
                memory_store=memory_store,
            )

        elif tool_name == "add_finding":
            return add_finding(
                state=state,
                file=arguments.get("file", ""),
                line=int(arguments.get("line", 0)),
                title=arguments.get("title", ""),
                body=arguments.get("body", ""),
                severity=arguments.get("severity", "medium"),
                citation=arguments.get("citation"),
            )

        elif tool_name == "verify_finding":
            return verify_finding(
                state=state,
                finding_index=int(arguments.get("finding_index", 0)),
                verifier=verifier,
            )

        elif tool_name == "run_reproduction_test":
            return run_reproduction_test(
                state=state,
                finding_index=int(arguments.get("finding_index", 0)),
                repo_root=repo_root,
                test_code=arguments.get("test_code"),
                proof_engine=proof_engine,
            )

        elif tool_name == "submit_review":
            return submit_review(state=state, summary=arguments.get("summary", ""))

        else:
            return f"Error: Unknown tool '{tool_name}'."

    except Exception as e:
        return f"Tool Execution Error in '{tool_name}': {e}"


__all__ = [
    "TOOL_DEFINITIONS",
    "add_finding",
    "analyze_impact",
    "delegate_subagent",
    "execute_tool",
    "get_blame",
    "get_diff",
    "list_changed_files",
    "read_file",
    "run_reproduction_test",
    "run_static_analysis",
    "search_code",
    "search_precedents",
    "submit_review",
    "verify_finding",
]
