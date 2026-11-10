# Delivery, default and dispute case matrix

This is the original proposal/audit. For the subsequently implemented workflows and remaining service dependencies, see [Resolution workflows](RESOLUTION_WORKFLOWS.md).

Scope: current fixed-role FairFlow portals, inspected 2026-11-06. This is a proposal and source audit, not an assertion that the proposed behavior is implemented. No finite list covers every real-world dispute.

Status vocabulary: **Implemented** means the described path exists in the current portal; **Partial** means protection or recording exists but the case cannot be fully settled; **Missing** means the proposed workflow is absent. Existing test coverage is evidence for the specific paths tested, not a guarantee against all defects or provider failures. No tests or new provider transactions were run for this documentation update.

## Deadline and silence cases

| Case | Current support | Proposed handling |
|---|---|---|
| Freelancer disappears before accepting/signing | Partial: invitation expiry/reminders and withdrawal exist; no delivery obligation yet | Let the client withdraw an unanswered invitation; retain history. No work payment is created. |
| Freelancer disappears after signing, before funding | Missing default closure | Remind, mark overdue, use the signed grace/notice terms, then close unperformed work. Never fabricate a payment for undelivered work. |
| Freelancer disappears after authorization, with no delivery | Partial: no delivery means no automatic capture; expiry is detected | Notify before/at due date, record overdue, apply signed grace and notice, then close only unperformed work and void the unused authorization through a verified provider operation. Retain paid/submitted milestones. |
| Freelancer submits after due date | Partial: delivery endpoint checks remaining authorization time, not milestone due date or grace | Accept within explicitly signed grace while recording lateness. Beyond grace, store as a late submission awaiting a deadline exception; do not start an automatic payment clock. Client may accept the late handover or request closure. This is acceptance of changed delivery timing, not a second ordinary payment approval. Silence is not acceptance of the exception. |
| Late delivery still has time for review | Currently can enter normal review despite missing due date | After valid deadline exception, start the complete signed review period at actual accepted submission; never shorten it to fit the old deadline. |
| Late delivery cannot fit authorization | Implemented protective block; replacement funding missing | Preserve attempted-delivery evidence separately. Reconcile/void old funding, obtain explicit new funding and deadline consent, then permit funded review. Reauthorization alone does not extend the original maximum lifetime in current code. |
| Freelancer misses an agreed revision deadline | Missing revision-default workflow | Track revision due date separately. Remind, allow signed cure/notice period, then offer settlement or escalate. Do not pay the old disputed version automatically because the freelancer is silent. |
| Client fails to provide agreed content/access | Missing operational delay tracking | Record dependency, evidence, responsible person and impact. Confirm a day-for-day extension or escalate disagreement; one party cannot indefinitely stop the clock by assertion alone. Recheck funding coverage. |
| Both agree a deadline extension before funding | Implemented as normal amendment, with locks after prior paid work | Sign the changed future dates in the same project before proceeding. |
| Both agree an extension after authorization | Missing funded amendment/replacement flow | Preserve original version and authorization; create an explicit deadline exception linked to original terms and safely replace funding if required. Never rewrite historical consent. |
| Client disappears after a valid ordinary submission | Implemented with provider limits | Signed 3-5-day review timer ends; without a timely objection the worker requests capture and payout. Receipt depends on valid authorization and verified provider outcomes. |
| Client disappears after filing a dispute | Partial: payment remains paused, no independent resolution | Set a response/evidence deadline agreed in advance, then escalate to a real support reviewer. No automatic win based only on absence or AI output. Protect funding against expiry, or mark it unfunded and unresolved. |
| Both disappear | Partial: ordinary submitted work can still release; other states may expire or remain disputed | Process each milestone independently: valid uncontested submission follows consent; no submission follows default closure; disputed/partial work remains an unresolved case requiring the agreed process. |

## Scope, quality and evidence disputes

