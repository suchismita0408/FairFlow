# Flow project assistant

Open **Ask Flow** on a portal page. The resizable chat window uses the current project, or offers a project picker elsewhere. Compact Clear, Review work and Disputes actions sit beside the attachment button. Messages, review findings, source details and errors remain in the scrollable conversation. The composer stays at the bottom; Enter sends and Shift+Enter adds a line.

Flow answers from signed agreements, accepted changes, proposals, payments and shared dispute evidence. Drafts are visible only to their owner, and unsubmitted project uploads are excluded. Chat attachments are private to the asking participant and do not become shared dispute evidence or accepted project records. Both parties still submit formal evidence and accept settlements through Resolution.

## Attachments and inspection

The attachment button accepts PDF, PNG, JPEG, WebP and UTF-8 text/code files, plus public HTTP(S) links. Up to three files (4 MB each, 5 MB combined) and three links can be attached. PDFs must be readable, unencrypted and at most 30 pages. Images are validated, stripped of metadata and resized to at most 2400 pixels per dimension before Gemini sees them. Text excerpts are limited to 24,000 characters and marked when truncated.

Gemini receives actual PDF pages and image data using inline media. Public website inspection reads the requested page's HTML text, links, image metadata and form count. It does not execute JavaScript, visit linked pages, submit forms, access login sessions or test interactions. Results identify inspection failures and limits. PDF/image links are processed as documents. Review buttons also inspect shared project attachments and evidence links within these limits, reporting when additional files are omitted.

Website requests validate every resolved address and redirect, reject private/local networks and nonstandard ports, pin connections to the validated public IP while retaining TLS hostname checks, send no user cookies, and enforce time and response-size bounds. Invalid certificates are not bypassed. Documents and websites are untrusted evidence, never instructions to the assistant.

Existing Gemini configuration, authentication, CSRF protection, throttling and project membership checks apply. The assistant endpoint permits up to 8 MiB JSON bodies for base64 attachments; other API endpoints retain their 1 MiB limit. Attachments and conversations remain in browser page memory and are cleared by Clear or reload. Attached content is resent for follow-up questions and is sent to Gemini for inspection. No database migration is needed. The assistant cannot approve work, change terms, settle disputes or move money.

## Verification

19 assistant/inspection tests passed, covering membership, read-only behavior, shared and private evidence, MIME validation, PDF limits, private-network blocking, redirect validation, pinned connections, extraction and attachment request size. All 20 existing Gemini fallback tests passed. Live Gemini checks identified text in an image, a date in a PDF, and the purpose of python.org from retrieved HTML. Browser checks covered uploads, link attachment, send and review actions, replies and errors within the conversation, clearing, and mobile layouts at 390px and 320px. Screenshots: reports/flow-chat-desktop.png and reports/flow-chat-mobile.png (mocked conversation content).

Provider references: [Gemini document understanding](https://ai.google.dev/gemini-api/docs/document-processing), [Gemini image understanding](https://ai.google.dev/gemini-api/docs/image-understanding).

## Rendered website reviews (10 November 2026)

Review work now uses isolated Playwright Chromium contexts for submitted public links. It executes JavaScript, captures top/bottom screenshots at desktop (1365px) and mobile (390px) widths, and inspects up to two relevant same-origin links. Screenshots are sent as actual image evidence to Gemini and displayed under What Flow inspected. Rendered text, native required-field validity, horizontal overflow and expanded native disclosures are recorded. Findings must enumerate milestone requirements and distinguish observed evidence from unverified behavior.

All browser GET resources pass through the existing public-IP-pinned fetcher; private targets, non-GET requests, service workers and WebSockets are blocked. No user cookies are reused. Each link has a 55-second deadline, a 90-request and 20 MiB resource budget; files retain their existing limits. Login, purchases, sending forms, backend email delivery and arbitrary button interactions are not tested. Failed inspection must not be reported as missing work. Ordinary questions retain the HTML inspection path; use Review work for browser review.

Setup: install requirements.txt, then run `python -m playwright install chromium`. Verified with 22 assistant tests, including real Chromium against controlled HTML with JavaScript and blocked writes. A live python.org inspection produced desktop/mobile and two linked-page views with eight screenshots. AI assessment accuracy is not established by these checks.
