# Live Gemini configuration and semantic review

Updated 2026-11-02. The running app now explicitly uses `AI_MODE=gemini`; payments remain in demo mode. The saved API key was already present, but AI_MODE had been unset, so earlier runs selected offline demo rules. That configuration oversight is corrected.

## Configuration

- Primary: `gemini-flash-lite-latest`, verified through live generation.
- Fallbacks: `gemini-flash-latest`, then `gemini-3.8-flash`.
- 25 seconds per attempt and 75 seconds total per structured call. Extraction and planning are separate calls, so a complete draft can take longer than one call's budget.
- The prior primary timed out during a live probe; the working fallback was promoted. Model aliases can change and are not a reliability guarantee.
- Existing credentials and payment mode were preserved. New local setup selects Gemini when an API key is present unless a mode was already selected explicitly. Demo mode remains an explicit development/testing option. Invalid AI_MODE values stop startup.

## Behavior

1. In Gemini mode, the full negotiation is sent to a structured semantic extraction review. It interprets negation, counteroffers, conditional responses and rejection; unclear cases return questions and no contract. Historical offers can be resolved by later clear confirmation instead of being rejected solely for containing multiple amounts.
2. Deterministic checks still enforce required fields, monetary arithmetic, currency precision, configured policies and explicit source/field numeric mismatches. Language checks in demo mode remain conservative.
3. Complete agreement validation runs a second Gemini review over source, clauses, all policy terms, milestone/item scope and financial fields. Contradictions or material uncertainty block progress.
4. Creation and revision repeat the semantic check on the server. Acceptance checks the current immutable version outside the project lock and rechecks the version/digest before committing. New funding also requires a successful semantic review.
5. Successful reviews are cached in the audit log for the exact content fingerprint, with a 24-hour expiry and prompt-version marker. Edits change the fingerprint. Cached output cannot approve modified terms. Failed or uncertain reviews are not stored as successful reviews.
6. Network/provider errors, invalid output and safety blocks remain visible. Gemini mode never substitutes offline draft or review results. Model output remains advisory; participants independently accept the exact terms and model output cannot authorize a payment.

The interface identifies Gemini API mode. Final validation displays the model that produced its successful semantic review. Refresh the browser after restarting the server.

## Verification

Live synthetic probes verified:

- Clear scope, $900 total, 2 revisions and a $50 hourly rate produced the expected $900 contract.
- An indirect objection (a price beyond what the client can stretch to) requested clarification.
- The exact screenshot text, "Agreed not. i will pay less.", requested clarification through a real Gemini API call.
- A consistent complete agreement received a ready result.
- Conflicting ownership and licensing terms requested clarification with references to both policy fields.
- The separate-account USD browser workflow passed with real Gemini extraction, planning and semantic review, including revised agreements, acceptance, simulated funding/delivery/capture, ledger, export and mobile width.

A full-document conflict probe also encountered provider timeouts. The request failed with HTTP 502 and no offline substitution. A subsequent focused live conflict probe succeeded. These examples demonstrate integration behavior, not a general model accuracy score.

Automated tests cover unclear extraction, provider outage behavior, malformed semantic review status, unknown top-level field references, resolved negotiation routing, exact-content fingerprints, cache invalidation and server-side write blocking during outage.

Reproduce the browser workflow with explicit provider-call opt-in:

```powershell
python tests/platform_browser_smoke.py --currency USD --gemini
```

The older agreement-browser regression is designed for offline-mode behavior. Use explicit demo mode to run that offline fixture; do not silently change the running application's mode merely to make a test pass.

Gemini can still miss or invent a conflict. Structured output constrains the response format, not semantic correctness. A held-out, independently labeled evaluation dataset and human consent remain necessary. See [Google structured-output documentation](https://ai.google.dev/gemini-api/docs/structured-output).