| Case | Current support | Proposed handling |
|---|---|---|
| Small in-scope color/font/spacing adjustment | Implemented included revision path | Select deliverable, consume available round, pause release, resubmit and restart full review. |
| Agreed functionality is broken | Partial: same revision/dispute path; no distinct free-defect workflow | Add defect report tied to acceptance criteria and reproducible evidence. Confirmed defects do not consume preference-revision allowance. Set a correction deadline. |
| Client requests new tab/functionality | Partial: chat and unfunded future amendment | Classify against signed scope; propose scope, price and timing change with both signatures. Existing agreed work remains independently payable. Funded paid change requests need a separate supported flow. |
| Included revisions exhausted | Implemented limit; extra paid change flow missing | Offer paid change proposal for preferences or dispute for genuine noncompliance. Exhausted preferences must not eliminate defect rights. |
| Client calls the entire delivery poor quality | Partial: timely dispute pauses release | Require specific unmet criteria and evidence. Offer correction, jointly agreed partial settlement or independent review. Subjective dislike alone cannot automatically erase earned payment. |
| Only some deliverables are complete | Missing partial settlement | Inventory accepted/rejected/missing items and handover evidence. Agree exact payable amount and remaining obligations; execute supported settlement. No guessed percentage or unilateral amount. |
| Freelancer uploads empty/irrelevant work to trigger payment | Partial and material gap: only a valid URL plus note required; content is not fetched/verified | Preserve immutable submission evidence and explicit deliverable checklist; notify client with review deadline. Allow timely dispute and human review. AI may flag inconsistencies but cannot certify quality or decide payment. |
| Link is broken, private, later changed or removed | Partial: links and audit timestamps only | Capture uploaded artifacts or an immutable snapshot/checksum when possible. Add access check/evidence, a clear unavailable-delivery rule and a repair deadline. Current code does not validate link content or access. |
| Delivery meets scope but client changes preference | Partial: revisions and dispute exist, independent enforcement absent | Compare signed criteria, use included preference rounds, quote extra changes separately. Escalate if contested; avoid rewriting scope after submission. |
| Freelancer disputes an objection as unfair | Partial: bilateral resolution only | Evidence-based response followed by support review if talks fail. Do not let repeated objections reset deadlines indefinitely. |
| Both agree a dispute resolution | Implemented limited outcome | Both confirm identical text; current app resumes full review if authorization can cover remaining schedule. It does not itself execute partial settlements, refunds or a reviewer award. |
| One rejects resolution or changes its wording | Implemented protective block; deadlock handling missing | Keep dispute paused. Version the proposal, record evidence and escalate after the response deadline. |
| Defect discovered after payout | Missing dedicated post-payment warranty process | Apply signed defect period with a correction request/evidence and escalation. Do not automatically claw back a verified completed payout. |
| Confidentiality, IP, licensing or plagiarism claim | Missing case workflow | Preserve signed clauses, authorship/source evidence and access history; route to a qualified human process. Automated timers cannot resolve ownership or licensing disputes. |

## Withdrawal, cancellation and project visibility

| Case | Current support | Proposed handling |
|---|---|---|
| Client deletes shared project after work starts | Implemented protection | Current deletion only hides from client; freelancer access, evidence and processing remain. Rename to Archive for clarity. |
| Freelancer withdraws with no submitted work | Partial: stops new funding/first delivery; unused authorization not released by this action | Offer handover/partial settlement before withdrawal; close unperformed work and verify void of unused funding under agreed rules. Do not imply acknowledgment settles money. |
| Freelancer withdraws after submission | Implemented preservation with settlement limits | Preserve delivery review/revision/payment. Stop remaining new work. Handle any unfinished portions separately. |
| Client wants cancellation after work starts | Missing funded cancellation | Record request and propose settlement. Merely requesting cancellation cannot pause an existing valid submitted-work payment clock. |
| One side refuses partial settlement | Missing escalation | Preserve original obligations/evidence; no arbitrary prorating or invented consent. Negotiate, then use an actually implemented support-review process. |
| M1 paid; cancel M2/M3 | Partial | Preserve M1; assess each remaining milestone as submitted/partial/unstarted. No automatic refund of already paid work. |
| Both want to resume after withdrawal | Missing reopen flow | Explicit mutually signed restart and current funding checks; never silently remove recorded withdrawal. |
| Client changes unfunded M2 after M1 verified paid | Implemented | Same-project amendment, paid rows locked, both approvals/signatures, funding at newly signed price. |
| Client tries to change funded or paid work retroactively | Implemented protective block; alternative flow missing for funded | Preserve paid terms. Funded changes need explicit replacement/settlement operations; no silent repricing. |

