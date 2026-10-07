"""Precedent mining and ingestion pipeline for repository memory."""

import inspect
import logging
import re
import uuid
from typing import Any

from review.memory.models import Precedent
from review.memory.store import MemoryStore

logger = logging.getLogger("review.memory.precedents")


class PrecedentMiner:
    """Extracts, formats, and ingests historical bug fixes and review precedents into memory."""

    def __init__(self, store: MemoryStore | Any) -> None:
        self.store = store

    def ingest_precedent(
        self,
        repo: str,
        title: str,
        description: str,
        citation: str,
        file_pattern: str = "*",
        code_snippet: str | None = None,
        category: str = "general",
        precedent_id: str | None = None,
    ) -> Precedent:
        """Create and store a repository precedent with deduplication."""
        pid = precedent_id or f"prec_{uuid.uuid4().hex[:12]}"
        precedent = Precedent(
            id=pid,
            repo=repo,
            title=title,
            description=description,
            citation=citation,
            file_pattern=file_pattern,
            code_snippet=code_snippet,
            category=category,
        )

        # Support sync or async store deduplicate_and_add
        if hasattr(self.store, "deduplicate_and_add"):
            res = self.store.deduplicate_and_add(precedent)
            if inspect.isawaitable(res):
                # If caller runs in async context, fallback to sync add_precedent
                self.store.add_precedent(precedent)
                return precedent
            saved_precedent, is_new = res
            if is_new:
                logger.info("Ingested new precedent '%s' [%s] for %s", title, citation, repo)
            else:
                logger.info("Merged precedent into existing '%s' for %s", saved_precedent.id, repo)
            return saved_precedent
        else:
            self.store.add_precedent(precedent)
            logger.info("Ingested precedent '%s' [%s] for %s", title, citation, repo)
            return precedent

    def ingest_from_commit(
        self,
        repo: str,
        commit_sha: str,
        commit_msg: str,
        diff_text: str,
    ) -> Precedent | None:
        """Analyze a git commit and ingest if it represents a bug fix, CVE fix, or reversion."""
        msg_lower = commit_msg.lower()

        # Identify fix / revert commits
        is_fix = any(
            k in msg_lower
            for k in ("fix", "cve", "bug", "patch", "revert", "vulnerability", "resolves", "closes")
        )
        if not is_fix:
            return None

        # Extract citation from commit message or SHA
        citation = f"commit {commit_sha[:7]}"
        pr_match = re.search(r"#(\d+)", commit_msg)
        cve_match = re.search(r"(cve-\d{4}-\d+)", commit_msg, re.IGNORECASE)
        category = "bug"
        if cve_match:
            citation = cve_match.group(1).upper()
            category = "security"
        elif pr_match:
            citation = f"PR #{pr_match.group(1)}"

        # Determine target file pattern from diff
        changed_files: set[str] = set()
        for raw in diff_text.splitlines():
            if raw.startswith("diff --git "):
                parts = raw.split(" b/")
                if len(parts) > 1:
                    changed_files.add(parts[-1].strip())
            elif raw.startswith("+++ b/"):
                target = raw[6:].strip()
                if target and target != "/dev/null":
                    changed_files.add(target)
        file_pattern = changed_files.pop() if len(changed_files) == 1 else "*"

        # Use first line of commit message as title
        first_line = commit_msg.strip().splitlines()[0] if commit_msg.strip() else "Bug Fix"
        title = first_line[:120]

        # Extract minimal snippet from diff if available
        snippet = diff_text[:500] if diff_text else None

        precedent = self.ingest_precedent(
            repo=repo,
            title=title,
            description=commit_msg.strip(),
            citation=citation,
            file_pattern=file_pattern,
            code_snippet=snippet,
            category=category,
            precedent_id=f"prec_{commit_sha[:10]}",
        )
        return precedent

    def ingest_from_review_comment(
        self,
        repo: str,
        comment_body: str,
        file_path: str,
        pull_number: int | None = None,
        author: str | None = None,
    ) -> Precedent | None:
        """Extract team convention or recurring review guideline from comment."""
        body_lower = comment_body.lower()
        convention_triggers = (
            "convention:",
            "guideline:",
            "team standard:",
            "per policy:",
            "rule:",
            "always remember:",
            "we prefer",
            "required pattern:",
        )
        matched_trigger = next((t for t in convention_triggers if t in body_lower), None)
        if not matched_trigger:
            return None

        citation = f"PR #{pull_number}" if pull_number else "Review Discussion"
        if author:
            citation += f" ({author})"

        title_line = comment_body.strip().splitlines()[0][:120]
        title = f"Team Convention: {title_line}"

        return self.ingest_precedent(
            repo=repo,
            title=title,
            description=comment_body.strip(),
            citation=citation,
            file_pattern=file_path or "*",
            category="convention",
        )

    def bulk_ingest_szz_cases(self, eval_cases: list[dict[str, Any]]) -> int:
        """Ingest labeled historical SZZ cases into memory."""
        count = 0
        for case in eval_cases:
            if case.get("is_clean"):
                continue  # Clean PRs are not bug precedents
            repo = case.get("repo", "")
            title = case.get("title", "")
            diff = case.get("diff", "")
            cid = case.get("id", "")
            citation = case.get("commit_sha") or cid

            if repo and title:
                self.ingest_precedent(
                    repo=repo,
                    title=f"Bug Pattern: {title}",
                    description=f"Historical bug in {repo}: {title}.",
                    citation=f"SZZ {citation}",
                    file_pattern="*",
                    code_snippet=diff[:400] if diff else None,
                    category="bug",
                    precedent_id=f"szz_{cid}",
                )
                count += 1
        return count
