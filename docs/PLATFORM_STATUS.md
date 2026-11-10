# FairFlow implementation status

Current consolidated code audit and remaining work: [FEATURE_AUDIT.md](FEATURE_AUDIT.md). It supersedes stale open-item lists below. Gross Step 1 capture/payout ledger postings and participant settlement history are now implemented; full accounting remains incomplete.

## Approval-triggered payout and authorization scheduling - 2026-11-05

Client delivery approval now verifies the displayed recipient, captures the authorized milestone, and automatically requests the full milestone payout after capture is confirmed. The recipient is frozen before capture. Payout failures preserve the captured record; duplicate approval cannot recapture it. Capture reconciliation resumes the approved payout using that frozen recipient. Previously captured payments retain the explicit payout recovery action. Only a verified PayPal item SUCCESS means the freelancer received payment; batch acceptance remains pending. The current integration still uses merchant capture followed by Payouts, not direct seller capture or escrow. Merchant balance/fees and provider approval can prevent or delay payout.

The deterministic agreement validator requires 3-5 calendar review days and checks each milestone's delivery + review + revision turnaround + one-day capture buffer against a 29-day planned authorization period. The first planning anchor is project start; subsequent anchors are the preceding milestone delivery date. The final deadline must also leave a capture buffer. Funding revalidates signed price/schedule blockers and the actual current authorization window. Delivery requires sufficient remaining expiry for the full review/revision/buffer; approval refuses an absent or expired authorization. No automatic acceptance/capture occurs when review time elapses. New defaults use three review days and shorter example schedules. Both editable and read-only agreement screens explain the limits, and review days are visible without expanding advanced settings.

PayPal's first three-day honor period is distinct from the 29-day authorization validity. FairFlow cannot guarantee reserved funds remain capturable for 29 days. Authorization renewal, automatic recovery of unknown payout batch IDs, live payouts and payment-aware amendments are still open. Signed historical JSON/hashes have not been rewritten; incompatible historical schedules are blocked from new checkout and require a suitable new agreement/amendment (funded agreements remain locked).

Verification: 334 passed, 3 skipped in the full suite; 167 passed in isolated PostgreSQL, including Playwright. Focused validation/payment tests: 124 passed. JavaScript syntax check passed. The local app was restarted with these changes. Provider payment calls in these tests are mocked; no new actual Sandbox capture or payout was sent during this update.


## Freelancer Sandbox receiving payments — 2026-11-05

The freelancer can save a PayPal Sandbox recipient email in Profile or Payments. The client can explicitly send a separate Payouts API transfer after a milestone has a verified completed capture. The server re-reads the capture before sending, takes the amount/currency from the saved payment, and freezes the freelancer's recipient in a payout intent. Each payment can have only one payout intent; concurrent requests are serialized, and PayPal request/batch IDs are persisted before submission. Both members can refresh the payout status. Only an exact, matching individual item with `SUCCESS` is displayed as received; pending, unclaimed, failed and reversed states remain distinct. Recipient changes do not redirect existing payouts. This is a Sandbox merchant-to-recipient transfer, not multiparty seller onboarding or live-money approval.

Migration `0004_sandbox_payouts` was applied after backup `backups/postgres-20261105-041617-before-sandbox-payouts.dump`. The supplied Finance App freelancer Sandbox recipient was saved. No payout was sent as part of configuration. Existing configured credentials advertise the Payouts OAuth scope; actual provider acceptance/recipient receipt still requires a submitted Sandbox payout. Tests cover recipient ownership, capture gating and re-verification, exact amount/recipient matching, item status versus batch status, immutable recipient snapshots, duplicates/concurrency and uncertain outcomes. Live payout receipt remains unverified until the user tests it.

Verification for this update: **324 passed, 3 skipped** in the standard suite; **157 passed** against isolated PostgreSQL, including the updated Playwright recipient configuration flow. The app on port 8000 was restarted and the new payout routes are available.

Updated 2026-11-03 for Step 1: two authenticated portals and deterministic agreement negotiation. This supersedes the older default-interface descriptions below. See [STEP1_IMPLEMENTATION.md](STEP1_IMPLEMENTATION.md) for the specification comparison, module map and explicit requirement interpretations.