## Payment and operational disputes

| Case | Current support | Proposed handling |
|---|---|---|
| Authorization expires without delivery | Partial: marked expired and notified | Record no delivery payment, verify provider status, close or explicitly replace funding if restarting. An authorization release is not a refund of captured money. |
| Authorization expires during review/revision/dispute | Partial: capture stops; easy replacement missing | Warn early; preserve full review and claims. Renew within supported limits or create verified replacement funding with consent. Never claim guaranteed payment without valid funds. |
| Capture succeeds but payout fails/pends | Partial recovery implemented | Preserve capture; never recapture. Reconcile exact payout intent and show captured/payout pending distinctly. Requires recovery/support when automated path cannot finish. |
| Timeout/duplicate click/concurrent objection and capture | Implemented safeguards with ambiguity limits | Locks, persisted provider IDs and reconciliation; never retry unknown remote effects as a new transfer. Report uncertainty honestly. |
| Receiving email changes | Implemented protection | Existing intent keeps frozen recipient; future funding may use new configured recipient. Wrong-recipient cases need provider investigation, not a second automatic payment. |
| Refund/reversal/chargeback or payout return after success | Partial detection; full case/accounting workflow missing | Open a payment case, preserve provider references, reconcile ledger and prevent double refund/recovery. In-app agreement acceptance cannot override provider dispute outcomes. |
| Historical payment belongs to old merchant | Implemented protective block | Retain historical receipts; investigate through that original merchant. Never retry its operations with a different merchant's credentials. |
| Worker/server offline | Partial: durable scans can resume; authorization may expire meanwhile | Monitor and alert operators; notify users of processing delay. Catch up only if consent and funding still permit. Never pretend a delayed capture succeeded. |
| User closes app and misses notification | Partial: in-app only/local email | Add configured email delivery and delivery logs; optional push. A stored notification is not proof a person received or read it. |

## Proposed decision policy

Use signed scope, acceptance criteria, timestamps and preserved evidence first. Give negotiation a clear response period, then offer mediation and an actual platform-support review workflow with a human operator, access controls and audit trail. These latter two stages currently appear in terms but are not operational review services. Do not imply they exist until implemented and staffed. Separate any internal settlement from PayPal's external dispute process.

Deadlines, grace, notice, evidence response periods, permitted support decisions and late-submission consequences must be clearly accepted before they apply. Existing agreements require explicit updated consent; new policy cannot be retroactively inserted. Use the agreed project timezone for date-only due dates, store exact UTC cutoffs and specify whether grace/notice uses calendar or working days. Do not choose an arbitrary lateness penalty.

**Implementation priority:** deadline/grace enforcement and reminders; default/late-delivery handling; cancellation/partial settlement and funding replacement; distinct defect correction; human dispute case queue; immutable delivery evidence; external notifications. AI can summarize the dispute and cite relevant clauses, while people and verified provider operations control the outcome.

## Evidence locations

- `app/assurance.py`: delivery eligibility, full review timing, objections, bilateral resolution, expiry/reserve guards, reauthorization limits and manual-approval rejection.
- `app/payment_scan.py`: review/authorization reminders, automatic release, expiry, interrupted operation handling and external payment-change detection. It does not implement delivery-overdue/default decisions.
- `tests/test_step1_assurance.py`: insufficient-authorization-time delivery rejection.
- `tests/test_step1_automation.py`: revisions/disputes pause release, identical bilateral resolution, expiry notifications and reauthorization lifetime limits.
- `tests/test_project_lifecycle.py`: withdrawal preservation, personal deletion, notifications and no new work after withdrawal.
- `tests/test_paid_amendments.py`: same-project amendments after payout and immutable paid terms.
