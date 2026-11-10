# Verification report

## Current portal audit — 2026-11-05

Updated suite: **357 passed, 3 skipped**. Final disposable PostgreSQL run: **190 passed**, including Playwright settlement history and concurrent manual/automatic capture. Focused ledger/payout/automation tests after correcting PostgreSQL lock ordering: **21 passed**. JavaScript syntax passed. See [FEATURE_AUDIT.md](FEATURE_AUDIT.md) for changes, remaining gaps and verification limits. All provider payment calls in this audit were mocked.

## Live Gemini activation - 2026-11-02

Gemini API mode is enabled. Live extraction accepted clear terms and rejected the exact screenshot objection and an indirect rejection. Live full-document review accepted a consistent agreement and a focused ownership/license conflict review requested clarification. The USD browser workflow passed with real Gemini calls and simulated payments. Provider timeouts were also observed and failed visibly without offline substitution.

The latest automated coverage comprises 162 tests, including semantic review, exact-content cache invalidation and outage write-blocking. See [GEMINI_STATUS.md](GEMINI_STATUS.md) for configuration, evidence and limits. Older offline-only reports below are historical.


## Non-numeric price objections - 2026-11-02

154 tests passed. The screenshot phrase "Agreed not. i will pay less." now blocks extraction and is flagged on existing stored versions. Tests cover eight objection variants in both provider modes, plus a valid "agreed not to include a database" negative control. The browser regression confirms the exact objection hides the extracted terms and displays clarification feedback. One affected stored version was verified read-only; no saved user terms were changed. The builder now explicitly identifies offline demo extraction. This remains a targeted English-language check, not general semantic verification.


## Agreement completeness and cross-field validation - 2026-11-02

137 tests passed. The full suite includes mandatory policy-field tests and a 23-case invalid-field/contradiction matrix. USD and JPY browser flows passed after the upgrade. The agreement browser regression passed source conflicts, missing policy fields, changes during final validation, extraction invalidation and corrected terms. USD browser coverage additionally verified the downloaded agreement includes the version, digest, ownership terms, priced items and acceptance records. JavaScript syntax passed. One pre-existing Starlette TestClient deprecation warning remains.

See [AGREEMENT_REVIEW.md](AGREEMENT_REVIEW.md) for comparison sources, exact coverage and limits. Tests do not establish legal enforceability or exhaustive natural-language contradiction detection.


## Negotiation validation regression - 2026-11-02

103 automated tests passed. The reported $900 / 800 dollars / 850$ conversation is rejected before extraction in demo and Gemini modes. Direct project creation and revision also reject contradictory source text, and existing affected agreement versions cannot be accepted or funded. Read-time warnings preserve original records; no stored user price was changed.

A browser regression verifies persistent conflict feedback, successful extraction of a clarified $850 summary, invalidation after editing the conversation or milestone count, and discarding stale extraction responses. The separate-account USD browser workflow also passed after these changes.

The source conversation is now included in new agreement versions. These conservative checks flag conflicting English monetary/revision terms and selected rejection phrases; they do not prove natural-language agreement or resolve arbitrary negotiations. Historical conflicting offers require a clear current-terms summary. Participants still review and accept the actual version independently.

Run `python tests/agreement_browser_smoke.py` against the local demo server to reproduce the browser regression. This creates a labeled QA account.


## Authenticated platform update - 2026-11-02

The resumed implementation passed **88 automated tests**, JavaScript syntax validation, USD and JPY browser workflows with separate accounts, and a PostgreSQL backup/isolated-restore check. Browser coverage includes preserving priced items when revising terms, independent consent, simulated funding/delivery/capture, matching ledger currencies, all tabs and 390px mobile width. No browser JavaScript errors occurred.

The restored snapshot contained 6 users, 6 projects, 4 agreement versions and 18 ledger entries. All project/currency ledger groups balanced. Migration version was `0001_platform`. The temporary restore database was removed; application data and the backup were retained.

The synthetic evaluation remains 8 exact labels out of 12 in offline mode. Real Sandbox buyer approval and marketplace payouts were not verified. See [PLATFORM_STATUS.md](PLATFORM_STATUS.md) for current features and outstanding external requirements. Reports below describe earlier implementation stages.

## Historical Agile v2 verification

Verified locally on 2026-11-02. Earlier v1 results are retained below as historical integration evidence.

## Current verification

