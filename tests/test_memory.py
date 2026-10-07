"""Unit tests for repository memory, precedent mining, and tenant-scoped retrieval."""

from review.agent.state import AgentState
from review.agent.tools.findings_tools import add_finding, search_precedents
from review.findings import Severity
from review.memory.miner import PrecedentMiner
from review.memory.models import Precedent
from review.memory.store import MemoryStore

SAMPLE_DIFF = """\
diff --git a/flask/wrappers.py b/flask/wrappers.py
--- a/flask/wrappers.py
+++ b/flask/wrappers.py
@@ -10,3 +10,4 @@
 def get_json(self):
+    return json.loads(self.data)
"""


def test_memory_store_add_and_get() -> None:
    store = MemoryStore(db_path=":memory:")
    prec = Precedent(
        id="prec-1",
        repo="pallets/flask",
        title="Unhandled JSON decode exception",
        description="Always catch ValueError when decoding user JSON input.",
        citation="PR #2758",
        file_pattern="flask/*.py",
        code_snippet="json.loads(data)",
    )
    store.add_precedent(prec)

    retrieved = store.get_precedent("prec-1")
    assert retrieved is not None
    assert retrieved.id == "prec-1"
    assert retrieved.repo == "pallets/flask"
    assert retrieved.title == "Unhandled JSON decode exception"
    assert retrieved.citation == "PR #2758"
    assert retrieved.file_pattern == "flask/*.py"


def test_memory_store_tenant_isolation() -> None:
    store = MemoryStore(db_path=":memory:")
    prec_flask = Precedent(
        id="flask-1",
        repo="pallets/flask",
        title="Flask JSON decoding crash",
        description="Catch ValueError in request wrapper.",
        citation="PR #2758",
    )
    prec_requests = Precedent(
        id="req-1",
        repo="psf/requests",
        title="Redirect auth header stripping",
        description="Strip authorization headers on cross-host redirects.",
        citation="CVE-2018-18074",
    )
    store.add_precedent(prec_flask)
    store.add_precedent(prec_requests)

    # Scoped search for pallets/flask MUST NOT return psf/requests precedents
    results_flask = store.search_precedents(
        query="redirect authorization header",
        repo="pallets/flask",
    )
    assert len(results_flask) == 0

    # Scoped search for psf/requests returns its own precedent
    results_requests = store.search_precedents(
        query="redirect authorization header",
        repo="psf/requests",
    )
    assert len(results_requests) == 1
    assert results_requests[0].precedent.id == "req-1"


def test_memory_store_file_pattern_matching() -> None:
    store = MemoryStore(db_path=":memory:")
    prec = Precedent(
        id="auth-rule",
        repo="my-org/web",
        title="Authentication cookie validation",
        description="Must validate session cookie signature.",
        citation="PR #50",
        file_pattern="src/auth/*.py",
    )
    store.add_precedent(prec)

    # Matching path
    res_match = store.search_precedents(
        query="cookie validation",
        repo="my-org/web",
        file_path="src/auth/session.py",
    )
    assert len(res_match) == 1

    # Non-matching path
    res_mismatch = store.search_precedents(
        query="cookie validation",
        repo="my-org/web",
        file_path="src/billing/invoice.py",
    )
    assert len(res_mismatch) == 0


def test_memory_store_delete() -> None:
    store = MemoryStore(db_path=":memory:")
    prec = Precedent(
        id="del-test",
        repo="org/repo",
        title="Temp rule",
        description="Description",
        citation="PR #1",
    )
    store.add_precedent(prec)
    assert store.get_precedent("del-test") is not None

    deleted = store.delete_precedent("del-test")
    assert deleted is True
    assert store.get_precedent("del-test") is None


def test_precedent_miner_ingest_from_commit() -> None:
    store = MemoryStore(db_path=":memory:")
    miner = PrecedentMiner(store=store)

    commit_msg = (
        "Fixes #412: Catch JSONDecodeError in request wrapper\n\n"
        "Resolves unhandled crash on malformed payloads."
    )
    diff_text = "diff --git a/flask/wrappers.py b/flask/wrappers.py\n+except ValueError:"

    prec = miner.ingest_from_commit(
        repo="pallets/flask",
        commit_sha="a1b2c3d4e5f6",
        commit_msg=commit_msg,
        diff_text=diff_text,
    )

    assert prec is not None
    assert prec.repo == "pallets/flask"
    assert prec.citation == "PR #412"
    assert "wrappers.py" in prec.file_pattern
    assert store.get_precedent(prec.id) is not None


def test_precedent_miner_ingest_cve_commit() -> None:
    store = MemoryStore(db_path=":memory:")
    miner = PrecedentMiner(store=store)

    commit_msg = "Fix CVE-2018-18074: Disallow authorization header leak on cross-host redirects"
    prec = miner.ingest_from_commit(
        repo="psf/requests",
        commit_sha="9876543210ab",
        commit_msg=commit_msg,
        diff_text="diff --git a/requests/sessions.py b/requests/sessions.py",
    )

    assert prec is not None
    assert prec.citation == "CVE-2018-18074"
    assert prec.repo == "psf/requests"


def test_precedent_miner_non_bug_commit_ignored() -> None:
    store = MemoryStore(db_path=":memory:")
    miner = PrecedentMiner(store=store)

    commit_msg = "docs: update contributor guidelines and README styling"
    prec = miner.ingest_from_commit(
        repo="my-org/my-repo",
        commit_sha="112233445566",
        commit_msg=commit_msg,
        diff_text="diff --git a/README.md b/README.md",
    )
    assert prec is None


def test_search_precedents_tool_integration() -> None:
    store = MemoryStore(db_path=":memory:")
    prec = Precedent(
        id="flask-json-1",
        repo="pallets/flask",
        title="Catch ValueError on JSON decode",
        description="Request.get_json raises ValueError on invalid payload. Always catch it.",
        citation="PR #2758",
        file_pattern="flask/wrappers.py",
        code_snippet="except (ValueError, TypeError): return None",
    )
    store.add_precedent(prec)

    state = AgentState(
        diff=SAMPLE_DIFF,
        repo="pallets/flask",
    )

    # Search with relevant query
    result_text = search_precedents(
        state=state,
        query="json decode error handling",
        file_path="flask/wrappers.py",
        memory_store=store,
    )

    assert "Found 1 relevant precedent(s)" in result_text
    assert "[PR #2758]" in result_text
    assert "Catch ValueError on JSON decode" in result_text


def test_search_precedents_tool_no_matches() -> None:
    store = MemoryStore(db_path=":memory:")
    state = AgentState(
        diff=SAMPLE_DIFF,
        repo="pallets/flask",
    )

    result_text = search_precedents(
        state=state,
        query="completely unrelated crypto hash function",
        file_path="flask/wrappers.py",
        memory_store=store,
    )

    assert "No relevant historical precedents found" in result_text


def test_add_finding_with_citation() -> None:
    state = AgentState(
        diff=SAMPLE_DIFF,
        repo="pallets/flask",
    )

    res = add_finding(
        state=state,
        file="flask/wrappers.py",
        line=11,
        title="Uncaught JSON decode exception",
        body="json.loads raises ValueError on malformed JSON.",
        severity=Severity.HIGH,
        citation="PR #2758",
    )

    assert "ACCEPTED" in res
    assert "Cited: PR #2758" in res
    assert len(state.findings) == 1
    assert state.findings[0].citation == "PR #2758"
