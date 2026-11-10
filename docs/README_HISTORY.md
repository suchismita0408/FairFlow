# Historical README

Archived on 10 November 2026. Payment and feature descriptions below include superseded implementations. Use the root README and current feature documents for present behavior.

# FairFlow

Delivery deadlines, cancellation/partial settlement, paid change requests, evidence, funding recovery and the human support queue: [RESOLUTION_WORKFLOWS.md](docs/RESOLUTION_WORKFLOWS.md). Open the **Resolution** tab in a signed project. Configure reviewer accounts and SMTP separately; existing agreements need explicit consent for the new delivery-protection policy.

Current payment-aware amendments and lifecycle edge-case support: [SCENARIO_SUPPORT_AUDIT.md](docs/SCENARIO_SUPPORT_AUDIT.md). Remaining unfunded work can be amended in the same project after verified payout; paid terms and payment records retain their original signed versions.

Current automatic-release, withdrawal, deletion and chat-notification rules: [WORKFLOW_UPDATE.md](docs/WORKFLOW_UPDATE.md). New signatures and funding require automatic-release terms; historical signed versions are preserved.

Current feature-by-feature code audit, completed fixes and unfinished work: [FEATURE_AUDIT.md](docs/FEATURE_AUDIT.md).

Payment receipt and 29-day signing priority: [PAYMENT_SETUP_STATUS.md](docs/PAYMENT_SETUP_STATUS.md). The USD 10 freelancer Sandbox payout is verified SUCCESS using a separate US sender account, while the original capture account remains preserved. Real email configuration is reserved for a future sender/provider.

Current project explanation, feature audit, limitations and demo runbook: [HACKATHON_REVIEW.md](docs/HACKATHON_REVIEW.md). Real verification-email setup: [EMAIL_SETUP.md](docs/EMAIL_SETUP.md). These supersede historical feature descriptions below where noted.

FairFlow provides separate client/freelancer portals, a compact four-step agreement builder, AI review of terms and conversation, deterministic validation, turn-based negotiation and exact-hash signatures. Signed agreements open a shared workspace with PayPal Sandbox milestone authorization, delivery and automatic capture after the signed review period without an objection. Capture goes to the configured merchant; approval automatically requests a separate Sandbox payout to send the full milestone amount to the freelancer's chosen test account.

The existing FastAPI/PostgreSQL/vanilla JavaScript stack is preserved. Specification comparison and implementation decisions: [STEP1_IMPLEMENTATION.md](docs/STEP1_IMPLEMENTATION.md). Current verification and remaining work: [PLATFORM_STATUS.md](docs/PLATFORM_STATUS.md).

## Step 1 quick start

```powershell
python -m pip install -r requirements-dev.txt
python -m scripts.setup_local
docker compose up -d --wait db
python -m alembic upgrade head
python -m scripts.seed_step1  # optional local demo accounts and three sample projects
./run.ps1
```

