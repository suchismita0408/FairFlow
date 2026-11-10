# Automatic milestone release, withdrawal, deletion and chat notifications

Subsequent deadline, settlement, handover, support and evidence functionality is documented in [Resolution workflows](RESOLUTION_WORKFLOWS.md).

This update supersedes the previous optional automatic-release flow for new agreements.

Latest amendment behavior and scenario-by-scenario boundaries: [SCENARIO_SUPPORT_AUDIT.md](SCENARIO_SUPPORT_AUDIT.md). Verified paid milestones can now remain locked while future unfunded work is amended within the same project; unsettled payment attempts still block that amendment flow.

- New milestones default to automatic acceptance after their agreed review period. The editor does not offer an opt-out. Both signatures require automatic-release terms on every milestone, and new funding independently checks the signed terms.
- Authorization alone never charges for undelivered work. Delivery starts the signed review period. When it expires without an objection, the worker requests capture and the separate payout. Revisions and disputes still pause release; expiry and provider failures remain visible. There is no second client approval button or usable manual approval API.
- Existing signed versions and funding consent are never rewritten. Historical agreements without automatic release need newly approved/signed terms before new funding. Existing historical transactions retain their original records and recovery limits.
- Freelancer withdrawal requires a reason. It is posted in project Chat and sent as a client notification. Before signing, the negotiation closes but chat remains available. After signing, new funding, new authorizations and first-time deliveries stop; previously submitted work retains its review, revision, dispute and automatic-release rules. The client can acknowledge the withdrawal in the workspace. Acknowledgment does not execute a refund, void or partial settlement. Unsubmitted authorizations need reconciliation or expiry; they are not silently captured.
- Client deletion of an unsent draft preserves it as an archived record. For shared/signed projects, deletion removes the project only from the client's normal list. Deleted projects can be viewed and restored. The freelancer keeps access and receives a notification. Agreements, chat, worker scans and existing payments remain intact.
- New chat messages create an unread in-app notification only for the counterpart. Consecutive unread messages in one project update the same notification rather than flooding the list. Reading Chat clears its chat notification. The sidebar badge polls while the authenticated app is open, and a new-message toast directs the user to Notifications. Notification links open the corresponding project Chat directly.
- Real email and browser push are not enabled by this update. Existing local email preview remains the configured mail channel. An app that is closed cannot display the in-app badge/toast.

Verification uses disposable databases and mocked PayPal adapters. No existing project was withdrawn/deleted and no new external payment was submitted during these checks.

Verification: 209 non-browser checks passed against disposable PostgreSQL, plus both browser workflows passed after updating the test-only review-timeout request to send its CSRF token. Browser coverage includes notification-to-chat navigation, withdrawal/acknowledgment, delete/restore, and automatic review-timeout capture/payout. Local smoke checks confirmed the restarted app, authenticated notification boundary, healthy worker jobs and unchanged historical payment records. The existing Starlette TestClient deprecation warning remains.