## Signed milestone Sandbox payments — 2026-11-04

The authenticated Step 1 workspace now has a Payments tab and project-scoped payment API. Only the client can create a PayPal Sandbox AUTHORIZE order for the next signed milestone, confirm buyer approval, and approve delivery for capture. Only the freelancer can submit a delivery link after authorization. Amount, currency and milestone ID come from the active signed hash, never request input. PayPal order and authorization responses are checked against that amount; capture is a separate client action. Each remote operation has a persisted idempotency key and uncertain responses enter `attention` for provider-status inspection. Duplicate capture attempts are rejected. The UI shows authorization, delivery, capture and expiry evidence. Amendments are blocked after payment activity until a payment-aware amendment design exists.

Migration `0003_step1_payments` is applied to the local PostgreSQL database after a backup at `backups/postgres-20261104-093118-before-step1-payments.dump`. Configured Sandbox OAuth returned HTTP 200 and a $1 AUTHORIZE order was accepted with a payer-action link. No Sandbox buyer approved that order; real authorization and capture remain unverified against the user's merchant. Mocked API tests cover role separation, exact amounts, delivery/capture ordering, duplicate prevention and uncertain provider responses. Verification: **318 passed, 3 skipped** in the standard suite; **151 passed** in isolated PostgreSQL, including the browser workspace flow.

The idempotent seed now creates a fourth local sample, a signed USD `DEMO Step 1 — Sandbox payment demo` project with a near-term milestone. It was created in the local app database and its Payments API returned HTTP 200. No order was started for that demo project. The older signed INR sample was preserved.

Payments are Sandbox-only and captured by the configured merchant. A separate Sandbox Payouts transfer is now available as described above. Marketplace seller onboarding, direct-to-seller capture, licensed escrow, capture refund/reversal reconciliation, automatic review release, authorization renewal and real email delivery remain open. A 29-day authorization has a shorter three-day honor period and cannot guarantee successful capture.

## Chat price signing gate — 2026-11-04

Signing now rechecks monetary amounts mentioned in project chat against the exact sent agreement version. Each difference requires a reasoned confirmation from both parties, bound to the version hash and all current text chat messages. A new message or agreement version invalidates the old confirmations. These are evidence questions: an offer in chat does not change agreement terms by itself. The project page displays each question and its confirmation status. This closes the demonstrated INR 1500 versus INR 1000 signing gap for amounts recognized by the deterministic parser; unrestricted semantic ambiguity in chat remains outside a guaranteed signing gate. Targeted negotiation and chat-confirmation tests passed, including invalidation by new chat.


## Guided builder update ? 2026-11-03

The default builder is now four compact steps: Brief & people, Deliverables, Price & dates, and Review terms. Only essential fields owned by the current role (or shared commercial fields) are editable up front. Optional details and visible proposed default clauses are expandable. Profiles fill party details; identifiers and single-milestone references are derived. No project-specific price, date or scope is invented.

Field ownership is now enforced by the API, including creation, edits and deletions: client brief/identity/inclusions/dependencies; freelancer identity/deliverables/revision terms; shared commercial and other policy terms during the holder's turn. Counterpart fields are read-only with a discussion/request-change action. Price discussion uses the existing append-only field-pinned project chat. This supersedes the earlier guidance-only ownership decision below.

New guided projects can invite after the client completes their brief, budget/dates, inclusions and dependencies. Freelancer-owned fields are completed after opening. Full deterministic validation still gates the completed proposal, approval and signing. Existing complete drafts retain their compatibility path.

The AI review button sends the exact draft/sent terms and complete project conversation, with speaker roles and message IDs, through the existing Gemini adapter. It returns clarification questions and individually reviewable field suggestions. Apply is permission-checked, revision-checked, conversation-hash-checked and validated; AI never approves/signs or mutates terms automatically. Reviews are invalidated when terms or chat change. Provider failure is explicit and leaves deterministic validation available. Audit records store review fingerprints/counts, not private draft suggestions visible to the counterpart.

