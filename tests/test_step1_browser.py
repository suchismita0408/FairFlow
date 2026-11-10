"""Isolated Playwright flow: real UI, two cookies, temporary database/server."""
import os, socket, subprocess, sys, time, re
from pathlib import Path
import httpx
import pytest
from sqlalchemy import update
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright, expect
from app import db


def test_approve_updated_draft_shares_exact_version_and_opens_signing_after_both_approve(portals,step1_server):
    import copy
    from app.auth import COOKIE
    from test_step1_negotiation import start,call
    client,freelancer,room=start(portals);pid=room['project']['id']
    room=call(client,'client',room,'approve',version_hash=room['versions'][-1]['digest'])
    proposal=copy.deepcopy(room['agreement'])
    proposal['scope']['deliverables'][0]['description']+=' Includes the agreed responsive layout.'
    saved=freelancer.put(f'/api/freelancer/projects/{pid}/draft',json={'revision':room['revision'],'agreement':proposal})
    assert saved.status_code==200,saved.text
    assert saved.json()['has_unshared_changes']
    with sync_playwright() as pw:
        browser=pw.chromium.launch();contexts=[];pages={}
        for role,cookies in [('client',client.cookies),('freelancer',freelancer.cookies)]:
            context=browser.new_context();contexts.append(context)
            context.add_cookies([{'name':COOKIE,'value':cookies.get(COOKIE),'url':step1_server}])
            pages[role]=context.new_page()
        f=pages['freelancer'];f.goto(step1_server+f'/freelancer/projects/{pid}')
        f.get_by_role('button',name='Approve version',exact=True).click()
        expect(f.locator('#toast')).to_contain_text('Waiting for the other participant')
        latest=client.get(f'/api/client/projects/{pid}').json()
        assert latest['versions'][-1]['number']==2
        assert [approval['role'] for approval in latest['versions'][-1]['approvals']]==['freelancer']
        c=pages['client'];c.goto(step1_server+f'/client/projects/{pid}')
        c.get_by_role('button',name='Approve version',exact=True).click()
        expect(c.locator('#dialog')).to_be_visible()
        expect(c.locator('#dialog')).to_contain_text('Sign this exact agreement')
        expect(c.locator('#typed-name')).to_have_value('')
        final=client.get(f'/api/client/projects/{pid}').json()
        assert final['project']['status']=='READY_TO_SIGN'
        assert len(final['versions'][-1]['approvals'])==2 and final['versions'][-1]['signatures']==[]
        for context in contexts:context.close()
        browser.close()

