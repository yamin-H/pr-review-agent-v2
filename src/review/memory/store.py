"""Persistent SQLite-backed repository memory store with tenant-scoped retrieval."""

import fnmatch
import math
import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from review.memory.models import Precedent, PrecedentSearchResult

DEFAULT_DB_PATH = Path(".reviewer_memory.db")


def _tokenize(text: str) -> set[str]:
    """Extract lowercase alphanumeric word tokens from text."""
    return set(re.findall(r"\b[a-zA-Z0-9_]{2,}\b", text.lower()))


def _compute_relevance_score(query_tokens: set[str], precedent: Precedent) -> float:
    """Compute normalized lexical/semantic overlap score between query and precedent content."""
    if not query_tokens:
        return 0.0

    title_tokens = _tokenize(precedent.title)
    desc_tokens = _tokenize(precedent.description)
    snippet_tokens = _tokenize(precedent.code_snippet or "")

    # Title matches are weighted highest (3.0x), description (1.5x), code (1.0x)
    title_matches = len(query_tokens & title_tokens) * 3.0
    desc_matches = len(query_tokens & desc_tokens) * 1.5
    snippet_matches = len(query_tokens & snippet_tokens) * 1.0

    total_matches = title_matches + desc_matches + snippet_matches
    if total_matches == 0:
        return 0.0

    max_possible = len(query_tokens) * 3.0
    raw_ratio = total_matches / max_possible
    # Normalized asymptotic score in [0.0, 1.0]
    return float(1.0 - math.exp(-raw_ratio))


class MemoryStore:
    """Stores and retrieves historical repository precedents with organization/repo isolation."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        self._mem_conn: sqlite3.Connection | None = None
        if self.db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        if self._mem_conn is not None:
            with self._mem_conn:
                yield self._mem_conn
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

    def close(self) -> None:
        """Close connection if keeping in-memory state."""
        if self._mem_conn is not None:
            self._mem_conn.close()
            self._mem_conn = None

    def _init_db(self) -> None:
        """Create tables and indexes if they do not already exist."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS precedents (
                    id TEXT PRIMARY KEY,
                    repo TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    citation TEXT NOT NULL,
                    file_pattern TEXT NOT NULL,
                    code_snippet TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_precedents_repo ON precedents(repo);"
            )
            conn.commit()

    def add_precedent(self, precedent: Precedent) -> None:
        """Insert or replace a repository precedent."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO precedents (
                    id, repo, title, description, citation, file_pattern, code_snippet, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    precedent.id,
                    precedent.repo.lower().strip(),
                    precedent.title.strip(),
                    precedent.description.strip(),
                    precedent.citation.strip(),
                    precedent.file_pattern.strip(),
                    precedent.code_snippet,
                    precedent.created_at.isoformat(),
                ),
            )
            conn.commit()

    def get_precedent(self, precedent_id: str) -> Precedent | None:
        """Retrieve a single precedent by its unique identifier."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM precedents WHERE id = ?;", (precedent_id,)
            ).fetchone()
            if not row:
                return None
            return Precedent.model_validate(dict(row))

    def list_precedents(self, repo: str | None = None) -> list[Precedent]:
        """List precedents, optionally scoped to a specific repository."""
        query = "SELECT * FROM precedents"
        params: list[str] = []
        if repo:
            query += " WHERE repo = ?"
            params.append(repo.lower().strip())
        query += " ORDER BY created_at DESC;"

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [Precedent.model_validate(dict(row)) for row in rows]

    def delete_precedent(self, precedent_id: str) -> bool:
        """Delete a precedent by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM precedents WHERE id = ?;", (precedent_id,))
            conn.commit()
            return cursor.rowcount > 0

    def search_precedents(
        self,
        query: str,
        repo: str | None = None,
        file_path: str = "",
        min_score: float = 0.1,
        limit: int = 5,
    ) -> list[PrecedentSearchResult]:
        """Search memory for relevant precedents respecting repository boundaries.

        Filters candidates by repository name and file path pattern.
        """
        candidates = self.list_precedents(repo=repo)
        query_tokens = _tokenize(query)

        results: list[PrecedentSearchResult] = []
        for prec in candidates:
            # File pattern filter
            if (
                file_path
                and prec.file_pattern
                and prec.file_pattern != "*"
                and not fnmatch.fnmatch(file_path, prec.file_pattern)
            ):
                continue

            score = _compute_relevance_score(query_tokens, prec)
            if score >= min_score:
                results.append(PrecedentSearchResult(precedent=prec, score=score))

        # Rank by relevance score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]
