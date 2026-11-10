"""Real two-party UI for policy consent, cancellation and evidence downloads."""
from datetime import timedelta,date
import base64
import httpx
from playwright.sync_api import sync_playwright,expect
from test_step1_browser import step1_server
from test_step1_assurance import signed_short_project


def test_two_party_resolution_policy_closure_and_evidence_ui(step1_server,tmp_path):
    clients={}
    for role,name in [('client','Alice Baker'),('freelancer','Rahul Kumar')]:
        c=httpx.Client(base_url=step1_server,timeout=20)
        body={'name':name,'email':role+'@example.com','password':'Workflow-browser-password-123!','country':'IN','time_zone':'Asia/Kolkata'}
        if role=='freelancer':body.update(professional_title='Developer',primary_category='Frontend',skills=['CSS'])
        r=c.post('/api/'+role+'/signup',json=body);assert r.status_code==201,r.text
        c.headers['x-csrf-token']=r.json()['csrf_token']
        token=next(m['body'].split('#verify=')[1] for m in c.get('/api/auth/mail').json() if '#verify=' in m['body'])
        assert c.post('/api/auth/verify',json={'token':token}).status_code==200
        clients[role]=c
    c,f,r=signed_short_project(lambda role:clients[role]);pid=r['project']['id']
    raw=b'body { color: blue; }'
    uploaded=f.post(f'/api/freelancer/projects/{pid}/workflow/files',json={'filename':'handover.css','content_base64':base64.b64encode(raw).decode()})
    assert uploaded.status_code==200
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True);pages={};errors=[]
        for role in ('client','freelancer'):
            context=browser.new_context(viewport={'width':1360,'height':980})
            context.add_cookies([{'name':cookie.name,'value':cookie.value,'url':step1_server} for cookie in clients[role].cookies.jar])
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(step1_server+f'/{role}/projects/{pid}?tab=Resolution')
            expect(page.get_by_role('heading',name='Work, deadlines & resolution')).to_be_visible()
            pages[role]=page
        c,f=pages['client'],pages['freelancer']
        c.get_by_role('button',name='Activate delivery protections',exact=True).click()
        c.get_by_label('Reason and proposed arrangement',exact=False).fill('We agree to explicit deadline, grace, notice and defect protection rules.')
        c.get_by_role('button',name='Create proposal',exact=True).click()
        expect(c.locator('#dialog')).not_to_be_visible()
        def accept(page,name):
            page.reload();page.get_by_role('tab',name='Resolution',exact=True).click()
            page.get_by_role('button',name='Review & accept',exact=True).last.click()
            page.get_by_label('Type your account name',exact=False).fill(name)
            page.get_by_role('button',name='Accept these terms',exact=True).click()
            expect(page.locator('#dialog')).not_to_be_visible()
        accept(c,'Alice Baker');accept(f,'Rahul Kumar')
        expect(f.get_by_text('Delivery protections active',exact=True)).to_be_visible()
        c.reload();c.get_by_role('tab',name='Resolution',exact=True).click()
        with c.expect_download() as info:c.get_by_role('link',name='handover.css',exact=True).click()
        download=info.value;download.save_as(tmp_path/'download.css');assert (tmp_path/'download.css').read_bytes()==raw
        c.get_by_role('button',name='Request cancellation',exact=True).click()
        c.get_by_label('Reason and proposed arrangement',exact=False).fill('We agree to stop the remaining unstarted work without payment.')
        c.get_by_role('button',name='Create proposal',exact=True).click()
        expect(c.locator('#dialog')).not_to_be_visible()
        accept(c,'Alice Baker');accept(f,'Rahul Kumar')
        expect(f.get_by_text('Remaining work closed',exact=True)).to_be_visible()
        c.reload();c.get_by_role('tab',name='Resolution',exact=True).click()
        c.set_viewport_size({'width':390,'height':844})
        assert c.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert errors==[],errors
        browser.close()
    for c in clients.values():c.close()
