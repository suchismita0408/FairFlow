import asyncio
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
import main
from app import db, worker, store

@pytest.fixture
def platform(tmp_path, monkeypatch, isolate_legacy_configuration):
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'platform.db'))
    monkeypatch.setenv('AUTH_ENABLED','true')
    monkeypatch.setenv('EMAIL_MODE','local')
    monkeypatch.setenv('TOKEN_ENCRYPTION_KEY',Fernet.generate_key().decode())
    monkeypatch.setenv('AI_MODE','demo')
    monkeypatch.setattr(main,'MODE','demo')
    db.metadata.create_all(db.engine())
    clients=[]
    def account(email):
        c=TestClient(main.app);clients.append(c)
        r=c.post('/api/auth/register',json={'email':email,'name':email.split('@')[0],'password':'Test-password-123!'})
        assert r.status_code==201,r.text
        c.headers['x-csrf-token']=r.json()['csrf_token']
        message=c.get('/api/auth/mail').json()[0]
        assert c.post('/api/auth/verify',json={'token':message['body'].split('#verify=')[1]}).status_code==200
        return c
    yield account
    for c in clients:c.close()

def post(c,path,body=None,status=200):
    r=c.post('/api/'+path,json=body or {})
    assert r.status_code==status,r.text
    return r.json()

def room(account,currency='USD',fund=True):
    client=account('client@example.com');freelancer=account('freelancer@example.com')
    data={'creator_role':'client','counterparty_email':'freelancer@example.com','client_name':'Client','freelancer_name':'Freelancer',
          'contract':{'title':'Static website','task_description':'Deliver a responsive static website with accessible navigation.','price_cents':10000,'allowed_revisions':1,'hourly_rate_cents':5000,'deliverables':['Static website'],'exclusions':['Database']},
          'milestones':[{'title':'Website delivery','scope':'Responsive site and accessible navigation delivered.','duration_days':14,'amount_cents':10000}],
          'clauses':[{'reference':ref,'kind':kind,'text':text} for ref,kind,text in [('D1','deliverable','Deliver the responsive static website.'),('A1','acceptance','Navigation works on mobile and desktop.'),('B1','defect_policy','Fix broken agreed functionality without charging revisions.'),('R1','revision_policy','One presentation revision is included.')]],
          'work_items':[[{'title':'Website layout','acceptance':'Responsive layout matches the approved design.','amount_cents':8000},{'title':'Navigation','acceptance':'Keyboard navigation works on desktop and mobile.','amount_cents':2000}]]}
    from app.agreement_checks import example_details
    data['details']=example_details()
    data['currency']=currency
    r=post(client,'platform/projects',data,201);pid=r['project']['id']
    invitation=next(m for m in freelancer.get('/api/auth/mail').json() if '#invite=' in m['body'])
    post(freelancer,'platform/invitations/accept',{'token':invitation['body'].split('#invite=')[1]})
    version=r['agreements'][0]
    for c in (client,freelancer):r=post(c,f"platform/projects/{pid}/agreements/{version['id']}/accept",{'digest':version['digest']})
    p=r['project'];root='agile/projects/'+pid
    post(client,root+'/vault/setup',{'agreed':True,'plan_hash':p['plan_hash']})
    post(client,root+'/vault/confirm')
    if fund:post(client,root+'/milestones/'+p['milestones'][0]['id']+'/fund')
    return client,freelancer,pid,p['milestones'][0]['id']

def test_membership_csrf_and_role_boundaries(platform):
    c,f,pid,mid=room(platform);outsider=platform('outsider@example.com')
    assert outsider.get('/api/platform/projects/'+pid).status_code==404
    assert outsider.get('/api/platform/projects').json()==[]
    post(f,f'agile/projects/{pid}/milestones/{mid}/approve',status=403)
    post(c,f'agile/projects/{pid}/milestones/{mid}/deliver',{'url':'https://example.com/work','notes':'Completed work'},403)
    post(f,f'agile/projects/{pid}/milestones/{mid}/progress',{},409)
    csrf=c.headers.pop('x-csrf-token')
    post(c,f'platform/projects/{pid}/messages',{'body':'Hello'},403)
    c.headers['x-csrf-token']=csrf
    post(c,f'platform/projects/{pid}/messages',{'body':'Hello'})
    with db.engine().connect() as conn:
        payload=db.one(conn,db.projects,db.projects.c.id==pid)['payload']
        assert 'token_id' not in payload['vault']
        assert payload['vault']['encrypted_token']

