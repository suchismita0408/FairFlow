# FairFlow: roadmap to a real platform

> Current authenticated-platform status and remaining work are in [PLATFORM_STATUS.md](PLATFORM_STATUS.md). The older architecture and issue list below are retained as historical context.

> Updated 2026-11-02: Agile v2 now implements milestone planning, Vault adapters, five-day reviews, pro-rata settlement, void/resume and funding-failure states. See [AGILE_ENGINE.md](AGILE_ENGINE.md) for current architecture. The original 72-hour flow described below remains only for legacy projects. The production, data, evaluation and launch requirements below still apply.


## Product analysis

The useful product is a shared, explicit agreement with a neutral change-order workflow. AI proposes structured terms and decisions; humans retain contractual and dispute authority. Deterministic code owns money, counters, deadlines and permissions. Neither Pydantic validation nor an AI verdict creates legal enforceability or guarantees collection.

The original prototype had an invalid top-level await/return block, duplicated models, no stored contract, no ownership checks, no authorization/capture lifecycle, no delivery clock and no frontend. This implementation supplies a runnable local demonstration plus Sandbox adapters. The previous stateless endpoints are replaced with project-scoped APIs so clients cannot supply a different baseline to bypass the firewall.

> Historical roadmap: use [HACKATHON_REVIEW.md](HACKATHON_REVIEW.md) for the current PostgreSQL architecture, implemented fixed-role features and remaining work.

## Architecture

Browser -> FastAPI -> Pydantic schemas -> Gemini or offline rules.
FastAPI -> SQLite project records + audit events + verified webhook inbox.
FastAPI -> PayPal Sandbox Orders -> buyer approval -> authorization -> capture.
Single-process scheduler -> persisted due dates -> guarded capture service.

Production: authenticated client/freelancer portals -> FastAPI -> PostgreSQL relational ledger + transactional outbox -> durable queue workers -> payment provider. Object storage holds versioned deliverables. An independent reconciler compares provider settlements, refunds, reversals and dispute events against the ledger.

### State transitions

Draft -> both parties accept -> checkout -> authorized -> active.
Active -> deliver -> in_review -> client approval OR deadline -> captured/completed.
In_review -> included revision -> revision_requested -> resubmit -> new review window.
Active/in_review -> paid addition proposed -> buyer approves authorization -> active with funded addition.
Ambiguous request or dispute -> disputed -> recorded mutual resolution -> active -> new delivery.
Capture failure -> payment_attention -> operator reconciliation -> idempotent retry.

Outstanding unaccepted quotes do not block payment on the baseline. Funded additions extend the scope, require delivery, and are captured alongside the baseline. A dispute blocks all release. The example implementation does not resolve partial-payment refunds or changed terms after funding; these require a dedicated cancellation/amendment workflow.

## Phase 1: hackathon (implemented)

A complete local vertical slice, editable extracted contracts, demo and Gemini modes, Sandbox adapters, quote generation, countdown and simulated time travel, audit trail, persistent database, API docs, tests and a responsive dashboard. Demo mode lets judges see every core concept without external account access.

Acceptance: replay the demo in README; inspect persisted activity; verify a dispute prevents auto-release; show a failed provider call does not report successful payment.

## Phase 2: authenticated private beta

1. Add OIDC identity, organization membership, client invitations and project-level RBAC. Distinct authenticated users must accept. Enforce freelancer-only delivery and client-only approval/request/dispute. Replace the shared operator token. Record immutable agreement versions, hashes, acceptance timestamps and identity evidence.
2. Migrate JSON documents to PostgreSQL tables: users, memberships, projects, contract_versions, consents, changes, deliveries, payment_orders, authorizations, captures, refunds, disputes, ledger_entries, outbox_jobs, webhook_events and audit_events. Use foreign keys, unique external IDs, monetary checks and transaction boundaries.
3. Replace process-local locking with row locks and version checks. Schedule jobs using a transactional outbox and durable queue. Workers claim jobs with leases, retry with bounded backoff, and use a dead-letter queue. Confirm idempotency across crashes, network timeouts and provider retention windows.
4. Add per-endpoint rate limits, request size limits, AI spend caps, CSRF protections appropriate to the selected session design, secret management, security headers, dependency scanning and an independent access-control review.
5. Store deliverables in signed object-storage uploads with checksums, immutable versions, malware scanning, retention and access policies. Current URLs are stored only; the server never fetches them.
6. Send delivery notices and deadline reminders through a transactional notification outbox; record evidence of notification. Define time zones, holidays, inaccessible deliverables and service-outage extensions in the terms.
7. Add genuinely bilateral dispute resolution, evidence uploads, impartial support review, human overrides with reasons, appeal, cancellation, refunds and partial settlement. Do not let a model decide irreversible financial disputes.

## Phase 3: payments suitable for a marketplace

