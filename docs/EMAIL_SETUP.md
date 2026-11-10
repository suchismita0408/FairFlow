# Enable real verification email

Priority: alongside payment completion. The user currently has no sender/provider and requested empty configuration slots. `.env` is prepared with SMTP placeholders; local mode remains active until those are filled. Real inbox verification is **not complete**.

SMTP transport is implemented. You need a provider account, a verified sender/domain and provider-issued SMTP credentials. The local preview proves the demo flow only, not control of a mailbox.

1. Choose an SMTP provider and verify your sending domain using its DNS instructions. Obtain SMTP host, port, username and password. Keep the credentials in the local `.env`.
2. Configure the values below. Use port 587 for STARTTLS or 465 for implicit TLS; this app requires TLS. Set `APP_BASE_URL` to the address recipients can actually open. A localhost URL only works on the machine running FairFlow.
3. Restart the API and worker with `./run.ps1`. SMTP delivery is performed by the worker, not the HTTP request.
4. Sign in and request a fresh verification email. Accounts verified with local preview must verify again in SMTP mode; old preview links cannot verify the mailbox. Open the link while signed into the corresponding account.
5. Confirm receipt and verification, then test password reset and a project notification. Inspect the provider's delivery dashboard if mail does not arrive. Do not expose verification links in public screenshots.

```dotenv
EMAIL_MODE=smtp
SMTP_HOST=your-provider-host
SMTP_PORT=587
SMTP_FROM=notifications@your-verified-domain.example
SMTP_USERNAME=your-provider-username
SMTP_PASSWORD=your-provider-secret
APP_BASE_URL=https://your-deployed-app.example
```

For example, Resend documents `smtp.resend.com`, username `resend`, an API key as the password, and port 587 with STARTTLS. A verified domain is a prerequisite. Substitute your verified sender for `SMTP_FROM`; the app already supports this configuration. See [Resend SMTP documentation](https://resend.com/docs/send-with-smtp).

Messages are encrypted in the database outbox. Only messages queued in SMTP mode are eligible for sending: changing modes never sends historical local previews. A failed or uncertain send enters `attention` instead of retrying blindly; inspect provider delivery evidence and request a fresh verification/reset link if needed. SMTP acceptance alone is not proof of inbox delivery. A process crash after a send starts can leave a `sending` record requiring investigation. There is not yet an operator mail-recovery or bounce-management UI.

Keep `EMAIL_MODE=local` for an entirely local demo until the sender is ready. Do not enable public local-preview verification. Provider configuration and actual inbox delivery were not completed during this audit because no sender account was supplied.
