import json
import pytest
from datetime import date,timedelta
from app import chat_monitor as monitor
from test_step1_auth_validation import portals
from test_step1_negotiation import start,call
from test_chat_terms import chat,room


def install(monkeypatch,state='disputed',value=None):
    monkeypatch.setenv('AI_MODE','gemini')
    async def review(schema,instruction,data):
        return monitor.Assessment(findings=[monitor.Finding(field_path='ip.source_format',state=state,
            explanation='Agree on the source file format.',proposed_value_json=json.dumps(value),
            message_ids=[m['id'] for m in data['conversation']])])
    monkeypatch.setattr(monitor.ai,'structured',review)


def test_unanswered_and_disputed_chat_blocks_even_before_acceptance(portals,monkeypatch):
    c,f,r=start(portals);pid=r['project']['id'];install(monkeypatch,'unanswered')
    chat(f,'freelancer',pid,'Should source files be ZIP or TAR?')
    r=room(c,pid)
    assert r['project']['chat_monitor']['findings'][0]['state']=='unanswered'
    response=c.post(f'/api/client/projects/{pid}/actions',json={'revision':r['revision'],'action':'approve','version_hash':r['versions'][-1]['digest']})
    assert response.status_code==409 and 'chat negotiation' in response.text
    install(monkeypatch,'disputed');chat(c,'client',pid,'I require a different format.')
    assert room(c,pid)['project']['chat_monitor']['findings'][0]['state']=='disputed'


def test_bilateral_ai_draft_goes_into_normal_proposal_without_signing(portals,monkeypatch):
    c,f,r=start(portals);pid=r['project']['id'];install(monkeypatch,'unanswered')
    chat(f,'freelancer',pid,'Source files should be ZIP.')
    install(monkeypatch,'agreed','ZIP');chat(c,'client',pid,'Yes, ZIP is agreed.')
    r=room(f,pid,'freelancer');before=r['agreement']['ip']['source_format']
    assert before!='ZIP'
    response=f.post(f'/api/freelancer/projects/{pid}/chat-monitor/propose',json={'revision':r['revision']})
    assert response.status_code==200,response.text
    r=response.json();assert r['agreement']['ip']['source_format']=='ZIP'
    assert r['project']['status']=='IN_NEGOTIATION'
    assert not r['versions'][-1]['signatures']
    assert not monitor.blockers(r['project'],r['agreement'])


def test_ai_outage_keeps_guard_and_recheck_recovers(portals,monkeypatch):
    c,f,r=start(portals);pid=r['project']['id'];monkeypatch.setenv('AI_MODE','gemini')
    async def failed(*args):raise RuntimeError('offline')
    monkeypatch.setattr(monitor.ai,'structured',failed)
    chat(f,'freelancer',pid,'Hello')
    r=room(c,pid);assert r['project']['chat_monitor']['status']=='unavailable'
    assert monitor.blockers(r['project'],r['agreement'])
    async def recovered(*args):return monitor.Assessment(findings=[])
    monkeypatch.setattr(monitor.ai,'structured',recovered)
    assert c.post(f'/api/client/projects/{pid}/chat-monitor/recheck',json={'revision':r['revision']}).status_code==200
    r=room(c,pid);assert not monitor.blockers(r['project'],r['agreement'])


def test_ai_cannot_invent_bilateral_consent(portals,monkeypatch):
    c,f,r=start(portals);pid=r['project']['id'];install(monkeypatch,'agreed','ZIP')
    chat(f,'freelancer',pid,'Use ZIP please.')
    r=room(c,pid);assert r['project']['chat_monitor']['status']=='unavailable'


@pytest.mark.parametrize('days,allowed',[(2,True),(60,False)])
def test_signed_deadline_draft_checks_funding_and_requires_both_votes(portals,monkeypatch,days,allowed):
    from test_workflows import authorized,vote
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch)
    due=str(date.fromisoformat(m['due_date'])+timedelta(days=days))
    monkeypatch.setenv('AI_MODE','gemini')
    async def review(schema,instruction,data):
        history=data['conversation']
        return monitor.Assessment(findings=[monitor.Finding(field_path='timeline.milestones.0.due_date',state='agreed' if len(history)>1 else 'unanswered',explanation='Extend the milestone deadline by the agreed number of days.',proposed_value_json=json.dumps(due if len(history)>1 else None),message_ids=[m['id'] for m in history])])
    monkeypatch.setattr(monitor.ai,'structured',review)
    chat(f,'freelancer',pid,f'Extend milestone 1 deadline by {days} days.')
    chat(c,'client',pid,'Agreed.')
    r=room(c,pid);old=r['agreement']['timeline']['milestones'][0]['due_date']
    response=c.post(f'/api/client/projects/{pid}/chat-monitor/propose',json={'revision':r['revision']})
    if not allowed:
        assert response.status_code==409 and 'coverage' in response.text;return
    assert response.status_code==200,response.text
    r=response.json();proposal=r['project']['workflow_requests'][-1]
    assert r['agreement']['timeline']['milestones'][0]['due_date']==old
    vote(c,pid,proposal);vote(f,pid,proposal,'freelancer')
    r=room(c,pid)
    assert r['agreement']['timeline']['milestones'][0]['due_date']==due
    assert not monitor.blockers(r['project'],r['agreement'])