Verification: updated full suite **311 passed, 1 historical browser test skipped**; updated isolated PostgreSQL suite **144 passed**, including the guided-flow tests and Playwright; targeted guided ownership, short-brief invitation, AI evidence, no-mutation and stale-review tests added. Updated Playwright covers split-role completion and the four-step builder. Live Gemini synthetic check correctly identified the unresolved INR 1000 versus INR 1500 counteroffer (one issue, no invented price suggestion). This demonstrates a working integration, not a guarantee of detecting every semantic ambiguity.

## Step 1 available now

- Separate `/client/login`, `/client/signup`, `/freelancer/login` and `/freelancer/signup`; fixed account role, role-specific profiles, verified email/password identities, HttpOnly/SameSite cookies, CSRF, rate limits/account lockout, 30-minute idle sessions, active session list, logout everywhere and auth audits.
- Role-specific dashboards, freelancer invitation inbox, the eleven-section agreement stepper, completeness/section status, helper text, field flags, decimal currency inputs backed by integer ISO minor units, light/dark modes and responsive layouts.
- Pure deterministic required/conditional/contradiction/ambiguity validation; blockers cannot be dismissed. Warning reasons from both parties are included in immutable version snapshots and the final signed hash.
- Turn-owned private drafts, ten-second autosave and device draft recovery after re-login; optimistic revisions, PostgreSQL row locks and SQLite write reservations. Sent snapshots, parent hashes, field diffs, approvals, field decisions/rejection reasons, typed-name/IP/user-agent signatures and audit history.
- Invitation opening, seven-day configurable expiry, 48/24-hour reminders, withdrawal, decline/cancellation and counter/signing transitions. The worker processes reminders/expiry and explicitly excludes Step 1 projects from payment scans.
- One append-only project chat with field pins, links, system events, unread counts, SSE and polling fallback. Chat cannot edit an agreement.
- Deterministic numbered clauses, clause navigator/search, HTML/JSON/PDF exports, field-to-clause map and agreement ID/version/SHA-256 PDF footer. Tests extract PDF text and compare all clauses with their structured source.
- Locked signed workspace with overview, agreement, read-only milestones/deliverables, revision counters, terms summary, chat/activity and amendments. The old signed version stays active until the amendment has both signatures. A membership-protected contract-summary API exposes the signed terms for later integrations.

Step 1 has no AI approvals/decisions, escrow, file upload/storage or dispute resolution. The signed agreement's payment fields now drive the separate Sandbox milestone flow described above. New fixed-role accounts are denied access to the older payment and AI decision APIs. Existing records and the legacy implementation remain available for explicit compatibility work; unclassified historical accounts are not silently assigned a global role. Legacy registration and password-free demo login are disabled by default.

## Step 1 verification

- Standard suite: **307 passed, 1 skipped**. The skip is the historical browser/payment scenario requiring a separately configured external server; the new isolated Playwright scenario runs in the standard suite.
- Isolated PostgreSQL suite: **140 passed**, including auth/roles, required fields, contradiction rules, state transitions, project membership, concurrent edits/double-signing and the full Playwright workflow.
- Browser coverage: separate signup/verification, create/invite, blocked contradiction, counter, exact-version approvals, both signatures, workspace/chat, idle-session expiry with local draft restoration/autosave, wrong-portal redirect, 390px layout and both themes. No browser JavaScript errors recorded.
- Migration tests cover both a fresh database and preserving old users, sessions, projects and agreement rows. Foreign-key integrity is checked after SQLite batch migration.
- The local PostgreSQL database was backed up to `backups/postgres-20261103-142027.dump` before applying `0002_step1`. Migration and the idempotent seed script succeeded. Three labeled local demo projects are available: contradictory draft, invitation and signed agreement.
- One pre-existing Starlette TestClient deprecation warning remains.

```powershell
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python -m pytest -q
python -m scripts.verify_step1_postgres
node --check static/portals.js
python -m scripts.seed_step1
```

The PostgreSQL verification script only resets/removes its own disposable `_qa_step1_*` database. It never resets the application database. SQLite is a local development/test fallback.

