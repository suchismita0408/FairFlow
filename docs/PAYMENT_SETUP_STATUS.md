# Payment and verification status — 2026-11-05

## Active India merchant removed — 2026-11-06

At the user's request, active `.env` checkout/capture credentials were replaced with the verified US credentials from `.env.paypal-us`. Payouts use that same US app. The old webhook ID was cleared for future US-app configuration. India credentials are no longer active in the app. No PayPal account was deleted.

The two existing India payment records were retained and marked `merchant_retired`; provider actions and worker scans cannot retry them under the US merchant. Newly created payments record their merchant fingerprint, and account mismatches are rejected. Historical capture/payout evidence and ledger records remain intact.

The existing USD 5,000 capture is still in the old India merchant account (PayPal reported net USD 4,769.55 after its USD 230.45 fee). Its failed payout has not been completed or refunded by this credential change. Manage these historical funds in that account's PayPal dashboard. Do not delete that PayPal account before resolving its balance. A fresh buyer-approved checkout is required to prove the complete flow under the US merchant.

## US sender test — SUCCESS

The US Sandbox app credentials supplied in `.env.paypal-us` authenticated and submitted the original USD 10 payout. PayPal then verified the matching freelancer item as `SUCCESS`: transaction `3RS51112RC6865832`, item `MP6VYSVAERNGS`. The local payout is now `paid`, with balanced receipt ledger postings. This supersedes the earlier blocked results below.

Payout API requests now use the separate US sender credentials, while capture requests retain the original `.env` account. This test used the US business account's Sandbox balance; the original capture remains in the original merchant account. Each new/recovered payout records its sender fingerprint, and mismatched sender credentials block submission/reconciliation. Secrets are not logged or committed. A fresh complete buyer-authorization-to-payout run under one US merchant is still a separate verification task; this successful test verifies the prior capture and the separate freelancer payout.

## Latest checkout fix

Removed the global payout-error gate from client checkout and its button. Valid signed agreements can create a PayPal authorization order even when a prior payout has a setup error. The warning remains visible, and capture/release waits for sender eligibility so additional funds are not stranded in the merchant account. Invalid schedules still require a new bilateral agreement.

Sandbox recheck on resumption confirmed the original capture is `COMPLETED`. Recovery with the original payout reference again returned `PAYOUT_NOT_AVAILABLE`, reference `ca55c7c2f7d36`. Actual freelancer receipt is still unverified. No second milestone authorization or buyer approval was fabricated. Blank email settings remain available for later configuration.

## Completed in code

- Captured milestone approval requests a separate payout of the full signed amount to the confirmed freelancer recipient.
- A pending payout batch is not shown as received. Exact item reference, recipient, currency, amount and `SUCCESS` are required.
- Checkout requires and freezes the recipient. Client capture cannot silently redirect that recipient.
- Interrupted payouts can be recovered using the original intent, sender batch ID and request ID within 29 days. No recapture or replacement transfer is created. Duplicate-batch links are restricted to PayPal Sandbox batch resources.
- Provider error names and safe debug references are visible, including setup guidance. Known Payouts eligibility errors stop new checkout and capture.
- Signing requires explicit acknowledgment of a visible 29-day schedule. The server reserves every included revision and its fresh review, not just one turnaround. Invalid schedules cannot be signed. Dates are checked again when funding and delivering.
- Gross settlement ledger/history and concurrent approval locking are implemented.

## Actual provider test: not completed

The existing first milestone has a USD 10 completed capture. Its original payout recovery was attempted against PayPal Sandbox, without changing the recipient or amount. PayPal returned `PAYOUT_NOT_AVAILABLE`; debug reference `f319574de3226`. The payout remains in attention, with no verified freelancer receipt. Credentials and a successful client capture do not establish that the sender can use Payouts.

Check the **business sender account** in PayPal Developer Dashboard → Testing Tools → Sandbox Accounts, and its app's Payouts capability. The account's registered country is separate from USD currency. PayPal's eligibility restrictions must be resolved by the account owner/PayPal. Keep sufficient Sandbox business balance for the transfer and fees. Do not replace credentials for an existing capture blindly: another merchant may not be able to read or reconcile it.

After fixing the existing sender, use **Recover original freelancer payout**, then **Check payout status**. Completion requires the saved recipient's exact payout item to return `SUCCESS`. If changing to a different eligible Sandbox merchant is necessary, preserve this captured record for reconciliation and use a fresh agreed buyer-approved test under the new merchant.

The second milestone **Autopay — USD 10** has no checkout, authorization or capture. Its current signed agreement has an incompatible review schedule. It needs a valid bilateral agreement and buyer-approved authorization before delivery/capture/payout can be tested. Funded agreements cannot be silently edited; payment-aware amendments remain unfinished. No second milestone payment was submitted during this work.

## Real email verification: prepared, not activated

The user has no email provider yet and requested blank configuration slots. `.env` has SMTP settings reserved; `EMAIL_MODE=local` remains active. Real transport, TLS, encrypted queue and mailbox re-verification are implemented. Sender/domain configuration and real inbox testing remain top-priority external steps. See EMAIL_SETUP.md.

## Provider references

- [Authorization and honor period](https://developer.paypal.com/payment-methods/auth-honor/): 29-day authorization and shorter 3-day honor period; reauthorization does not extend the original lifetime.
- [Payout API and status checks](https://developer.paypal.com/api/payouts): sender setup, funded balance and individual item status.
- [Duplicate payout protection](https://developer.paypal.com/docs/payouts/standard/integrate-api/customize/): reuse original sender batch ID; duplicate protection is limited to 30 days. FairFlow conservatively stops recovery at 29 days.
- [Sandbox account management](https://developer.paypal.com/sandbox-testing/accounts): account type and country are chosen separately from transaction currency.

PayPal Sandbox transfers are test money. Actual live-money payouts are not enabled or claimed.
