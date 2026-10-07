"""Tools for searching repository memory, historical precedents, and team standards."""

import logging

from review.agent.state import AgentState
from review.memory.store import MemoryStore

logger = logging.getLogger("review.agent.tools.memory_tools")


def search_precedents(
    state: AgentState,
    query: str,
    file_path: str = "",
    memory_store: MemoryStore | None = None,
) -> str:
    """Search repository memory for historical precedents, bug fixes, or team conventions.

    Args:
        state: Current agent conversation state.
        query: Semantic query text or incident keywords.
        file_path: Optional file path to scope precedents by directory or pattern.
        memory_store: Optional MemoryStore instance; defaults to global store.

    Returns:
        Formatted markdown description of matching precedents and citations.
    """
    store = memory_store or MemoryStore()
    repo_scope = state.repo or None
    results = store.search_precedents(
        query=query,
        repo=repo_scope,
        file_path=file_path,
        limit=5,
    )

    if not results:
        scope_msg = f" for '{state.repo}'" if state.repo else ""
        return f"No relevant historical precedents found in repository memory{scope_msg}."

    lines: list[str] = [
        f"Found {len(results)} relevant precedent(s) in repository memory:"
    ]
    for i, res in enumerate(results, 1):
        prec = res.precedent
        lines.append(
            f"\n{i}. [{prec.citation}] {prec.title} (Relevance: {res.score:.2f})\n"
            f"   Details: {prec.description}"
        )
        if prec.code_snippet:
            clean_snippet = prec.code_snippet.strip().replace("\n", " ")[:150]
            lines.append(f"   Snippet: {clean_snippet}...")

    return "\n".join(lines)


__all__ = ["search_precedents"]
