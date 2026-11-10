# Upfront-funded PayPal milestones

The authenticated portals now have a separate PayPal Multiparty Sandbox adapter. Credentials remain in `.env.paypal-platform`; legacy merchant and Payouts credentials are unchanged.

## Behavior

New guided agreements include the explicit `paypal_funded_v1` funding model and full funding terms when platform credentials are configured. Upfront funding is the default for every future attempt. Existing unsigned drafts and normal amendments include the new terms automatically; there is no separate funding opt-in. Ordinary agreement signatures still record the parties? actual consent. Paid change requests include the same policy in their normal acceptance. Existing payment attempts are never converted.

The freelancer connects a Business Sandbox receiving account in Payments. PayPal must report a distinct merchant from the platform, confirmed email, payments receivable and the delayed-disbursement scope granted to this exact client ID. The link is only visible to its owning freelancer. The receiving merchant and app fingerprint are frozen before checkout.

The client approves an upfront `CAPTURE` order whose payee is the freelancer and whose disbursement mode is `DELAYED`. Only a verified completed capture with matching order/milestone/seller/currency/amount and delayed mode sets `funded`. Delivery is blocked before that state. Work and its complete review/revision reserve must fit within 26 days.

Delivery starts the agreed 1?5 day review. A timely objection pauses the FairFlow release request. Otherwise the worker requests a referenced disbursement of the existing capture. It never sends a separate Standard Payouts transfer. A release POST alone is not marked received; provider evidence is read again. Gross accounting is balanced; PayPal seller processing fees reduce net receipt.

Accepted withdrawal/cancellation refunds unsubmitted funded work, preserving already submitted work and open partial-work claims. Unsubmitted work without a claim is also closed/refunded by day 26 under the accepted funding policy. A joint partial settlement refunds the difference first and then requests release of the agreed remainder, with separate persisted operations and verification. Unknown operations remain paused; reconciliation never blindly resends them. Lost order references can be recovered with exact provider identity checks.

## Provider boundary

PayPal automatically disburses after 28 days. The signed terms disclose this, and the worker warns both parties and opens a case near the limit. An internal dispute cannot hold funds indefinitely. Reversals, external disputes, restrictions, unavailable provider operations and fees remain possible. This is delayed disbursement, not regulated escrow or 100% guaranteed payment. A stalled worker cannot prevent a provider-driven automatic release.

Official reference: https://developer.paypal.com/platforms/checkout/delayed-disbursement/

## Remaining real Sandbox verification

The original standalone referral connected the platform account as its own seller. It is rejected by the new verifier. The project needs the distinct freelancer Business Sandbox account to complete its own new connection link. Then perform buyer approval, capture, provider-held funding, review-timeout release, and a separate refund case. Automated payment adapters in tests are simulated; no end-to-end live provider success is asserted.

The application remains Sandbox-only. Existing signed payment amounts, agreements, signatures, captures and paid payouts are preserved.

## Future milestone amendments

An unresolved existing payment locks that funded milestone, rather than the entire project. Future unfunded milestones can be amended in the same project. Existing funded deliverables, currency, identity and shared terms are immutable. Financial state is snapshotted at amendment creation; a payment transition during review requires a fresh proposal before signatures can take effect. New funding still follows its own prerequisites.

Default-flow migration updated four unsigned drafts. SHA-256 checks verified that agreement versions, signatures, payments, payouts and ledger records were unchanged.


## Release verification and repeated approvals (9 November 2026)

The existing platform flow creates CAPTURE orders with DELAYED disbursement, verifies the separate seller's permission, and requires a completed delayed capture before funding work. No change to that model was needed. Release now also checks that the original platform credential fingerprint matches. A repeated approval returns the recorded released/processing status without another provider call, and the confirmation button is disabled while submitting. The payment UI explains that collection and disbursement are different stages.

The observed $1 Sandbox payment returned DELAYED before approval and a matching successful referenced disbursement after approval. This verifies the API sequence, but does not independently establish that the seller's available balance excluded the funds beforehand. Before claiming a verified balance hold, use an isolated seller test with available-balance snapshots before collection, after collection, and after release, with no unrelated activity, or obtain PayPal confirmation. Do not create a replacement payment or retry a successful release to investigate this distinction.

Partner approval and onboarding with DELAY_FUNDS_DISBURSEMENT are provider prerequisites; permission checks in code do not replace PayPal enablement. PayPal's automatic 28-day disbursement limit still applies. Reference: https://developer.paypal.com/platforms/checkout/delayed-disbursement/


## Capture-before-work hardening

Collection now checks the original platform fingerprint and requires the provider's approved order to explicitly contain payment_instruction.disbursement_mode=DELAYED before sending any capture request. If the mode is absent or INSTANT, the attempt pauses without collection. After capture, a matching completed DELAYED capture is still required before allowing delivery. The collection verification timestamp and reported provider mode are exposed in Payments, separately from release request and receipt timestamps.

Periodic reconciliation records the reported disbursement mode. Missing/unknown capture modes pause the workflow. A seller release without a recorded FairFlow request is recorded as released, with a persistent unexpected-release warning and an audit event, rather than falsely remaining in review. These checks detect provider behavior; they cannot reverse an external release or override PayPal's automatic 28-day limit. Formal partner enablement and a controlled balance test remain external requirements.

A read-only check of the current seller confirmed a separate merchant, payments receivable, confirmed email, and this platform's delayed-disbursement OAuth permission. No new charge, refund or release was sent during this audit.