def test_defect_requires_both_votes_and_preserves_revision_allowance(platform):
    c,f,pid,mid=room(platform);root=f'platform/projects/{pid}'
    r=post(c,root+'/decisions',{'milestone_id':mid,'request':'The agreed navigation is broken.'})
    d=r['decisions'][0]
    assert d['result']['category']=='defect'
    r=post(c,root+'/actions',{'kind':'decision','milestone_id':mid,'decision_id':d['id'],'category':'defect','reason':'Both parties reviewed the broken navigation.'})
    a=r['actions'][0]
    asyncio.run(worker.tick())
    assert store.get(pid)['milestones'][0]['status']=='active'
    post(f,root+'/actions/'+a['id']+'/accept',{'digest':a['digest']})
    asyncio.run(worker.tick())
    p=store.get(pid)
    assert p['milestones'][0]['status']=='revision_requested'
    assert p['milestones'][0]['revisions_used']==0
    post(c,root+'/actions',a['payload'],409)

def test_priced_partial_capture_and_balanced_ledger(platform):
    c,f,pid,mid=room(platform);root=f'platform/projects/{pid}'
    r=post(f,root+'/evidence',{'milestone_id':mid,'title':'Layout evidence','url':'https://example.com/layout','description':'The responsive layout has been delivered for review.'})
    item=r['project']['milestones'][0]['items'][0]
    r=post(f,root+'/actions',{'kind':'partial_items','milestone_id':mid,'reason':'Accept the delivered layout and carry navigation forward.','item_ids':[item['id']],'evidence_id':r['evidence'][0]['id']})
    a=r['actions'][0];post(c,root+'/actions/'+a['id']+'/accept',{'digest':a['digest']})
    asyncio.run(worker.tick())
    r=c.get('/api/'+root).json()
    assert r['actions'][0]['status']=='applied',r['jobs']
    assert r['project']['milestones'][0]['settled_cents']==8000
    carry=r['project']['milestones'][1]
    assert carry['amount_cents']==sum(i['amount_cents'] for i in carry['items'])==2000
    assert len(r['ledger'])==2
    assert sum(x['amount_cents'] for x in r['ledger'])==0
    asyncio.run(worker.tick())
    assert len(c.get('/api/'+root).json()['ledger'])==2

def test_demo_draft_separates_budget_from_hourly_rate(platform):
    c=platform('draft@example.com')
    r=post(c,'agile/draft',{'chat_history':'Build a static website for $900 with 2 revisions. Extra work is $50 per hour.','milestone_count':3})
    assert r['contract']['price_cents']==90000
    assert r['contract']['hourly_rate_cents']==5000

def test_reset_revokes_sessions_and_token_is_single_use(platform):
    c=platform('reset@example.com')
    post(c,'auth/forgot-password',{'email':'reset@example.com'})
    message=next(m for m in c.get('/api/auth/mail').json() if '#reset=' in m['body'])
    body={'token':message['body'].split('#reset=')[1],'password':'Changed-password-456!'}
    post(c,'auth/reset-password',body)
    assert c.get('/api/auth/me').status_code==401
    post(c,'auth/reset-password',body,400)
    post(c,'auth/login',{'email':'reset@example.com','password':body['password']})

