# Judge quick demo

The landing and portal pages offer **Try quick demo** and **Try PayPal Sandbox**.
Each browser receives its own client, freelancer, and unsigned example agreement.
The demo toolbar switches between those two identities using a private HttpOnly
cookie. Both views use the regular project APIs and permission checks. Demo
sessions expire after 12 hours. No FairFlow signup or automatic signatures are
required. Opening the other payment mode creates a separate demo project.

The quick walkthrough blocks PayPal calls. It supports exploring, editing,
negotiating and signing sample terms without a PayPal transaction. It does not
pretend to have funded a real Sandbox order.

**Customize demo accounts** allows client and freelancer names and optional buyer
and receiver emails. In Sandbox mode the receiver email saves the freelancer payout
destination. The buyer email is a reminder, not authentication: the buyer signs in
to a test account separately at PayPal checkout. For the platform payment flow,
the freelancer must complete the existing seller onboarding and verification in
the project payment section. Entering an email never marks a seller verified.
Use the agreement editor to change contract terms.

On Vercel, with FAIRFLOW_MODE=demo or sandbox and no DATABASE_URL, the application
creates a temporary authenticated SQLite demo in /tmp/fairflow-judge-demo, opens
the normal landing page, and disables the embedded worker. This overrides
AUTH_ENABLED=false for the disposable demo. Data may disappear or differ across
function instances. Use hosted PostgreSQL for durable shared data, and run
`alembic upgrade head` before deploying against an existing database.

Temporary Vercel walkthroughs recover their two demo identities, initial sample
agreement and login when a request reaches a fresh instance. Edits can still reset
between instances. Sandbox entry is blocked with a setup message in this temporary
configuration: payment records require a shared hosted database.

The toolbar requires AUTH_ENABLED=true, EMAIL_MODE=local, and a supported payment
mode. Set ENABLE_JUDGE_DEMO=false to disable it. It is unavailable in SMTP mode.

## Sandbox on Vercel

Set APP_BASE_URL to the public HTTPS site URL. PayPal credentials stay on the
server; judges enter test-account emails and log in at PayPal, never API secrets.

For the existing platform flow set PAYPAL_PAYMENT_FLOW=platform and provide
PAYPAL_PLATFORM_CLIENT_ID and PAYPAL_PLATFORM_CLIENT_SECRET as Vercel secrets.
PAYPAL_PLATFORM_BN_CODE is optional. Environment values now override the private
local .env.paypal-platform file, so no credential file is committed or uploaded.
The app must have the necessary Sandbox partner capabilities for seller onboarding
and delayed disbursement.

For the existing legacy checkout/payout flow, use PAYPAL_PAYMENT_FLOW=legacy with
PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET for the Sandbox sender. Payouts still
require the sender account to support them. All provider endpoints remain Sandbox.
Automatic review/settlement needs an independently scheduled worker; a Vercel
function does not continuously run the in-process scheduler.
