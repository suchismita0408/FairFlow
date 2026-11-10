# Work tracking and resolution: current behavior

Checked against the implementation on 7 November 2026.

## Milestone, deliverable and submission

A milestone is a payment stage with a price, delivery date and review period.
A deliverable describes the specific output within that stage, including its
specification, acceptance criteria and revision allowance. One milestone can
contain several deliverables. A submission is the actual link/files sent for
review. These remain distinct in the agreement; the former Deliverables tab is
now Work Tracking.

Work Tracking contains current submitted links and notes, submission timestamps,
review deadlines, available submission/revision/dispute actions, revision usage,
agreed specifications, preserved evidence and files, work claims and related
schedule proposals. Payments retains collection, payment references, payout
reconciliation and settlement history, with links to the corresponding work.

## Resolution actions

| Label | Current behavior | Boundary |
| --- | --- | --- |
| Original signed rules | Optional delivery-protection addendum is not active. | Existing signed review/payment rules still apply. |
| Delivery cutoff | End of the delivery date in the signed project timezone, shown as next midnight. | Grace and notice determine later default handling. |
| Activate delivery protections | Both participants approve explicit late-delivery, default and defect rules. | A proposal alone does not activate them or pause payment. |
| Request cancellation | Both agree to close unperformed work; unused authorized funds are voided or platform captures refunded. | Existing submissions keep their review rules; open partial-work claims or unknown payment effects must be resolved. |
| Propose partial settlement / refund | Both approve an exact award/refund within the funded cap, with handover for partial awards. | Platform partial settlement refunds the difference and releases the agreed award; legacy captured payments support only full unsettled refund. Completed payouts are not automatically reversed. |
| Propose deadline extension | Both approve a delivery exception; effective schedule/final completion adjust. | Only unfunded, authorized, funded or revision-requested work; existing funding must cover delivery plus review/revision reserve. It does not automatically shift every later milestone. |
| Record client-caused delay | Records a jointly accepted revised delivery date caused by missing client input. | Same funding-coverage checks; a unilateral assertion or chat message changes no dates. |
| Propose paid change | Both approve additional scope, price, acceptance criteria, date and review period. | Creates a separate change-order milestone and funding path; does not rewrite the original paid work. |
| Open dispute / work claim | Preserves statements/evidence, allows replies and escalation. | Does not itself pause review or move money. The client must make a timely delivery objection in Work Tracking to pause automatic release. |
| Requests & accepted addenda | Shows proposal state and both participants' exact acceptance record. | Payment/signed-version changes can invalidate a pending proposal; it must be proposed again. |
| Confirm shared resolution | Both confirm the same resolution to resume disputed delivery review. | Needs sufficient provider coverage; a fresh full review period starts. |

## Can a paid change be used at any time?

Either participant can propose one after the agreement is signed, including
after an earlier milestone was paid. It requires a positive price, clear scope,
acceptance criteria, a current/future delivery date and 1–5 review days. It is
blocked during an agreement amendment, after withdrawal or remaining-work
closure, and when the signed out-of-scope policy requires a new project.

Acceptance creates a `CO-...` milestone; it does not charge the client or enable
unfunded submission by itself. Funding needs the receiving account and payment
prerequisites. An included correction/revision should use the existing work's
review actions. Changing an existing unfunded stage uses Amendments; moving all
remaining dates earlier uses Start milestone early.

## Current operational limitations

- Independent reviewer implementation is outside this project's scope by choice.
  Both participants can record evidence, reply and confirm a shared resolution.
  There is no independent decision service; existing internal escalation records
  can show `support_unconfigured`. AI cannot decide a dispute or authorize money.
- Email mode is local preview. In-app notices work; external email delivery is
  not configured.
- Uploaded snapshots/checksums preserve evidence, but a link does not prove
  quality, continued availability or that the output meets acceptance criteria.
- New platform funding has a 26-day planning limit and records the provider's
  automatic disbursement deadline. An internal dispute cannot hold funds
  indefinitely. This is not an unconditional payment guarantee or an escrow.
- Failed/unknown provider operations need reconciliation. Schedule approval,
  case closure or an internal status cannot substitute for verified receipt.
- In the Apartment Booking project, M1 belongs to the retired merchant and its
  payout is in attention. No verified completed milestone exists, so early start
  is blocked until the original payout is genuinely reconciled. Changing to the
  new platform account cannot move that historical capture.
  The user will test with a fresh project; these old payouts are left untouched.