def test_refund_needs_joint_consent_and_cannot_exceed_capture(platform):
    c,f,pid,mid=room(platform);root=f'platform/projects/{pid}'
    post(f,f'agile/projects/{pid}/milestones/{mid}/deliver',{'url':'https://example.com/work','notes':'All agreed work has been delivered.'})
    p=post(c,f'agile/projects/{pid}/milestones/{mid}/approve')
    cap=p['payments'][0]['captures'][0]['id']
    body={'kind':'refund','milestone_id':mid,'capture_id':cap,'amount_cents':2500,'reason':'Both participants agree to this partial refund.'}
    r=post(c,root+'/actions',body);a=r['actions'][0]
    asyncio.run(worker.tick())
    assert not store.get(pid)['payments'][0].get('refund_operations')
    post(f,root+'/actions/'+a['id']+'/accept',{'digest':a['digest']})
    asyncio.run(worker.tick())
    r=c.get('/api/'+root).json()
    assert r['actions'][0]['status']=='applied',r['jobs']
    assert len(r['ledger'])==4
    assert sum(x['amount_cents'] for x in r['ledger'])==0
    body['amount_cents']=8000
    r=post(c,root+'/actions',body);a=r['actions'][-1]
    post(f,root+'/actions/'+a['id']+'/accept',{'digest':a['digest']})
    asyncio.run(worker.tick())
    assert len(store.get(pid)['payments'][0]['refund_operations'])==1
    assert c.get('/api/'+root).json()['actions'][-1]['status']=='needs_review'

def test_unknown_model_citations_rejected(platform,monkeypatch):
    from app import decisions,ai
    from app.platform_models import Recommendation
    from fastapi import HTTPException
    monkeypatch.setenv('AI_MODE','gemini')
    async def fabricated(*args):
        return Recommendation(category='additional_scope',clause_ids=['INVENTED'],evidence_ids=[],explanation='Unsupported invented contract reference.',missing_information=[],recommended_action='Review proposed addition.',estimated_hours=10)
    monkeypatch.setattr(ai,'structured',fabricated)
    with pytest.raises(HTTPException) as error:
        asyncio.run(decisions.recommend([],[],'Build a backend','Static website'))
    assert error.value.status_code==502

@pytest.mark.parametrize('currency',['EUR','JPY'])
def test_currency_survives_agreement_payment_capture_and_ledger(platform,currency):
    c,f,pid,mid=room(platform,currency)
    post(f,f'agile/projects/{pid}/milestones/{mid}/deliver',{'url':'https://example.com/work','notes':'All agreed work delivered.'})
    p=post(c,f'agile/projects/{pid}/milestones/{mid}/approve')
    assert p['currency']==p['payments'][0]['currency']==currency
    r=c.get(f'/api/platform/projects/{pid}').json()
    assert r['agreements'][0]['payload']['currency']==currency
    assert {x['currency'] for x in r['ledger']}=={currency}

def test_currency_precision_and_sandbox_gate():
    from app.currencies import value,funding_available
    from fastapi import HTTPException
    assert value(12300,'JPY')=='123'
    assert value(12345,'EUR')=='123.45'
    with pytest.raises(ValueError):value(12345,'JPY')
    with pytest.raises(ValueError):value(100,'XYZ')
    with pytest.raises(HTTPException):funding_available({'mode':'sandbox','currency':'JPY'})


def test_revised_currency_invalidates_saved_method_consent(platform):
    c,f,pid,mid=room(platform,fund=False)
    root=f'platform/projects/{pid}'
    before=c.get('/api/'+root).json()
    old=before['agreements'][0]
    body={**old['payload'],'currency':'EUR','creator_role':'client',
          'counterparty_email':'freelancer@example.com','client_name':'Client','freelancer_name':'Freelancer'}
    revised=post(c,root+'/agreements',body)
    assert revised['project']['vault']=={'status':'not_connected'}
    assert revised['project']['accepted']==[]
    post(c,root+f"/agreements/{old['id']}/accept",{'digest':old['digest']},409)
    post(c,f'agile/projects/{pid}/vault/confirm',status=409)
    current=next(v for v in revised['agreements'] if v['id']==revised['project']['agreement_id'])
    for participant in (c,f):post(participant,root+f"/agreements/{current['id']}/accept",{'digest':current['digest']})
    mid=revised['project']['milestones'][0]['id']
    result=post(c,f'agile/projects/{pid}/milestones/{mid}/fund')
    assert result['milestones'][0]['status']=='awaiting_payment'
    assert result['payments'][0]['currency']=='EUR'


