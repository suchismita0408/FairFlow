"""Regression for rejected negotiations and stale extraction; creates one labeled QA account."""
import uuid

from playwright.sync_api import sync_playwright, expect

BASE = 'http://127.0.0.1:8000'
BAD = ('Client: Build a responsive coffee-brand landing page, with hero, menu, about and contact details. '
       'No database or customer accounts. Freelancer: $900 for the project, with 2 revisions per milestone. '
       'Additional work is $50 per hour. Client: Not Agreed. I will pay 800 dollars. Freelancer: Okay 850$.')
GOOD = ('Client: Final project total is 850 dollars for a responsive landing page, including 2 revisions. '
        'Additional work is $50 per hour. Freelancer: Agreed to these current terms.')

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(BASE)
    page.locator('#switch-auth').click()
    page.locator('#name').fill('QA Agreement Validation')
    page.locator('#email').fill(f'qa-agreement-{uuid.uuid4().hex[:10]}@example.com')
    page.locator('#password').fill('QA-validation-only-123!')
    page.locator('#auth-form button[type=submit]').click()
    page.locator('#verify-mail').click()
    page.locator('#modal a').click()
    page.locator('#create-project').click()
    page.locator('#sample').click()
    page.locator('#extract').click()
    expect(page.locator('#price')).to_have_value('900')
    page.locator('#chat').fill(BAD)
    expect(page.locator('#terms')).not_to_be_visible()
    page.locator('#extract').click()
    expect(page.locator('#draft-feedback')).to_contain_text('Conflicting project prices')
    for value in ('$800.00', '$850.00', '$900.00'):
        expect(page.locator('#draft-feedback')).to_contain_text(value)
    expect(page.locator('#project-form button[type=submit]')).not_to_be_visible()
    assert page.evaluate('state.draft') is None
    page.locator('#chat').fill('Client: Build a responsive coffee-brand landing page. Freelancer: $900 with 2 revisions. Additional work is $50 per hour. Client: Agreed not. i will pay less.')
    page.locator('#extract').click()
    expect(page.locator('#draft-feedback')).to_contain_text('rejection or unresolved terms')
    expect(page.locator('#terms')).not_to_be_visible()
    page.locator('#chat').fill(GOOD)
    page.locator('#extract').click()
    expect(page.locator('#price')).to_have_value('850')
    expect(page.locator('#terms')).to_be_visible()
    page.locator('#creator-role').select_option('client')
    page.locator('#counterparty').fill('qa-other@example.com')
    page.locator('#client-name').fill('QA Client')
    page.locator('#freelancer-name').fill('QA Freelancer')
    page.locator('#validate-agreement').click()
    expect(page.locator('#agreement-errors')).to_contain_text('checks passed')
    page.locator('#price').fill('900')
    page.locator('#validate-agreement').click()
    expect(page.locator('#agreement-errors')).to_contain_text('Total differs')
    page.locator('#price').fill('850')
    original_law=page.locator('#term-governing_law').input_value()
    page.locator('#term-governing_law').fill('   ')
    page.locator('#validate-agreement').click()
    expect(page.locator('#agreement-errors')).to_contain_text('governing_law')
    page.locator('#term-governing_law').fill(original_law)
    page.locator('#validate-agreement').click()
    expect(page.locator('#agreement-errors')).to_contain_text('checks passed')
    def change_during_validation(route):
        response=route.fetch()
        page.locator('#rate').fill('60')
        route.fulfill(response=response)
    page.route('**/api/platform/agreements/validate',change_during_validation)
    page.locator('#validate-agreement').click()
    expect(page.locator('#agreement-errors')).to_contain_text('Fields changed during validation')
    page.unroute('**/api/platform/agreements/validate')
    page.locator('#rate').fill('50')
    page.locator('#count').fill('2')
    expect(page.locator('#terms')).not_to_be_visible()

    # The user edits the source while a successful response is in flight.
    def change_during_extraction(route):
        response = route.fetch()
        page.locator('#chat').fill(BAD)
        route.fulfill(response=response)
    page.route('**/api/agile/draft', change_during_extraction)
    page.locator('#extract').click()
    expect(page.locator('#draft-feedback')).to_contain_text('changed. Extract again')
    expect(page.locator('#terms')).not_to_be_visible()
    assert page.evaluate('state.draft') is None
    assert not errors, errors
    browser.close()
    print('PASS: screenshot negotiation blocked, clear $850 terms work, edits invalidate drafts, stale async results discarded; final price mismatch and missing mandatory policy blocked.')
