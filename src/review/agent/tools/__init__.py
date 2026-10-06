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
from review.agent.tools.repo_tools import read_file, search_code
from review.agent.tools.submit import submit_review
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
                        "type": "integer",
                        "description": "Optional 1-indexed starting line number",
                    },
                    "end_line": {
                        "type": "integer",
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
            "name": "add_finding",
            "description": (
                "Add an inline review finding. The line must be an added line in the diff, "
                "or it will be rejected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Target file path"},
                    "line": {
                        "type": "integer",
                        "description": "Exact new-file line number from the diff",
                    },
                    "title": {"type": "string", "description": "Short issue title"},
                    "body": {
                        "type": "string",
                        "description": "Detailed explanation and recommendation",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["high", "medium", "low", "info"],
                        "description": "Finding severity level",
                    },
                },
                "required": ["file", "line", "title", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_finding",
            "description": (
                "Adversarially challenge and verify a candidate finding by its number or index. "
                "Disproves false positives or suggests revisions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "finding_index": {
                        "type": "integer",
                        "description": "Number of the finding to verify (1-indexed or 0-indexed)",
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
                "Execute a reproduction test in an isolated sandbox to prove a suspected bug. "
                "Optionally supply custom Python test code or let the engine synthesize one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "finding_index": {
                        "type": "integer",
                        "description": "Finding number/index to reproduce (1-indexed or 0-indexed)",
                    },
                    "test_code": {
                        "type": "string",
                        "description": "Optional Python test script content to run in the sandbox",
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

        elif tool_name == "add_finding":
            return add_finding(
                state=state,
                file=arguments.get("file", ""),
                line=int(arguments.get("line", 0)),
                title=arguments.get("title", ""),
                body=arguments.get("body", ""),
                severity=arguments.get("severity", "medium"),
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
    "execute_tool",
    "get_diff",
    "list_changed_files",
    "read_file",
    "run_reproduction_test",
    "search_code",
    "submit_review",
    "verify_finding",
]
