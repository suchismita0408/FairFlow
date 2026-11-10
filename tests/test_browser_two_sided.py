"""Playwright browser end-to-end test for the FairFlow Two-Sided Platform."""
import time
import uuid
import os
import pytest
import httpx
from playwright.sync_api import sync_playwright, expect

BASE = 'http://127.0.0.1:8000'
pytestmark=pytest.mark.skipif(os.getenv('FAIRFLOW_LEGACY_BROWSER')!='1',reason='Legacy payment browser scenario requires an explicitly configured external legacy server. Step 1 has its own isolated Playwright test.')

def test_two_sided_browser_flow():
    # 1. Verify health
    for attempt in range(15):
        try:
            health = httpx.get(BASE + '/api/health', timeout=3).json()
            if health.get('status') == 'ok':
                break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError('Server not reachable at ' + BASE)

    tag = uuid.uuid4().hex[:8]
    client_email = f"client-{tag}@example.com"
    freelancer_email = f"freelancer-{tag}@example.com"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        # ----------------------------------------------------
        # 1. Client Signup & Login
        # ----------------------------------------------------
        client_ctx = browser.new_context(viewport={'width': 1280, 'height': 900})
        client_page = client_ctx.new_page()
        client_page.goto(BASE)

        # Select Client role toggle
        client_page.locator('#role-client').click()
        client_page.locator('#switch-auth').click()
        client_page.locator('#name').fill('Alice Baker')
        client_page.locator('#email').fill(client_email)
        client_page.locator('#password').fill('SecurePassword123!')
        client_page.locator('#auth-form button[type=submit]').click()

        # Email verify modal
        client_page.locator('#verify-mail').click()
        client_page.locator('#modal a').click()

        # Verify Client Workspace is displayed
        expect(client_page.locator('#portal-badge-sidebar')).to_contain_text('CLIENT PLATFORM')
        expect(client_page.locator('.portal-tag.client')).to_contain_text('Client Workspace')

        # ----------------------------------------------------
        # 2. Freelancer Signup & Login
        # ----------------------------------------------------
        freelancer_ctx = browser.new_context(viewport={'width': 1280, 'height': 900})
        freelancer_page = freelancer_ctx.new_page()
        freelancer_page.goto(BASE)

        # Select Freelancer role toggle
        freelancer_page.locator('#role-freelancer').click()
        freelancer_page.locator('#switch-auth').click()
        freelancer_page.locator('#name').fill('Bob Developer')
        freelancer_page.locator('#email').fill(freelancer_email)
        freelancer_page.locator('#password').fill('SecurePassword123!')
        freelancer_page.locator('#auth-form button[type=submit]').click()

        # Email verify modal
        freelancer_page.locator('#verify-mail').click()
        freelancer_page.locator('#modal a').click()

        # Verify Freelancer Studio is displayed
        expect(freelancer_page.locator('#portal-badge-sidebar')).to_contain_text('FREELANCER STUDIO')
        expect(freelancer_page.locator('.portal-tag.freelancer')).to_contain_text('Freelancer Studio')

        # ----------------------------------------------------
        # 3. Client creates Two-Sided Collaborative Project
        # ----------------------------------------------------
        client_page.locator('#btn-two-sided-first').click()
        expect(client_page.locator('#quick-two-sided-form')).to_be_visible()

        client_page.locator('#qts-title').fill('Bakery Landing Page')
        client_page.locator('#qts-counterparty').fill(freelancer_email)
        client_page.locator('#qts-goal').fill('I need a landing page that captures emails for my new bakery.')
        client_page.locator('#qts-assets').fill('Figma: https://figma.com/file/bakery, Colors: warm gold and cream, Ref: sweetgreen.com')
        client_page.locator('#quick-two-sided-form button[type=submit]').click()

        # Modal closes and Smart Proposal tab loads
        expect(client_page.locator('#modal')).not_to_be_visible(timeout=10000)
        expect(client_page.locator('.checkout-card')).to_be_visible()
        expect(client_page.locator('#client-vision-goal')).to_have_value('I need a landing page that captures emails for my new bakery.')

        # ----------------------------------------------------
        # 4. Freelancer joins project via Invitation Mail
        # ----------------------------------------------------
        freelancer_page.locator('#mail').click()
        freelancer_page.locator('#modal article').filter(has_text='FairFlow Invitation: Bakery Landing Page').locator('a').click()

        # Freelancer is in the project room
        freelancer_page.locator('[data-tab="Smart Proposal"]').click()
        expect(freelancer_page.locator('#btn-trigger-ai-draft')).to_be_visible()

        # ----------------------------------------------------
        # 5. Two-Sided Communication (Real-Time Messaging)
        # ----------------------------------------------------
        client_page.locator('[data-tab="Messages"]').click()
        expect(client_page.locator('#chat-msg-input')).to_be_visible()
        client_page.locator('#chat-msg-input').fill('Hi Bob! Excited to work with you on the bakery site.')
        client_page.locator('#chat-send-form button').click()

        # Freelancer views message and replies
        freelancer_page.locator('[data-tab="Messages"]').click()
        expect(freelancer_page.locator('.badge-role.client').first).to_be_visible(timeout=10000)
        expect(freelancer_page.locator('#chat-stream')).to_contain_text('Hi Bob! Excited to work with you')

        freelancer_page.locator('#chat-msg-input').fill('Hi Alice! I will deliver 3 sections with Mailchimp for $450 in 16 days.')
        freelancer_page.locator('#chat-send-form button').click()

        # ----------------------------------------------------
        # 6. Freelancer clicks "Draft Agreement with AI"
        # ----------------------------------------------------
        freelancer_page.locator('[data-tab="Smart Proposal"]').click()
        expect(freelancer_page.locator('#btn-trigger-ai-draft')).to_be_visible()

        # Verify Client Vision is displayed to Freelancer
        expect(freelancer_page.locator('#room-body')).to_contain_text('I need a landing page that captures emails for my new bakery.')

        # Click AI drafting
        freelancer_page.locator('#btn-trigger-ai-draft').click()

        # Wait for Two-Sided Canvas Deliverables and Boundaries to be populated
        expect(freelancer_page.locator('#canvas-price')).to_have_value('450.00', timeout=25000)
        expect(freelancer_page.locator('#canvas-days')).to_have_value('16')
        expect(freelancer_page.locator('.canvas-deliv-input')).not_to_have_count(0)
        expect(freelancer_page.locator('.canvas-scope-input')).not_to_have_count(0)

        # Freelancer clicks "Send to Client"
        freelancer_page.locator('#btn-send-to-client').click()
        time.sleep(1.0)
        expect(freelancer_page.get_by_text('PROPOSAL SENT TO CLIENT', exact=True)).to_be_visible()

        # ----------------------------------------------------
        # 7. Client views Frictionless Checkout & Authorizes Hold
        # ----------------------------------------------------
        client_page.locator('#refresh-room').click()
        client_page.locator('[data-tab="Smart Proposal"]').click()

        # Verify Sections 1 to 4 on checkout card
        expect(client_page.locator('.checkout-card')).to_be_visible()
        expect(client_page.locator('.checkout-card')).to_contain_text('Bakery Landing Page', ignore_case=True)
        expect(client_page.locator('.checkout-card')).to_contain_text('3-section', ignore_case=True)
        expect(client_page.locator('.checkout-card')).to_contain_text('logo design', ignore_case=True)
        expect(client_page.locator('.checkout-card')).to_contain_text('$450.00')

        # Client clicks "Authorize PayPal Hold"
        expect(client_page.locator('#btn-authorize-paypal-hold')).to_be_visible()
        client_page.locator('#btn-authorize-paypal-hold').click()
        time.sleep(1.0)

        # ----------------------------------------------------
        # 8. Verify Escrow Hold is Active & Milestone Funded
        # ----------------------------------------------------
        expect(client_page.locator('.checkout-card')).to_contain_text('ESCROW HOLD ACTIVE')

        # Verify Milestones tab shows active funded milestone
        client_page.locator('[data-tab="Milestones"]').click()
        expect(client_page.locator('#room-body')).to_contain_text('Frontend Design & Build')
        expect(client_page.locator('#room-body')).to_contain_text('$450.00')

        # Verify Payment ledger has the hold authorized
        client_page.locator('[data-tab="Payments"]').click()
        expect(client_page.locator('#room-body')).to_contain_text('authorized $450.00')

        print("SUCCESS: Full two-sided collaborative flow verified end-to-end in browser!")
        browser.close()

if __name__ == '__main__':
    test_two_sided_browser_flow()
