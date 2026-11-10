# FairFlow â€” AI-assisted Agile payment engine

FairFlow turns freelance conversations into reviewable agreements, splits the scope into 14â€“21 day milestones, and protects work through explicit funding and change-order workflows. This is a functional hackathon prototype with an offline demo and PayPal Sandbox adapters, not a licensed escrow or guaranteed-payment service.

## Run

```powershell
# First setup only, if venv is missing:
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
# Copy .env.example to .env ONLY if you do not already have .env.
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. API explorer: **http://127.0.0.1:8000/docs**. Existing environment secrets and stored projects are preserved. Use one worker; do not expose the unauthenticated demo publicly.

## Implemented Agile v2 features

| Feature | Behavior |
|---|---|
| Chat-to-contract + planning | Gemini structured output or explicit offline rules; editable baseline and milestone scope/acceptance criteria |
| Bounded milestones | 14â€“21 work days per milestone; exact integer-cent total matching the baseline |
| Continuous consent | Hash of the original plan; separately recorded acceptance by both parties; separate saved-method consent |
| Payment method vault | PayPal Wallet setup token, hosted approval, payment-token exchange, server-side token storage and consent revocation |
| Sequential funding | Authorize one milestone at a time; saved method can fund the next accepted milestone after settlement |
| Revision firewall | Compare to stored scope; enforce per-milestone allowance; uncertain decisions open a dispute |
| Paid additions | Generate a proposed micro-milestone at estimated hours x agreed rate; both parties accept before funding |
| Five-day review | Delivery starts a UTC five-day countdown; approval or unchallenged deadline makes capture eligible |
| Late-delivery guard | Reject deliveries that cannot fit five full review days plus a one-day buffer before authorization expiry |
| Delayed sprint | Evidence-backed, client-approved progress; proportional partial capture with final_capture=false; void unused hold; merge unpaid scope/value into next unfunded milestone |
| Day-28 guard | Automatically settle approved progress near expiry; without approval, void and pause instead of inventing a charge |
| Emergency pause | Immediately block work, void confirmed open holds, preserve captured work, and require fresh authorization on resume |
| Red light | Failed/uncertain/expired funding blocks work; future milestones do not silently proceed |
| Disputes | Block full and partial capture; remain open across expiry and emergency pause |
| Recovery | Persist operation IDs before remote calls; retry saved operations after reconciliation; recover local completion after confirmed operations |
| Demo laboratory | Advance project-local clock, decline the next vaulted hold, update simulated billing, replay all edge cases |
| Dashboard | Funding indicators, review clocks, milestone rail, progress evidence, payment journal, audit trail, mobile layout |

The existing v1 projects retain their original 72-hour semantics and routes. New dashboard projects use `/api/agile/...` and five-day milestone reviews. Legacy write endpoints reject v2 projects. Existing paid agreements are not silently migrated.

## Demo script

1. **New project â†’ Use sample conversation â†’ Extract contract.** Review the three milestone prices and scope. Customize the generic offline phase descriptions; Gemini mode can propose semantic phases.
2. Record both parties' acceptance. Check the saved-method consent box â†’ save â†’ simulate PayPal approval.
3. Authorize milestone 1. The work indicator turns green. Only that milestone is funded.
4. Open **Delayed sprint**. Enter 80%, an evidence URL, delivered work and remaining work. Propose it, then record client approval of capture and carry-over.
5. In **Demo laboratory**, choose **Decline next saved-method hold**, then **Advance 28 days**. The engine captures 80%, voids the old balance, carries forward 20%, and shows a red light for the failed next hold.
6. Simulate updated billing â†’ authorize the paused milestone. Work resumes only after confirmation.
7. Enter an emergency reason â†’ **Pause project & void open holds**. Resume to obtain a fresh hold.
8. Request a database backend for the static-site scope. Observe a proposed addition. Record each party's acceptance; no unauthorized charge occurs.
9. Submit a delivery URL â†’ advance five days. The completed milestone is captured and the next agreed milestone is authorized using the saved method.
10. On another project, open a dispute and verify that neither deadline nor partial capture bypasses it.

Demo funds and payment methods are entirely simulated. The current console records the parties' actions through one operator account; it does not authenticate independent client and freelancer identities.

## Stack and why

- **Python / FastAPI:** asynchronous HTTP endpoints and one shared state-machine service for UI and scheduled work.
- **Pydantic:** explicit schemas, 14â€“21 day constraints, exact budget validation, progress and consent validation.
- **HTTPX:** server-side calls to Gemini and PayPal; no credentials in frontend code.
- **SQLite WAL:** durable local project state, operation journal, consent history and webhook inbox. Makes the hackathon easy to run.
- **Asyncio scheduler:** scans persisted state every 15 seconds and catches up after restart. One process only.
- **HTML / CSS / vanilla JavaScript:** retained the existing responsive frontend instead of migrating to Next.js. No Node build, separate frontend server or new deployment surface is needed; the interface uses the same FastAPI APIs a future React/Next.js client would use.
- **Gemini REST structured output:** configurable Gemini fallback chain; defaults to `gemini-3.8-flash`, then `gemini-flash-lite-latest`, then `gemini-flash-latest`. Output is validated; AI does not directly select or execute money movements.
- **PayPal Orders v2 + Payments v2 + Payment Method Tokens v3:** hosted Wallet vaulting, authorizations, full/partial captures and voids. Raw-card collection is deliberately absent.
- **pytest + Playwright:** backend/state-machine/provider payload tests and real browser workflow checks.

## Gemini setup

Set `AI_MODE=gemini`, `GEMINI_API_KEY` and an available `GEMINI_MODEL`; restart. Model access and billing/quota must be configured. Both contract extraction and milestone planning use structured output. A plan whose prices do not sum exactly is rejected. Low-confidence scope decisions go to human review.

No training is required for the prototype. Production needs consented evaluation data, source-grounded extraction, calibrated abstention thresholds and human appeals; see the production roadmap. Offline mode uses transparent keyword rules and generic phases, not model inference or a market-price dataset.

## PayPal Sandbox setup

Set these in `.env`:

```dotenv
FAIRFLOW_MODE=sandbox
AI_MODE=demo
PAYPAL_CLIENT_ID=your-sandbox-client-id
PAYPAL_CLIENT_SECRET=your-sandbox-secret
FAIRFLOW_ADMIN_TOKEN=a-random-secret-of-at-least-24-characters
APP_BASE_URL=http://127.0.0.1:8000
DATABASE_PATH=fairflow-sandbox.db
PAYPAL_VAULT_ENABLED=false
```

Restart, then use the gear icon to set the operator token for the browser tab. Manual per-milestone checkout works without Vault. Open PayPal's approval page as a Sandbox buyer, return, and verify approval in FairFlow.

To test continuous authorizations, have PayPal enable the applicable Vault/merchant-initiated-payment capabilities for your merchant and buyer region. Set `PAYPAL_VAULT_ENABLED=true` only after eligibility is configured. The app creates a Wallet setup token, redirects to hosted consent, verifies APPROVED status server-side, exchanges the setup token, and retains the resulting token server-side. Returning to the app is not proof of consent; the verification step is required. Subsequent orders use the saved token and merchant-initiated metadata. If PayPal requests payer action, the UI presents approval instead of reporting funding success.

Partial captures may need to be enabled on the PayPal account. Actual end-to-end Vault approval, partial capture and void behavior must be exercised with your eligible Sandbox buyer; mocked tests are not proof of merchant eligibility.

For webhooks, expose `/api/webhooks/paypal` through a controlled HTTPS test endpoint, register it in PayPal, and set `PAYPAL_WEBHOOK_ID`. The current webhook endpoint verifies signatures and deduplicates events into an inbox. Automated reconciliation of refunds, reversals and external disputes is still a production requirement.

## Financial boundaries

- PayPal's 29-day authorization period is not a universal legal deadline for all payment gateways. Use provider-specific expiration data. Capture after the three-day honor period remains conditional on risk/fund availability; 21 + 5 days does not guarantee success.
- A saved token authorizes only the agreed use. FairFlow requires separate acceptance of added scope and never charges a model's proposal automatically.
- Pro-rata settlement uses explicit client-approved evidence and remaining scope; an AI completion estimate alone cannot trigger it. Previously captured cents are never carried forward.
- Voiding releases the authorization; issuer balance updates are not guaranteed to be instantaneous. Voiding does not refund captured money.
- Green/amber signals reflect the application's last confirmed authorization and clock checks, not a bank guarantee or real-time issuer balance monitor.
- Sandbox captures go to the configured merchant. Seller onboarding, marketplace disbursement, refunds, legal escrow and insurance/guarantee products are not implemented.
- Reconciliation is required after an uncertain provider response. Reusing operation IDs reduces duplicates but does not replace reconciliation, particularly outside provider idempotency retention windows.

## Verify

```powershell
.\venv\Scripts\python.exe -m pytest -q
node --check static/app.js
node --check static/agile.js
# Optional browser verification, with local demo server running:
.\venv\Scripts\python.exe -m pip install playwright
.\venv\Scripts\python.exe -m playwright install chromium
.\venv\Scripts\python.exe tests/browser_smoke.py
```

Unit tests use temporary databases. The browser scenario creates labeled demo data in the running local app.

## Code map

- `app/agile.py`: milestone transitions, consent, journal, expiry safeguards and v2 endpoints.
- `app/agile_models.py`: plan/progress/consent schemas.
- `app/paypal.py`: original and new provider adapters.
- `app/ai.py`: Gemini structured output and explicit demo rules.
- `main.py`: app boundary, scheduler, legacy compatibility and webhook inbox.
- `app/store.py`: SQLite persistence.
- `static/agile.js`: new milestone interface; `static/app.js`: shared dashboard and legacy view.
- `tests/test_agile.py`: milestone lifecycle, edge-case and recovery tests.

See [Agile architecture and launch gates](docs/AGILE_ENGINE.md), [production/data/training roadmap](docs/ROADMAP.md), and [verification report](docs/VERIFICATION.md).


## Gemini fallback configuration

```dotenv
GEMINI_MODEL=gemini-3.8-flash
GEMINI_FALLBACK_MODELS=gemini-flash-lite-latest,gemini-flash-latest
GEMINI_ATTEMPT_TIMEOUT_SECONDS=12
GEMINI_TOTAL_TIMEOUT_SECONDS=40
```

Existing `.env` settings are preserved; missing fallback settings were added. Set `AI_MODE=gemini` and restart to use real inference. The running demonstration remains explicitly offline. An empty `GEMINI_FALLBACK_MODELS=` disables fallbacks. Duplicates are removed; at most four distinct configured model names are allowed. Alias names may resolve to the same underlying model and can change over time; pin evaluated versions for deployment.

Each structured generation has a 12-second per-attempt limit and a 40-second total budget by default, including bounded exponential waits. Contract extraction and milestone planning are separate calls, so creating an AI plan can consume up to two budgets. HTTP 404/408/429/5xx, transport failures and invalid structured output can move to the next model. Retry-After is respected; if the requested cooldown exceeds the remaining budget, the request stops. Bad requests, authentication/permission errors and safety-blocked output stop immediately. This is not quota circumvention or safety-filter bypass.

Every successful response must pass the same Pydantic schema. Failed generation never returns offline demo rules as if they were AI output. Server logs record model names and outcomes, not prompts, raw provider messages or API keys. Exhausted requests report a sanitized attempt summary. The last call's successful model and attempt trace are available internally through `app.ai.request_trace`.

Fallbacks improve resilience to a single unavailable model; they cannot repair a shared network outage, revoked key, exhausted project quota, or incorrect model reasoning. See [remaining issues and contradictions](docs/ISSUES.md).
