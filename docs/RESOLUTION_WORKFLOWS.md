Latest payment model: see [UPFRONT_FUNDING.md](UPFRONT_FUNDING.md). Its funded-milestone rules apply only after explicit consent; the authorization rules below remain relevant to historical payments.

# Delivery, settlement and support workflows

Implemented for the authenticated client/freelancer portals. The original agreement versions, signatures and historical payment amounts remain immutable. The **Resolution** tab contains deadline status, proposals, bilateral acceptance records, cases and preserved delivery evidence. **Change Requests** provides paid additions in the same project. Existing shared-project deletion is labelled **Archive**, with restore.

## Consent and ordinary payment

New guided agreements include the exact delivery-protection clause in `termination.delivery_rules`, visible in the signed agreement/export. It activates only after both signatures. Existing agreements keep their original behavior until both participants explicitly accept **Activate delivery protections** in Resolution. This does not rewrite the old signed version.

Ordinary submitted-work payment still releases through the worker after the signed review period unless a timely objection pauses it. No second client payment approval is added. Merely creating a cancellation request or support case cannot pause that clock. The case form tells users to raise a timely payment objection through Payments when appropriate.

## Implemented cases

| Scenario | Behavior |
|---|---|
| Deadline approaching or missed | Durable in-app/email-outbox reminders, an Overdue state and exact cutoff in the signed project timezone. Date-only due dates end at local midnight; grace/notice use calendar days under the protection clause. |
| Freelancer ghosts with no submission | With protection consent, close unperformed work after signed grace plus termination notice. Void any remaining unused authorization and verify provider state. No payment is invented for undelivered work. Open partial-work claims prevent default closure. |
| First delivery after grace | With protection consent, record `late_pending`; no review clock starts until the client accepts the timing exception. Rejection records a dispute. Accepted late timing starts a complete review period if funding has enough remaining coverage. |
| Ordinary late delivery under a historical agreement | Preserve its existing rules until explicit updated consent; do not retroactively apply default/late consequences. |
| Client disappears after normal delivery | Existing automatic review-timeout capture and payout continue, subject to funding, timely objections, worker availability and verified provider outcomes. |
| Freelancer misses revision turnaround | Record correction due time and notify when overdue. Payment stays paused; no automatic award for the old disputed version. |
| Defect in agreed work during review | Separate defect objection; pause for correction without consuming a preference-revision round. Requires the protection consent. Ordinary included revisions still consume their agreed allowance. |
| Defect after capture/payout | A warranty-defect case checks the signed reporting period, records a correction deadline and evidence, and leaves payment/revision counters intact. It cannot automatically claw back a completed payout. |
| Deadline extension or client-caused dependency delay | Exact bilateral addendum, linked to the governing signed version. Check authorization coverage, retain original dates in the original version, and show the accepted operational dates. Include applicable accepted future dates when drafting a later amendment. |
| Extra tab/functionality | Paid change proposal with explicit scope, acceptance criteria, currency, exact additional price, due date and automatic review terms. Both participants type their account names to accept. A separately identified change milestone receives its own buyer checkout, authorization, delivery and payout within the same project. The original signed out-of-scope policy is respected. |
| Cancellation | Both accept the same proposal. Close remaining unsubmitted work; preserve already-submitted work and its review/payment rules. Unresolved checkout states or open partial-work claims require resolution before closure. |
| Partial work | Both accept exact amount and handover link. Capture that amount with final-capture semantics and transfer only the captured amount. Original signed milestone cap stays unchanged; settlement amount is separate. Do not label receipt complete until payout verification. |
| Unused authorization | Bilateral zero-payment settlement or consented no-delivery default verifies void; this is distinct from refunding a capture. |
| Captured charge with no payout intent | Bilateral explicit full-refund settlement with provider verification and balanced gross ledger postings. Refund references persist while pending. |
| Capture/payout already pending or paid | No duplicate capture, arbitrary refund or forced reversal. Retain frozen intents and provider recovery controls. Existing/returned payout intents block an unsafe new refund; the support case records unresolved recovery. |
| Lost settlement/refund response | Persist operation ID before dispatch; leave uncertain operations paused. Reconcile by exact reference, amount, currency and original capture association. A merchant-dashboard refund ID may be supplied for a lost response; reads must verify it belongs to the original capture. Never blindly resend an unknown transfer. |
| Expired/abandoned funding | Verify original order/authorization reference, amount and absence of capture. Void any live unused authorization, preserve previous-attempt evidence and create a new buyer-approved checkout. Preserve submissions/objections; a resubmission starts a complete review. |
| Lost checkout reference | Recover an existing order ID by provider reads, exact milestone/amount/currency checks and rejection of retired attempt IDs. This read recovery creates no charge. |
| Withdrawal then handover | Freelancer can upload partial-work evidence and open a handover claim even after withdrawal. This does not masquerade as full delivery or start payment review. Both can negotiate a settlement. |
| Restart after withdrawal/cancellation | Both accept reopening. Preserve prior closure/withdrawal history; require valid funding before new work. |
| Client archives suddenly | Visibility only. Freelancer access, chat, agreement records and worker processing remain. Restore is available. |
| One participant disputes quality, scope, access, delay, partial work, IP, confidentiality or payment | Open a categorized case, add responses/evidence and escalate after the response window. Cases are separate from financial objections, so filing after the review deadline cannot silently stop earned payment. |
| Dispute deadlock or absent respondent | Escalate to configured human support. No silence-based win or AI money decision. With no configured reviewer, show `support_unconfigured`, rather than pretending an adjudication service exists. |
| Human review | Allowlisted registered/verified accounts can read escalated cases, governing terms/addenda and preserved evidence, record mediation/support findings and issue recommendations. They cannot review their own projects or execute money. Both participants can confirm the same case conclusion; changing its wording invalidates earlier confirmation. Existing payment disputes still need their separate shared-resolution or settlement path. |
| External refund/reversal/payout return | Detect adverse provider states, open a payment support case, block unsafe continuation and retain provider references. Verified full refunds and returned previously received payouts have gross ledger adjustments. Unknown partial refunds/chargebacks require investigation. |
| Changed proposal, stale funding snapshot, duplicate votes | Exact proposal hashes, typed account names, actor/time/client metadata, project revisions and row locks protect acceptance. Revalidate financial state at final acceptance and claim provider operations before network calls. |
| Broken or later changed link | Preserve the original URL/note/checklist in append-only evidence. Optional uploaded files retain their original bytes and SHA-256. Delivery content/access is not automatically certified. |

