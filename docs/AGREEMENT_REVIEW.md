# Agreement review and implementation

Updated 2026-11-02. These are product requirements and implementation findings, not a jurisdiction-specific legal opinion.

## Comparison with established platforms

| Reference | Relevant practice | FairFlow implementation |
|---|---|---|
| [Upwork contract guide](https://www.upwork.com/resources/how-to-make-freelance-contract) and [optional service terms](https://www.upwork.com/legal#optional-service-contract-terms) | Written scope, payment terms, ownership and termination provisions | Clause-based scope, priced milestones, added ownership, cancellation, confidentiality, responsibilities and legal-terms fields |
| [Fiverr terms](https://www.fiverr.com/legal-portal/legal-terms/terms-of-service) | Requirements, delivery/revision processes and ownership terms | Explicit acceptance criteria, separate defect/revision policy, handoff and third-party material terms |
| [Fiverr cancellation guidance](https://help.fiverr.com/hc/en-us/articles/37552956376977-How-cancellations-work-for-clients) | Clear briefs, communication, revisions, cancellation and partial refunds | Recorded dependencies, cancellation terms, messages, evidence and jointly approved partial settlements/refunds |

These platforms have their own infrastructure and dispute mechanisms. FairFlow retains the user's chosen joint-only dispute policy; it does not implement their support adjudication, escrow service or guarantees. The Upwork guide/legal pages returned HTTP 403 on direct fetch; their official indexed descriptions were available. Fiverr pages were read directly. No platform terms were copied as a contract template.

## What changed

The builder now collects contracting names, dependencies and missing-input handling, handoff files/formats/access, ownership/licensing, pre-existing and third-party materials, confidentiality, cancellation/notice/earned fees, expenses/taxes/provider fees, governing law/jurisdiction and responsibilities/warranty/liability terms.

All these fields are mandatory. Their presence does not verify identity, legal enforceability or suitability. New forms start with empty policy terms. The opt-in **Use complete demo example** button supplies clearly identified synthetic example terms, including an illustrative jurisdiction. Users must replace these for actual contracts. No actual business-registration country is inferred.

Platform constraints are explicit and immutable within a proposal: five calendar days of delivery review, calendar-day milestone durations starting from confirmed authorization, revision allowances per milestone, and joint dispute resolution. Fixed calendar delivery dates and suspension for missing client inputs are not separately automated; record and mutually agree a schedule change when needed. Cancellation text records an agreement; it does not introduce a new automatic cancellation/refund engine.

The agreement display now includes policy terms, milestone schedules and priced acceptance items alongside scope/clauses. These fields are included in the immutable version digest. Altering terms requires a new version and independent acceptances. Saving revised terms cannot replace project participants or roles. An acceptance record shows each role, account reference and timestamp. A downloadable text record includes the version digest, clauses, policies, milestone/item prices and acceptance records.

## Blocking validation

A final **Validate complete agreement** action checks the whole document and lists errors. Submission repeats this check and the server independently validates creation/revision. Acceptance and new funding recheck stored documents. Changes during an in-flight validation invalidate its result.

Covered cases include:

- Missing required fields, whitespace, placeholders, empty deliverable/exclusion entries, unsupported policy values and unknown policy fields.
- Source-versus-entered USD total/hourly rate/revision count; conflicting offers and rejection phrases; prefix/suffix dollar formats and punctuation.
- Milestone amounts versus project total, priced items versus milestone amounts, missing item groups, positive integer amounts, precision/currency rules and milestone duration bounds.
- Identical included/excluded deliverables, duplicated deliverables/exclusions or milestone titles, identical contracting names.
- Numeric revision clauses versus baseline, unlimited revisions, selected defect clauses that charge revision allowance, and review windows that differ from five days/120 hours.
- Same-account participation, stale agreement digests, revised role/counterparty changes, unaccepted versions, legacy incomplete agreements and funded-baseline edits.
- Conversation/count changes after extraction, extraction responses for stale input, and document edits during final validation.

Text checks are conservative English rules. They do not establish arbitrary semantic equivalence or find every possible contradiction in natural language. For example, indirect promises, subtle IP/license conflicts, implied deadlines, translations and cross-currency interpretation still require review. Non-USD entered amounts retain the existing explicit-entry workflow; there is no automatic provider FX quote or interpretation of foreign-currency conversation amounts. Do not advertise a successful validation as legal or semantic certification.

Existing records are preserved. Incomplete older versions display clarification errors and cannot gain new acceptance/funding. Unfunded records can be revised to add missing terms. Funded records remain immutable; resolving a missing-term case for those records requires a properly designed re-consent/amendment workflow, rather than changing signed history.

## Verification

The automated contradiction matrix covers each of the 11 mandatory policy-text fields and 23 invalid/mismatched configurations. It tests both validation reports and write rejection. Browser regressions check the actual form, source-price changes, mandatory governing-law text, stale responses and successful correction. Separate-account browser workflows cover revision, acceptance, simulated funding, delivery, capture, ledger and mobile width.

Run:

```powershell
python -m pytest -q
python tests/agreement_browser_smoke.py
python tests/platform_browser_smoke.py --currency USD
python tests/platform_browser_smoke.py --currency JPY
```

No finite test suite covers every possible conversation or legal interpretation. Current limitations must remain visible during a real-world pilot.

## Steps toward an actual service

1. Choose the actual business country and initial client/freelancer markets. Have a qualified professional review a template for those jurisdictions, including consumer rights, IP/licenses, tax, cancellation and dispute/deadlock terms. The current no-override policy can leave disputes unresolved indefinitely.
2. Obtain the appropriate PayPal marketplace/platform eligibility and implement seller onboarding, disbursement/payouts, provider eligibility checks and actual FX quote presentation where applicable. Complete buyer-approved Sandbox authorize/capture/partial capture/void/refund tests before enabling live money. See [PayPal marketplace docs](https://developer.paypal.com/docs/multiparty/).
3. Implement real email delivery/receipt tracking, verified delivery evidence, secure file storage and access, malware checks, retention/deletion and privacy controls. Local email previews and example URLs are development conveniences.
4. Evaluate Gemini with independently labeled actual project cases, multilingual/adversarial examples, and per-model results. Keep uncertain recommendations subject to participant review; add a broader semantic consistency audit before claiming automated document review.
5. Finish provider-event reconciliation for external refunds/reversals/disputes; validate ledger invariants, concurrent edits, uncertain results and crash recovery. Complete security review, dependency scanning, monitoring, alerts and restore exercises.
6. Deploy a restricted pilot with HTTPS, secure sessions, secret management, a production email sender, operations/support procedures and measurable limits. Production/live modes currently remain deliberately disabled.

Inputs needed later: actual business location, launch countries, payment-provider eligibility, a reviewed agreement template, sender domain/email provider, hosting preferences and consented evaluation examples. Synthetic sample terms are enough to exercise the local hackathon workflow; they are not a production legal choice.
