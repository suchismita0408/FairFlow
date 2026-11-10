import copy,asyncio
from test_step1_auth_validation import portals
from test_step1_negotiation import start,call,approve_both
from app import agreement_assistant
from app.agreement_examples import example_agreement

def test_short_brief_invites_without_freelancer_work_but_cannot_sign(portals):
    c=portals('client');f=portals('freelancer')
    r=c.post('/api/client/projects',json={'agreement':{}}).json()
    a=r['agreement'];sample=example_agreement()
    a['scope'].update(inclusions=sample['scope']['inclusions'],dependencies=sample['scope']['dependencies']);a['basics']=sample['basics'];a['payment'].update(currency='INR',total_minor=100000)
    a['timeline'].update(start_date=sample['timeline']['start_date'],deadline=sample['timeline']['deadline'])
    r=c.put('/api/client/projects/'+r['project']['id']+'/draft',json={'revision':r['revision'],'agreement':a}).json()
    r=call(c,'client',r,'invite',recipient='freelancer@example.com')
    r=call(f,'freelancer',r,'open')
    assert r['agreement']['parties']['freelancer_name']=='Rahul Kumar'
    assert r['flags']
    call(f,'freelancer',r,'send',422)

def test_backend_ownership_including_deleted_fields(portals):
    c,f,r=start(portals);a=copy.deepcopy(r['agreement']);a['basics']['title']='Unauthorized new title'
    assert c.post('/api/client/projects',json={'agreement':{'scope':{'deliverables':[{'name':'Client cannot author freelancer specifications'}]}}}).status_code==403
    root='/api/freelancer/projects/'+r['project']['id']
    assert f.put(root+'/draft',json={'revision':r['revision'],'agreement':a}).status_code==403
    a=copy.deepcopy(r['agreement']);del a['parties']['client_name']
    assert f.put(root+'/draft',json={'revision':r['revision'],'agreement':a}).status_code==403
    r=call(f,'freelancer',r,'send');r=call(c,'client',r,'counter');a=copy.deepcopy(r['agreement']);a['scope']['deliverables'][0]['specification']='Changed by client'
    assert c.put('/api/client/projects/'+r['project']['id']+'/draft',json={'revision':r['revision'],'agreement':a}).status_code==403

def test_ai_reads_chat_and_cannot_change_terms(portals,monkeypatch):
    c,f,r=start(portals);pid=r['project']['id']
    f.post('/api/freelancer/projects/'+pid+'/chat',json={'body':'I propose 1200 INR, not yet agreed.','field_path':'payment.total_minor'})
    captured={}
    async def fake(agreement,messages):
        captured.update(agreement=agreement,messages=messages)
        return {'available':True,'summary':'Clarify price.','issues':[{'field_path':'payment.total_minor','question':'Do both parties accept 1200 INR?','explanation':'Counteroffer is unanswered.','message_ids':[]}],'suggestions':[]}
    monkeypatch.setattr(agreement_assistant,'review',fake)
    response=f.post('/api/freelancer/projects/'+pid+'/ai-review',json={'revision':r['revision']})
    assert response.status_code==200,response.text
    assert any('1200 INR' in m['body'] for m in captured['messages'])
    after=f.get('/api/freelancer/projects/'+pid).json()
    assert after['agreement']==r['agreement'] and after['revision']==r['revision']
    f.post('/api/freelancer/projects/'+pid+'/chat',json={'body':'New clarification after review.'})
    assert f.put('/api/freelancer/projects/'+pid+'/draft',json={'revision':r['revision'],'agreement':r['agreement'],'review_agreement_hash':response.json()['agreement_hash'],'review_conversation_hash':response.json()['conversation_hash']}).status_code==409
    outsider=portals('freelancer','outside@example.com')
    assert outsider.post('/api/freelancer/projects/'+pid+'/ai-review',json={'revision':r['revision']}).status_code==404

def test_ai_provider_failure_is_explicit_and_citations_checked(monkeypatch):
    monkeypatch.setenv('AI_MODE','gemini');monkeypatch.setenv('GEMINI_API_KEY','test-only')
    async def fake(*args):return agreement_assistant.Review(summary='Unsafe evidence',issues=[agreement_assistant.Issue(field_path='payment.total_minor',question='Price?',explanation='Unsupported',message_ids=['fabricated'])],suggestions=[])
    monkeypatch.setattr(agreement_assistant.ai,'structured',fake)
    assert asyncio.run(agreement_assistant.review(example_agreement(),[]))['available'] is False

