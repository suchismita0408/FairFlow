from playwright.sync_api import sync_playwright,expect
from app.auth import COOKIE
from test_step1_auth_validation import portals
from test_step1_browser import step1_server
from test_step1_negotiation import start


def test_chat_record_is_bilateral_and_old_agreement_cannot_proceed(portals,step1_server):
    c,f,r=start(portals);pid=r['project']['id']
    message=f.post(f'/api/freelancer/projects/{pid}/chat',json={'body':'We will deliver the source as a ZIP archive.'})
    assert message.status_code==200
    with sync_playwright() as pw:
        browser=pw.chromium.launch();pages={};errors=[]
        for role,client in [('client',c),('freelancer',f)]:
            context=browser.new_context(viewport={'width':1360,'height':980})
            context.add_cookies([{'name':COOKIE,'value':client.cookies.get(COOKIE),'url':step1_server}])
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(step1_server+f'/{role}/projects/{pid}?tab=Chat');pages[role]=page
        freelancer=pages['freelancer'];freelancer.get_by_role('button',name='Record term change',exact=True).last.click()
        freelancer.locator('#chat-term-field').select_option('ip.source_format')
        freelancer.locator('#chat-term-value').fill('ZIP archive of the original source files')
        freelancer.locator('#chat-term-name').fill('Rahul Kumar')
        freelancer.get_by_role('button',name='Confirm exact chat change',exact=True).click()
        expect(freelancer.locator('#dialog')).not_to_be_visible()
        client=pages['client'];client.reload()
        client.get_by_role('button',name='Review & confirm chat change',exact=True).click()
        expect(client.locator('#chat-term-value')).to_have_value('ZIP archive of the original source files')
        expect(client.locator('#chat-term-value')).to_have_attribute('readonly','')
        client.locator('#chat-term-name').fill('Alice Baker')
        client.get_by_role('button',name='Confirm exact chat change',exact=True).click()
        expect(client.locator('#dialog')).not_to_be_visible()
        expect(client.get_by_role('button',name='Approve version',exact=True)).to_be_disabled()
        expect(client.get_by_role('region',name='Chat changes awaiting agreement update')).to_contain_text('ZIP archive')
        assert not errors,errors
        browser.close()
