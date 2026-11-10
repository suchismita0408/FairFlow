# FairFlow Step 1: comparison and implementation

Updated 2026-11-03. Read with [PLATFORM_STATUS.md](PLATFORM_STATUS.md).

Current status: [HACKATHON_REVIEW.md](HACKATHON_REVIEW.md) supersedes this original specification comparison, including its old email, AI and payment limitations.

> Superseded UX/ownership decisions: the November 3 guided-builder update in PLATFORM_STATUS.md replaces the eleven-step editing experience with four compact steps, enforces role ownership, permits client-only brief invitations, and adds live Gemini conversation/field review with individually applied suggestions. Final signing still requires the full structured validation.

## Comparison with the previous platform

| Specification | Previous project | Step 1 implementation |
|---|---|---|
| Two authenticated portals | Cosmetic portal switch; roles attached to individual projects | Fixed account role, separate signup/login and dashboards, server page/API guards; outsiders receive 404 |
| Authentication | Argon2id, verification/reset, CSRF, 12-hour sessions, IP/email throttling | Reuses those foundations; adds account lockout, 30-minute idle timeout, session list/revocation and auth audits |
| Complete structured agreement | Smaller brief, arbitrary clauses and 25 payment-provider currencies using hundredths | Eleven-section typed draft catalog, repeatable deliverables/dependencies/milestones/attachments, ISO currency and integer minor-unit terms |
| Contradictions | Limited cross-field and conversation checks | Pure required/conditional/contradiction validator, deterministic ambiguity flags, field guidance and bilateral warning reasons |
| Negotiation | Mutable proposals and independent acceptance | Private turn-owned drafts, immutable sent snapshots, parent versions, exact hashes, field decisions, diffs, approvals and signatures |
| Export | Clause records | One deterministic clause renderer, clause-ID map, HTML/JSON/PDF exports, PDF hash footer and extracted-text coverage tests |
| Chat | Project text messages | Append-only thread, field pins, system events, unread counts, SSE plus polling fallback |
| Agreement workspace | Funding/delivery/AI/payment room | Locked agreement, overview, read-only milestones/deliverables, terms, chat, activity and bilateral amendments |
| Later integrations | Existing payment and Gemini engines | Step 1 accounts cannot enter those APIs; signed contract-summary API provides the future integration boundary |

The existing HTML/CSS/JavaScript frontend is retained. FastAPI, Pydantic v2, SQLAlchemy and Alembic remain the server stack. PostgreSQL is the normal local database; isolated SQLite tests exercise the local development fallback. Legacy projects, historical money units and configuration are preserved.

## Source map

- `app/auth.py`: identity/session boundary used by both the existing system and fixed-role portals. A future OIDC adapter can supply the same authenticated actor; deterministic portal and membership checks remain mandatory.
- `app/db.py`, `migrations/versions/0002_step1.py`: profiles, sent-version lineage, field changes, validation snapshots, signatures, chat/read cursors and notifications. Database triggers protect sent agreements and append-only evidence. Existing unclassified accounts are retained with a null role and cannot use Step 1 portals. New generic legacy registration is disabled by default.
- `app/agreement_schema.py`: strict Pydantic draft types and the shared field/section/form catalog. Unknown fields cannot become unsigned, unrendered clauses.
- `app/agreement_validation.py`: `validate(agreement_json, today=...)`; validation has no database, network or AI dependency. The caller supplies the current date in the agreed time zone for send/sign checks.
- `app/agreement_renderer.py`: canonical hash, numbered clauses, source map, amount/date formatting and in-memory PDF. JSON, HTML and PDF are generated from the same sent payload.
- `app/negotiation.py`: role-specific API, turn ownership, revision compare-and-swap, row locks, approvals/signatures, warning snapshots, lifecycle, chat, reminders and export boundary. SQLite reserves a write transaction before reading a mutable project revision.
- `static/portals.*`: responsive portals, light/dark themes, stepper and flags, ten-second autosave, device draft recovery, version history and workspace.
- `scripts/seed_step1.py`: idempotent local demo identities and three labeled projects. No external email or money operations.
- `scripts/verify_step1_postgres.py`: creates and removes only a disposable `_qa_step1_*` PostgreSQL database; runs auth, validation, state, permission, concurrency and browser tests there.

## Decisions resolving conflicting requirements

1. **Initial invitation completeness:** the specification requires all mandatory fields before any send, including fields owned first by a freelancer who has not received the invitation yet. Step 1 enforces the strict completeness rule: the client proposes complete initial terms, including freelancer details, and the freelancer reviews/confirms or changes them after opening. Field ownership is guidance; either party can edit during its turn.
2. **Private counters versus existing consent:** unsent counters do not alter the receiver's sent version or approval. Sending a replacement voids the previous proposed version's consent. Beginning an edit after a partial signature explicitly voids that proposal's signature/approvals immediately. Evidence rows remain immutable and show their voided status through the project audit/version history.
3. **Acknowledgments versus immutable versions:** a warning acknowledgment creates an immutable child snapshot containing the acknowledging role and mandatory reason. Both reasons are part of the final hash. Approvals must then be recorded on that exact current version. Clients cannot inject or edit the other party's acknowledgment through draft JSON.
4. **Amendment consent:** the active signed version is never voided by an amendment draft. The proposed replacement needs its own approvals and signatures; rejection restores the previous active version.

## Verification and limitations

The standard suite includes the isolated Playwright flow. The historical external-server payment browser scenario is opt-in with `FAIRFLOW_LEGACY_BROWSER=1`; it is outside Step 1 and is not claimed as tested here. Its legacy server and registration compatibility must be configured explicitly.

The Playwright flow covers separate registration and email verification, project creation/invitation, a blocked contradictory counter, recovery after idle-session expiry, automatic draft save, both approvals/signatures, workspace, chat, wrong-portal redirect, mobile width and both themes. PDF tests extract the PDF text and compare every numbered clause to the same structured terms, and verify every page's agreement ID/version/hash footer. Migration tests retain existing users, sessions, projects and agreements and check foreign-key integrity.

Remaining external or optional work:

- Real email transport, production deployment and independent security/accessibility review remain open. Local email previews are for local development. Keyboard labels, focus, tied errors, tab controls and responsive layouts are implemented; a full WCAG audit is not claimed.
- Optional Gemini chat suggestions and second-pass ambiguity lint are omitted. The authoritative flow works offline without an AI provider.
- Exclusion overlap is a deterministic normalized keyword heuristic. The specified structured contradictions and vocabulary are tested; arbitrary semantic contradictions in unrestricted prose cannot be guaranteed absent by this heuristic.
- This original Step 1 boundary has since been extended: the signed workspace now supports PayPal Sandbox authorization, delivery links and client-approved merchant capture. See `PLATFORM_STATUS.md` for the current limits. Escrow, seller payout, file uploads/storage, scope-firewall decisions, automated dispute resolution and change-request execution remain open.
- OIDC and migration of ambiguous historical identities into fixed roles require explicit future design; roles are not guessed from names/emails or historical UI preferences.

Implementation references: [Babel currency formatting](https://babel.pocoo.org/en/latest/api/numbers.html), [pycountry ISO datasets](https://github.com/pycountry/pycountry), [ReportLab document generation](https://docs.reportlab.com/reportlab/userguide/ch5_platypus/).
