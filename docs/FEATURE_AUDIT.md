# Current portal feature audit — 2026-11-05

This audit compares every Markdown document in the project with the current code and tests. Historical engine capabilities are not counted as current portal features. This report supersedes conflicting unfinished-feature lists in older documents.

## Latest priorities and actual completion status

Blocker instructions: every deterministic validation flag now includes plain-language problem/solution fields and related-field shortcuts without changing its original rule, severity, ID or acknowledgment behavior. Timing explanations show actual dates, available/required days, revision/review arithmetic and conditional date options; out-of-order schedules are identified before suggesting spacing changes. Deliverable explanations name the row, its linked milestone and every milestone including it. Totals show formatted currency amounts and the exact difference. Other rules identify the affected field/current value, supported choices, required conditional input or contradictory policy. UI displays “How to fix” and links to related fields. Existing historical flags fall back to their stored messages.

Amendment presentation refinement: comparisons now appear only inside the Amendments tab as a compact changed-field/current/proposed table, with no strikethrough or inline arrows. Full proposed terms open through a separate button. The tab and rows briefly highlight changes, respecting reduced-motion settings. Form labels and inputs are larger; additional display names explain payment triggers, automatic-approval days, included work, exclusions and additional-work prices. This supersedes the earlier globally expanded amendment-summary presentation.

Agreement usability update: clearer display labels for project dates, milestone delivery/review, revision turnaround and prices; grouped field sections and a field jump selector. Blocker/dispute navigation opens collapsed ancestors, focuses/highlights editable or counterpart-owned fields and falls back to rendered clauses in read-only review. Amendment comparisons are expanded by default with section/row context and explicitly labeled signed/proposed values; private draft and shared proposal headings differ. Agreement data, validation, payment rules and signatures are unchanged by this UI update.

Workspace action repair: signed-project API now exposes why funded amendments are unavailable. Both amendment entry points lead to explicit change options for funded projects, including a working new-agreement link for clients and signed-agreement review. Unfunded projects retain the original amendment workflow. Payments list the actual schedule blockers and disable incompatible checkout before a request. Dialog action errors appear inside the dialog. This repairs misleading controls; funded amendments themselves remain unfinished.

Latest result: **actual USD 10 freelancer Sandbox payout verified SUCCESS**, transaction `3RS51112RC6865832`, item `MP6VYSVAERNGS`. Separate US payout credentials resolved the sender-country blocker while retaining original capture-account access. The payout is recorded as paid. This supersedes earlier receipt-blocked entries below. It is a successful original-capture-plus-separate-US-payout test, not a fresh complete checkout under one US merchant. See PAYMENT_SETUP_STATUS.md.

Latest resumption: corrected the global checkout block. Valid agreements can authorize a hold despite an older payout setup failure. Capture/release still requires sender eligibility. The app was restarted and its client portal returned HTTP 200. A real Sandbox recheck verified the existing capture is COMPLETED, but the original payout recovery again returned PAYOUT_NOT_AVAILABLE (reference ca55c7c2f7d36). Actual recipient receipt remains unfinished. See the latest section of PAYMENT_SETUP_STATUS.md; earlier statements below about blocking checkout are superseded.

1. **Freelancer receipt — NOT complete against PayPal.** The existing USD 10 capture was verified during recovery, but resubmitting its original payout intent returned `PAYOUT_NOT_AVAILABLE` (provider reference `f319574de3226`). No received status was recorded. Recovery now preserves the original ID, amount and recipient, supports duplicate-batch lookup, and refuses recovery outside a conservative 29-day retry window. Known sender-eligibility failures block further checkout/capture. PayPal sender eligibility must be resolved before actual receipt can be confirmed. USD is the currency; it does not establish sender-country eligibility.
2. **29-day signing enforcement — implemented.** Every included revision and its fresh review period now count toward the authorization budget, together with initial review and capture buffer. Both signers see calculated milestone dates and must acknowledge the rules. The API independently rejects incompatible historical or new versions. Checkout, authorization, delivery and dispute resolution apply the remaining revision budget. New checkout requires the freelancer's receiving email and freezes it for that payment.
3. **Real email verification — code implemented, activation deferred by user.** Empty SMTP configuration slots are prepared in `.env`; existing values were preserved. User has no provider/sender credentials yet and asked to leave space. Local mode remains enabled; no actual inbox delivery is claimed. Follow EMAIL_SETUP.md when a sender is available.

