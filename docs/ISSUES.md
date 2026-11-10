# Current issues and product contradictions

> Current authenticated-platform status and remaining work are in [PLATFORM_STATUS.md](PLATFORM_STATUS.md). The older architecture and issue list below are retained as historical context.

Reviewed 2026-11-02 while adding Gemini fallbacks. These are outstanding limitations, not claims that they have all been fixed.

## Concrete implementation gaps

| Priority | Finding | Impact and next step |
|---|---|---|
| P0 before public access | Single operator console; party acceptance is operator-recorded | A logged-in operator can act for either party. Add independent identities, project RBAC and verified consent before real client use. |
| P0 before money | PayPal Vault/partial capture/void verified through mocks, not an eligible buyer flow | Test actual sandbox approval, merchant-initiated authorization, payer action, partial capture, void and funding declines. Provider/product access cannot be inferred from OAuth success. |
| P0 before money | Webhooks are a verified inbox, not a settlement reconciler | External reversals, refunds, revoked authorizations and disputes do not automatically update the financial ledger/work indicator. Implement provider-state retrieval and idempotent reconciliation. |
| P0 before money | Capturing to one merchant is not paying a freelancer | Seller onboarding, marketplace disbursement, fees and payout reconciliation remain unimplemented. |
| P1 | Shared process lock is held across Gemini and payment network calls | A slow request blocks other project mutations and the scheduled scanner. Use per-project/version checks, release locks around inference, and revalidate before committing; use durable job claims and transactional outbox for money. |
| P1 | SQLite JSON records and process-local locking | One process only. No production multi-worker concurrency, relational ledger or distributed recovery guarantee. Migrate to PostgreSQL and durable workers. |
| P1 | Tokens are redacted from APIs but stored in the local database | Use encrypted token storage, restricted service identity, secret management and deletion/retention controls in production. |
| P1 | Demo extraction takes the last dollar amount | A conversation containing a project price followed by an hourly rate can be misparsed. Review all draft terms; replace the demo parser with explicit rate/price parsing or require manual entry for ambiguity. Gemini mode uses a separate structured extraction path. |
| P1 | Demo scope classification is keyword-based | A backend request can be flagged as new work even when the contract includes a backend. Demo mode must not decide real charges. Evaluate real classification against agreed clauses, with human appeals. |
| P1 | Model confidence is self-reported, not calibrated | The current threshold is a demo heuristic. Use adjudicated evaluation data to measure false billable decisions and tune abstention. |
| P1 | Generation fallback success is not proof of semantic correctness | All models can output schema-valid but wrong scope, rates or amounts. Human review, exact budget checks, provenance and per-model evaluation remain necessary. |
| P2 | Latest aliases can point to the same backend/version | Fallback names do not guarantee provider diversity. Pin evaluated model versions when deploying. |
| P2 | AI plan generation has two separate generation budgets | At default settings, extraction plus planning can take about 80 seconds. Move long-running planning to a job with progress/status if latency is unacceptable. |

## Contradictions in the original pitch

1. **“Exactly 29 days by law”** is not a universal payment rule. The adapter follows PayPal's provider window and returned expiry; other products/providers differ.
2. **“21 + 5 days guarantees payment”** is false. This fits the nominal window, but extends beyond PayPal's three-day honor period. Captures can still fail or be reversed. Milestones reduce exposure; they do not insure payment.
3. **“Vault is a skeleton key”** is false. Saved methods require scoped consent and merchant eligibility; new charges can require payer action or decline. New scope still requires agreement.
4. **“AI knows 80% is complete”** confuses an estimate with accepted delivery. Pro-rata billing needs agreed evidence and allocation. If a client ghosts before approving partial progress, the engine cannot fairly invent that approval; it pauses/voids rather than charging the proposed percentage.
5. **“Void instantly returns money”** conflates cancellation of a hold with immediate issuer balance updates. A void also does not refund a captured payment.
6. **“Pydantic makes a binding contract”** confuses schema validation with legal enforceability. Identity, actual consent, terms and applicable law remain separate requirements.
7. **“Fair-market price”** is unsupported by the current estimator. It uses estimated hours times an agreed rate, not a verified market dataset.
8. **“No code is ever written without backing”** is an overstatement. The app can block its own workflow and display last-confirmed authorization status; it cannot control external development tools or guarantee the issuer will honor a future capture.

## Recommended next order

First fix independent identities/consent and demo-parser ambiguity. Then verify an actual eligible PayPal buyer flow and build reconciliation. Next replace global lock/SQLite assumptions with transactional financial state and durable jobs. Finally benchmark model outputs across contract types, languages and adversarial requests before considering automated billing decisions.

Source references:
- https://developer.paypal.com/payment-methods/auth-honor/
- https://developer.paypal.com/v5/checkout/recurring-payments
- https://ai.google.dev/gemini-api/docs/troubleshooting
- https://ai.google.dev/gemini-api/docs/models
