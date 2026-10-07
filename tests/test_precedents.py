"""Tests for PrecedentMiner mining git history, CVEs, review comments, and SZZ benchmarks."""

from review.memory.miner import PrecedentMiner as CompatMiner
from review.memory.precedents import PrecedentMiner
from review.memory.store import MemoryStore


def test_precedent_miner_import_compatibility() -> None:
    """Verify miner.py re-export preserves backward compatibility."""
    assert PrecedentMiner is CompatMiner


def test_precedent_miner_commit_and_cve_mining() -> None:
    """Verify mining bug fix and CVE details from git commit metadata."""
    store = MemoryStore(db_path=":memory:")
    miner = PrecedentMiner(store=store)

    diff_text = """diff --git a/src/auth/tokens.py b/src/auth/tokens.py
index abc..def 100644
--- a/src/auth/tokens.py
+++ b/src/auth/tokens.py
@@ -10,3 +10,3 @@
-    return jwt.decode(token, verify=False)
+    return jwt.decode(token, key=SECRET, algorithms=['HS256'])
"""
    commit_msg = "Fix CVE-2024-9988: enforce cryptographic JWT signature validation (#412)"

    precedent = miner.ingest_from_commit(
        repo="myorg/auth-server",
        commit_sha="a1b2c3d4e5f6",
        commit_msg=commit_msg,
        diff_text=diff_text,
    )

    assert precedent is not None
    assert precedent.citation == "CVE-2024-9988"
    assert precedent.category == "security"
    assert precedent.file_pattern == "src/auth/tokens.py"
    assert "jwt.decode" in (precedent.code_snippet or "")

    # Retrieve from memory
    stored = store.get_precedent(precedent.id)
    assert stored is not None
    assert stored.title.startswith("Fix CVE-2024-9988")


def test_precedent_miner_review_comment_convention() -> None:
    """Verify extracting team conventions and guidelines from reviewer feedback."""
    store = MemoryStore(db_path=":memory:")
    miner = PrecedentMiner(store=store)

    comment = (
        "Team standard: Always wrap external database network calls in asyncio.timeout(5.0)\n"
        "to prevent thread pool starvation during network partitions."
    )

    p = miner.ingest_from_review_comment(
        repo="myorg/orders-api",
        comment_body=comment,
        file_path="src/db/client.py",
        pull_number=88,
        author="lead-dev",
    )

    assert p is not None
    assert p.category == "convention"
    assert "PR #88" in p.citation
    assert "lead-dev" in p.citation
    assert p.file_pattern == "src/db/client.py"
    assert "asyncio.timeout" in p.description


def test_precedent_miner_deduplication() -> None:
    """Verify that similar precedents merge citations rather than creating duplicates."""
    store = MemoryStore(db_path=":memory:")
    miner = PrecedentMiner(store=store)

    p1 = miner.ingest_precedent(
        repo="org/repo",
        title="SQL injection vulnerability in user login",
        description="Raw string concatenation in query parameter",
        citation="PR #10",
        file_pattern="login.py",
    )

    p2 = miner.ingest_precedent(
        repo="org/repo",
        title="SQL injection vulnerability in user login",
        description="Raw string concatenation in query parameter",
        citation="PR #20",
        file_pattern="login.py",
    )

    assert p1.id == p2.id
    assert "PR #10" in p2.citation
    assert "PR #20" in p2.citation


def test_bulk_ingest_szz_cases() -> None:
    """Verify bulk ingestion of SZZ benchmark cases into repository memory."""
    store = MemoryStore(db_path=":memory:")
    miner = PrecedentMiner(store=store)

    cases = [
        {
            "id": "szz-1",
            "repo": "org/repo",
            "title": "Buffer overflow in packet parser",
            "diff": "diff --git a/net.c b/net.c\n...",
            "commit_sha": "c111111",
            "is_clean": False,
        },
        {
            "id": "szz-2",
            "repo": "org/repo",
            "title": "Refactor docs",
            "diff": "",
            "commit_sha": "c222222",
            "is_clean": True,  # Should be skipped
        },
    ]

    count = miner.bulk_ingest_szz_cases(cases)
    assert count == 1

    precedents = store.list_precedents(repo="org/repo")
    assert len(precedents) == 1
    assert precedents[0].id == "szz_szz-1"