Open [Client portal](http://127.0.0.1:8000/client/login) or [Freelancer portal](http://127.0.0.1:8000/freelancer/login). New accounts choose their fixed role at signup; use **Local email preview** to verify email in local development. Invite a known freelancer; discovery is outside this step.

Seeded demo emails: `step1-client@example.com` and `step1-freelancer@example.com`. Local development password: `Demo-terms-only-123!` unless `FAIRFLOW_DEMO_PASSWORD` was set when seeding. The client has a deliberately contradictory draft, an invitation, a signed agreement and a separate signed USD **Sandbox payment demo** project. The payment demo is not funded until its client starts checkout and a Sandbox buyer approves it.

```powershell
python -m playwright install chromium
python -m pytest -q
python -m scripts.verify_step1_postgres
```

Run `python -m pytest -q` for the current test result; the isolated PostgreSQL script also covers the milestone payment and Sandbox payout APIs. SMTP transport is implemented but needs a configured sender. Marketplace seller onboarding, live payout approval, production hosting and independent security/accessibility audits remain open.

## PayPal Sandbox milestone demo

Keep the Sandbox client ID and secret in `.env` on the server. Set `PAYPAL_ENABLED_CURRENCIES=USD` (or another currency confirmed for your Sandbox merchant) and `APP_BASE_URL` to the URL you actually use. Run `python -m alembic upgrade head` before starting the app. Never put the client secret in browser code or a public repository.

Sign a project in an enabled currency with its first milestone due within PayPal's authorization window. In the signed workspace, open **Payments**. The client starts checkout, approves it in a personal PayPal Sandbox account, returns to FairFlow and selects **Verify buyer approval**. The freelancer then submits a delivery link. The client can request an included revision or raise a dispute during the signed review period. Without an objection, the running worker automatically requests capture and payout when that period ends; there is no second client payment-approval step. Each provider step has a separate recorded status and audit event. A return from PayPal alone does not mark funds authorized.

Before checkout, the freelancer saves a **PayPal Sandbox recipient email** in Profile or Payments and confirms it is their receiving test account. The client reviews and confirms the recipient at checkout. Successful capture automatically requests the payout, with no second payment click. The **Send freelancer Sandbox payout** action remains for historical captured payments or recovery before any payout intent exists. This is a separate transfer from the merchant's Sandbox balance. The full milestone amount goes to the recipient; the merchant covers payout fees. Click **Check payout status** in either portal to fetch the individual payout item's latest state. Only PayPal item `SUCCESS` is shown as received; a submitted or successful batch alone does not prove receipt. Existing payout amounts and recipients are frozen, and one payout intent per captured milestone prevents duplicate release. The configured app must have PayPal Payouts access and sufficient Sandbox merchant balance. A Payouts OAuth scope alone does not prove a transfer will succeed.

Each milestone must fit delivery, 3-5 calendar review days, revision turnaround and a one-day capture buffer within its planned 29-day authorization period. The actual expiry is checked again at authorization and delivery. PayPal recommends capture within the first three-day honor period; authorizations can expire, and captures or payouts can fail. An uncertain provider response pauses the operation for reconciliation; failed/uncertain payout intents cannot be sent again from the UI. This demo does not provide licensed escrow or guarantee collection. The seeded INR agreement may not be fundable if this merchant only enables USD; agree and sign a USD amendment or create a new USD project for the Sandbox demo. Local email preview remains the notification channel. Real marketplace seller onboarding and live payout approval remain separate launch requirements.

The previous implementation below is retained as historical/compatibility documentation. Its payment and AI decision actions are blocked for new Step 1 accounts. Historical accounts retain their original data and cannot enter fixed-role portals until an explicit migration policy is implemented. Legacy account creation and password-free demo login are disabled by default.

## Run locally

```powershell
python -m pip install -r requirements.txt
python -m scripts.setup_local
docker compose up -d db
python -m alembic upgrade head
# Only when importing existing SQLite demo projects:
python -m scripts.migrate_legacy
python -m uvicorn main:app --host 127.0.0.1 --port 8000
# In a second terminal:
python -m app.worker
```

Open http://127.0.0.1:8000. Register your own account and open **Local email preview** to verify it. Use a separate browser profile or private window for the other participant. No external emails are sent. Keep the server bound to localhost: the local email preview is a development convenience, not proof of email ownership suitable for production.

Setup preserves existing configuration. Keep `.env`, `.env.database`, and the encryption key private and backed up. Losing the encryption key makes saved payment tokens and email previews unreadable. Docker stores PostgreSQL data in a named volume; do not delete that volume to restart the app.

## What is implemented

| Area | Current behavior |
|---|---|
| Accounts | Argon2id passwords, server-side sessions, HttpOnly cookies, CSRF checks, verification, password reset and login throttling |
| Private projects | Explicit client/freelancer roles, email invitations and membership checks |
| Agreements | Immutable versions, clause references and independent acceptance of the exact version by both accounts |
| AI | Chat extraction, configurable Gemini fallback chain, clause retrieval, evidence references, citation validation and advisory classifications |
| Decisions | Defects are separate from presentation revisions. Both participants approve the final classification and any added price; AI never charges money |
| Milestones | 14-21 days, exact cent totals, priced work items, saved-method consent, five-day reviews and authorization-expiry checks |
| Currencies | USD default, 25 PayPal catalog currencies, whole-unit validation where required, Sandbox merchant gating and no FairFlow FX markup |
| Partial work | Both participants accept specific priced items and evidence; capture their value and carry forward unpaid work |
| Disputes | Joint resolution only. No support or administrator override. Unresolved disputes stop automatic settlement |
| Payments | Demo simulation, existing PayPal Sandbox adapters, operation journal, reconciliation reads, joint retry proposals and joint refunds |
| Persistence | PostgreSQL, Alembic, optimistic versions, per-project advisory locks, encrypted vaulted tokens and balanced capture/refund ledger entries |
| Worker | Separate durable job queue, leases with heartbeat, bounded retries, review notifications and uncertain-action review states |
| Interface | Shared project room with Agreement, Milestones, AI review, Messages, Payments and Activity tabs |

Older SQLite projects are backed up and imported as unassigned archives. They are not assigned to invented users or treated as newly accepted agreements. The original database remains intact. Historical operator-console instructions are in [LEGACY_DEMO.md](docs/LEGACY_DEMO.md); they do not describe the authenticated interface.

## Try the workflow

1. Register and verify two separate accounts using local email previews.
2. Create an agreement, select your role, enter the other participant's email, and review the extracted terms, acceptance criteria and priced milestones.
3. Accept the invitation in the other account. Both accounts accept the agreement independently.
4. The client consents to the saved payment method and authorizes a milestone. Demo mode uses simulated money.
5. The freelancer submits work or evidence. The client can approve delivery or request an evidence-based AI review.
6. For a defect, addition, partial settlement, refund or administrative retry, review the explicit proposal in both accounts. The worker executes eligible jointly approved actions.
7. Refresh the room to see asynchronous job results. Unchallenged delivered work becomes capture-eligible after five days; disputes block capture.

## Verification

```powershell
python -m pytest -q
python tests/platform_browser_smoke.py
python -m scripts.evaluate_decisions
python -m scripts.backup_database
python -m scripts.verify_backup_restore
```

Current verification: **162 tests passed**. Browser smoke passed in USD and JPY with two separate accounts against local PostgreSQL, including revised priced items, invitation, acceptance, simulated funding, delivery, capture, ledger currency, all tabs and mobile width. Browser smoke creates clearly labelled QA records. PostgreSQL backup restoration passed in an isolated QA database with balanced ledgers; the application database was preserved.

The synthetic evaluation has 12 cases; offline demo rules match 8 labels and abstain on cases they cannot establish. This is not a real-model accuracy score. `python -m scripts.evaluate_decisions --gemini` explicitly opts into provider calls. The report is written to `reports/decision-evaluation.json`.

## Stack

- FastAPI, Pydantic and HTTPX for API validation, workflow and integrations.
- PostgreSQL 17 in Docker, SQLAlchemy and Alembic for persistence and schema management.
- A separate Python durable worker using PostgreSQL jobs and advisory locks.
- Argon2id for passwords and Fernet for vaulted-token/local-message encryption.
- HTML, CSS and vanilla JavaScript served by FastAPI. The existing frontend was extended instead of introducing a second Next.js server.
- Gemini structured output with configurable fallbacks and bounded timeouts. The configured primary model is not assumed to be available; provider errors try the configured alternatives and never silently switch a live request to demo rules.
- PayPal Orders, Payments, Payment Method Tokens and Sandbox Payouts adapters. Live seller onboarding and payout approval remain open.

## Before a real launch

See [PLATFORM_STATUS.md](docs/PLATFORM_STATUS.md) for the six implementation areas, operational limits and external setup. `APP_ENV=production` and live-payment mode are deliberately blocked until those gaps are addressed. Countries, PayPal marketplace eligibility, real email, seller onboarding and payouts still need decisions or provider setup. A saved payment method and a 29-day authorization do not guarantee collection.

Agreement completeness, platform comparison, contradiction coverage and real-world launch steps: [AGREEMENT_REVIEW.md](docs/AGREEMENT_REVIEW.md). The agreement builder requires policy terms and blocks supported field conflicts before saving or acceptance.

Current AI configuration and live verification: [Gemini status](docs/GEMINI_STATUS.md). The app now runs Gemini semantic extraction and whole-agreement review; demo payment mode is unchanged.
