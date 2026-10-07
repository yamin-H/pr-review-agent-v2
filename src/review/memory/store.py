"""Persistent SQLite and PostgreSQL pgvector-backed repository memory stores."""

import fnmatch
import hashlib
import json
import logging
import math
import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from review.db.models import RepoPrecedent, Repository
from review.memory.models import Precedent, PrecedentSearchResult

logger = logging.getLogger("review.memory.store")

DEFAULT_DB_PATH = Path(".reviewer_memory.db")
EMBEDDING_DIM = 384


def _tokenize(text: str) -> set[str]:
    """Extract lowercase alphanumeric word tokens from text."""
    return set(re.findall(r"\b[a-zA-Z0-9_\-\.]{2,}\b", text.lower()))


def _compute_relevance_score(query_tokens: set[str], precedent: Precedent) -> float:
    """Compute normalized lexical overlap score between query and precedent content."""
    if not query_tokens:
        return 0.0

    title_tokens = _tokenize(precedent.title)
    desc_tokens = _tokenize(precedent.description)
    snippet_tokens = _tokenize(precedent.code_snippet or "")

    title_matches = len(query_tokens & title_tokens) * 3.0
    desc_matches = len(query_tokens & desc_tokens) * 1.5
    snippet_matches = len(query_tokens & snippet_tokens) * 1.0

    total_matches = title_matches + desc_matches + snippet_matches
    if total_matches == 0:
        return 0.0

    max_possible = len(query_tokens) * 3.0
    raw_ratio = total_matches / max_possible
    return float(1.0 - math.exp(-raw_ratio))