## Evidence handling

Both participants can download project artifacts. Escalated-case reviewers have separate authorized downloads. Files are served as attachments with `nosniff` and private caching; no file content is executed or extracted by the app. Limits: five files per submission, 5 MiB per file, 50 MiB per project. Database guards prevent artifact/evidence changes or deletion. A checksum proves which bytes were stored, not originality, safety or acceptance quality. Antivirus/content certification is not supplied.

## Setup that requires actual service/account information

- `FAIRFLOW_SUPPORT_EMAILS`: comma-separated registered reviewer account emails. Participants cannot review their own cases. No reviewer identity, staffing or binding arbitrator is fabricated. Reviewers sign into their ordinary portal and use **Support queue**.
- Real inbox notifications: set `EMAIL_MODE=smtp` and configure the existing `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USERNAME`, `SMTP_PASSWORD`. SMTP mode requires actual mailbox verification. Local previews are never silently sent when SMTP is enabled later. Delivery uncertainty remains visible in the outbox audit. Browser push is not configured.
- Run the durable worker. With `WORKER_STEP1_ONLY=true`, it processes the current portal and email jobs. Real payment execution still requires the original merchant credentials, PayPal eligibility/balance and valid authorizations. The app remains Sandbox-only.

## Boundaries that cannot be solved by automatically choosing a winner

The support workflow is a human evidence/recommendation process, not legal arbitration or a staffed service by itself. It does not force an award when parties disagree, recover already-transferred money from the freelancer, resolve IP ownership, override PayPal's external dispute process, or guarantee capture after authorization expiry. A partial refund after a payout, chargeback accounting and provider recovery outside the supported paths need merchant/provider investigation and a separately agreed settlement. The UI records these cases and uncertainty instead of inventing consent or settlement success.

Provider adapters follow PayPal's [authorization/capture guidance](https://developer.paypal.com/checkout/delay-capture/), [void operation](https://developer.paypal.com/api/payments/v2/authorizations-void/) and [refund guidance](https://developer.paypal.com/checkout/refund-payment). A new Sandbox transfer was not executed as part of automated verification; tests replace external provider adapters.

## Verification and activation

Final isolated PostgreSQL verification: **266 passed**, including **four browser scenarios**. Separate migration, rendering and agreement checks: **39 passed**. JavaScript syntax/Python compilation and activated HTTP health/assets checks passed. The original agreement, signature, payment, payout and ledger hashes matched before and after activation; worker scan errors were zero. Provider effects were mocked in automated tests, with no new external payment transfer.

See `tests/test_workflows.py` for consent, deadlines, settlement amounts, duplicate votes, stale proposals, refunds, handover, funding recovery, artifacts, warranty and reviewer isolation. `tests/test_workflows_browser.py` exercises actual two-party policy acceptance, closure, evidence download and mobile layout. `scripts/verify_step1_postgres.py` uses only a disposable database; it does not reset application data.

Migration `0005_delivery_evidence` adds the artifact table and append-only evidence guards. Application activation uses an agreement/payment/payout/ledger hash comparison before and after migration/restart. Final test totals and activation results are recorded in `reports/resolution-activation-check.json`.