def test_client_turn_can_propose_bilateral_freelancer_owned_revision_change(portals,monkeypatch):
    c,f,r=start(portals);pid=r['project']['id']
    r=call(f,'freelancer',r,'send');r=call(c,'client',r,'counter')
    monkeypatch.setenv('AI_MODE','gemini')
    async def review(schema,instruction,data):
        history=data['conversation']
        return monitor.Assessment(findings=[monitor.Finding(field_path='scope.deliverables.0.revision_rounds',state='agreed' if len(history)>1 else 'unanswered',explanation='Both agreed to one revision round.',proposed_value_json='1' if len(history)>1 else 'null',message_ids=[m['id'] for m in history])])
    monkeypatch.setattr(monitor.ai,'structured',review)
    chat(c,'client',pid,'Included revision rounds must be 1.')
    chat(f,'freelancer',pid,'Okay, agreed.')
    r=room(c,pid)
    other=f.post(f'/api/freelancer/projects/{pid}/chat-monitor/propose',json={'revision':r['revision']})
    assert other.status_code==409
    response=c.post(f'/api/client/projects/{pid}/chat-monitor/propose',json={'revision':r['revision']})
    assert response.status_code==200,response.text
    r=response.json()
    assert r['agreement']['scope']['deliverables'][0]['revision_rounds']==1
    assert r['project']['turn']=='freelancer'
    assert not r['versions'][-1]['signatures']


def test_worker_automatically_retries_failed_chat_review(portals,monkeypatch):
    import asyncio,copy
    from app import db,negotiation as n
    c,f,r=start(portals);pid=r['project']['id']
    with n.transaction() as conn:
        row=db.one(conn,db.projects,db.projects.c.id==pid);p=copy.deepcopy(row['payload'])
        p['chat_monitor']={'token':'retry-token','status':'unavailable','findings':[],'checked_at':'2020-01-01T00:00:00+00:00'}
        n.write(conn,p,row['version'])
    monkeypatch.setenv('AI_MODE','gemini');calls=[]
    async def fake(pid,token):calls.append((pid,token))
    monkeypatch.setattr(monitor,'scan',fake)
    asyncio.run(monitor.retry_pending())
    assert calls==[(pid,'retry-token')]


@pytest.mark.parametrize('field,owner',[('scope.deliverables.0.revision_rounds','freelancer'),('payment.total_minor','both')])
def test_ai_field_owner_approval_gate(portals,field,owner):
    import copy
    from app import db,negotiation as n
    from app.agreement_schema import at
    c,f,r=start(portals);pid=r['project']['id']
    r=call(f,'freelancer',r,'send')
    with n.transaction() as conn:
        row=db.one(conn,db.projects,db.projects.c.id==pid);p=copy.deepcopy(row['payload'])
        p['ai_field_reviews']=[{'id':'test-review','field_path':field,'expected':at(r['agreement'],field),'owner':owner,'approved_by':None}]
        n.write(conn,p,row['version'])
    r=room(c,pid)
    blocked=c.post(f'/api/client/projects/{pid}/actions',json={'revision':r['revision'],'action':'approve','version_hash':r['versions'][-1]['digest']})
    assert blocked.status_code==409 and 'field owners' in blocked.text
    result=c.post(f'/api/client/projects/{pid}/ai-field-reviews/test-review/approve',json={'revision':r['revision']})
    if owner=='freelancer':
        assert result.status_code==403
        result=f.post(f'/api/freelancer/projects/{pid}/ai-field-reviews/test-review/approve',json={'revision':r['revision']})
    assert result.status_code==200,result.text
    r=result.json();assert r['project']['ai_field_reviews'][0]['approved_by']['role']==('freelancer' if owner=='freelancer' else 'client')
    assert not r['versions'][-1]['approvals']