def test_fractional_yen_proposal_rejected_without_payment(platform):
    c,f,pid,mid=room(platform,'JPY')
    post(c,f'platform/projects/{pid}/actions',{
        'kind':'refund','milestone_id':mid,'capture_id':'not-created',
        'amount_cents':125,'reason':'Reject fractional yen before any provider request.'},422)
    assert c.get(f'/api/platform/projects/{pid}').json()['actions']==[]


def test_conflicting_source_cannot_bypass_extraction_with_direct_creation(platform):
    c,f,pid,mid=room(platform,fund=False)
    room_data=c.get(f'/api/platform/projects/{pid}').json()
    terms=room_data['agreements'][0]['payload']
    body={**terms,'creator_role':'client','counterparty_email':'freelancer@example.com',
          'client_name':'Client','freelancer_name':'Freelancer',
          'source_conversation':'Freelancer: $900 with 2 revisions. Client: Not agreed, 800 dollars. Freelancer: Okay 850$.'}
    before=len(c.get('/api/platform/projects').json())
    post(c,'platform/projects',body,422)
    post(c,f'platform/projects/{pid}/agreements',body,422)
    assert len(c.get('/api/platform/projects').json())==before
    assert len(c.get(f'/api/platform/projects/{pid}').json()['agreements'])==1


def test_existing_bad_agreement_is_flagged_and_cannot_be_accepted_or_funded(platform):
    from sqlalchemy import update,delete
    from app.workspace import hash_payload
    c,f,pid,mid=room(platform,fund=False)
    p=store.get(pid)
    # Reproduce a record created by the old extractor, which copied the chat into scope.
    bad='Client: Landing page. Freelancer: $900 with 2 revisions. Client: Not Agreed, 800 dollars. Freelancer: Okay 850$.'
    with db.engine().begin() as conn:
        version=db.one(conn,db.agreements,db.agreements.c.id==p['agreement_id'])
        terms=version['payload'];terms['contract']['task_description']=bad
        digest=hash_payload(terms)
        conn.execute(update(db.agreements).where(db.agreements.c.id==version['id']).values(payload=terms,digest=digest,status='proposed'))
        conn.execute(delete(db.acceptances).where(db.acceptances.c.agreement_id==version['id']))
        p['contract']['task_description']=bad;p['accepted']=[]
        db.save_project(p,conn)
    detail=c.get(f'/api/platform/projects/{pid}').json()
    assert detail['agreements'][0]['validation_issues']
    post(c,f"platform/projects/{pid}/agreements/{version['id']}/accept",{'digest':digest},409)
    post(c,f'agile/projects/{pid}/milestones/{mid}/fund',status=409)
    assert not c.get(f'/api/platform/projects/{pid}').json()['agreements'][0]['acceptances']
    assert not store.get(pid)['payments']


@pytest.mark.parametrize('field',[
 'client_legal_name','freelancer_legal_name','dependencies','handoff','ownership',
 'third_party_materials','confidentiality','cancellation','expenses','governing_law','liability'])
def test_each_policy_is_mandatory_and_cannot_be_blank(platform,field):
    c,f,pid,mid=room(platform,fund=False)
    terms=c.get(f'/api/platform/projects/{pid}').json()['agreements'][0]['payload']
    body={**terms,'creator_role':'client','counterparty_email':'freelancer@example.com','client_name':'Client','freelancer_name':'Freelancer'}
    body['details'][field]='          '
    report=post(c,'platform/agreements/validate',body)
    assert not report['valid']
    post(c,'platform/projects',body,422)

