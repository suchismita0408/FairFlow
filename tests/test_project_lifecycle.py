import asyncio
import copy
from datetime import datetime

from app import db, payment_scan
from test_step1_auth_validation import portals
from test_step1_negotiation import start, call, approve_both, read
from test_step1_automation import prepared


def test_automatic_release_is_required_before_new_signatures(portals):
    c,f,r=start(portals)
    terms=copy.deepcopy(r['agreement'])
    terms['timeline']['milestones'][0]['auto_accept']=False
    response=f.put(f"/api/freelancer/projects/{r['project']['id']}/draft",json={'revision':r['revision'],'agreement':terms})
    assert response.status_code==200
    r=call(f,'freelancer',response.json(),'send')
    r,sha=approve_both(c,f,r)
    result=call(c,'client',r,'sign',422,version_hash=sha,typed_name='Alice Baker')
    assert 'automatic release' in result['detail']


def test_chat_notifies_only_counterpart_groups_unread_and_clears_on_read(portals):
    c,f,r=start(portals);pid=r['project']['id']
    root=f'/api/client/projects/{pid}'
    for body in ['Please review the scope.','Can we discuss the delivery date?']:
        assert c.post(root+'/chat',json={'body':body}).status_code==200
    notices=[n for n in f.get('/api/freelancer/notifications').json() if n['kind']=='chat_message']
    assert len(notices)==1 and not notices[0]['read']
    assert 'Alice Baker' in notices[0]['body'] and 'delivery date' in notices[0]['body']
    assert not [n for n in c.get('/api/client/notifications').json() if n['kind']=='chat_message']
    assert c.post('/api/client/notifications/'+notices[0]['id']+'/read').status_code==404
    assert f.post(f'/api/freelancer/projects/{pid}/chat/read').status_code==200
    assert all(n['read'] for n in f.get('/api/freelancer/notifications').json() if n['kind']=='chat_message')
    assert c.post(root+'/chat',json={'body':'There is a new message after you read the last one.'}).status_code==200
    assert len([n for n in f.get('/api/freelancer/notifications').json() if n['kind']=='chat_message' and not n['read']])==1


def test_freelancer_can_withdraw_before_signing_and_keep_chat(portals):
    c,f,r=start(portals);pid=r['project']['id']
    r=call(f,'freelancer',r,'freelancer-withdraw',reason='I cannot continue because of an emergency.')
    assert r['project']['status']=='FREELANCER_WITHDRAWN'
    assert any(m['kind']=='system' and 'withdrew' in m['body'] for m in r['messages'])
    assert any(n['kind']=='freelancer_withdrawal' for n in c.get('/api/client/notifications').json())
    assert f.post(f'/api/freelancer/projects/{pid}/chat',json={'body':'I am available to discuss the handover.'}).status_code==200
    call(f,'freelancer',r,'send',409)


def test_withdrawal_stops_new_work_but_preserves_submitted_payment(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,delivered=prepared(portals,monkeypatch)
    r=f.get(f'/api/freelancer/projects/{pid}').json()
    r=call(f,'freelancer',r,'freelancer-withdraw',reason='I cannot take on the remaining milestones.')
    assert c.post(root+'/start',json={'milestone_id':'NEXT','recipient_email':'seller@business.example.com'}).status_code==409
    assert c.get(root).json()['payments'][0]['status']=='in_review'
    r=call(c,'client',r,'acknowledge-withdrawal')
    assert r['project']['withdrawal']['status']=='accepted'
    asyncio.run(payment_scan.scan(pid,now=datetime.fromisoformat(delivered['review_deadline'])))
    assert calls==['capture','payout']
    assert c.get(root).json()['payments'][0]['status']=='captured'


def test_client_delete_is_personal_and_does_not_stop_existing_payment(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,delivered=prepared(portals,monkeypatch)
    r=c.get(f'/api/client/projects/{pid}').json()
    call(f,'freelancer',r,'delete',403)
    r=call(c,'client',r,'delete')
    assert r['project']['hidden_by_client']
    assert not c.get('/api/client/projects').json()
    assert len(c.get('/api/client/projects?include_deleted=true').json())==1
    assert len(f.get('/api/freelancer/projects').json())==1
    assert any(n['kind']=='project_removed' for n in f.get('/api/freelancer/notifications').json())
    asyncio.run(payment_scan.scan(pid,now=datetime.fromisoformat(delivered['review_deadline'])))
    assert calls==['capture','payout']
    r=c.get(f'/api/client/projects/{pid}').json()
    call(c,'client',r,'restore')
    assert len(c.get('/api/client/projects').json())==1


def test_new_authorization_and_new_delivery_stop_after_withdrawal(portals,monkeypatch):
    from sqlalchemy import update
    c,f,pid,m,root,fr,calls,delivered=prepared(portals,monkeypatch)
    with db.engine().begin() as conn:
        payment=db.one(conn,db.milestone_payments,db.milestone_payments.c.project_id==pid)
        payload={k:v for k,v in payment['payload'].items() if k not in ('delivery_url','delivery_note','review_deadline')}
        conn.execute(update(db.milestone_payments).where(db.milestone_payments.c.id==payment['id']).values(status='authorized',payload=payload))
    r=f.get(f'/api/freelancer/projects/{pid}').json()
    call(f,'freelancer',r,'freelancer-withdraw',reason='I cannot begin this milestone after all.')
    assert f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/new','note':'Attempting a new delivery after withdrawal.'}).status_code==409
    assert c.post(root+'/'+m['id']+'/authorize').status_code==409
    assert not calls
