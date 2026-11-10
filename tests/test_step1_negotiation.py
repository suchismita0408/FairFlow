import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import pytest
from sqlalchemy import select,update,text
from fastapi.testclient import TestClient
import main
from app import db, negotiation
from app.agreement_examples import example_agreement
from test_step1_auth_validation import portals

def endpoint(c,role,pid=''):return '/api/'+role+'/projects'+('/'+pid if pid else '')
def call(c,role,room,action,status=200,**kw):
    if action=='sign':kw.setdefault('payment_rules_confirmed',True)
    r=c.post(endpoint(c,role,room['project']['id'])+'/actions',json=dict(revision=room['revision'],action=action,**kw))
    assert r.status_code==status,r.text
    return r.json()
def read(c,role,room):
    r=c.get(endpoint(c,role,room['project']['id']));assert r.status_code==200,r.text;return r.json()
def create(c):
    # Existing fully specified drafts remain supported after the guided-intake upgrade.
    r=c.post('/api/client/projects',json={'agreement':{}});assert r.status_code==201,r.text
    pid=r.json()['project']['id']
    with db.engine().begin() as conn:
        row=db.one(conn,db.projects,db.projects.c.id==pid);p=row['payload'];p['draft']=example_agreement();p['guided']=False
        conn.execute(update(db.projects).where(db.projects.c.id==pid).values(payload=p))
    return c.get('/api/client/projects/'+pid).json()
def start(portals):
    c=portals('client');f=portals('freelancer');room=create(c)
    room=call(c,'client',room,'invite',recipient='freelancer@example.com')
    room=call(f,'freelancer',room,'open')
    return c,f,room
def approve_both(c,f,room):
    sha=room['versions'][-1]['digest']
    room=call(c,'client',room,'approve',version_hash=sha)
    room=call(f,'freelancer',room,'approve',version_hash=sha)
    assert room['project']['status']=='READY_TO_SIGN'
    return room,sha
def sign_both(c,f,room):
    room,sha=approve_both(c,f,room)
    room=call(c,'client',room,'sign',version_hash=sha,typed_name='Alice Baker')
    assert room['project']['status']=='PARTIALLY_SIGNED'
    room=call(f,'freelancer',room,'sign',version_hash=sha,typed_name='Rahul Kumar')
    assert room['project']['status']=='AGREED'
    return room

def test_lifecycle_counter_blocked_invisible_then_sign_and_exports(portals):
    c,f,r=start(portals);pid=r['project']['id'];old=copy.deepcopy(r['agreement'])
    r=call(c,'client',r,'approve',version_hash=r['versions'][-1]['digest'])
    bad=copy.deepcopy(r['agreement']);bad['payment']['total_minor']+=1
    save=f.put(endpoint(f,'freelancer',pid)+'/draft',json=dict(revision=r['revision'],agreement=bad));assert save.status_code==200,save.text;r=save.json()
    assert any(x['severity']=='BLOCKER' for x in r['flags'])
    call(f,'freelancer',r,'send',422)
    client_view=read(c,'client',r)
    assert client_view['agreement']==old and len(client_view['versions'][-1]['approvals'])==1
    good=copy.deepcopy(old);good['scope']['assumptions']=['Client supplies the approved brand guide.']
    save=f.put(endpoint(f,'freelancer',pid)+'/draft',json=dict(revision=r['revision'],agreement=good));assert save.status_code==200;r=save.json()
    r=call(f,'freelancer',r,'send');assert r['project']['turn']=='client' and len(r['versions'])==2
    assert r['versions'][0]['voided']
    r=sign_both(c,f,r)
    assert c.put(endpoint(c,'client',pid)+'/draft',json=dict(revision=r['revision'],agreement=good)).status_code==409
    root=endpoint(c,'client',pid)
    exported=c.get(root+'/export/json');assert exported.status_code==200
    assert 'attachment;' in exported.headers['content-disposition']
    assert exported.json()['agreement']==good
    assert c.get(root+'/export/pdf').content.startswith(b'%PDF-')
    assert c.get(root+'/contract-summary').json()['version_hash']==exported.json()['sha256']

def test_signature_voiding_and_amendment_old_active_until_both_sign(portals):
    c,f,r=start(portals);r,sha=approve_both(c,f,r)
    r=call(c,'client',r,'sign',version_hash=sha,typed_name='Alice Baker')
    r=call(c,'client',r,'counter');draft=r['agreement'];draft['basics']['background']='Updated brand guide.'
    save=c.put(endpoint(c,'client',r['project']['id'])+'/draft',json=dict(revision=r['revision'],agreement=draft));assert save.status_code==200;r=save.json()
    assert r['versions'][-1]['voided'] and len(r['versions'][-1]['signatures'])==1
    call(f,'freelancer',r,'sign',409,version_hash=sha,typed_name='Rahul Kumar')
    r=call(c,'client',r,'send');r=sign_both(c,f,r)
    active=r['project']['active_agreement_id']
    r=call(f,'freelancer',r,'amend');draft=r['agreement'];draft['communication']['response_hours']=12
    save=f.put(endpoint(f,'freelancer',r['project']['id'])+'/draft',json=dict(revision=r['revision'],agreement=draft));assert save.status_code==200;r=save.json()
    r=call(f,'freelancer',r,'send')
    assert r['project']['active_agreement_id']==active
    assert c.get(endpoint(c,'client',r['project']['id'])+'/contract-summary').json()['agreement_id']==active
    r=call(c,'client',r,'reject-amendment',reason='Keep the current response window.')
    assert r['project']['status']=='AGREED' and r['project']['active_agreement_id']==active
    r=call(c,'client',r,'amend');r=call(c,'client',r,'send');r=sign_both(c,f,r)
    assert r['project']['active_agreement_id']!=active

