# Project lifecycle and payment scenario audit

Historical snapshot from before the resolution implementation. For current deadline, settlement, evidence and support behavior, use [Resolution workflows](RESOLUTION_WORKFLOWS.md). Remaining operational boundaries are stated there explicitly.

This audit describes the current fixed-role portals. Historical legacy-engine features are not counted as current capabilities. Automated payment checks use mocked PayPal adapters; this is not a new provider transaction report.

Verification: **228 tests passed** in one disposable PostgreSQL run, including three browser scenarios. Coverage includes a same-project amendment after a verified first payout, second-milestone funding at the newly signed price, original-version history/ledger preservation, blocked paid-field changes, unresolved payout gates, changed historical start dates and completion across two signed versions. One existing Starlette TestClient deprecation warning remains.

## Changes implemented in this update

- Remaining unfunded milestones can be amended in the same project after existing payouts are recorded as verified received. Both people approve and sign the new version. The current agreement remains active until the second signature.
- Paid milestone rows and their deliverables remain unchanged, including their references and positions. Project identity, currency and common legal/policy terms are locked after payment. Remaining milestone prices, dates, scope/specifications and the matching project total/final deadline can change within normal role ownership and validation rules.
- New funding pauses while an amendment is being negotiated or signed. A prior capture alone is insufficient to start the next milestone: the prior freelancer payout must be recorded as paid.
- Each payment retains its original signed-version ID. Historical payment/payout reads, worker scans, completion checks and ledger history continue across agreement versions. Existing payment attempts cannot be reused to pay a paid milestone again.
- Paid historical dates do not block amendments just because the original project start is now in the past. Remaining work is checked against current timing and authorization limits.
- The overview selects the next unpaid milestone. Payment references link to the specific version of the signed terms governing that payment.

## Scenario support

| Scenario | Support | Current behavior or limitation |
|---|---|---|
| Milestone 1 paid; change the price/date/scope of unfunded milestone 2 | Supported | Same-project amendment, locked paid work, comparison, both approvals and signatures, new funding against the amended version. |
| Change or remove already-paid work while amending | Protected against | Server rejects edits/removal/reordering of paid rows and deliverables. UI shows locked fields. |
| Change project identity, currency or shared legal terms after payment | Not in this amendment flow | Those fields are locked. Requires a separate agreement/project rather than retroactively rewriting paid work. |
| Start funding while an amendment is pending or only one party has signed | Protected against | Funding remains blocked until both signatures activate the replacement, or the proposal is rejected. |
| One party rejects the amendment | Supported | Previous signed agreement remains active and prior payments are untouched. |
| Pay or re-fund a paid milestone under a newer version | Protected against | Existing project/milestone payment lookup blocks another attempt; payment history keeps its original version. |
| Amend while payout is pending, failed, unclaimed, returned or uncertain | Blocked safely; recovery partial | Resolve existing provider state and verify receipt first. Some failures require manual PayPal investigation/funding. |
| Amend a milestone already authorized, delivered or disputed | Not supported | Existing payment attempt must not be silently repriced. Full void/refund/re-funding workflow is not implemented. |
| Abandoned or expired checkout/authorization | Partial | State/reconciliation evidence is retained. Seamless replacement of an expired/abandoned payment attempt in the same project is missing. |
| Change future dates after the original start date has passed | Supported after verified paid work | Historical start/paid dates remain unchanged; remaining milestones must satisfy current timing and review/reserve rules. |
| Client disappears after delivery | Supported with provider limits | Worker requests automatic capture and payout after the signed review period without a timely objection. Valid funding, a running worker and successful provider responses remain necessary. |
| Client requests a small in-scope CSS/design adjustment | Supported | Request revision, consume an included round, pause payment, resubmit and start a fresh review period. |
| Correct an objective defect without consuming a preference-revision round | Not implemented distinctly | Defect-fix terms exist, but current revision requests consume the same revision counter. |
| Request an extra tab/new functionality after submission | Partial | Discuss it in chat; future unfunded scope can be amended. There is no complete paid Change Requests flow for the currently funded milestone. |
| Client rejects the delivery as poor quality | Partial | Timely dispute pauses release; both parties can confirm a resolution and restart review if funding has enough time. No independent quality adjudication or arbitration. |
| Freelancer withdraws after accepting/signing | Supported with settlement limits | Reason, system chat event, client notification and acknowledgment. New work/funding stops; submitted work retains its agreed review/payment rules. No automatic refund/partial settlement. |
| Freelancer completes only part of one milestone | Missing settlement flow | No bilateral prorated payment, cancellation/refund or partial-work settlement UI. |
| Client deletes a shared project to avoid payment | Protected against | Deletion hides it only from that client's list. Freelancer access, records and worker processing remain. Restore is available. |
| Reopen remaining work after withdrawal | Not implemented | Withdrawal is not silently undone. A new mutually agreed work arrangement is needed. |
| Payout fails after capture | Partial recovery | Capture remains recorded; no recapture. Original payout intent can be recovered when eligible, with frozen recipient/amount and bounded retry window. |
| Previously successful payout is returned/reversed or capture is refunded | Partial | Worker/provider evidence updates known states; known unsettled prior payouts block subsequent funding/amendment. Full chargeback/refund case management and accounting remain incomplete. |
| Freelancer changes their receiving email after checkout | Protected against silent redirection | Existing checkout freezes the recipient. Future checkouts can use the newly configured recipient; saved intents retain their original recipient. |
| Two simultaneous approvals/funding/amendment actions | Supported safeguards | Project revision checks, project/payment locks, persisted provider request IDs and one payout intent protect state transitions. Remote ambiguity still requires reconciliation. |
| New project-chat message | Supported in-app | Counterpart gets a grouped unread notification, badge/toast while the app is open, and a link to that conversation. Chat never changes signed terms. |
| Participant is offline or closes the app | Partial notifications | Messages persist. Real email sender and browser push are not enabled; an in-app toast cannot reach a closed app. |
| Submit a file and prove what was delivered | Links only | No immutable uploads, checksum/version evidence, malware scanning or automatic content-quality verification. |
| AI detects scope/price contradictions | Advisory integration | Gemini reviews supplied agreement/conversation evidence. People confirm changes; AI cannot sign, arbitrate or authorize money. Broad independent accuracy evaluation remains unfinished. |

## Highest-priority remaining implementation gaps

1. Cancel/void and replace abandoned or expired authorizations safely, retaining original attempts and provider evidence.
2. Bilateral cancellation, refunds and partial-work settlement for funded projects.
3. Separate defect fixes from included preference-revision counters.
4. Paid Change Requests for work already in progress, including explicit price/scope consent and funding.
5. Independent dispute/support process; the current flow requires joint resolution.
6. Real notification delivery outside the open app and immutable deliverable uploads.

New functionality should preserve the same boundaries: no retroactive edits to signed paid terms, no invented consent, no duplicate capture/payout, and no reporting freelancer receipt before exact provider verification.