class TextEmbedder:
    """Generates deterministic 384-dimensional unit-normalized embeddings."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        """Compute a normalized 384-dimensional dense semantic embedding for text."""
        cleaned = text.strip().lower()
        if not cleaned:
            return [0.0] * self.dim

        vec = [0.0] * self.dim

        # 1. Word tokens (weight = 2.0)
        words = re.findall(r"\b[a-zA-Z0-9_\-\.]{2,}\b", cleaned)
        for w in words:
            self._project_feature(w, weight=2.0, vec=vec)

        # 2. Word bigrams (weight = 2.5)
        for i in range(len(words) - 1):
            bigram = f"{words[i]}_{words[i+1]}"
            self._project_feature(bigram, weight=2.5, vec=vec)

        # 3. Character 3-grams and 4-grams (weight = 0.5)
        chars = "".join(cleaned.split())
        for n in (3, 4):
            if len(chars) >= n:
                for i in range(len(chars) - n + 1):
                    sub = chars[i : i + n]
                    self._project_feature(sub, weight=0.5, vec=vec)

        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 1e-9:
            return [float(v / norm) for v in vec]
        return [0.0] * self.dim

    def _project_feature(self, feature: str, weight: float, vec: list[float]) -> None:
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % self.dim
        sign = 1.0 if (digest[4] % 2 == 0) else -1.0
        vec[idx] += weight * sign

    @staticmethod
    def cosine_similarity(u: list[float], v: list[float]) -> float:
        """Compute cosine similarity between two unit-normalized vectors."""
        if not u or not v or len(u) != len(v):
            return 0.0
        dot = sum(a * b for a, b in zip(u, v, strict=True))
        return float(max(-1.0, min(1.0, dot)))

    @staticmethod
    def cosine_distance(u: list[float], v: list[float]) -> float:
        """Compute cosine distance (1.0 - similarity) bounded in [0.0, 2.0]."""
        sim = TextEmbedder.cosine_similarity(u, v)
        return float(max(0.0, 1.0 - sim))


default_embedder = TextEmbedder()


class MemoryStore:
    """Stores and retrieves historical repository precedents with organization/repo isolation."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        embedder: TextEmbedder | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self.embedder = embedder or default_embedder
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
                    category TEXT NOT NULL DEFAULT 'general',
                    embedding TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            # Add columns if migrating older sqlite schema
            cursor = conn.execute("PRAGMA table_info(precedents);")
            columns = {row["name"] for row in cursor.fetchall()}
            if "category" not in columns:
                conn.execute(
                    "ALTER TABLE precedents ADD COLUMN category TEXT NOT NULL DEFAULT 'general';"
                )
            if "embedding" not in columns:
                conn.execute("ALTER TABLE precedents ADD COLUMN embedding TEXT;")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_precedents_repo ON precedents(repo);"
            )
            conn.commit()

    def _ensure_embedding(self, precedent: Precedent) -> list[float]:
        """Compute embedding if not already set on precedent."""
        if precedent.embedding is not None and len(precedent.embedding) == EMBEDDING_DIM:
            return precedent.embedding
        text = f"{precedent.title} {precedent.description} {precedent.code_snippet or ''}"
        emb = self.embedder.embed(text)
        precedent.embedding = emb
        return emb

    def add_precedent(self, precedent: Precedent) -> None:
        """Insert or replace a repository precedent."""
        emb = self._ensure_embedding(precedent)
        emb_json = json.dumps(emb)

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO precedents (
                    id, repo, title, description, citation, file_pattern,
                    code_snippet, category, embedding, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    precedent.id,
                    precedent.repo.lower().strip(),
                    precedent.title.strip(),
                    precedent.description.strip(),
                    precedent.citation.strip(),
                    precedent.file_pattern.strip(),
                    precedent.code_snippet,
                    precedent.category,
                    emb_json,
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
            data = dict(row)
            if data.get("embedding"):
                try:
                    data["embedding"] = json.loads(data["embedding"])
                except Exception:
                    data["embedding"] = None
            return Precedent.model_validate(data)

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
            results: list[Precedent] = []
            for row in rows:
                data = dict(row)
                if data.get("embedding"):
                    try:
                        data["embedding"] = json.loads(data["embedding"])
                    except Exception:
                        data["embedding"] = None
                results.append(Precedent.model_validate(data))
            return results

    def delete_precedent(self, precedent_id: str) -> bool:
        """Delete a precedent by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM precedents WHERE id = ?;", (precedent_id,))
            conn.commit()
            return cursor.rowcount > 0

    def find_duplicates(self, precedent: Precedent, threshold: float = 0.85) -> list[Precedent]:
        """Find existing precedents in the same repo with cosine similarity >= threshold."""
        emb = self._ensure_embedding(precedent)
        candidates = self.list_precedents(repo=precedent.repo)
        duplicates: list[Precedent] = []
        for cand in candidates:
            if cand.id == precedent.id:
                continue
            cand_emb = cand.embedding or self.embedder.embed(
                f"{cand.title} {cand.description} {cand.code_snippet or ''}"
            )
            sim = TextEmbedder.cosine_similarity(emb, cand_emb)
            if sim >= threshold:
                duplicates.append(cand)
        return duplicates

    def deduplicate_and_add(
        self, precedent: Precedent, threshold: float = 0.85
    ) -> tuple[Precedent, bool]:
        """Add precedent, or merge citations into existing precedent if duplicate detected.

        Returns (precedent, is_new).
        """
        duplicates = self.find_duplicates(precedent, threshold=threshold)
        if duplicates:
            existing = duplicates[0]
            if precedent.citation not in existing.citation:
                existing.citation = f"{existing.citation}, {precedent.citation}"
                self.add_precedent(existing)
                logger.info(
                    "Merged precedent citation into %s: %s", existing.id, existing.citation
                )
            return existing, False

        self.add_precedent(precedent)
        return precedent, True

    def search_precedents(
        self,
        query: str,
        repo: str | None = None,
        file_path: str = "",
        min_score: float = 0.1,
        limit: int = 5,
    ) -> list[PrecedentSearchResult]:
        """Search memory for relevant precedents combining semantic and lexical scores."""
        candidates = self.list_precedents(repo=repo)
        query_tokens = _tokenize(query)
        query_emb = self.embedder.embed(query)

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

            lexical_score = _compute_relevance_score(query_tokens, prec)
            cand_emb = prec.embedding or self.embedder.embed(
                f"{prec.title} {prec.description} {prec.code_snippet or ''}"
            )
            sim = TextEmbedder.cosine_similarity(query_emb, cand_emb)
            cos_score = max(0.0, sim)

            # Combined hybrid score (70% semantic, 30% lexical)
            score = float(0.70 * cos_score + 0.30 * lexical_score)
            if score >= min_score or cos_score >= min_score:
                final_score = max(score, cos_score)
                results.append(PrecedentSearchResult(precedent=prec, score=final_score))

        # Rank by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]


class PgVectorMemoryStore:
    """Async PostgreSQL-backed memory store utilizing pgvector HNSW indexing."""

    def __init__(
        self,
        session: AsyncSession,
        embedder: TextEmbedder | None = None,
    ) -> None:
        self.session = session
        self.embedder = embedder or default_embedder

    async def _resolve_repo_id(self, repo_full_name: str) -> str:
        """Resolve repository UUID from full_name (e.g. 'owner/repo'), or create on demand."""
        clean_name = repo_full_name.lower().strip()
        stmt = select(Repository).where(func.lower(Repository.full_name) == clean_name)
        res = await self.session.execute(stmt)
        repo_obj = res.scalar_one_or_none()
        if repo_obj:
            return repo_obj.id

        # Fallback query if direct match
        stmt2 = select(Repository).where(Repository.id == clean_name)
        res2 = await self.session.execute(stmt2)
        repo_by_id = res2.scalar_one_or_none()
        if repo_by_id:
            return repo_by_id.id

        raise ValueError(f"Repository not found for: {repo_full_name}")

    async def add_precedent(self, precedent: Precedent, repo_id: str | None = None) -> None:
        """Insert or update a precedent in PostgreSQL."""
        if precedent.embedding is None:
            text = f"{precedent.title} {precedent.description} {precedent.code_snippet or ''}"
            precedent.embedding = self.embedder.embed(text)

        resolved_repo_id = repo_id or await self._resolve_repo_id(precedent.repo)

        stmt = select(RepoPrecedent).where(RepoPrecedent.id == precedent.id)
        res = await self.session.execute(stmt)
        record = res.scalar_one_or_none()

        if record:
            record.title = precedent.title
            record.description = precedent.description
            record.citation = precedent.citation
            record.file_pattern = precedent.file_pattern
            record.code_snippet = precedent.code_snippet
            record.embedding = precedent.embedding
        else:
            record = RepoPrecedent(
                id=precedent.id,
                repository_id=resolved_repo_id,
                citation=precedent.citation,
                title=precedent.title,
                description=precedent.description,
                file_pattern=precedent.file_pattern,
                code_snippet=precedent.code_snippet,
                embedding=precedent.embedding,
                created_at=precedent.created_at,
            )
            self.session.add(record)

        await self.session.flush()

    async def get_precedent(self, precedent_id: str, repo_name: str = "") -> Precedent | None:
        """Retrieve a precedent by ID."""
        stmt = select(RepoPrecedent, Repository.full_name).join(
            Repository, RepoPrecedent.repository_id == Repository.id
        ).where(RepoPrecedent.id == precedent_id)
        res = await self.session.execute(stmt)
        row = res.first()
        if not row:
            return None
        rec, full_name = row
        return Precedent(
            id=rec.id,
            repo=full_name,
            title=rec.title,
            description=rec.description,
            citation=rec.citation,
            file_pattern=rec.file_pattern,
            code_snippet=rec.code_snippet,
            embedding=rec.embedding,
            created_at=rec.created_at,
        )

    async def list_precedents(self, repo: str | None = None) -> list[Precedent]:
        """List precedents scoped to a repo."""
        stmt = select(RepoPrecedent, Repository.full_name).join(
            Repository, RepoPrecedent.repository_id == Repository.id
        )
        if repo:
            clean_repo = repo.lower().strip()
            stmt = stmt.where(
                (func.lower(Repository.full_name) == clean_repo)
                | (Repository.id == clean_repo)
            )
        stmt = stmt.order_by(RepoPrecedent.created_at.desc())

        res = await self.session.execute(stmt)
        rows = res.all()
        return [
            Precedent(
                id=rec.id,
                repo=full_name,
                title=rec.title,
                description=rec.description,
                citation=rec.citation,
                file_pattern=rec.file_pattern,
                code_snippet=rec.code_snippet,
                embedding=rec.embedding,
                created_at=rec.created_at,
            )
            for rec, full_name in rows
        ]

    async def delete_precedent(self, precedent_id: str) -> bool:
        """Delete a precedent by ID."""
        stmt = select(RepoPrecedent).where(RepoPrecedent.id == precedent_id)
        res = await self.session.execute(stmt)
        rec = res.scalar_one_or_none()
        if rec:
            await self.session.delete(rec)
            await self.session.flush()
            return True
        return False

    async def search_precedents(
        self,
        query: str,
        repo: str | None = None,
        file_path: str = "",
        min_score: float = 0.1,
        limit: int = 5,
    ) -> list[PrecedentSearchResult]:
        """Perform semantic vector similarity search via pgvector cosine distance."""
        query_emb = self.embedder.embed(query)

        # Dialect check
        bind = self.session.bind
        dialect_name = bind.dialect.name if bind else "postgresql"

        if dialect_name == "postgresql":
            # Native pgvector cosine_distance query
            distance_expr = RepoPrecedent.embedding.cosine_distance(query_emb).label("distance")
            stmt = (
                select(RepoPrecedent, Repository.full_name, distance_expr)
                .join(Repository, RepoPrecedent.repository_id == Repository.id)
                .where(RepoPrecedent.embedding.isnot(None))
            )
            if repo:
                clean_repo = repo.lower().strip()
                stmt = stmt.where(
                    (func.lower(Repository.full_name) == clean_repo)
                    | (Repository.id == clean_repo)
                )
            stmt = stmt.order_by(distance_expr).limit(limit * 2)

            res = await self.session.execute(stmt)
            rows = res.all()

            results: list[PrecedentSearchResult] = []
            for rec, full_name, dist in rows:
                if (
                    file_path
                    and rec.file_pattern
                    and rec.file_pattern != "*"
                    and not fnmatch.fnmatch(file_path, rec.file_pattern)
                ):
                    continue

                sim_score = max(0.0, 1.0 - float(dist))
                if sim_score >= min_score:
                    prec = Precedent(
                        id=rec.id,
                        repo=full_name,
                        title=rec.title,
                        description=rec.description,
                        citation=rec.citation,
                        file_pattern=rec.file_pattern,
                        code_snippet=rec.code_snippet,
                        embedding=rec.embedding,
                        created_at=rec.created_at,
                    )
                    results.append(PrecedentSearchResult(precedent=prec, score=sim_score))
                if len(results) >= limit:
                    break
            return results
        else:
            # Fallback for SQLite in-memory testing
            candidates = await self.list_precedents(repo=repo)
            results = []
            for prec in candidates:
                if (
                    file_path
                    and prec.file_pattern
                    and prec.file_pattern != "*"
                    and not fnmatch.fnmatch(file_path, prec.file_pattern)
                ):
                    continue
                cand_emb = prec.embedding or self.embedder.embed(
                    f"{prec.title} {prec.description} {prec.code_snippet or ''}"
                )
                sim = TextEmbedder.cosine_similarity(query_emb, cand_emb)
                if sim >= min_score:
                    results.append(PrecedentSearchResult(precedent=prec, score=sim))
            results.sort(key=lambda r: r.score, reverse=True)
            return results[:limit]

    async def find_duplicates(
        self, precedent: Precedent, threshold: float = 0.85
    ) -> list[Precedent]:
        """Find existing duplicates with cosine similarity >= threshold."""
        results = await self.search_precedents(
            query=f"{precedent.title} {precedent.description}",
            repo=precedent.repo,
            min_score=threshold,
            limit=5,
        )
        return [r.precedent for r in results if r.precedent.id != precedent.id]

    async def deduplicate_and_add(
        self, precedent: Precedent, repo_id: str | None = None, threshold: float = 0.85
    ) -> tuple[Precedent, bool]:
        """Add precedent, or merge citations into duplicate if found.

        Returns (precedent, is_new).
        """
        duplicates = await self.find_duplicates(precedent, threshold=threshold)
        if duplicates:
            existing = duplicates[0]
            if precedent.citation not in existing.citation:
                existing.citation = f"{existing.citation}, {precedent.citation}"
                await self.add_precedent(existing, repo_id=repo_id)
                logger.info(
                    "Merged pgvector precedent citation into %s: %s",
                    existing.id,
                    existing.citation,
                )
            return existing, False

        await self.add_precedent(precedent, repo_id=repo_id)
        return precedent, True