@pytest.fixture
def step1_server(tmp_path,monkeypatch,request):
    target=os.getenv('FAIRFLOW_TEST_DATABASE_URL')
    monkeypatch.setenv('DATABASE_URL',target or 'sqlite:///'+str(tmp_path/'browser.db'))
    monkeypatch.setenv('AUTH_ENABLED','true')
    monkeypatch.setenv('EMAIL_MODE','local')
    monkeypatch.setenv('TOKEN_ENCRYPTION_KEY',Fernet.generate_key().decode())
    if target:
        from sqlalchemy import text
        from alembic.config import Config
        from alembic import command
        assert db.engine().url.database.startswith('_qa_step1_')
        with db.engine().begin() as conn:
            conn.execute(text('DROP SCHEMA public CASCADE'));conn.execute(text('CREATE SCHEMA public'))
        command.upgrade(Config('alembic.ini'),'head')
    else:db.metadata.create_all(db.engine())
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    env={**os.environ,'AUTH_ENABLED':'true','AI_MODE':'demo','PAYMENT_MODE':'demo','RUN_EMBEDDED_WORKER':'false','ENABLE_LEGACY_ACCOUNTS':'false','APP_BASE_URL':f'http://127.0.0.1:{port}'}
    server_target='main:app'
    if getattr(request,'param',None)=='payments':
        (tmp_path/'provider_stub.py').write_text("import copy\nfrom datetime import datetime,timedelta,timezone\nfrom main import app\nfrom app import paypal\npayouts={}\nasync def create(row,pid):\n    return {'id':'ORDER-'+row['id'],'links':[{'rel':'approve','href':'https://www.sandbox.paypal.com/checkoutnow?token=TEST'}]}\nasync def authorize(row):\n    return {'id':'AUTH-'+row['id'],'expiration_time':(datetime.now(timezone.utc)+timedelta(days=29)).isoformat()}\nasync def capture(row):\n    return {'id':'CAP-'+row['id'],'status':'COMPLETED','amount':paypal.step1_amount(row['amount_minor'],row['currency'])}\nasync def read_capture(cid):\n    from app import db\n    with db.engine().connect() as conn:row=db.one(conn,db.milestone_payments,db.milestone_payments.c.id==cid.removeprefix('CAP-'))\n    return {'id':cid,'status':'COMPLETED','amount':paypal.step1_amount(row['amount_minor'],row['currency'])}\nasync def payout(row):\n    batch='BATCH-'+row['id'];payouts[batch]=copy.deepcopy(row)\n    return {'batch_header':{'payout_batch_id':batch,'batch_status':'PENDING'}}\nasync def read_payout(batch):\n    row=payouts[batch];amount=paypal.step1_amount(row['amount_minor'],row['currency'])\n    return {'batch_header':{'payout_batch_id':batch,'sender_batch_header':{'sender_batch_id':row['id']}},'items':[{'payout_item_id':'ITEM-'+row['id'],'transaction_id':'TRANSFER-'+row['id'],'transaction_status':'SUCCESS','payout_item':{'sender_item_id':row['id'],'recipient_type':'EMAIL','receiver':row['recipient_email'],'amount':{'currency':amount['currency_code'],'value':amount['value']}}}]}\npaypal.step1_create_order=create\npaypal.step1_authorize=authorize\npaypal.step1_capture=capture\npaypal.step1_get_capture=read_capture\npaypal.step1_create_payout=payout\npaypal.step1_get_payout=read_payout\n",encoding='utf-8')
        # Only the isolated provider-stub server exposes this review-clock helper.
        # It runs the real worker scan; capture/payout adapters remain mocked.
        with (tmp_path/'provider_stub.py').open('a',encoding='utf-8') as fixture:
            fixture.write("\nfrom app import db,assurance,payment_scan\nfrom sqlalchemy import update\n@app.post('/_test/review-timeout/{pid}')\nasync def review_timeout(pid:str):\n    with db.engine().begin() as conn:\n        for row in db.rows(conn,db.milestone_payments,db.milestone_payments.c.project_id==pid):\n            if row['status']=='in_review':assurance.save(conn,row,'in_review',review_deadline=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())\n    await payment_scan.scan(pid)\n    return {'ok':True}\n")
        env['PYTHONPATH']=str(tmp_path)+os.pathsep+str(Path.cwd())
        env['PAYPAL_CLIENT_ID']='test-id';env['PAYPAL_CLIENT_SECRET']='test-secret'
        server_target='provider_stub:app'
    with (tmp_path/'server.log').open('w') as log:
        proc=subprocess.Popen([sys.executable,'-m','uvicorn',server_target,'--host','127.0.0.1','--port',str(port)],env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        base=f'http://127.0.0.1:{port}'
        try:
            for _ in range(100):
                if proc.poll() is not None:raise RuntimeError((tmp_path/'server.log').read_text())
                try:
                    if httpx.get(base+'/api/health',timeout=.5).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:raise RuntimeError('Isolated server did not start.')
            yield base
        finally:proc.terminate();proc.wait(timeout=10)

def test_client_freelancer_counter_contradiction_sign_workspace(step1_server,tmp_path):
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        client=browser.new_context(viewport={'width':1440,'height':1000});freelancer=browser.new_context(viewport={'width':1280,'height':1000})
        c=client.new_page();f=freelancer.new_page();errors=[]
        c.on('pageerror',lambda e:errors.append(str(e)));f.on('pageerror',lambda e:errors.append(str(e)))
        def signup(page,role,name):
            page.goto(step1_server+f'/{role}/signup')
            page.get_by_label('Full name',exact=False).fill(name)
            page.get_by_label('Email',exact=False).fill(role+'@example.com')
            page.locator('input#password').fill('Browser-test-password-123!')
            if role=='freelancer':
                page.get_by_label('Professional title',exact=False).fill('Frontend developer')
                page.get_by_label('Skills (comma separated)',exact=False).fill('HTML, CSS')
            page.get_by_role('button',name='Create '+role+' account',exact=True).click()
            page.get_by_role('button',name='Verify email',exact=True).first.click()
            expect(page.get_by_role('heading',name='Your work, on fair terms.')).to_be_visible()
        signup(c,'client','Alice Baker');signup(f,'freelancer','Rahul Kumar')
        c.get_by_role('button',name='＋ New project',exact=True).click()
        # Seed explicit fixture terms through the same authenticated draft API;
        # the production UI no longer exposes a demo-data button.
        def load_fixture_terms(page):
            page.evaluate("""async()=>{
                const result=await api(url('example-terms'));
                for(const [section,values] of Object.entries(result.agreement)){
                    if(section==='acknowledgments')continue;
                    for(const [key,value] of Object.entries(values)){
                        if(['both',S.role].includes(fieldOwner(section+'.'+key)))put(S.room.agreement,section+'.'+key,value);
                    }
                }
                S.dirty=true;persist();S.tab='Agreement';renderProject();
            }""")
        expect(c.locator('#open-builder')).to_be_visible()
        load_fixture_terms(c)
        expect(c.locator('#field-basics-title')).to_have_value('Bakery landing page')
        c.locator('#field-basics-title').fill('Bakery landing page revised')
        with db.engine().begin() as conn:
            conn.execute(update(db.sessions).where(db.sessions.c.user_id==c.evaluate('S.user.id')).values(last_active_at='2000-01-01T00:00:00+00:00'))
        c.get_by_role('button',name='Save draft',exact=True).click()
        expect(c.get_by_role('heading',name='Welcome back.')).to_be_visible()
        c.get_by_label('Email',exact=False).fill('client@example.com')
        c.locator('input#password').fill('Browser-test-password-123!')
        c.get_by_role('button',name='Sign in',exact=True).click()
        expect(c.locator('#field-basics-title')).to_have_value('Bakery landing page revised')
        expect(c.locator('#save-status')).to_have_text('All changes saved',timeout=14000)
        c.get_by_role('button',name='Invite freelancer',exact=True).click()
        c.get_by_label('Freelancer email or @handle',exact=False).fill('freelancer@example.com')
        c.get_by_role('button',name='Send invitation',exact=True).click()
        expect(c.locator('#dialog')).not_to_be_visible()
        project_path=c.url.split(step1_server)[1]
        f.get_by_role('link',name='Invitation inbox',exact=True).click()
        f.get_by_role('button',name='Open project brief',exact=True).click()
        expect(f.locator('#open-builder')).to_be_visible()
        load_fixture_terms(f)
        assert f.locator('input#field-basics-title').count()==0
        expect(f.locator('.read-field#field-basics-title')).to_have_count(1)
        f.locator('[data-section=payment]').click()
        total=f.locator('#field-payment-total_minor');total.fill('999.99')
        f.get_by_role('button',name='Save draft',exact=True).click()
        expect(f.locator('#save-status')).to_have_text('All changes saved')
        f.get_by_role('button',name='Send counter',exact=True).click()
        expect(f.locator('#global-flags')).to_contain_text('Milestone prices add up')
        f.locator('[data-goto="payment.total_minor"]').click()
        expect(f.locator('#field-payment-total_minor')).to_be_focused()
        f.evaluate("goToField('timeline.milestones.0.currency')")
        expect(f.locator('#field-timeline-milestones-0-currency')).to_be_visible()
        expect(f.locator('#field-timeline-milestones-0-currency')).to_be_focused()
        f.evaluate("goToField('scope.inclusions')")
        expect(f.locator('#field-scope-inclusions')).to_be_visible()
        expect(f.locator('#field-scope-inclusions')).to_be_focused()
        f.locator('[data-section=payment]').click()
        total.fill('1000.00');f.get_by_role('button',name='Send counter',exact=True).click()
        expect(f.locator('[data-field]')).to_have_count(0)
        assert f.evaluate('S.room.can_edit') is False
        c.reload();c.get_by_role('button',name='Approve version',exact=True).click()
        f.reload();f.get_by_role('button',name='Approve version',exact=True).click()
        c.reload()
        for page,name in ((c,'Alice Baker'),(f,'Rahul Kumar')):
            page.reload();page.get_by_role('button',name='Sign agreement',exact=True).click()
            page.get_by_label('Type your full name',exact=False).fill(name)
            expect(page.locator('#dialog')).to_contain_text('Payment terms')
            page.locator('#payment-rules-confirmed').check()
            page.locator('#sign-form').get_by_role('button',name='Sign agreement',exact=True).click()
            expect(page.locator('#dialog')).not_to_be_visible()
        c.reload();expect(c.get_by_text('Agreement signed',exact=True)).to_be_visible()
        c.get_by_role('button',name='AI review of terms & conversation',exact=True).click()
        expect(c.locator('#dialog')).to_contain_text('AI review is unavailable')
        c.locator('#dialog-close').click()
        c.get_by_role('tab',name='Agreement',exact=True).click();expect(c.locator('.agreement-clauses')).to_be_visible()
        c.get_by_label('Search clauses',exact=True).fill('Ownership')
        c.get_by_role('tab',name='Milestones',exact=True).click();expect(c.locator('#tab-content tbody tr')).to_have_count(1)
        c.get_by_role('tab',name='Payments',exact=True).click();expect(c.get_by_role('heading',name='Payments',exact=True)).to_be_visible()
        f.reload();f.get_by_role('tab',name='Payments',exact=True).click()
        f.get_by_role('button',name='Set Sandbox recipient',exact=True).click()
        f.get_by_label('PayPal Sandbox recipient email',exact=False).fill('seller@business.example.com')
        f.get_by_label('This is my Sandbox receiving account.').check()
        f.get_by_role('button',name='Save recipient',exact=True).click()
        expect(f.locator('#dialog')).not_to_be_visible()
        c.reload();c.get_by_role('tab',name='Payments',exact=True).click()
        expect(c.get_by_text('seller@business.example.com',exact=True)).to_be_visible()
        expect(c.get_by_role('button',name='Send freelancer Sandbox payout',exact=True)).to_have_count(0)
        c.get_by_role('tab',name=re.compile('^Chat')).click();c.get_by_label('Message',exact=False).fill('Ready to start. https://example.com/brief')
        c.get_by_role('button',name='Send message',exact=True).click();expect(c.locator('#chat-log')).to_contain_text('Ready to start.')
        f.goto(step1_server+project_path.replace('/client/','/freelancer/'));f.get_by_role('tab',name=re.compile('^Chat')).click();expect(f.locator('#chat-log')).to_contain_text('Ready to start.')
        c.evaluate("""async()=>{await perform('amend');const a=structuredClone(S.room.agreement);a.payment.currency='USD';a.payment.total_minor=2000;a.timeline.milestones[0].currency='USD';a.timeline.milestones[0].amount_minor=2000;S.room=await api(url(`projects/${S.room.project.id}/draft`),{revision:S.room.revision,agreement:a},'PUT');await perform('send');}""")
        f.reload()
        comparison=f.get_by_role('region',name='Proposed amendment changes')
        expect(comparison).to_have_count(0)
        f.get_by_role('tab',name='Amendments',exact=True).click()
        expect(comparison).to_be_visible()
        expect(comparison.get_by_role('columnheader',name='Current',exact=True)).to_be_visible()
        expect(comparison.get_by_role('columnheader',name='Proposed',exact=True)).to_be_visible()
        assert comparison.locator('td').first.evaluate("e=>getComputedStyle(e).textDecorationLine")=='none'
        expect(comparison).to_contain_text('$20.00')
        expect(comparison).to_contain_text('INR')
        expect(comparison).to_contain_text('Budget and payment terms')
        f.get_by_role('button',name='Read full proposed agreement',exact=True).click()
        expect(comparison).to_have_count(0)
        expect(f.get_by_role('heading',name=re.compile('^Proposed amendment · v'))).to_be_visible()
        c.set_viewport_size({'width':390,'height':844});c.get_by_role('tab',name='Overview',exact=True).click()
        assert c.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        c.screenshot(path=str(tmp_path/'step1-mobile.png'),full_page=True)
        c.get_by_role('button',name='Switch color theme',exact=True).click()
        c.screenshot(path=str(tmp_path/'step1-mobile-theme.png'),full_page=True)
        c.set_viewport_size({'width':1440,'height':1000})
        f.goto(step1_server+'/freelancer/dashboard')
        c.get_by_role('tab',name=re.compile('^Chat')).click()
        c.get_by_label('Message',exact=False).fill('Please discuss the remaining milestone before withdrawal.')
        c.get_by_role('button',name='Send message',exact=True).click()
        expect(c.locator('#chat-log')).to_contain_text('before withdrawal.')
        f.evaluate('refreshNotifications()')
        expect(f.locator('#notification-count')).to_be_visible()
        f.goto(step1_server+'/freelancer/notifications')
        f.locator('.notification-card').filter(has_text='Please discuss the remaining milestone').get_by_role('link',name='Open conversation',exact=True).click()
        expect(f.locator('#chat-log')).to_contain_text('before withdrawal.')
        f.get_by_role('button',name='Withdraw from project',exact=True).click()
        f.get_by_label('Reason',exact=False).fill('I cannot continue with the remaining work due to an emergency.')
        f.get_by_role('button',name='Record reason',exact=True).click()
        expect(f.locator('.withdrawal-notice')).to_contain_text('awaiting acknowledgment')
        expect(f.locator('#chat-log')).to_contain_text('The freelancer withdrew')
        c.reload()
        c.get_by_role('button',name='Acknowledge withdrawal',exact=True).click()
        c.locator('#withdrawal-confirm').click()
        expect(c.locator('.withdrawal-notice')).to_contain_text('recorded')
        c.get_by_role('button',name='Archive project',exact=True).click()
        c.locator('#delete-project-confirm').click()
        expect(c.get_by_role('heading',name='Your work, on fair terms.')).to_be_visible()
        expect(c.get_by_role('link',name='Bakery landing page revised',exact=True)).to_have_count(0)
        c.get_by_role('link',name='Archived projects',exact=True).click()
        c.get_by_role('button',name='Restore',exact=True).click()
        c.get_by_role('link',name='Back to projects',exact=True).click()
        expect(c.get_by_role('link',name='Bakery landing page revised',exact=True)).to_be_visible()
        f.goto(step1_server+'/client/dashboard');expect(f.get_by_role('heading',name='Your work, on fair terms.')).to_be_visible()
        assert '/freelancer/dashboard' in f.url
        assert errors==[],errors
        browser.close()


# Provider functions are stubbed in the isolated server; all UI, API, auth,
# database and agreement logic below is real. No external money operation occurs.
from test_step1_auth_validation import portals
from test_step1_assurance import signed_short_project

@pytest.mark.parametrize('step1_server',['payments'],indirect=True)
def test_payment_delivery_revision_capture_and_verified_payout_ui(portals,step1_server):
    from app.auth import COOKIE
    client,freelancer,room=signed_short_project(portals,auto=True)
    pid=room['project']['id']
    freelancer.put('/api/freelancer/payout-recipient',json={'email':'seller@business.example.com','sandbox_account_confirmed':True})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        cc=browser.new_context();fc=browser.new_context()
        cc.add_cookies([{'name':COOKIE,'value':client.cookies.get(COOKIE),'url':step1_server}])
        fc.add_cookies([{'name':COOKIE,'value':freelancer.cookies.get(COOKIE),'url':step1_server}])
        c=cc.new_page();f=fc.new_page();errors=[]
        c.on('pageerror',lambda e:errors.append(str(e)));f.on('pageerror',lambda e:errors.append(str(e)))
        c.goto(step1_server+'/client/projects/'+pid);f.goto(step1_server+'/freelancer/projects/'+pid)
        c.get_by_role('tab',name='Payments',exact=True).click()
        c.get_by_role('button',name='Start Sandbox checkout',exact=True).click()
        c.get_by_role('button',name='Verify buyer approval',exact=True).click()
        expect(c.get_by_text('Payment release: automatic after review, unless disputed',exact=True)).to_be_visible()
        f.reload();f.get_by_role('tab',name='Work Tracking',exact=True).click()
        def deliver():
            f.get_by_role('button',name='Submit delivery link',exact=True).click()
            f.get_by_label('Delivery URL',exact=False).fill('https://example.com/delivery')
            f.get_by_label('What was delivered',exact=False).fill('The full agreed landing page is delivered.')
            f.get_by_role('button',name='Submit for client review',exact=True).click()
            expect(f.locator('#dialog')).not_to_be_visible()
        deliver()
        c.reload();c.get_by_role('tab',name='Work Tracking',exact=True).click()
        c.get_by_role('button',name='Request revision / raise dispute',exact=True).click()
        c.get_by_label('Explain what needs to change',exact=False).fill('Please correct the mobile navigation layout.')
        c.get_by_role('button',name='Submit objection',exact=True).click()
        expect(c.get_by_role('region',name='Client objection')).to_contain_text('Please correct the mobile navigation layout.')
        f.reload();f.get_by_role('tab',name='Work Tracking',exact=True).click()
        f.get_by_text('Agreed work & revision allowance',exact=True).first.click()
        expect(f.locator('[data-work-milestone]').first).to_contain_text('Revision rounds used 1')
        deliver()
        c.reload();c.get_by_role('tab',name='Payments',exact=True).click()
        expect(c.get_by_role('button',name='Approve delivery and pay freelancer',exact=True)).to_have_count(0)
        response=c.request.post(step1_server+'/_test/review-timeout/'+pid,headers={'X-CSRF-Token':c.evaluate('S.csrf')})
        assert response.status==200,response.text()
        c.reload();c.get_by_role('tab',name='Payments',exact=True).click()
        expect(c.get_by_text('Freelancer payout: pending',exact=True)).to_be_visible()
        c.get_by_role('button',name='Agreement change options',exact=True).click()
        expect(c.get_by_role('heading',name='Agreement change options',exact=True)).to_be_visible()
        expect(c.get_by_role('link',name='Create agreement for remaining work')).to_have_count(0)
        c.get_by_role('button',name='Open payments',exact=True).click()
        c.get_by_role('button',name='Check payout status',exact=True).click()
        expect(c.get_by_text('Freelancer payout: received',exact=True)).to_be_visible()
        c.get_by_role('button',name='Agreement change options',exact=True).click()
        expect(c.locator('#tab-content')).to_contain_text('All existing milestones have funding records and are locked')
        expect(c.get_by_role('button',name='Amend remaining milestones',exact=True)).to_have_count(0)
        c.get_by_role('tab',name='Agreement',exact=True).click()
        expect(c.locator('.agreement-clauses')).to_be_visible()
        c.get_by_role('tab',name='Payments',exact=True).click()
        c.get_by_role('button',name='View settlement history',exact=True).click()
        expect(c.locator('#dialog')).to_contain_text('provider receivable')
        expect(c.locator('#dialog tbody tr')).to_have_count(4)
        assert errors==[]
        browser.close()


@pytest.mark.parametrize('step1_server',['payments'],indirect=True)
def test_same_project_remaining_milestone_amendment_ui(portals,step1_server,monkeypatch):
    from app.auth import COOKIE
    from test_paid_amendments import paid_first
    client,freelancer,room,paid,payout=paid_first(portals,monkeypatch,merchant='test-id')
    pid=room['project']['id']
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        cc=browser.new_context(viewport={'width':1440,'height':1000});fc=browser.new_context()
        cc.add_cookies([{'name':COOKIE,'value':client.cookies.get(COOKIE),'url':step1_server}])
        fc.add_cookies([{'name':COOKIE,'value':freelancer.cookies.get(COOKIE),'url':step1_server}])
        c=cc.new_page();f=fc.new_page();errors=[]
        c.on('pageerror',lambda e:errors.append(str(e)));f.on('pageerror',lambda e:errors.append(str(e)))
        c.goto(step1_server+'/client/projects/'+pid)
        expect(c.get_by_role('heading',name='Second milestone',exact=True)).to_be_visible()
        c.get_by_role('button',name='Amend remaining milestones',exact=True).click()
        c.get_by_role('button',name='Edit amendment fields',exact=True).click()
        c.locator('[data-section=payment]').click()
        expect(c.locator('input#field-timeline-milestones-0-amount_minor')).to_have_count(0)
        expect(c.locator('.locked-amendment-field#field-timeline-milestones-0-amount_minor')).to_be_visible()
        c.locator('#field-payment-total_minor').fill('25.00')
        c.locator('#field-timeline-milestones-1-amount_minor').fill('15.00')
        c.get_by_role('button',name='Send counter',exact=True).click()
        expect(c.locator('#toast')).to_contain_text('Version sent.')
        c.get_by_role('button',name='Approve version',exact=True).click()
        expect(c.locator('#toast')).to_contain_text('approve recorded.')
        f.goto(step1_server+'/freelancer/projects/'+pid)
        f.get_by_role('tab',name='Amendments',exact=True).click()
        expect(f.locator('.amendment-comparison')).to_contain_text('$15.00')
        f.get_by_role('button',name='Approve version',exact=True).click()
        for page,name in ((c,'Alice Baker'),(f,'Rahul Kumar')):
            page.reload();page.get_by_role('button',name='Sign agreement',exact=True).click()
            page.get_by_label('Type your full name',exact=False).fill(name)
            page.locator('#payment-rules-confirmed').check()
            page.locator('#sign-form').get_by_role('button',name='Sign agreement',exact=True).click()
            expect(page.locator('#dialog')).not_to_be_visible()
        c.reload();c.get_by_role('tab',name='Payments',exact=True).click()
        expect(c.locator('[data-milestone-card="M1"]')).to_contain_text('Freelancer payout: received')
        expect(c.locator('[data-milestone-card="M2"] .payment-amount')).to_have_text('$15.00')
        c.locator('[data-milestone-card="M2"] [data-pay-start]').click()
        c.get_by_role('button',name='Verify buyer approval',exact=True).click()
        expect(c.locator('[data-milestone-card="M2"] .pill').first).to_have_text('authorized')
        assert c.url.endswith('/client/projects/'+pid)
        assert c.evaluate('S.room.project.active_agreement_id')!=paid['agreement_id']
        c.set_viewport_size({'width':390,'height':844})
        assert c.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors
        browser.close()