- Confirm the business model with PayPal's platforms team: merchant of record versus marketplace, seller countries, supported currencies and integration eligibility. Implement seller onboarding and required identity checks. Never represent money in your merchant account as seller escrow without an approved legal/product structure.
- Choose approved delayed disbursement or a licensed escrow provider if actual escrow is required. Authorization/capture alone is inadequate for long projects; plan milestone funding, expiry alerts and reauthorization/new buyer approval where allowed.
- Implement provider-verified webhook reconciliation for pending, completed, denied, refunded, reversed and disputed payments, handling out-of-order and duplicate deliveries. Current webhook inbox only stores verified evidence.
- Build a double-entry ledger and scheduled settlement reconciliation. Separate authorized, captured, available, payable and paid-out balances. Record fees, tax, FX, reserves and chargebacks. A successful capture is not a seller payout.
- Agree authorization expiry and retry policies with the provider. If a capture response is lost, query provider state and reconcile before retrying; idempotency keys alone are insufficient indefinitely.
- Contract qualified counsel for payment-service/escrow obligations, electronic signatures, consumer protections, tax and privacy obligations in the actual launch jurisdictions. Decide who absorbs losses and supports disputes. “Guaranteed payment” would require an explicit funded guarantee product and appropriate terms.

## Data needed

| Data | Purpose | Collection policy |
|---|---|---|
| Consented negotiation transcripts | Extract scope, price and revisions | Redact unnecessary personal data; retain source provenance and access controls |
| Explicit deliverables, exclusions and acceptance criteria | Establish objective scope baseline | Version and sign before funding |
| Labeled change requests with explanations | Evaluate scope decisions | Two independent reviewers plus adjudication for disagreements |
| Revision counters, delivery evidence and events | Determine eligibility and review time | Append-only audit history; define retention |
| Freelancer rates, estimated hours, actual hours | Calibrate pricing | Get consent; keep rates project-specific; do not label AI guesses as market rates |
| Provider IDs, statuses, fees and settlement reports | Financial reconciliation | Encrypt/access-control; avoid storing card or bank credentials |
| Disputes, appeals and resolution outcomes | Detect systematic errors | Restrict access, limit retention, analyze for category/language bias |

## AI evaluation and training

No model training is needed to launch the hackathon: use a pretrained Gemini model, structured output and deterministic validation. Start with a consented, de-identified evaluation set; synthetic examples supplement it but do not establish real-world reliability.

Suggested initial evaluation set: 300-500 contract extractions and 500-1,000 change requests across design, frontend, backend, writing and video. These are planning targets, not data already collected. Include multilingual messages, contradictory prices, ambiguous acceptance, excluded features, negation, multiple deliverables, exhausted revisions and prompt injection. Split by project/client, not individual message, to reduce leakage.

Label extraction fields with exact source spans; label scope as in-scope, out-of-scope or needs-human-review, with a clause reference and rationale. Measure field accuracy, money/currency exactness, classification precision/recall, false billable decisions, disagreement rate, abstention quality, latency and cost. Model self-reported confidence is not calibrated probability; derive thresholds using held-out data.

Build an adversarial suite: “ignore the contract”, hidden instructions inside chat, invented prices, paid additions misclassified as new work, legitimate defect fixes mislabeled revisions, and maliciously repeated requests. Route uncertain cases to humans. Keep every AI decision linked to model version, prompt version, input digest, output, human decision and charge outcome.

Improve prompts and retrieval over accepted contract clauses before considering tuning. Fine-tune only if the chosen model supports it and a measured recurring failure justifies it, using consented data and a held-out set. Retrain/evaluate by category and language; do not automatically learn from unreviewed client complaints. Pricing should use an agreed rate card and validated hour estimates, with visible negotiation and explicit approval.

## Deployment plan

Hackathon: one Python process on localhost, SQLite on persistent disk. A private VM/container can host the same app for a controlled sandbox demonstration, with HTTPS and the operator token; do not horizontally scale it. A Dockerfile is supplied for that controlled scenario, defaulting to demo.

Production: containerized FastAPI API instances, managed PostgreSQL, Redis or a managed durable queue, independent workers, object storage, OIDC, secret manager, HTTPS ingress, telemetry and backups. Select regions and subprocessors after defining privacy requirements. Separate development/sandbox/live accounts and secrets. Live mode is deliberately absent in this code.

CI gates: unit and state-machine tests; mocked provider contracts; Sandbox end-to-end tests; migration checks; browser accessibility and mobile flows; concurrency and crash-recovery tests; replayed/out-of-order webhooks; security scanning. Test backup restoration, worker outages, timezone boundaries, long authorizations and network loss during capture.

Observability: AI/provider latency and error rates, pending authorization age, capture discrepancies, overdue jobs, repeated webhook failures, disputes and unexpected ledger imbalance. Send operators alerts. Do not log raw credentials or unrestricted chat bodies. Establish runbooks, support escalation, SLOs, incident response and restore drills.

## Launch gates still open

- Real authenticated participants and enforceable consent flow.
- Provider approval and verified seller onboarding/disbursement.
- Automated financial reconciliation, refunds and reversals.
- Distributed transaction/worker safety and recovery drills.
- Calibrated AI evaluation and human appeal process.
- Security/privacy review and jurisdiction-specific legal review.
- Real Sandbox end-to-end approval/capture verification with buyer interaction.

Until these are closed, call FairFlow a functional prototype, not production escrow or a guaranteed-payment service.
