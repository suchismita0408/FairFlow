"""Creates explicitly labelled QA accounts/project in the local demo PostgreSQL database."""
import argparse,uuid,time
import httpx
from playwright.sync_api import sync_playwright,expect

BASE='http://127.0.0.1:8000'
parser=argparse.ArgumentParser()
parser.add_argument('--currency',choices=['USD','EUR','JPY'],default='USD')
parser.add_argument('--gemini',action='store_true',help='Explicitly allow paid Gemini calls in the local browser flow.')
options=parser.parse_args()
currency=options.currency
expect.set_options(timeout=120000 if options.gemini else 5000)
for attempt in range(30):
    try:
        health=httpx.get(BASE+'/api/health',timeout=2).json()
        break
    except httpx.HTTPError:
        if attempt==29:raise
        time.sleep(0.25)
if health['ai_mode']=='gemini' and not options.gemini:
    parser.error('Server uses Gemini; pass --gemini to explicitly allow provider calls.')

tag=uuid.uuid4().hex[:10]
with sync_playwright() as pw:
    browser=pw.chromium.launch()
    errors=[]
    def signup(role):
        page=browser.new_context(viewport={'width':1440,'height':1000}).new_page()
        if options.gemini:page.set_default_timeout(120000)
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(BASE)
        page.locator('#switch-auth').click()
        page.locator('#name').fill('QA '+role)
        page.locator('#email').fill(f'qa-{role}-{tag}@example.com')
        page.locator('#password').fill('QA-only-password-123!')
        page.locator('#auth-form button[type=submit]').click()
        page.locator('#verify-mail').click()
        page.locator('#modal a').click()
        expect(page.locator('#create-project')).to_be_visible()
        return page
    client=signup('client');freelancer=signup('freelancer')
    client.locator('#create-project').click()
    client.locator('#creator-role').select_option('client')
    client.locator('#counterparty').fill(f'qa-freelancer-{tag}@example.com')
    client.locator('#client-name').fill('QA Client')
    client.locator('#freelancer-name').fill('QA Freelancer')
    client.locator('#sample').click();client.locator('#extract').click()
    expect(client.locator('.plan-row')).to_have_count(3)
    if currency!='USD':
        client.locator('#currency').select_option(currency)
        expect(client.locator('#price')).to_have_value('')
        client.locator('#price').fill('900')
        client.locator('#rate').fill('50')
        for amount in client.locator('.mp').all():amount.fill('300')
    if currency=='JPY':
        expect(client.locator('#price')).to_have_attribute('step','1')
    client.locator('.mi').first.fill('Layout | 200 | Matches the approved responsive design.\nNavigation | 100 | Navigation works on mobile and desktop.')
    client.locator('#title').fill('QA platform smoke '+tag)
    client.locator('#project-form button[type=submit]').click()
    expect(client.locator('[data-do=accept-version]')).to_be_visible()
    client.locator('[data-do=revise]').click()
    expect(client.locator('#currency')).to_have_value(currency)
    assert 'Layout | 200 |' in client.locator('.mi').first.input_value()
    assert 'Navigation | 100 |' in client.locator('.mi').first.input_value()
    client.locator('#counterparty').fill(f'qa-freelancer-{tag}@example.com')
    client.locator('#project-form button[type=submit]').click()
    expect(client.locator('#modal')).not_to_be_visible()
    client.locator('[data-do=accept-version]').click()
    freelancer.locator('#mail').click()
    freelancer.locator('#modal article').filter(has_text='project invitation').locator('a').click()
    freelancer.locator('[data-do=accept-version]').click()
    client.locator('#refresh-room').click()
    client.locator('[data-tab=Milestones]').click()
    client.locator('#vault-agree').check()
    client.locator('[data-do=vault-setup]').click()
    client.locator('[data-do=vault-confirm]').click()
    client.locator('[data-do=fund]').first.click()
    freelancer.locator('#refresh-room').click()
    freelancer.locator('[data-tab=Milestones]').click()
    freelancer.locator('[data-do=delivery-form]').first.click()
    freelancer.locator('#delivery-url').fill('https://example.com/qa-delivery')
    freelancer.locator('#delivery-notes').fill('QA milestone delivery for browser validation.')
    freelancer.locator('#delivery-form button').click()
    expect(freelancer.locator('#modal')).not_to_be_visible()
    client.locator('#refresh-room').click()
    client.locator('[data-do=approve]').first.click()
    client.locator('[data-tab=Payments]').click()
    expect(client.locator('#room-body')).to_contain_text('provider_receivable')
    assert client.evaluate('state.room.project.currency')==currency
    assert client.evaluate('state.room.ledger.every(row=>row.currency===state.room.project.currency)')
    for tab in ['Agreement','Milestones','AI review','Messages','Payments','Activity']:
        client.locator(f'[data-tab="{tab}"]').click()
    client.locator('[data-tab=Agreement]').click()
    with client.expect_download() as downloaded:
        client.locator('[data-do=download-agreement]').click()
    from pathlib import Path
    record=Path(downloaded.value.path()).read_text(encoding='utf-8')
    assert 'Version: 2' in record and 'ownership:' in record and 'SHA-256:' in record
    assert 'Layout' in record and 'Acceptance records' in record
    client.set_viewport_size({'width':390,'height':844})
    assert client.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile overflow'
    assert not errors,errors
    print('PASS:',currency,'separate accounts, revised priced items, invitation, consent, funding, delivery, capture, ledger, tabs and mobile width. QA tag:',tag)
    browser.close()
