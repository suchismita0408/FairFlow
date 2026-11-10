# FairFlow

### Clear agreements. Evidence-based reviews. Traceable payments.

FairFlow is a shared workspace for clients and freelancers that connects **what was agreed, what was delivered, and when payment should be released**. It combines versioned agreements, PayPal Sandbox milestone funding, delivery evidence, structured resolution workflows and **Ask Flow**, a project-aware AI assistant.

The goal is to make freelance collaboration fairer for both sides: give freelancers stronger confidence before starting work, help clients assess delivery before approving release, and give both participants a clear record when expectations change.

## The problem

Freelance disagreements often start before a payment fails. Important promises are scattered across messages, deliverables are vague, and each person interprets ?finished? differently. A client may assume a feature is included while a freelancer sees additional work. When a project goes wrong, reconstructing the agreement and deciding what was delivered becomes difficult.

FairFlow addresses these problems as one connected workflow:

| Problem in freelance collaboration | FairFlow's approach | Value for both sides |
| --- | --- | --- |
| Unclear scope and conflicting promises | Structured deliverables, acceptance criteria, milestones, exclusions and agreement checks | A clearer definition of completion before work starts |
| Starting work without verified funding | Upfront PayPal Sandbox collection with requested delayed seller disbursement; provider verification before delivery | Stronger funding evidence for freelancers and a defined client review stage |
| Endless unpaid additions | Paid change requests with explicit bilateral acceptance | Extra work has its own agreed scope, price and schedule |
| Unclear revision expectations | Recorded revision allowances and resubmission history | A shared record of requested corrections and included revisions |
| Clients forgetting terms across projects | Ask Flow answers from the current project's agreement, accepted changes and payment records | Prices, requirements and deadlines are easier to retrieve |
| Difficulty reviewing technical deliveries | AI comparison of milestone criteria with submitted links, documents and screenshots | Concrete findings and visible evidence support human review |
| Disputes based on competing claims | Shared cases, responses, preserved evidence and mutually accepted resolution proposals | A structured route to discuss cancellation, partial work and settlement |
| Silent clients or missed deadlines | Agreed review periods, reminders and eligible automatic release requests | Both participants know the next action and timing |
| Confusing payment status | Separate collection, release request and verified release records | A checkout return or submitted request is not mistaken for confirmed payment |

## How it works

**Agree ? Sign ? Fund ? Deliver ? Review ? Release or resolve**

1. **Define the work.** The client creates a project and invites a freelancer. The agreement records deliverables, acceptance criteria, prices, deadlines, revision allowances and responsibilities.
2. **Review and sign.** Structured validation checks supported conflicts; AI provides advisory review of terms and conversation. Both participants sign the exact same immutable agreement version.
3. **Verify milestone funding.** The freelancer connects an eligible PayPal Sandbox receiving account. The client approves upfront collection, and FairFlow verifies the matching provider payment before allowing delivery.
4. **Submit evidence.** The freelancer submits a delivery link, notes and supported files. The project records the submission and its agreed review deadline.
5. **Review with assistance.** The client inspects the work, asks Flow questions, requests an included revision, or raises a timely objection when appropriate.
6. **Complete or resolve.** Approval, or an eligible unopposed review timeout, triggers a seller-release request. Where there is disagreement, the parties can exchange evidence and accept a supported resolution or settlement.

## Core functionality

### 1. Clear agreements before work begins

FairFlow's agreement builder brings scope, acceptance criteria, milestone amounts, review terms, revisions and policy clauses into one document. Supported validation checks catch missing fields, inconsistent totals and conflicting structured terms. Gemini can help identify ambiguity and suggest clarifications.

Negotiation produces versioned proposals. Both parties independently accept and sign the same version; later changes do not silently overwrite signed history. Agreement exports and acceptance records keep the baseline accessible throughout the project.

### 2. Payment confidence through funded milestones

The current payment model uses **PayPal Multiparty Sandbox capture with delayed disbursement**:

- The client funds an agreed milestone before delivery.
- The order must explicitly report delayed disbursement before FairFlow sends capture.
- A matching completed delayed capture is required before the milestone is treated as funded.
- The payment's seller, amount, currency and original platform association are checked.
- FairFlow requests seller release after client approval or an eligible review timeout without an objection.
- Provider reconciliation verifies outcomes; duplicate actions are guarded, uncertain operations pause, and unexpected releases are flagged.

This improves funding visibility and ties release requests to the review workflow. **It is a Sandbox prototype, not regulated escrow or a payment guarantee.** PayPal controls actual availability, fees, reversals and external disputes. Its automatic disbursement limit is 28 days; an internal case cannot extend that limit. Independent balance-hold verification remains a deployment prerequisite, even where API responses report delayed mode.

See [the current funding model and verification notes](docs/UPFRONT_FUNDING.md).

### 3. Ask Flow: an assistant that knows the project

Ask Flow is a floating, resizable chat window available across the portals. Within a selected project it can:

- Explain agreed prices, dates, deliverables, revisions and payment status.
- Distinguish signed terms and accepted changes from pending proposals.
- Compare submitted work with individual acceptance criteria.
- Analyse supported PDFs, images, screenshots and text/code attachments.
- Inspect public websites using rendered Chromium evidence during work review.
- Summarise both parties' dispute evidence and suggest a non-binding resolution.

Replies use concise conclusions, findings labelled **Met**, **Missing** or **Not verified**, and a practical next step. Sources, review limits and inspection screenshots are available in expandable details.

Website review executes JavaScript, captures desktop/mobile views, reads rendered content and visits up to two relevant linked pages. It does not provide exhaustive functional testing: authentication, purchases, form submission and backend delivery still require separate checks. A matching website is not proof of freelancer authorship or ownership.

**AI assists participants; it cannot sign agreements, approve work, decide a binding settlement or move money.** Private chat attachments do not automatically become shared dispute evidence.

See [assistant capabilities and inspection limits](docs/PROJECT_ASSISTANT.md).

### 4. Evidence-based dispute resolution

FairFlow gives disagreements a structured path instead of relying on an unorganised conversation. Participants can open a case, describe the issue, add responses and submit shared evidence. Delivery history and uploaded artifacts preserve what was submitted; checksums identify stored file contents.

Supported workflows include cancellation, deadline extensions, paid changes, client-caused delays, partial-work handover and mutually accepted partial settlements/refunds. Each financial action remains subject to its payment state, consent requirements and provider verification.

Ask Flow can compare claims with the agreement and inspect supplied evidence, identify gaps and recommend possible next steps. Configured human reviewers can assist escalated cases. The system does not invent a winner or treat silence as agreement to a disputed settlement. Filing a case and raising a timely payment objection are distinct actions; an internal case cannot override PayPal's deadlines.

See [resolution workflows and boundaries](docs/RESOLUTION_WORKFLOWS.md).

### 5. Changes without losing the original agreement

**Change Requests** handles additional paid scope. **Resolution** handles problems with the existing agreement, such as cancellation, delays and partial-work claims. Future unfunded work can be amended while preserving the terms and records attached to existing funded milestones.

### 6. A shared, traceable project room

Separate client and freelancer portals provide agreements, milestones, payments, work tracking, chat, changes, resolution and activity history. Role and project membership checks protect access. Database-backed processing, recorded operation identities and reconciliation support recovery when external operations are uncertain.

## Technology

| Layer | Tools |
| --- | --- |
| Frontend | HTML, CSS and vanilla JavaScript |
| Backend | Python, FastAPI, Pydantic and HTTPX |
| Data | PostgreSQL, SQLAlchemy and Alembic |
| AI | Gemini structured responses and multimodal evidence |
| Website inspection | Playwright Chromium with bounded public-network inspection |
| Payments | PayPal Sandbox Orders/Payments and Multiparty delayed disbursement; historical adapters preserved |
| Background processing | Dedicated Python worker with persisted jobs |
| Verification | pytest and Playwright browser checks |

## Run locally

Prerequisites: Python, Docker Desktop with Compose, and Chromium installed through Playwright.

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m playwright install chromium
.\run.ps1
```

The launcher generates missing local configuration, starts PostgreSQL, applies migrations and starts the API with its background worker. Open [localhost:8000](http://127.0.0.1:8000). Use separate browser profiles for the client and freelancer.

Configure Gemini in the private `.env` file for live AI responses. The separate PayPal platform configuration belongs in `.env.paypal-platform`; seller eligibility and partner permissions are required. Use `.env.example` as a configuration reference. Local email preview is provided for development; real email requires SMTP setup.

**Never commit private configuration, API keys, database contents or backups.** These files are excluded from this repository.

## Verification and project status

Recent focused checks covered payment safeguards, assistant access and evidence handling, rendered website inspection, and desktop/mobile answer formatting. Most payment tests use simulated provider responses; they do not establish production payment readiness. Test results are scoped to their recorded runs, not a claim that the entire repository has passed a fresh full-suite run.

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_funded_payments.py -q
.\venv\Scripts\python.exe -m pytest tests/test_project_assistant.py tests/test_assistant_inspection.py tests/test_assistant_browser.py -q
```

FairFlow currently demonstrates the collaboration workflow in a **local, Sandbox environment**. Production hosting, independent security review, real email operations, provider approval and full end-to-end payment verification remain launch requirements. AI findings are advisory and do not certify legal enforceability, work quality or originality.

## Further reading

- [Upfront funding and payment verification](docs/UPFRONT_FUNDING.md)
- [Ask Flow, attachments and website inspection](docs/PROJECT_ASSISTANT.md)
- [Disputes, cancellation and settlement](docs/RESOLUTION_WORKFLOWS.md)
- [Payment-aware amendments and lifecycle scenarios](docs/SCENARIO_SUPPORT_AUDIT.md)
- [Email setup](docs/EMAIL_SETUP.md)
- [Historical README](docs/README_HISTORY.md) ? retained for context; includes superseded payment flows.