@pytest.mark.parametrize('case',[
 'total','items','price_source','rate_source','revision_source','revision_clause','unlimited',
 'review_window','schedule','dispute','placeholder','scope_exclusion','duplicate','party','missing_items',
 'empty_deliverable','duration','currency','negative','fractional','missing_policy','defect_charge','hours_review'])
def test_final_validation_blocks_contradiction_matrix(platform,case):
    c,f,pid,mid=room(platform,fund=False)
    terms=c.get(f'/api/platform/projects/{pid}').json()['agreements'][0]['payload']
    body={**terms,'creator_role':'client','counterparty_email':'freelancer@example.com','client_name':'Client','freelancer_name':'Freelancer'}
    if case=='total':body['milestones'][0]['amount_cents']=9900
    elif case=='items':body['work_items'][0][0]['amount_cents']=1
    elif case=='price_source':body['source_conversation']='Build a static website for $900, including 1 revision.'
    elif case=='rate_source':body['source_conversation']='Build a static website for $100, including 1 revision. Extra work $60 per hour.'
    elif case=='revision_source':body['source_conversation']='Build a static website for $100, including 2 revisions.'
    elif case=='revision_clause':body['clauses'][-1]['text']='Includes 3 revisions per milestone.'
    elif case=='unlimited':body['clauses'][-1]['text']='Unlimited revisions are included.'
    elif case=='review_window':body['clauses'][1]['text']='Delivery includes a 3-day review window.'
    elif case=='schedule':body['details']['schedule_basis']='business_days'
    elif case=='dispute':body['details']['dispute_process']='admin_override'
    elif case=='placeholder':body['details']['ownership']='to be confirmed'
    elif case=='scope_exclusion':body['contract']['exclusions']=['Static website']
    elif case=='duplicate':body['contract']['deliverables']=['Static website','Static website']
    elif case=='party':body['details']['freelancer_legal_name']=body['details']['client_legal_name']
    elif case=='missing_items':body['work_items']=[]
    elif case=='empty_deliverable':body['contract']['deliverables']=['   ']
    elif case=='duration':body['milestones'][0]['duration_days']=30
    elif case=='currency':body['currency']='XYZ'
    elif case=='negative':body['contract']['hourly_rate_cents']=-1
    elif case=='fractional':body['work_items'][0][0]['amount_cents']=8000.5
    elif case=='missing_policy':del body['details']['review_days']
    elif case=='defect_charge':body['clauses'][2]['text']='Defect fixes consume presentation revisions.'
    elif case=='hours_review':body['clauses'][1]['text']='There is a 72-hour review window.'
    report=post(c,'platform/agreements/validate',body)
    assert not report['valid'],case
    assert report['issues'],case
    post(c,'platform/projects',body,422)
    assert len(c.get('/api/platform/projects').json())==1


def test_gemini_cache_tracks_exact_document_and_outage_blocks_writes(platform,monkeypatch):
    from app import ai
    from fastapi import HTTPException
    c,f,pid,mid=room(platform,fund=False)
    terms=c.get(f'/api/platform/projects/{pid}').json()['agreements'][0]['payload']
    body={**terms,'creator_role':'client','counterparty_email':'freelancer@example.com','client_name':'Client','freelancer_name':'Freelancer'}
    monkeypatch.setenv('AI_MODE','gemini')
    calls=[]
    async def ready(schema,instruction,data):
        calls.append(data)
        return schema(status='ready',issues=[])
    monkeypatch.setattr(ai,'structured',ready)
    for _ in range(2):assert post(c,'platform/agreements/validate',body)['valid']
    assert len(calls)==1
    body['details']['ownership']+=' Portfolio use requires written permission.'
    assert post(c,'platform/agreements/validate',body)['valid']
    assert len(calls)==2
    async def outage(*args):raise HTTPException(502,'Gemini unavailable; no offline substitution.')
    monkeypatch.setattr(ai,'structured',outage)
    body['details']['confidentiality']+=' Review sensitive data access jointly.'
    post(c,'platform/projects',body,502)
    assert len(c.get('/api/platform/projects').json())==1