## Step 1 still open or deliberately deferred

- Real email delivery and production hosting are not configured. Verification/reset and notification messages use the existing encrypted local email outbox/preview.
- Independent security and WCAG accessibility audits are still needed; the implemented keyboard, labeling, focus, error and layout behavior is not a full certification.
- Gemini advisory review is now integrated as described above. Deterministic validation and human approval remain authoritative.
- Exclusion overlap and ambiguity checks are deterministic heuristics. They cover the specified rules, not arbitrary semantic contradictions in unrestricted prose.
- The specification simultaneously requires freelancer-owned fields before sending and expects the freelancer to fill them only after invitation. This implementation enforces complete initial terms: the client proposes them and the freelancer reviews/counters. Field ownership is guidance, while turn ownership is enforced.
- OIDC and a deliberate migration policy for historical accounts are future work. Sandbox milestone funding, link delivery, client-approved capture and separate freelancer payouts are available; live seller payout approval, payment-aware amendments, automatic acceptance, capture refund/reversal processing, change-request execution, file storage, scope-firewall decisions and dispute operations remain open.

## Run Step 1

Start Docker Desktop, install requirements, then run `./run.ps1`. Open http://127.0.0.1:8000/client/login or http://127.0.0.1:8000/freelancer/login. Local demo emails are `step1-client@example.com` and `step1-freelancer@example.com`; the development seed password defaults to `Demo-terms-only-123!`, or uses `FAIRFLOW_DEMO_PASSWORD` when set. These are labeled local demonstration accounts only.

## Previous platform: retained context

The six areas below describe the preceding hackathon implementation, preserved for compatibility. Its Gemini and simulated-payment flows are not part of Step 1. Production rollout, marketplace onboarding and real provider verification remain incomplete.

## Six implementation areas

| Area | Available now | Remaining work |
|---|---|---|
| Identity | Email/password registration, Argon2id, verification, password reset, session revocation, CSRF protection, login throttling, invitations and project roles | Real email sender, independent security review, broader abuse controls |
| Agreements | Versioned clauses, acceptance criteria, exclusions, defect/revision policies, priced items, independent acceptance and exact-version digests | Richer amendment workflows; funded baseline edits are blocked |
| AI review | Gemini fallback chain, clause retrieval, evidence IDs, citation validation, six recommendation categories, clarification fields and joint human decisions | Evidence relevance evaluation, stronger retrieval, broader multilingual tests; evidence URLs are references, not downloaded or verified files |
| Evaluation | Reproducible 12-case synthetic evaluation, persisted model traces, reviewed decisions and regression tests | Consented real cases, independent labels, held-out splits, model-by-model benchmarks and pilot measurements |
| Payments | Demo lifecycle, Sandbox Orders/Vault/Payments adapters, priced partial settlement, joint refunds/retries, provider reconciliation reads, operation journal and balanced ledger | Buyer-approved Sandbox end-to-end verification, full external refund/reversal reconciliation, seller onboarding, marketplace approval, payouts and provider FX quote ingestion |
| Operations | Local PostgreSQL 17, Alembic, preserved legacy archives, encrypted tokens, durable jobs with leases/heartbeat, per-project locks, local notifications, backup and isolated restore check | Production hosting, real notifications/delivery evidence, monitoring, scale/crash recovery exercises and restricted customer pilot |

Participants resolve disputes jointly. There is no support override. Unresolved disputes block settlement. Payment network calls still hold a per-project lock; other projects can progress, but a busy project may return a retryable conflict. AI inference runs outside that lock.

## Currency and global audience