| Check | Result |
|---|---|
| Backend lifecycle, recovery and mocked-provider tests | 52 passed |
| New and shared JavaScript syntax | Passed |
| Python modules | Compile successfully |
| Browser plan creation and two-party acceptance | Passed |
| Browser saved-method consent and simulated approval | Passed |
| 80% partial capture / 20% carry-over | Passed, including unpaid-balance arithmetic |
| Declined next hold and red-light work block | Passed |
| Updated billing and explicit retry | Passed |
| Emergency void and fresh authorization on resume | Passed |
| Separate consent for an out-of-scope addition | Passed |
| Five-day capture and sequential next funding | Passed |
| Desktop and 390px mobile browser flow | Passed; mobile document width exactly 390px |
| Browser JavaScript errors during successful smoke run | None |
| Uncertain capture and void recovery | Tested: stable operation IDs; no duplicate partial capture |
| Restart after confirmed capture/void | Tested: local completion and carry-over recovered once |
| Dispute survives expiry and emergency pause | Tested |
| Sandbox demo-clock restrictions and token redaction | Tested |
| PayPal Vault / merchant-initiated / partial-capture / void adapters | Request/response contract tests passed with mocked provider |
| Real Sandbox buyer-approved Vault + partial capture + void | Not exercised; merchant eligibility and buyer approval required |
| Requested Gemini 3.8 model metadata | HTTP 200 with existing credentials; generation result recorded separately below |
| Docker deployment | Supplied configuration, not deployed or container-tested |

The existing Uvicorn reload server serves http://127.0.0.1:8000. It reports `review_hours: 120`, milestone bounds `[14, 21]`, and demo payment/AI modes. Existing `.env` secrets were preserved. No real payment was taken. Browser tests left clearly labeled synthetic Agile projects in the local database, alongside the original v1 demo project.

A first browser pass found mobile horizontal overflow in the timeline's grid container. The grid minimum-width rules were corrected; the entire browser workflow was rerun successfully. The earlier failed pass also created a labeled demo record, intentionally retained rather than deleting workspace data.

The installed FastAPI/Starlette version emits one TestClient HTTPX deprecation warning. It does not affect test results.

## Screenshots

- `agile-overview.png`: selected v2 project in desktop workspace.
- `agile-milestones.png`: saved-method status and milestone timeline.
- `agile-desktop.png`: milestone review and payment workflow.
- `agile-red-light.png`: failed funding after partial settlement.
- `agile-mobile.png`: full mobile workflow.
- `agile-mobile-overview.png`: 390px overview.

## Reproduce

```powershell
.\venv\Scripts\python.exe -m pytest -q
node --check static/app.js
node --check static/agile.js
# With local DEMO server running and Playwright installed:
.\venv\Scripts\python.exe tests/browser_smoke.py
```

Unit tests use temporary databases. The browser test creates a new labeled demo project.

## Historical v1 evidence (2026-11-01)

PayPal Sandbox OAuth returned HTTP 200 and a $1 Sandbox order was created with an approval link; no buyer approval/capture was performed. Gemini model discovery returned HTTP 200, but generation probes returned a model 404 or timed out. Signature verification and webhook deduplication passed mocked tests; no external webhook was delivered. The v1 desktop/mobile lifecycle passed with a simulated 72-hour review. Original projects retain those legacy terms.

## Gemini generation probe (2026-11-02)

`gemini-3.8-flash` model metadata returned HTTP 200. A real structured extraction request using that model timed out at the adapter's 45-second limit. Its subsequent revision-classification probe was therefore not executed. Gemini generation is not claimed as verified. The running app remains in explicit offline AI demo mode; configured Gemini mode fails visibly and never silently substitutes offline output.

## Gemini fallback update (2026-11-02)

The suite now contains **72 passing tests** (52 previous tests plus 20 fallback tests). Coverage includes timeout/network failover, 404/408/429/5xx handling, bounded total deadline, Retry-After, invalid JSON/schema fallback, model de-duplication, opt-out, credential errors and refusal to bypass safety blocks.

A real generation test succeeded with the existing credentials:

1. `gemini-3.8-flash`: timeout/network failure under the 12-second attempt limit.
2. `gemini-flash-lite-latest`: successful structured extraction, validated with Pydantic; returned $900 (90000 cents) and two revisions.
3. `gemini-flash-latest`: not needed in this successful request.

This supersedes the earlier statement that no Gemini generation had been verified. It proves one extraction/fallback path, not broad semantic accuracy or full model reliability. The running application still reports `AI_MODE=demo`; the real test invoked the adapter directly with a synthetic conversation. Set `AI_MODE=gemini` and restart to use the chain in the UI. Existing payment mode was not changed.

Default configuration: primary `gemini-3.8-flash`; fallbacks `gemini-flash-lite-latest,gemini-flash-latest`; 12 seconds per attempt; 40 seconds per structured call. Missing fallback settings were added to `.env`; existing settings and secrets were preserved. Python compilation and the running health endpoint were checked after the changes. See `ISSUES.md` for remaining gaps and contradictions.
