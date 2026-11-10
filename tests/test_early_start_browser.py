"""Two participants approve an automatic schedule through the actual UI."""
from playwright.sync_api import sync_playwright,expect
from app.auth import COOKIE
from test_step1_auth_validation import portals
from test_step1_browser import step1_server
from test_paid_amendments import paid_first


def test_early_start_preview_and_bilateral_approval(portals,step1_server,monkeypatch):
    c,f,room,_,_=paid_first(portals,monkeypatch)
    pid=room['project']['id']
    preview=c.get(f'/api/client/projects/{pid}/workflow/early-start/M2').json()
    with sync_playwright() as pw:
        browser=pw.chromium.launch();pages={};errors=[]
        for role,client in [('client',c),('freelancer',f)]:
            context=browser.new_context(viewport={'width':1360,'height':980})
            context.add_cookies([{'name':COOKIE,'value':client.cookies.get(COOKIE),'url':step1_server}])
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            pages[role]=page
        client=pages['client'];client.goto(step1_server+f'/client/projects/{pid}?tab=Payments')
        client.get_by_role('button',name='Start milestone early',exact=True).click()
        expect(client.locator('#dialog')).to_contain_text('Final project deadline')
        client.locator('#early-start-name').fill('Alice Baker')
        client.get_by_role('button',name='Propose & approve schedule',exact=True).click()
        expect(client.locator('#dialog')).not_to_be_visible()
        expect(client.get_by_text('Early-start schedule: waiting for the other participant',exact=True)).to_be_visible()
        freelancer=pages['freelancer'];freelancer.goto(step1_server+f'/freelancer/projects/{pid}?tab=Payments')
        freelancer.get_by_role('button',name='Review early-start dates',exact=True).click()
        expect(freelancer.locator('#dialog')).to_contain_text('Final project deadline')
        freelancer.locator('#workflow-name').fill('Rahul Kumar')
        freelancer.get_by_role('button',name='Accept these terms',exact=True).click()
        expect(freelancer.locator('#dialog')).not_to_be_visible()
        current=c.get(f'/api/client/projects/{pid}').json()
        assert current['agreement']['timeline']['deadline']==preview['deadline']
        assert current['project']['status']=='AGREED'
        assert current['agreement']['timeline']['milestones'][1]['due_date']==preview['milestones'][0]['due_date']
        assert not errors,errors
        browser.close()


def test_early_start_explains_unsettled_previous_payout(portals,step1_server,monkeypatch):
    c,f,room,_,_=paid_first(portals,monkeypatch,payout_status='attention')
    with sync_playwright() as pw:
        browser=pw.chromium.launch();context=browser.new_context()
        context.add_cookies([{'name':COOKIE,'value':c.cookies.get(COOKIE),'url':step1_server}])
        page=context.new_page();page.goto(step1_server+f'/client/projects/{room["project"]["id"]}?tab=Payments')
        card=page.locator('[data-milestone-card="M2"]')
        expect(card.get_by_role('button',name='Start milestone early',exact=True)).to_be_disabled()
        expect(card).to_contain_text('Available after previous milestone payouts are verified.')
        browser.close()
