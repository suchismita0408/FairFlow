# Cancellation and withdrawal settlement implementation plan

Status: implementation now described in [Resolution workflows](RESOLUTION_WORKFLOWS.md). This file retains the original plan; use the implementation document for supported operations and limits.

## User-facing behavior

1. Rename deletion of shared/signed projects to **Archive project**, with an **Archived projects** list and restore. Retain **Delete draft** for unsent drafts. Archiving is personal visibility only and does not alter work, agreements or payment processing.
2. Add **Request cancellation** to signed projects for either participant. Record the reason, actor, time, agreed version and milestone payment snapshot. Notify the counterpart and add a system message with a direct link to the request.
3. Withdrawal continues to stop new funding and first submissions. Add a handover/settlement entry point so the freelancer can record partial work, delivery evidence and requested compensation before closing remaining work. Already-submitted deliveries keep their existing review/payment rules.
4. Show each milestone in a compact closure summary: already paid, submitted/in review, started/partial, or not started. Display the proposed outcome, handover evidence and exact amounts. Clearly distinguish an authorization, capture and verified freelancer receipt.
5. Allow both participants to negotiate and explicitly accept an exceptional settlement. Paid milestones are immutable. A revision to a proposal invalidates prior acceptance. This acceptance applies only to cancellation/partial-work terms; ordinary submitted-work release continues automatically under the signed agreement.
6. Close remaining work only after the accepted outcomes have been executed and provider results reconciled. Keep chat, signed versions, submissions, payment history and settlement history accessible. Surface unresolved provider operations as pending/needs attention rather than completed.

## Money and consent boundaries

- Requesting cancellation or archiving cannot by itself pause a submitted delivery's payment clock. Existing timely revision/dispute rules continue to govern submitted work. A new request cannot be used to evade the original objection deadline.
- A cancellation request must not permanently block new work merely because one party filed it. Only jointly accepted closure, existing withdrawal, or an existing valid dispute changes execution rules. The UI should warn about funding while a request is pending.
- Re-read current payment state under project/payment locks before accepting and executing settlement. If the financial snapshot changed, require a refreshed proposal and renewed acceptance. Serialize settlement execution against automatic capture/payout.
- For unfunded future work, joint closure needs no refund transaction.
- For an uncaptured authorization, verify the provider-supported partial-capture and remaining-authorization void behavior before implementation. Distinguish releasing an unused authorization from refunding a captured charge.
- Do not offer automatic refunds of already-paid work or recovery from the freelancer. Captured funds with pending/uncertain payouts require reconciliation and an explicitly supported operation path before settlement can execute. Unsupported cases remain clearly blocked with a reason.
- Any payable partial work requires both participants' explicit acceptance of amount, scope/evidence and handover terms. A silent counterpart is not acceptance. Preserve the existing submitted-work protection while leaving partial-work requests unresolved.
- Persist operation IDs and frozen recipient/amounts before provider calls. Reconcile ambiguous responses before retrying; never duplicate capture, payout, void or refund. State transitions must follow verified provider evidence and corresponding ledger entries.
- No unilateral fee/penalty, automatic arbitrary percentage, AI quality decision, forced refund or invented consent.

## Proposed request states

`proposed -> accepted_by_one -> accepted_by_both -> executing -> settled`

Alternative outcomes: `declined`, `withdrawn`, `superseded`, `needs_attention`. Financial completion is independent of proposal acceptance; a pending operation is not settled. Existing project status remains readable until remaining work is actually closed.

## Verification before release

- Archive during work and during review preserves freelancer access and processing.
- Cancellation before funding, after authorization, after submission and after first verified payout.
- Withdrawal with partial work, submitted work, an active revision and a pending payout.
- Proposal editing, stale financial snapshots, one signature, rejection and an absent counterpart.
- Simultaneous settlement execution/automatic release, duplicate requests, provider timeout, expired authorization, retired merchant and unsuccessful/returned payout.
- Both roles' browser flows, notifications, mobile compactness, immutable history and balanced ledger.
- Use isolated database fixtures and mocked providers first. Report any later real Sandbox transaction separately; never label mocked checks as live payment verification.

## Deliberate limits

Independent arbitration, compulsory partial-work awards, reversing completed payouts and automatic quality judgments require separate functionality. The UI must disclose an unresolved settlement instead of suggesting these exist.