def test_rejection_requires_reason_and_dispute_resolution(portals):
    c,f,r=start(portals)
    call(f,'freelancer',r,'reject',422,decisions=[dict(field_path='payment.total_minor',decision='reject')])
    r=call(f,'freelancer',r,'reject',decisions=[dict(field_path='payment.total_minor',decision='reject',reason='Please confirm the budget.')])
    call(c,'client',r,'send',409)
    r=call(c,'client',r,'resolve-field',field_path='payment.total_minor',reason='Budget confirmed after discussion.')
    r=call(c,'client',r,'send');assert not r['project']['disputes']

def test_warning_acknowledgments_are_immutable_and_cannot_be_forged(portals):
    c=portals('client');f=portals('freelancer');r=create(c);a=r['agreement'];a['basics']['background']='Reasonable support.'
    r=c.put(endpoint(c,'client',r['project']['id'])+'/draft',json=dict(revision=r['revision'],agreement=a)).json()
    r=call(c,'client',r,'invite',recipient='freelancer@example.com');r=call(f,'freelancer',r,'open')
    sha=r['versions'][-1]['digest'];call(c,'client',r,'approve',422,version_hash=sha)
    flag=next(x for x in r['flags'] if x['severity']=='WARNING')
    call(c,'client',r,'acknowledge',422,flag_id=flag['id'])
    r=call(c,'client',r,'acknowledge',flag_id=flag['id'],reason='Limited to the listed deliverables.')
    r=call(f,'freelancer',r,'acknowledge',flag_id=flag['id'],reason='Confirmed the measurable acceptance criteria.')
    assert len(r['versions'])==3 and r['flags'][0]['status']=='acknowledged'
    r=sign_both(c,f,r)
    assert r['versions'][-1]['payload']['acknowledgments'][flag['id']]['client']

@pytest.mark.parametrize('action,expected',[('withdraw','WITHDRAWN'),('decline','DECLINED'),('cancel','CANCELLED_BEFORE_AGREEMENT')])
def test_side_states_and_illegal_transitions(portals,action,expected):
    c=portals('client');f=portals('freelancer');r=create(c)
    call(c,'client',r,'sign',409)
    bad=copy.deepcopy(r['agreement']);bad['basics']['summary']=''
    draft=c.put(endpoint(c,'client',r['project']['id'])+'/draft',json=dict(revision=r['revision'],agreement=bad)).json()
    call(c,'client',draft,'invite',422,recipient='freelancer@example.com')
    assert read(c,'client',r)['project']['status']=='DRAFT'
    r=c.put(endpoint(c,'client',r['project']['id'])+'/draft',json=dict(revision=draft['revision'],agreement=example_agreement())).json()
    r=call(c,'client',r,'invite',recipient='freelancer@example.com')
    if action=='cancel':r=call(f,'freelancer',r,'open');call(c,'client',r,'withdraw',403)
    actor=f if action=='decline' else c;role='freelancer' if action=='decline' else 'client'
    r=call(actor,role,r,action,reason='Changed availability.',reason_code='capacity')
    assert r['project']['status']==expected
    assert f.post(endpoint(f,'freelancer',r['project']['id'])+'/chat',json={'body':'Hello'}).status_code==409

def test_expiry_reminders_reinvite_and_email_recipient_binding(portals):
    c=portals('client');r=create(c);r=call(c,'client',r,'invite',recipient='future@example.com')
    outsider=portals('freelancer','outsider@example.com')
    iid=r['project']['invitation_id']
    assert outsider.post('/api/freelancer/invitations/'+iid+'/open',json={}).status_code==404
    with db.engine().begin() as conn:
        p,rev=negotiation.project(conn,r['project']['id'],{'id':c.get('/api/auth/me').json()['user']['id'],'role':'client'},True)
        p['invitation_expires_at']=(datetime.now(timezone.utc)+timedelta(hours=23)).isoformat();negotiation.write(conn,p,rev)
    negotiation.sweep_invitations();negotiation.sweep_invitations()
    with db.engine().connect() as conn:assert len(db.rows(conn,db.audit,(db.audit.c.project_id==r['project']['id'])&(db.audit.c.kind=='invitation_reminder')))==2
    with db.engine().begin() as conn:
        row=db.one(conn,db.projects,db.projects.c.id==r['project']['id']);p=row['payload'];p['invitation_expires_at']='2000-01-01T00:00:00+00:00';negotiation.write(conn,p,row['version'])
    negotiation.sweep_invitations();r=read(c,'client',r);assert r['project']['status']=='EXPIRED'
    r=call(c,'client',r,'invite',recipient='future@example.com');assert r['project']['status']=='INVITED'
    future=portals('freelancer','future@example.com');inbox=future.get('/api/freelancer/inbox').json()
    opened=future.post('/api/freelancer/invitations/'+r['project']['invitation_id']+'/open',json={})
    assert opened.status_code==200,opened.text

