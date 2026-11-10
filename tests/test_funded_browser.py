"""Authenticated funded milestone UI, with no external payment calls."""
import hashlib
import os
from datetime import datetime,timedelta,timezone
from pathlib import Path
import httpx
from sqlalchemy import insert,update
from playwright.sync_api import sync_playwright,expect
from app import db
from test_step1_browser import step1_server
from test_step1_assurance import signed_short_project


def test_funded_delivery_ui_and_compact_receiving_status(step1_server):
    clients={}
    for role,name in [('client','Alice Baker'),('freelancer','Rahul Kumar')]:
        client=httpx.Client(base_url=step1_server,timeout=20)
        body={'name':name,'email':role+'@example.com','password':'Funded-browser-password-123!','country':'IN','time_zone':'Asia/Kolkata'}
        if role=='freelancer':body.update(professional_title='Developer',primary_category='Frontend',skills=['CSS'])
        response=client.post('/api/'+role+'/signup',json=body);assert response.status_code==201,response.text
        client.headers['x-csrf-token']=response.json()['csrf_token']
        token=next(m['body'].split('#verify=')[1] for m in client.get('/api/auth/mail').json() if '#verify=' in m['body'])
        assert client.post('/api/auth/verify',json={'token':token}).status_code==200
        clients[role]=client
    c,f,room=signed_short_project(lambda role:clients[role]);pid=room['project']['id'];m=room['agreement']['timeline']['milestones'][0]
    # Make only fake configuration visible to the isolated subprocess; this
    # test never calls a financial/provider endpoint.
    Path(os.environ['PAYPAL_PLATFORM_CONFIG']).write_text('PAYPAL_PLATFORM_CLIENT_ID=FAKE\nPAYPAL_PLATFORM_CLIENT_SECRET=FAKE\n')
    with db.engine().begin() as conn:
        row=db.one(conn,db.projects,db.projects.c.id==pid);project=row['payload'];project['funding_policy']={'version':'paypal_funded_v1'}
        project['platform_seller']={'status':'verified','email':'seller@example.com','merchant_id':'SELLER'}
        conn.execute(update(db.projects).where(db.projects.c.id==pid).values(payload=project))
        conn.execute(insert(db.milestone_payments).values(id=db.uid(),project_id=pid,agreement_id=project['active_agreement_id'],milestone_id=m['id'],
            amount_minor=m['amount_minor'],currency='USD',status='funded',created_at=db.now(),payload={'funding_model':'paypal_funded_v1',
            'platform_fingerprint':hashlib.sha256(b'FAKE').hexdigest(),'capture_id':'CAP','seller_merchant_id':'SELLER','seller_email':'seller@example.com',
            'auto_release':True,'provider_disburse_by':(datetime.now(timezone.utc)+timedelta(days=28)).isoformat()}))
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch()
        context=browser.new_context(viewport={'width':1280,'height':900})
        context.add_cookies([{'name':cookie.name,'value':cookie.value,'url':step1_server} for cookie in f.cookies.jar])
        page=context.new_page();page.goto(step1_server+f'/freelancer/projects/{pid}?tab=Payments')
        card=page.locator(f'[data-milestone-card="{m["id"]}"]')
        expect(card).to_contain_text('Milestone funded.')
        expect(page.get_by_text('Receiving account connected',exact=True)).to_be_visible()
        expect(page.get_by_role('button',name='Connect PayPal',exact=True)).to_have_count(0)
        expect(page.get_by_text('Open the resulting PayPal link in a private window',exact=False)).to_have_count(0)
        card.get_by_role('button',name='Submit work & view feedback',exact=True).click()
        card=page.locator(f'[data-work-milestone="{m["id"]}"]')
        card.get_by_role('button',name='Submit delivery link',exact=True).click()
        page.locator('#delivery-url').fill('https://example.com/completed-work')
        page.locator('#delivery-note').fill('Completed the agreed website and its acceptance criteria.')
        page.locator('#delivery-form').get_by_role('button',name='Submit for client review').click()
        expect(card).to_contain_text('in review')
        expect(card.get_by_role('button',name='Refresh authorization honor period')).to_have_count(0)
        expect(page.get_by_role('tab',name='Deliverables',exact=True)).to_have_count(0)
        card.get_by_text('Submission history & files',exact=False).click()
        expect(card.get_by_role('link',name='Open submitted work',exact=True)).to_have_attribute('href','https://example.com/completed-work')
        card.get_by_role('button',name='Open dispute / work claim',exact=True).click()
        page.locator('#case-body').fill('The client requested an extra mobile app outside the agreed website scope.')
        page.get_by_role('button',name='Open case & notify counterpart',exact=True).click()
        expect(page.locator('#dialog')).not_to_be_visible()
        expect(card).to_contain_text('outside the agreed website scope')
        card.get_by_role('button',name='Respond / add evidence',exact=True).click()
        page.locator('#case-reply-body').fill('The signed acceptance criteria and the submitted website show the agreed scope.')
        page.get_by_role('button',name='Add response',exact=True).click()
        expect(page.locator('#dialog')).not_to_be_visible()
        expect(card).to_contain_text('show the agreed scope')
        page.get_by_role('tab',name='Payments',exact=True).click()
        expect(page.get_by_role('button',name='Submit delivery link',exact=True)).to_have_count(0)
        expect(page.locator('.delivery-link')).to_have_count(0)
        expect(page.get_by_role('button',name='Submit work & view feedback',exact=True)).to_be_visible()
        context.close();browser.close()
    for client in clients.values():client.close()