def test_ai_cannot_overrule_signed_price_chat_conflict(monkeypatch):
    monkeypatch.setenv('AI_MODE','gemini');monkeypatch.setenv('GEMINI_API_KEY','test-only')
    a=example_agreement();a['payment']['total_minor']=60000;a['timeline']['milestones'][0]['amount_minor']=60000
    async def falsely_clear(*args):
        return agreement_assistant.Review(summary='There are no unresolved issues.',issues=[],suggestions=[])
    monkeypatch.setattr(agreement_assistant.ai,'structured',falsely_clear)
    context={'current_agreement':a,'active_agreement':a,'active_version_hash':'signed-hash','reviewed_versions':[{'id':'v1','number':1,'hash':'signed-hash','payload':a}]}
    result=asyncio.run(agreement_assistant.review(context,[{'id':'chat-1','body':'We accepted milestone 1 at ₹10,000.','kind':'text'}]))
    assert result['available'] is True
    assert result['issues'][0]['field_path']=='timeline.milestones.0.amount_minor'
    assert result['issues'][0]['message_ids']==['chat-1']
    assert 'no unresolved' not in result['summary'].lower()
    assert result['reviewed_versions'][0]['hash']=='signed-hash'

def test_signing_requires_both_price_confirmations_and_new_chat_invalidates_them(portals):
    c,f,r=start(portals);pid=r['project']['id']
    assert f.post('/api/freelancer/projects/'+pid+'/chat',json={'body':'Could milestone 1 be INR 1500 instead of INR 1000?','field_path':'timeline.milestones.0.amount_minor'}).status_code==200
    r,sha=approve_both(c,f,r)
    issue=r['chat_price_conflicts'][0]
    call(c,'client',r,'sign',422,version_hash=sha,typed_name='Alice Baker')
    r=call(c,'client',r,'confirm-chat-price',flag_id=issue['id'],version_hash=sha,reason='The agreement amount is the final agreed price.')
    call(f,'freelancer',r,'sign',422,version_hash=sha,typed_name='Rahul Kumar')
    r=call(f,'freelancer',r,'confirm-chat-price',flag_id=issue['id'],version_hash=sha,reason='The chat figure was only a proposal.')
    assert c.post('/api/client/projects/'+pid+'/chat',json={'body':'Can we change milestone 1 to INR 1800?','field_path':'timeline.milestones.0.amount_minor'}).status_code==200
    call(c,'client',r,'sign',422,version_hash=sha,typed_name='Alice Baker')
    r=c.get('/api/client/projects/'+pid).json()
    assert len(r['chat_price_conflicts'])==2
    assert all(not conflict['confirmed_by'] for conflict in r['chat_price_conflicts'])
    for conflict in r['chat_price_conflicts']:
        r=call(c,'client',r,'confirm-chat-price',flag_id=conflict['id'],version_hash=sha,reason='The agreement amount remains final.')
        r=call(f,'freelancer',r,'confirm-chat-price',flag_id=conflict['id'],version_hash=sha,reason='The chat amounts were proposals only.')
    r=call(c,'client',r,'sign',version_hash=sha,typed_name='Alice Baker')
    r=call(f,'freelancer',r,'sign',version_hash=sha,typed_name='Rahul Kumar')
    assert r['project']['status']=='AGREED'