The requested second USD 10 milestone, “Autopay”, is unfunded. The existing signed agreement has review periods outside 3-5 days and insufficient spacing for the full revision/review reserve. It cannot be funded under current rules. No signed data was rewritten, no buyer approval fabricated and no second charge made. Its real payout test remains blocked by funding/schedule and sender setup. A separate isolated two-milestone provider-mocked test covers the second milestone's own capture, payout amount, recipient and confirmed receipt.

Change Requests work was paused before implementation at the user's request to prioritize payments. Full refunds/cancellation/partial settlement, funded amendments/deposits, uploads, complete financial reporting and production hosting remain unfinished. See PAYMENT_SETUP_STATUS.md for the concrete payment blockers and next steps.

## Implemented in the current portals

| Feature | Evidence | Boundary |
|---|---|---|
| Separate roles, signup, verification/reset, sessions, CSRF and throttling | `app/auth.py`, auth tests | Local preview by default; OIDC absent |
| Four-step builder, field ownership, required terms, totals and schedule checks | `app/guided_agreement.py`, `agreement_validation.py`, guided tests | Free-text semantics are not exhaustively validated |
| Invitations, counters, private drafts, autosave, immutable versions, approvals and exact-hash signatures | `app/negotiation.py`, negotiation/browser tests | No independent identity/signature provider |
| HTML/JSON/PDF exports | `app/agreement_renderer.py`, renderer tests | Exports record consent; no external signature certification |
| Chat, field references, amount/date/currency clarification and Gemini suggestions | `app/agreement_assistant.py`, guided/negotiation tests | Advisory; limited real-case and multilingual evaluation |
| Unfunded amendments with bilateral signatures and comparison | Negotiation and browser tests | Payment activity locks amendments |
| Sandbox authorization, delivery links, approval, separate freelancer payout | `app/assurance.py`, `payouts.py`, payment/browser tests | Actual buyer-to-recipient provider run still unverified in this audit |
| Included revisions, joint dispute resolution, signed automatic release, reminders, authorization refresh | `app/assurance.py`, `payment_scan.py`, automation tests | Requires worker; expired funding cannot be seamlessly replaced |
| Verified webhook handling and adverse-event pauses | `main.py`, automation tests | Local webhook configuration absent; incomplete case management |
| Encrypted SMTP outbox and TLS transport | `app/mail_delivery.py`, mail tests | Sender unconfigured; no bounce dashboard or uncertain-send recovery UI |
| PostgreSQL migrations, durable jobs, backup/restore utilities | `app/db.py`, `worker.py`, migration tests and scripts | Hosting, supervision, monitoring and recovery exercises remain |

## Work completed in this audit

Current captures and payout receipts previously did not enter the accounting ledger. `app/step1_ledger.py` now writes balanced gross postings in the transaction that confirms each capture or payout. Pending/unclaimed payouts do not discharge the liability. Verified returns/refunds/reversals of previously received payouts restore it. Repeated observations are idempotent; conflicting references fail instead of changing old entries. A receipt after an already recorded return requires investigation.

ISO minor units are converted exactly to the existing ledger's hundredths-of-currency convention, including JPY. Both participants can inspect settlement history in Payments; outsiders cannot read the endpoint. Capture reconciliation and provider-verified payout initiation also record captures, covering those recovery paths.

PostgreSQL verification exposed a deadlock between simultaneous manual approval and automatic release: a completion transaction held the payment row while another held the project row. Locked payment reads now acquire the project first, matching the API's project-then-payment order. The existing two-thread race test checks that only one capture wins.