def test_membership_turn_concurrency_and_append_only(portals):
    c,f,r=start(portals);outsider=portals('client','outsider@example.com');pid=r['project']['id']
    assert outsider.get('/api/client/projects/'+pid).status_code==404
    assert outsider.get('/api/client/projects').json()==[]
    assert c.get('/api/freelancer/projects/'+pid).status_code==403
    assert f.get('/client/dashboard').status_code==403
    assert c.put('/api/client/projects/'+pid+'/draft',json=dict(revision=r['revision'],agreement=r['agreement'])).status_code==409
    body=dict(revision=r['revision'],agreement=r['agreement'])
    def save(_):return f.put('/api/freelancer/projects/'+pid+'/draft',json=body).status_code
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(save,range(2)))==[200,409]
    r=read(f,'freelancer',r);r,sha=approve_both(c,f,r)
    def sign(_):return c.post('/api/client/projects/'+pid+'/actions',json=dict(revision=r['revision'],action='sign',version_hash=sha,typed_name='Alice Baker',payment_rules_confirmed=True)).status_code
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(sign,range(2)))==[200,409]
    with db.engine().begin() as conn:
        with pytest.raises(Exception):conn.execute(update(db.agreements).where(db.agreements.c.id==r['versions'][-1]['id']).values(digest='forged'))
    assert f.post('/api/freelancer/projects/'+pid+'/chat',json={'body':'Discuss the specification.','field_path':'scope.deliverables.0.specification'}).status_code==200
    with db.engine().begin() as conn:
        with pytest.raises(Exception):conn.execute(text("UPDATE chat_messages SET body='changed'"))

@pytest.mark.parametrize('method,suffix,body',[
    ('get','agreement-schema',None),('get','example-terms',None),('get','profile',None),('get','projects',None),('get','inbox',None),('get','notifications',None),
    ('post','validate',{'agreement':{}}),('post','projects',{'agreement':{}}),('get','projects/unknown',None),
    ('put','projects/unknown/draft',{'revision':1,'agreement':{}}),('post','projects/unknown/actions',{'revision':1,'action':'approve'}),
    ('post','projects/unknown/chat',{'body':'Hello'}),('post','projects/unknown/chat/read',{}),('get','projects/unknown/events',None),
    ('get','projects/unknown/export/json',None),('get','projects/unknown/contract-summary',None),('post','invitations/unknown/open',{}),
])
def test_every_portal_route_denies_opposite_and_anonymous(portals,method,suffix,body):
    c=portals('client');f=portals('freelancer');anon=TestClient(main.app)
    kwargs={'json':body} if body is not None else {}
    for actor,role in ((c,'freelancer'),(f,'client')):
        response=getattr(actor,method)(f'/api/{role}/{suffix}',**kwargs)
        assert response.status_code==403,response.text
    assert getattr(anon,method)('/api/client/'+suffix,**kwargs).status_code==401

@pytest.mark.parametrize('method,suffix,body',[
    ('get','',None),('put','/draft',{'revision':1,'agreement':{}}),('post','/actions',{'revision':1,'action':'approve'}),
    ('post','/chat',{'body':'Hello'}),('post','/chat/read',{}),('get','/events',None),('get','/export/json',None),('get','/contract-summary',None),
])
def test_every_project_route_hides_existence_from_nonmembers(portals,method,suffix,body):
    c,f,r=start(portals)
    kwargs={'json':body} if body is not None else {}
    for role in ('client','freelancer'):
        outsider=portals(role,role+'-outsider@example.com')
        response=getattr(outsider,method)(f'/api/{role}/projects/'+r['project']['id']+suffix,**kwargs)
        assert response.status_code==404,response.text

def test_security_url_request_size_csrf_and_invitation_edit(portals):
    c,f,r=start(portals);pid=r['project']['id']
    token=f.headers.pop('x-csrf-token')
    assert f.post('/api/freelancer/projects/'+pid+'/chat',json={'body':'Hello'}).status_code==403
    f.headers['x-csrf-token']=token
    assert f.post('/api/freelancer/validate',content='x'*(1024*1024+1),headers={'Content-Type':'application/json'}).status_code==413
    from app.agreement_validation import validate
    bad=example_agreement();bad['attachments']['links']=[dict(label='Invalid link',url='javascript:alert(1)',binding=False)]
    assert any(x['severity']=='BLOCKER' and 'HTTP' in x['message'] for x in validate(bad))
    call(c,'client',r,'edit-invitation',403)