def test_new_agreement_version_invalidates_chat_price_confirmations(portals):
    c,f,r=start(portals);pid=r['project']['id'];sha=r['versions'][-1]['digest']
    assert f.post('/api/freelancer/projects/'+pid+'/chat',json={'body':'Could milestone 1 be INR 1500?','field_path':'timeline.milestones.0.amount_minor'}).status_code==200
    r=f.get('/api/freelancer/projects/'+pid).json();issue=r['chat_price_conflicts'][0]
    r=call(c,'client',r,'confirm-chat-price',flag_id=issue['id'],version_hash=sha,reason='The contract price is final.')
    r=call(f,'freelancer',r,'confirm-chat-price',flag_id=issue['id'],version_hash=sha,reason='It was only a question.')
    assert len(r['chat_price_conflicts'][0]['confirmed_by'])==2
    r=call(f,'freelancer',r,'counter');changed=copy.deepcopy(r['agreement']);changed['scope']['deliverables'][0]['specification']='Updated exact file specification for this project.'
    saved=f.put('/api/freelancer/projects/'+pid+'/draft',json={'revision':r['revision'],'agreement':changed});assert saved.status_code==200,saved.text
    r=call(f,'freelancer',saved.json(),'send')
    assert r['versions'][-1]['digest']!=sha
    assert not r['chat_price_conflicts'][0]['confirmed_by']


def test_chat_different_currency_and_explicit_date_cannot_be_silently_ignored():
    from app.agreement_assistant import price_chat_issues
    from app.agreement_examples import example_agreement
    a=example_agreement()
    issues=price_chat_issues(a,[{'id':'currency','body':'milestone 1 should be USD 1000','kind':'text'}, {'id':'date','body':'Final deadline: 2099-12-01','kind':'text'}])
    assert {i['field_path'] for i in issues}=={'timeline.milestones.0.amount_minor','timeline.deadline'}
    assert 'INR' in issues[0]['explanation'] and 'USD' in issues[0]['explanation']


def test_chat_currency_punctuation_does_not_crash_review():
    from app.agreement_assistant import price_chat_issues
    from app.agreement_examples import example_agreement
    assert price_chat_issues(example_agreement(),[{'id':'punctuation','body':'What currency, USD , or INR , should we use?','kind':'text'}])==[]


def test_client_brief_rejects_invalid_optional_links_and_past_start():
    from datetime import date,timedelta
    from app.guided_agreement import brief_flags
    a=example_agreement()
    a['basics']['reference_links']=['None']
    a['timeline']['start_date']=str(date.today()-timedelta(days=1))
    flags=brief_flags(a,date.today())
    assert any(f['field_path']=='timeline.start_date' for f in flags)
    assert any(f['field_path'].startswith('basics.') and 'link' in f['message'].lower() for f in flags)


def test_counter_can_return_client_owned_error_but_approval_stays_blocked(portals):
    from app import db,negotiation as n
    c,f,r=start(portals);pid=r['project']['id']
    # Reproduce a legacy brief already handed to the freelancer.
    with n.transaction() as conn:
        row=db.one(conn,db.projects,db.projects.c.id==pid)
        p=copy.deepcopy(row['payload'])
        p['draft']['basics']['reference_links']=['None']
        n.write(conn,p,row['version'])
    r=f.get('/api/freelancer/projects/'+pid).json()
    r=call(f,'freelancer',r,'send')
    assert r['project']['turn']=='client'
    call(c,'client',r,'approve',422,version_hash=r['versions'][-1]['digest'])
    r=call(c,'client',r,'counter')
    a=copy.deepcopy(r['agreement']);a['basics']['reference_links']=[]
    response=c.put('/api/client/projects/'+pid+'/draft',json={'revision':r['revision'],'agreement':a})
    assert response.status_code==200,response.text
    r=call(c,'client',response.json(),'send')
    assert not any(f['field_path']=='basics.reference_links' for f in r['flags'])


def test_editor_can_transfer_private_draft_to_field_owner(portals):
    c,f,r=start(portals);pid=r['project']['id']
    assert r['project']['turn']=='freelancer'
    root=f'/api/freelancer/projects/{pid}'
    denied=c.post(f'/api/client/projects/{pid}/handoff-editing',json={'revision':r['revision']})
    assert denied.status_code==409
    sent=f.post(root+'/handoff-editing',json={'revision':r['revision']})
    assert sent.status_code==200,sent.text
    r=sent.json();assert r['project']['turn']=='client' and r['can_edit'] is False
    r=c.get(f'/api/client/projects/{pid}').json();assert r['can_edit']
    terms=copy.deepcopy(r['agreement']);terms['basics']['reference_links']=[]
    result=c.put(f'/api/client/projects/{pid}/draft',json={'revision':r['revision'],'agreement':terms})
    assert result.status_code==200,result.text
