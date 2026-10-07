# ADR 005: GitHub App Webhook Architecture and Security Hardening

**Date:** 2026-10-07  
**Status:** Accepted  

## Context
Transitioning the autonomous review agent from a CLI utility into an automated GitHub App service introduces critical operational and security requirements:
1. **Webhook Timeout Constraints:** GitHub drops webhook connections if the receiver does not return an HTTP response within 10 seconds. Multi-step LLM reviews take between 15 and 90 seconds.
2. **Untrusted Payload Verification:** Webhooks originate from public internet networks and must be cryptographically authenticated to prevent spoofing.
3. **Replay Attack Defense:** Attackers or duplicate network retries could resend old webhook events, causing redundant reviews and API credit exhaustion.
4. **Merge Governance Boundary:** The agent must never auto-approve or block merges; human engineers retain absolute merge authority.

## Decision
We implement a decoupled, hardened service layer (`src/review/service` and `src/review/github`):

1. **Immediate 202 Accepted Response:** The FastAPI webhook endpoint validates the request and enqueues the review task to an asynchronous background worker before returning HTTP 202 in under 50 milliseconds.
2. **Constant-Time HMAC-SHA256 Verification:** Inbound requests validate the `X-Hub-Signature-256` header against `GITHUB_WEBHOOK_SECRET` using `hmac.compare_digest` to prevent timing attacks. Requests failing verification return HTTP 401 immediately.
3. **Delivery ID Deduplication (`DeliveryDeduplicator`):** Every webhook delivery is tracked by its `X-GitHub-Delivery` UUID with an automatic 1-hour TTL. Duplicate deliveries are dropped with HTTP 200 `status: ignored`.
4. **GitHub App RS256 Authentication (`GitHubAppAuth`):** Generates ephemeral, short-lived RS256 JWTs using the GitHub App's private key to exchange for installation access tokens, cached with expiration tracking.
5. **Enforced Non-Governance (`COMMENT` Reviews Only):** `GitHubClient.post_review` programmatically overrides any review event to `"COMMENT"`. Approval and merge-blocking actions are rejected by code guardrails.
6. **Execution Proof Formatting (`ReviewPublisher`):** Inline findings and top-level summary tables embed reproduction traces and proof-by-execution badges when defects are confirmed in the execution sandbox.

## Consequences
- Protects the service from unauthorized webhooks and replay attacks.
- Prevents webhook timeouts while executing thorough multi-turn agent investigations.
- Ensures human engineers remain in total control of PR merge workflows.