This closes the missing gross capture/payout posting path, **not the entire accounting roadmap**. No historical backfill was run. Existing captures can be recorded when provider reconciliation runs; historical payout transitions cannot be reconstructed from a final status alone. Fees, taxes, capture refunds, chargebacks, provider balances, settlement exports and financial reports remain unfinished. Recorded entries are append-only through this module, but the shared legacy ledger has no database-level immutability trigger.

## Still unfinished, in implementation order

| Priority | Feature | What is missing |
|---|---|---|
| 1 | Payment recovery and financial reconciliation | Capture refund/reversal postings, unknown payout batch investigation, expired-authorization replacement, complete provider dispute handling, historical reconciliation and fee accounting |
| 2 | Change Requests / paid additions | Current tab has a disabled button. Needs proposals, exact scope/price versions, independent consent and funding integration; legacy scope decisions are inaccessible to current accounts |
| 3 | Cancellation, refunds and partial work | No complete current-portal bilateral settlement/void/refund flow; termination text does not execute it |
| 4 | Funded amendments and deposits | Amendments blocked after payment activity; deposit terms rejected at checkout; no collection/reallocation flow |
| 5 | Uploaded deliverables | Links only. No immutable uploads, private storage, access controls, malware scanning or retention workflow |
| 6 | Mail operations | Sender configuration, actual inbox verification, bounce tracking and recovery for interrupted/uncertain sends |
| 7 | Identity and accessibility | OIDC, deliberate historical account migration, independent security/WCAG audits and stronger public abuse controls |
| 8 | AI quality | Consented real cases, independent labels, multilingual/adversarial evaluation and measured false-billable decisions |
| 9 | Production operation | HTTPS deployment, process supervision, monitoring/alerts, recovery exercises, seller onboarding and live-provider eligibility |

Hourly billing, provider FX quotes/conversion, marketplace direct-to-seller capture and a full financial reporting UI are also absent. They must not be inferred from editable contract text or historical demos.

## Configuration observed locally

Read-only readiness inspection found PostgreSQL at `0004_sandbox_payouts`, Gemini and PayPal credentials present, local email mode, no SMTP sender configuration, no webhook ID, zero Step 1 scan jobs, no project-owned Git directory and no license. Credential presence does not verify provider access. No secrets, application data, external transactions, hosting or license were changed by this audit.

These setup items require an actual sender/domain, reachable webhook host, buyer interaction, provider eligibility and deployment choices. Code changes alone cannot complete them. Zero scan jobs means automatic payment processing is not demonstrated by the local readiness result.

## Documentation corrections

`PLATFORM_STATUS.md` contains earlier claims that automatic release, revisions, authorization refresh and SMTP are missing; those have since been implemented. `STEP1_IMPLEMENTATION.md` retains superseded guidance-only field ownership and missing AI descriptions. `ROADMAP.md`, `ISSUES.md`, `AGILE_ENGINE.md`, `LEGACY_DEMO.md`, `AGREEMENT_REVIEW.md`, `GEMINI_STATUS.md` and older sections of `VERIFICATION.md` describe historical behavior. Their legacy refunds, partial payments, Vault and scope decisions are not available to the current fixed-role portals. README and HACKATHON_REVIEW are more recent, but neither replaces verification of the actual code.

## Verification

Baseline before edits: 353 passed, 3 skipped. Updated full suite: **357 passed, 3 skipped**. Final isolated PostgreSQL suite: **190 passed**, including the concurrent approval/timeout regression and Playwright settlement-history flow. Focused ledger/payout/automation tests after the lock-order fix: **21 passed**. JavaScript syntax validation passed. The first PostgreSQL run exposed the deadlock described above; the rerun after correction passed. One existing Starlette TestClient deprecation warning remains. Provider payment calls are mocked; this audit does not claim a real Sandbox transfer. The running application was not restarted; restart with `./run.ps1` to load the changes.