- USD remains the default. Agreements can select the 25 currency codes in the PayPal adapter catalog, including EUR and JPY.
- JPY, HUF and TWD require whole-unit amounts. Frontend and API reject invalid precision. Existing `*_cents` fields always store hundredths of the agreement currency; they are not ISO minor units.
- Changing currency clears price inputs for explicit re-entry. It does not apply an exchange rate. AI draft extraction currently produces USD terms; other currency budgets must be entered and reviewed explicitly.
- Revised agreements preserve priced items in the form, require fresh acceptance, and invalidate saved-method consent for the old plan. Funded agreements cannot change currency.
- Currency follows the agreement through payment requests, capture, refund and ledger records. `PAYPAL_ENABLED_CURRENCIES` gates Sandbox funding and defaults to USD until the merchant's receiving settings have been confirmed.
- FairFlow adds no FX markup. PayPal may present conversion costs in eligible hosted checkout. FairFlow does not yet retrieve or persist a provider exchange-rate quote, and saved-method payments do not promise an interactive FX quote.
- The intended audience is worldwide. This does not establish payment availability in every country or currency; there is no automatic fallback into a different charge currency.

Catalog and precision reference: [PayPal currency codes](https://developer.paypal.com/api/codes/currency/), checked 2026-11-02. Provider account, country and receiving-preference restrictions still apply.

## Verification from the resumed work

- 162 automated tests passed: authentication/roles, consent, lifecycle, recovery, fallback behavior, provider request contracts, currency precision, stale consent and ledger propagation.
- Separate-account browser workflows passed in USD and JPY against local PostgreSQL: revised priced items, invitations, acceptance, simulated funding, delivery, capture, ledger, all six room tabs and 390px width. No browser JavaScript errors were recorded.
- Offline synthetic evaluation: 8 exact labels out of 12. Remaining cases abstain. This is not a real-model accuracy score.
- PostgreSQL backup restored successfully into a fresh isolated QA database. Migration version and core records were readable, and all project/currency ledger groups balanced. The temporary restore database was then removed; the application database and backup were preserved.
- One existing Starlette TestClient deprecation warning remains.

```powershell
python -m pytest -q
node --check static/platform.js
python tests/platform_browser_smoke.py --currency USD
python tests/platform_browser_smoke.py --currency JPY
python -m scripts.evaluate_decisions
python -m scripts.backup_database
python -m scripts.verify_backup_restore
```

Browser smoke creates labeled QA accounts/projects and requires the local demo server. Restore verification defaults to the newest PostgreSQL dump in `backups/`, or accepts an explicit backup path. It checks restore integrity, not a full production disaster-recovery procedure. Preserve `.env` and its encryption key separately: restoring the database alone cannot recover encrypted tokens without the original key.

## Run and next external steps

Start Docker Desktop, then run `./run.ps1` to prepare local configuration, start PostgreSQL, migrate, and run the API with its worker. Stopping that launcher stops its worker; PostgreSQL data remains in its Docker volume. Open http://127.0.0.1:8000 and register separate client/freelancer accounts.

For the PayPal hackathon, the next external dependency is an eligible Sandbox merchant and a Sandbox buyer completing PayPal approval. Test funding, saved methods, capture, partial capture, void and refund before claiming provider end-to-end verification. Existing credentials are preserved; tests in this resumed run used mocks and demo money.

Production work additionally requires the actual business registration country, merchant/seller eligibility, a real email sender, deployment details and approved seller-disbursement design. Those are not inferred from the worldwide audience requirement. Live payment and production modes remain blocked.

Current AI configuration and live verification: [Gemini status](GEMINI_STATUS.md). The app now runs Gemini semantic extraction and whole-agreement review; demo payment mode is unchanged.
# Agreement and chat review update (4 Nov 2026)

- The advisory review now receives every saved agreement version, the active signed agreement, the current private draft when applicable, and the complete project chat. The response identifies reviewed version hashes and message count.
- A deterministic comparison surfaces currency amount differences between chat and the active agreement even if Gemini reports no issues. It asks the parties to clarify whether a discussed amount was superseded. AI findings remain advisory and cannot change or approve terms.
- Large evidence sets are explicitly reported as not fully AI reviewed rather than silently truncated. Chat amount detection is conservative and does not establish that an offer was accepted; the parties must confirm changes in a new version.

Amendment review update: both portals show a field comparison against the active signed agreement, including old and proposed currency amounts. Proposed versions are labeled as amendments and their download links select the proposed version. Playwright verified the freelancer can see an INR-to-USD 20 amendment before approval (1 browser scenario passed).
