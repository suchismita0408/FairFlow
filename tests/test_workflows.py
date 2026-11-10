import asyncio
import base64
import copy
from datetime import datetime,timedelta,timezone,date
import pytest
from sqlalchemy import update,insert
from app import db,assurance as a,workflows as w,paypal,payment_scan,step1_ledger
from test_step1_auth_validation import portals
from test_step1_automation import prepared
from test_step1_assurance import signed_short_project
from test_step1_negotiation import call


def room(c,pid):return c.get(f'/api/client/projects/{pid}').json()
def base(pid,role='client'):return f'/api/{role}/projects/{pid}/workflow'
def propose(c,pid,kind,**values):
    result=c.post(base(pid)+'/requests',json={'revision':room(c,pid)['revision'],'kind':kind,'reason':'Both parties can review this specific arrangement.',**values})
    assert result.status_code==200,result.text
    return result.json()['request']
def vote(c,pid,r,role='client',decision='accept',status=200):
    response=c.post(base(pid,role)+f'/requests/{r["id"]}/vote',json={'revision':c.get(f'/api/{role}/projects/{pid}').json()['revision'],
        'digest':r['digest'],'decision':decision,'typed_name':'Alice Baker' if role=='client' else 'Rahul Kumar'})
    assert response.status_code==status,response.text
    return response
def enable(c,f,pid):
    r=propose(c,pid,'protections');vote(c,pid,r);vote(f,pid,r,'freelancer');return r
def authorized(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,delivery=prepared(portals,monkeypatch)
    with db.engine().begin() as conn:
        row=a.payment(conn,pid,m['id'])
        fields={k:v for k,v in row['payload'].items() if k not in ('delivery_url','delivery_note','delivered_at','review_deadline','delivery_history','delivery_evidence_id','late')}
        conn.execute(update(db.milestone_payments).where(db.milestone_payments.c.id==row['id']).values(status='authorized',payload=fields))
    return c,f,pid,m,root,fr,calls
def fake_void(monkeypatch):
    calls=[]
    async def request(method,path,body=None,key=None):
        assert method=='POST' and path.endswith('/void') and key;calls.append(key);return {}
    async def auth(aid):return {'id':aid,'status':'VOIDED'}
    monkeypatch.setattr(paypal,'call',request);monkeypatch.setattr(paypal,'step1_get_authorization',auth)
    return calls


def test_policy_requires_both_acceptances_and_preserves_signed_snapshot(portals):
    c,f,r=signed_short_project(portals);pid=r['project']['id'];original=copy.deepcopy(r['agreement'])
    proposal=propose(c,pid,'protections');vote(c,pid,proposal)
    assert not c.get(base(pid)).json()['enabled']
    vote(c,pid,proposal,status=409);vote(f,pid,proposal,'freelancer')
    assert c.get(base(pid)).json()['enabled']
    assert room(c,pid)['agreement']==original
    assert c.get(base(pid)+f'/requests/{proposal["id"]}/export').json()['votes'][1]['typed_name']=='Rahul Kumar'


def test_bad_name_digest_and_nonmember_cannot_accept(portals):
    c,f,r=signed_short_project(portals);pid=r['project']['id'];proposal=propose(c,pid,'protections')
    bad={'revision':room(c,pid)['revision'],'digest':proposal['digest'],'decision':'accept','typed_name':'Someone Else'}
    assert c.post(base(pid)+f'/requests/{proposal["id"]}/vote',json=bad).status_code==422
    outsider=portals('client','outsider@example.com')
    assert outsider.post(base(pid)+f'/requests/{proposal["id"]}/vote',json=bad).status_code==404
    assert c.post(base(pid)+f'/requests/{proposal["id"]}/vote',json={**bad,'digest':'0'*64}).status_code==409


def test_cancellation_request_does_not_pause_automatic_release_and_stale_vote_blocked(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,delivered=prepared(portals,monkeypatch)
    r=propose(c,pid,'cancel');vote(c,pid,r)
    asyncio.run(payment_scan.scan(pid,now=datetime.fromisoformat(delivered['review_deadline'])))
    assert calls==['capture','payout']
    vote(f,pid,r,'freelancer',status=409)
    assert not room(c,pid)['project'].get('work_closed')


def test_joint_cancellation_voids_only_unsubmitted_work_once(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch);voids=fake_void(monkeypatch)
    r=propose(c,pid,'cancel');vote(c,pid,r);assert not voids
    vote(f,pid,r,'freelancer');assert len(voids)==1
    assert c.get(root).json()['payments'][0]['status']=='voided'
    assert room(c,pid)['project']['work_closed']
    vote(f,pid,r,'freelancer',status=409);assert len(voids)==1
    assert f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'A new first delivery after cancellation.'}).status_code==409


def test_cancellation_preserves_submitted_work(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,d=prepared(portals,monkeypatch)
    r=propose(c,pid,'cancel');vote(c,pid,r);vote(f,pid,r,'freelancer')
    assert c.get(root).json()['payments'][0]['status']=='in_review'
    asyncio.run(payment_scan.scan(pid,now=datetime.fromisoformat(d['review_deadline'])))
    assert calls==['capture','payout']


def test_legacy_late_delivery_is_not_retroactively_restricted(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch)
    monkeypatch.setattr(w,'clock',lambda:datetime.fromisoformat(m['due_date']+'T23:59:59+00:00')+timedelta(days=1))
    r=f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'Completed work after the original delivery date.'})
    assert r.status_code==200 and r.json()['status']=='in_review'


def test_late_delivery_requires_timing_acceptance_with_full_review(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch);enable(c,f,pid)
    with db.engine().connect() as conn:
        p,v=a.current(conn,pid,db.one(conn,db.users,db.users.c.email=='client@example.com'));now=w.grace_end(p,v,m)+timedelta(seconds=1)
    monkeypatch.setattr(w,'clock',lambda:now)
    bad=f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'Delivered the agreed landing page with all completed sections.'})
    assert bad.status_code==422
    delivered=f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'Delivered the agreed landing page with all completed sections.','checklist':m['deliverable_ids']})
    assert delivered.status_code==200,delivered.text
    assert delivered.json()['status']=='late_pending' and delivered.json()['review_deadline'] is None
    assert f.post(base(pid,'freelancer')+'/'+m['id']+'/late',json={'accept':True}).status_code==403
    accepted=c.post(base(pid)+'/'+m['id']+'/late',json={'accept':True})
    assert accepted.status_code==200,accepted.text
    assert datetime.fromisoformat(accepted.json()['review_deadline'])==now+timedelta(days=m['review_days'])
    assert not calls


def test_rejected_late_delivery_does_not_auto_release(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch);enable(c,f,pid)
    with db.engine().begin() as conn:
        row=a.payment(conn,pid,m['id']);a.save(conn,row,'late_pending',delivery_url='https://example.com/late')
    r=c.post(base(pid)+'/'+m['id']+'/late',json={'accept':False})
    assert r.status_code==200 and r.json()['status']=='disputed' and not calls


def test_consented_no_delivery_default_voids_after_notice_and_deduplicates(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch);enable(c,f,pid);voids=fake_void(monkeypatch)
    with db.engine().connect() as conn:
        user=db.one(conn,db.users,db.users.c.email=='client@example.com');p,v=a.current(conn,pid,user)
        due=w.grace_end(p,v,m)+timedelta(days=v['payload']['termination']['notice_days'])
    asyncio.run(w.scan(pid,now=due-timedelta(seconds=1)));assert not voids
    asyncio.run(w.scan(pid,now=due));asyncio.run(w.scan(pid,now=due+timedelta(seconds=1)))
    assert len(voids)==1 and not calls
    assert c.get(root).json()['payments'][0]['status']=='voided'
    assert len([x for x in c.get('/api/client/notifications').json() if x['kind']=='delivery_default'])==1


def test_partial_work_claim_prevents_no_delivery_default(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch);enable(c,f,pid);voids=fake_void(monkeypatch)
    r=f.post(base(pid,'freelancer')+'/cases',json={'revision':room(c,pid)['revision'],'milestone_id':m['id'],'category':'partial_work','reason':'Half the agreed page is implemented; I request a documented settlement.'})
    assert r.status_code==200
    asyncio.run(w.scan(pid,now=datetime.now(timezone.utc)+timedelta(days=20)))
    assert not voids and c.get(root).json()['payments'][0]['status']=='authorized'


def test_defect_fix_does_not_consume_preference_revision(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,d=prepared(portals,monkeypatch);enable(c,f,pid)
    result=c.post(root+'/'+m['id']+'/object',json={'kind':'defect','deliverable_id':m['deliverable_ids'][0],'reason':'The agreed navigation link fails to work on mobile.'})
    assert result.status_code==200,result.text
    assert result.json()['status']=='revision_requested' and result.json()['revision_counts']=={}
    assert result.json()['revision_due_at'] and len(result.json()['defect_requests'])==1
    asyncio.run(payment_scan.scan(pid,now=datetime.fromisoformat(d['review_deadline'])))
    assert not calls


def test_extension_is_a_separate_accepted_addendum(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch);original=copy.deepcopy(room(c,pid)['agreement'])
    due=str(date.today()+timedelta(days=12))
    r=propose(c,pid,'extension',milestone_id=m['id'],due_date=due);vote(c,pid,r);vote(f,pid,r,'freelancer')
    current=room(c,pid)
    assert current['agreement']['timeline']['milestones'][0]['due_date']==due
    with db.engine().connect() as conn:
        assert db.one(conn,db.agreements,db.agreements.c.id==current['project']['active_agreement_id'])['payload']==original
    assert c.get(base(pid)).json()['operational_agreement']['timeline']['milestones'][0]['due_date']==due


def test_extension_cannot_outlive_funding(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch)
    result=c.post(base(pid)+'/requests',json={'revision':room(c,pid)['revision'],'kind':'extension','milestone_id':m['id'],
        'due_date':str(date.today()+timedelta(days=28)),'reason':'Requesting more time beyond the authorization window.'})
    assert result.status_code==409 and 'coverage' in result.text


def test_paid_change_has_exact_bilateral_scope_price_and_separate_funding(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch)
    r=propose(c,pid,'change',title='Additional contact tab',amount_minor=500,due_date=str(date.today()+timedelta(days=15)),criteria=['The new tab opens the contact form.'])
    vote(c,pid,r)
    assert not any(x['id'].startswith('CO-') for x in c.get(base(pid)).json()['milestones'])
    vote(f,pid,r,'freelancer');mid='CO-'+r['id']
    checkout=c.post(root+'/start',json={'milestone_id':mid,'recipient_email':'seller@business.example.com'})
    assert checkout.status_code==200,checkout.text
    assert checkout.json()['amount_minor']==500
    assert c.post(root+'/start',json={'milestone_id':mid,'recipient_email':'seller@business.example.com'}).status_code==409
    assert c.post(root+'/'+mid+'/authorize').status_code==200
    assert f.post(fr+'/deliver',json={'milestone_id':mid,'url':'https://example.com/extra','note':'The extra contact tab is available here.'}).status_code==200


def test_partial_settlement_captures_exact_amount_and_preserves_signed_cap(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch);original=room(c,pid)['agreement'];remote=[]
    async def request(method,path,body=None,key=None):
        remote.append((path,body,key));return {'id':'PARTIAL-CAP','status':'COMPLETED','amount':paypal.step1_amount(400,'USD')}
    monkeypatch.setattr(paypal,'call',request)
    r=propose(c,pid,'settlement',milestone_id=m['id'],outcome='partial',amount_minor=400,handover_url='https://example.com/partial')
    vote(c,pid,r);assert not remote
    vote(f,pid,r,'freelancer');assert len(remote)==1
    assert remote[0][1]['final_capture'] is True and remote[0][1]['amount']['value']=='4.00'
    saved=c.get(root).json()['payments'][0]
    assert saved['amount_minor']==m['amount_minor'] and saved['settlement_amount_minor']==400
    assert saved['status']=='captured' and room(c,pid)['agreement']==original
    with db.engine().connect() as conn:
        entries=db.rows(conn,db.ledger);assert len(entries)==2 and sorted(x['amount_cents'] for x in entries)==[-400,400]
    async def capture(cid):return {'id':cid,'status':'COMPLETED','amount':paypal.step1_amount(400,'USD')}
    monkeypatch.setattr(paypal,'step1_get_capture',capture)
    asyncio.run(payment_scan.scan(pid))
    payout=c.get(root).json()['payments'][0]['payout'];assert payout['amount_minor']==400 and payout['status']=='pending'


def test_unknown_settlement_is_not_blindly_retried(portals,monkeypatch):
    from fastapi import HTTPException
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch);remote=[]
    async def failed(method,path,body=None,key=None):remote.append(key);raise HTTPException(502,'Uncertain provider response.')
    monkeypatch.setattr(paypal,'call',failed)
    r=propose(c,pid,'settlement',milestone_id=m['id'],outcome='partial',amount_minor=400,handover_url='https://example.com/partial')
    vote(c,pid,r);vote(f,pid,r,'freelancer',status=502)
    assert len(remote)==1 and c.get(root).json()['payments'][0]['status']=='settling'
    with db.engine().connect() as conn:user=db.one(conn,db.users,db.users.c.email=='client@example.com')
    asyncio.run(w.execute(pid,r['id'],user));assert len(remote)==1
    assert next(x for x in c.get(base(pid)).json()['requests'] if x['id']==r['id'])['status']=='needs_attention'


def test_refund_requires_capture_without_any_payout_intent(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,d=prepared(portals,monkeypatch)
    asyncio.run(payment_scan.scan(pid,now=datetime.fromisoformat(d['review_deadline'])))
    response=c.post(base(pid)+'/requests',json={'revision':room(c,pid)['revision'],'kind':'settlement','milestone_id':m['id'],
        'reason':'We would like to refund this previously paid work.','outcome':'refund','amount_minor':m['amount_minor']})
    assert response.status_code==409 and 'payout intent' in response.text


def test_full_unsettled_capture_refund_is_verified_and_balanced(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch)
    with db.engine().begin() as conn:row=a.payment(conn,pid,m['id']);a.save(conn,row,'captured',capture_id='UNPAID-CAP')
    async def request(method,path,body=None,key=None):return {'id':'REFUND','status':'COMPLETED','amount':paypal.step1_amount(m['amount_minor'],'USD')}
    async def capture(cid):return {'id':cid,'status':'REFUNDED','amount':paypal.step1_amount(m['amount_minor'],'USD')}
    monkeypatch.setattr(paypal,'call',request);monkeypatch.setattr(paypal,'step1_get_capture',capture)
    r=propose(c,pid,'settlement',milestone_id=m['id'],outcome='refund',amount_minor=m['amount_minor']);vote(c,pid,r);vote(f,pid,r,'freelancer')
    assert c.get(root).json()['payments'][0]['status']=='refunded'
    with db.engine().connect() as conn:
        entries=db.rows(conn,db.ledger);assert len(entries)==4
        assert sum(e['amount_cents'] for e in entries if e['account']=='project_liability')==0


def test_evidence_files_immutable_access_control_and_checksums(portals,monkeypatch):
    c,f,r=signed_short_project(portals);pid=r['project']['id'];raw=b'Original submitted CSS'
    uploaded=f.post(base(pid,'freelancer')+'/files',json={'filename':'styles.css','content_base64':base64.b64encode(raw).decode()})
    assert uploaded.status_code==200,uploaded.text
    fid=uploaded.json()['id'];download=c.get(base(pid)+'/files/'+fid)
    assert download.content==raw and download.headers['x-content-sha256']==uploaded.json()['digest']
    outsider=portals('client','outsider@example.com');assert outsider.get(base(pid)+'/files/'+fid).status_code==404
    with pytest.raises(Exception):
        with db.engine().begin() as conn:conn.execute(update(db.delivery_files).where(db.delivery_files.c.id==fid).values(content='changed'))


def test_invalid_file_and_non_author_artifact_cannot_be_submitted(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch)
    assert f.post(base(pid,'freelancer')+'/files',json={'filename':'x','content_base64':'invalid!'}).status_code==422
    file=c.post(base(pid)+'/files',json={'filename':'client.txt','content_base64':base64.b64encode(b'client file').decode()}).json()
    result=f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'Completed work with an artifact from the wrong author.','file_ids':[file['id']]})
    assert result.status_code==404


def test_support_escalation_role_separation_and_no_money_effect(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,d=prepared(portals,monkeypatch)
    support=portals('client','support@example.com');monkeypatch.setenv('FAIRFLOW_SUPPORT_EMAILS','support@example.com,client@example.com')
    r=f.post(base(pid,'freelancer')+'/cases',json={'revision':room(c,pid)['revision'],'milestone_id':m['id'],'category':'quality','reason':'We disagree about the signed mobile layout criteria.','evidence_url':'https://example.com/evidence'})
    assert r.status_code==200;r=r.json()
    assert not support.get('/api/support/cases').json()
    asyncio.run(w.scan(pid,now=datetime.fromisoformat(r['response_due_at'])))
    queued=support.get('/api/support/cases');assert queued.status_code==200,queued.text
    assert len(queued.json())==1 and not c.get('/api/support/cases').json()
    assert c.get(f'/api/support/projects/{pid}').status_code==403
    assert support.get(f'/api/support/projects/{pid}').json()['agreement']['basics']['title']
    r=queued.json()[0]
    review=support.post(f'/api/support/projects/{pid}/cases/{r["case"]["id"]}',json={'revision':r['revision'],'stage':'recommendation','body':'Compare the supplied evidence to the signed mobile layout acceptance criteria.'})
    assert review.status_code==200,review.text
    assert not calls and c.get(root).json()['payments'][0]['status']=='in_review'


def test_case_without_reviewer_shows_unconfigured_not_fake_resolution(portals,monkeypatch):
    monkeypatch.delenv('FAIRFLOW_SUPPORT_EMAILS',raising=False)
    c,f,r=signed_short_project(portals);pid=r['project']['id'];mid=r['agreement']['timeline']['milestones'][0]['id']
    result=c.post(base(pid)+'/cases',json={'revision':room(c,pid)['revision'],'milestone_id':mid,'category':'ip','reason':'Please review the source asset license evidence.'}).json()
    asyncio.run(w.scan(pid,now=datetime.fromisoformat(result['response_due_at'])))
    data=c.get(base(pid)).json();assert not data['support_available'] and data['cases'][0]['status']=='support_unconfigured'


def test_reopen_requires_both_and_preserves_withdrawal_history(portals):
    c,f,r=signed_short_project(portals);pid=r['project']['id']
    call(f,'freelancer',r,'freelancer-withdraw',reason='I cannot continue the remaining work this week.')
    proposal=propose(c,pid,'reopen');vote(c,pid,proposal)
    assert room(c,pid)['project'].get('withdrawal')
    vote(f,pid,proposal,'freelancer')
    p=room(c,pid)['project'];assert not p.get('withdrawal') and p['closure_history'][0]['withdrawal']


def test_replacement_funding_voids_original_preserves_history_and_needs_buyer_approval(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch);remote=[]
    with db.engine().connect() as conn:row=a.payment(conn,pid,m['id'])
    state={'value':'CREATED'}
    async def request(method,path,body=None,key=None):
        if method=='GET':return {'id':'ORDER','status':'COMPLETED','purchase_units':[{'custom_id':row['id'],'amount':paypal.step1_amount(row['amount_minor'],'USD'),'payments':{'authorizations':[{'id':'AUTH'}]}}]}
        assert path.endswith('/void');remote.append(key);state['value']='VOIDED';return {}
    async def auth(aid):return {'id':aid,'status':state['value'],'amount':paypal.step1_amount(row['amount_minor'],'USD')}
    monkeypatch.setattr(paypal,'call',request);monkeypatch.setattr(paypal,'step1_get_authorization',auth)
    result=c.post(base(pid)+'/'+m['id']+'/replace-funding',json={})
    assert result.status_code==200,result.text
    assert result.json()['status']=='awaiting_approval' and len(remote)==1
    assert result.json()['previous_attempts'][0]['payload']['authorization_id']=='AUTH'
    assert result.json()['authorization_id'] is None


def test_replacement_does_not_ignore_a_remote_capture(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch)
    with db.engine().connect() as conn:row=a.payment(conn,pid,m['id'])
    async def request(method,path,body=None,key=None):return {'id':'ORDER','purchase_units':[{'custom_id':row['id'],'amount':paypal.step1_amount(row['amount_minor'],'USD'),'payments':{'captures':[{'id':'CAP'}]}}]}
    monkeypatch.setattr(paypal,'call',request)
    response=c.post(base(pid)+'/'+m['id']+'/replace-funding',json={})
    assert response.status_code==409 and 'capture exists' in response.text
    assert c.get(root).json()['payments'][0]['status']=='attention'


def test_abandoned_unapproved_checkout_can_be_replaced_without_an_authorization(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch)
    with db.engine().begin() as conn:
        row=a.payment(conn,pid,m['id']);payload=copy.deepcopy(row['payload']);payload.pop('authorization_id')
        conn.execute(update(db.milestone_payments).where(db.milestone_payments.c.id==row['id']).values(status='awaiting_approval',payload=payload))
    async def request(method,path,body=None,key=None):
        assert method=='GET'
        return {'id':'ORDER','status':'CREATED','purchase_units':[{'custom_id':row['id'],'amount':paypal.step1_amount(row['amount_minor'],'USD')}]}
    monkeypatch.setattr(paypal,'call',request)
    result=c.post(base(pid)+'/'+m['id']+'/replace-funding',json={})
    assert result.status_code==200,result.text
    assert result.json()['status']=='awaiting_approval' and len(result.json()['previous_attempts'])==1


def test_pending_refund_reference_survives_and_read_reconciliation_finishes_once(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch);remote=[]
    with db.engine().begin() as conn:row=a.payment(conn,pid,m['id']);a.save(conn,row,'captured',capture_id='CAP-REFUND')
    async def request(method,path,body=None,key=None):
        remote.append(method)
        return {'id':'REF-PENDING','status':'PENDING' if method=='POST' else 'COMPLETED','amount':paypal.step1_amount(m['amount_minor'],'USD')}
    async def capture(cid):return {'id':cid,'status':'REFUNDED','amount':paypal.step1_amount(m['amount_minor'],'USD')}
    monkeypatch.setattr(paypal,'call',request);monkeypatch.setattr(paypal,'step1_get_capture',capture)
    r=propose(c,pid,'settlement',milestone_id=m['id'],outcome='refund',amount_minor=m['amount_minor']);vote(c,pid,r);vote(f,pid,r,'freelancer',status=502)
    saved=next(x for x in c.get(base(pid)).json()['requests'] if x['id']==r['id'])
    assert saved['operations'][0]['provider_id']=='REF-PENDING'
    result=c.post(base(pid)+f'/requests/{r["id"]}/reconcile',json={});assert result.status_code==200,result.text
    assert remote==['POST','GET'] and c.get(root).json()['payments'][0]['status']=='refunded'
    assert c.post(base(pid)+f'/requests/{r["id"]}/reconcile',json={}).status_code==200
    assert remote==['POST','GET']


def test_duplicate_final_settlement_votes_do_not_capture_twice(portals,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch);remote=[]
    async def request(method,path,body=None,key=None):remote.append(key);return {'id':'ONCE','status':'COMPLETED','amount':paypal.step1_amount(400,'USD')}
    monkeypatch.setattr(paypal,'call',request)
    r=propose(c,pid,'settlement',milestone_id=m['id'],outcome='partial',amount_minor=400,handover_url='https://example.com/partial');vote(c,pid,r)
    revision=room(c,pid)['revision']
    def accept(_):return f.post(base(pid,'freelancer')+f'/requests/{r["id"]}/vote',json={'revision':revision,'digest':r['digest'],'decision':'accept','typed_name':'Rahul Kumar'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(accept,range(2)))
    assert sorted(results)==[200,409] and len(remote)==1


def test_case_conclusion_requires_both_and_new_wording_invalidates_old_confirmation(portals):
    c,f,r=signed_short_project(portals);pid=r['project']['id'];mid=r['agreement']['timeline']['milestones'][0]['id']
    created=c.post(base(pid)+'/cases',json={'revision':room(c,pid)['revision'],'milestone_id':mid,'category':'scope','reason':'Clarify whether the additional layout is in scope.'}).json()
    def respond(client,role,body):
        result=client.post(base(pid,role)+f'/cases/{created["id"]}/reply',json={'revision':room(c,pid)['revision'],'body':body,'close':True})
        assert result.status_code==200,result.text
        return result.json()
    assert respond(c,'client','Keep the existing signed layout as originally agreed.')['status']!='closed'
    changed=respond(f,'freelancer','Keep the signed layout and document the separate change request.')
    assert changed['conclusion']['roles']==['freelancer']
    assert respond(c,'client',changed['conclusion']['body'])['status']=='closed'


def test_warranty_defect_claim_after_capture_does_not_reverse_payment(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,d=prepared(portals,monkeypatch)
    asyncio.run(payment_scan.scan(pid,now=datetime.fromisoformat(d['review_deadline'])))
    before=c.get(root).json()['payments'][0]
    result=c.post(base(pid)+'/cases',json={'revision':room(c,pid)['revision'],'milestone_id':m['id'],'category':'defect','reason':'The agreed navigation fails with the documented mobile browser.'})
    assert result.status_code==200 and result.json()['correction_due_at']
    after=c.get(root).json()['payments'][0]
    assert after['status']=='captured' and after['payout']==before['payout'] and after['revision_counts']==before['revision_counts']
    with db.engine().connect() as conn:v=a.signed_terms(conn,room(c,pid)['project'])
    monkeypatch.setattr(w,'clock',lambda:datetime.fromisoformat(d['delivered_at'])+timedelta(days=v['payload']['revisions']['defect_days']+1))
    expired=c.post(base(pid)+'/cases',json={'revision':room(c,pid)['revision'],'milestone_id':m['id'],'category':'defect','reason':'Reporting a defect after the free reporting period ended.'})
    assert expired.status_code==409


def test_signed_new_guided_agreements_contain_explicit_delivery_policy(portals):
    from app import guided_agreement
    from app.workflow_policy import POLICY_TEXT
    c=portals('client')
    with db.engine().connect() as conn:
        user=db.one(conn,db.users,db.users.c.email=='client@example.com')
        profile=db.one(conn,db.client_profiles,db.client_profiles.c.user_id==user['id'])
    assert guided_agreement.defaults(user,profile['payload'])['termination']['delivery_rules']==POLICY_TEXT


def test_deadline_extension_is_included_in_next_unsigned_amendment(portals,monkeypatch):
    c,f,r=signed_short_project(portals);pid=r['project']['id'];mid=r['agreement']['timeline']['milestones'][0]['id']
    new_date=str(date.today()+timedelta(days=12))
    proposal=propose(c,pid,'extension',milestone_id=mid,due_date=new_date);vote(c,pid,proposal);vote(f,pid,proposal,'freelancer')
    amended=call(c,'client',room(c,pid),'amend')
    assert amended['agreement']['timeline']['milestones'][0]['due_date']==new_date


def test_open_workflow_proposal_must_finish_before_main_amendment(portals):
    c,f,r=signed_short_project(portals);pid=r['project']['id'];propose(c,pid,'cancel')
    call(c,'client',room(c,pid),'amend',409)


def test_external_full_refund_creates_case_and_reconciles_gross_ledger(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,d=prepared(portals,monkeypatch)
    asyncio.run(payment_scan.scan(pid,now=datetime.fromisoformat(d['review_deadline'])))
    async def capture(cid):return {'id':cid,'status':'REFUNDED','amount':paypal.step1_amount(m['amount_minor'],'USD')}
    async def read_payout(batch):
        with db.engine().connect() as conn:payout=db.rows(conn,db.milestone_payouts)[0]
        return {'batch_header':{'payout_batch_id':batch,'batch_status':'PENDING','sender_batch_header':{'sender_batch_id':payout['id']}},'items':[]}
    monkeypatch.setattr(paypal,'step1_get_capture',capture)
    monkeypatch.setattr(paypal,'step1_get_payout',read_payout)
    with db.engine().begin() as conn:
        row=a.payment(conn,pid,m['id']);a.save(conn,row,'captured',capture_checked_at=None)
    asyncio.run(payment_scan.scan(pid))
    assert c.get(root).json()['payments'][0]['status']=='attention'
    cases=c.get(base(pid)).json()['cases'];assert len(cases)==1 and cases[0]['category']=='payment'
    with db.engine().connect() as conn:
        entries=db.rows(conn,db.ledger)
        assert sum(x['amount_cents'] for x in entries if x['account']=='project_liability')==0


def test_handover_after_withdrawal_preserves_partial_work_without_starting_payment(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch)
    call(f,'freelancer',room(c,pid),'freelancer-withdraw',reason='An emergency prevents me completing the rest of the milestone.')
    result=f.post(base(pid,'freelancer')+'/handover',json={'revision':room(c,pid)['revision'],'milestone_id':m['id'],
        'url':'https://example.com/partial','note':'The header and navigation are complete; the main content is unfinished.'})
    assert result.status_code==200,result.text
    assert result.json()['case']['category']=='partial_work' and result.json()['evidence']['digest']
    assert c.get(root).json()['payments'][0]['status']=='authorized' and not calls


def test_unknown_refund_can_only_attach_reference_from_original_capture(portals,monkeypatch):
    from fastapi import HTTPException
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch)
    with db.engine().begin() as conn:row=a.payment(conn,pid,m['id']);a.save(conn,row,'captured',capture_id='ORIGINAL-CAP')
    capture_link={'value':'WRONG-CAP'};mutations=[]
    async def request(method,path,body=None,key=None):
        if method=='POST':mutations.append(key);raise HTTPException(502,'Lost refund response.')
        return {'id':'REF-FOUND','status':'COMPLETED','amount':paypal.step1_amount(m['amount_minor'],'USD'),
            'links':[{'rel':'up','href':'https://api-m.sandbox.paypal.com/v2/payments/captures/'+capture_link['value']}]}
    async def capture(cid):return {'id':cid,'status':'REFUNDED'}
    monkeypatch.setattr(paypal,'call',request);monkeypatch.setattr(paypal,'step1_get_capture',capture)
    r=propose(c,pid,'settlement',milestone_id=m['id'],outcome='refund',amount_minor=m['amount_minor']);vote(c,pid,r);vote(f,pid,r,'freelancer',status=502)
    route=base(pid)+f'/requests/{r["id"]}/reconcile'
    assert c.post(route,json={'refund_id':'REF-FOUND'}).status_code==422  # only provider alphanumeric IDs
    request_result='REF123'
    async def read(method,path,body=None,key=None):
        assert method=='GET'
        return {'id':request_result,'status':'COMPLETED','amount':paypal.step1_amount(m['amount_minor'],'USD'),
            'links':[{'rel':'up','href':'https://api-m.sandbox.paypal.com/v2/payments/captures/'+capture_link['value']}]}
    monkeypatch.setattr(paypal,'call',read)
    assert c.post(route,json={'refund_id':request_result}).status_code==502
    assert c.get(root).json()['payments'][0]['status']=='settling'
    capture_link['value']='ORIGINAL-CAP'
    result=c.post(route,json={'refund_id':request_result});assert result.status_code==200,result.text
    assert c.get(root).json()['payments'][0]['status']=='refunded' and len(mutations)==1


def test_recovered_order_requires_exact_project_amount_and_never_posts_money(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch)
    with db.engine().begin() as conn:
        row=a.payment(conn,pid,m['id']);a.save(conn,row,'attention',attention='Lost checkout response.',order_id=None,authorization_id=None)
    unit={'custom_id':'WRONG','amount':paypal.step1_amount(row['amount_minor'],'USD')}
    async def request(method,path,body=None,key=None):
        assert method=='GET'
        return {'id':'FOUNDORDER','status':'APPROVED','purchase_units':[unit]}
    monkeypatch.setattr(paypal,'call',request)
    route=base(pid)+'/'+m['id']+'/recover-order'
    assert c.post(route,json={'order_id':'FOUNDORDER'}).status_code==502
    unit['custom_id']=row['id']
    result=c.post(route,json={'order_id':'FOUNDORDER'});assert result.status_code==200,result.text
    assert result.json()['status']=='awaiting_approval' and not calls


def test_accepted_withdrawal_voids_unsubmitted_hold_once(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch);voids=fake_void(monkeypatch)
    with db.engine().begin() as conn:
        row=db.one(conn,db.projects,db.projects.c.id==pid);p=copy.deepcopy(row['payload'])
        p['withdrawal']={'status':'accepted','reason':'Unable to continue remaining work.'}
        conn.execute(update(db.projects).where(db.projects.c.id==pid).values(payload=p))
    asyncio.run(w.scan(pid));asyncio.run(w.scan(pid))
    assert len(voids)==1
    assert c.get(root).json()['payments'][0]['status']=='voided'


def test_accepted_withdrawal_preserves_submitted_work(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,delivered=prepared(portals,monkeypatch);voids=fake_void(monkeypatch)
    with db.engine().begin() as conn:
        row=db.one(conn,db.projects,db.projects.c.id==pid);p=copy.deepcopy(row['payload'])
        p['withdrawal']={'status':'accepted','reason':'Cannot undertake further work.'}
        conn.execute(update(db.projects).where(db.projects.c.id==pid).values(payload=p))
    asyncio.run(w.scan(pid))
    assert not voids
    assert c.get(root).json()['payments'][0]['status']=='in_review'


def test_withdrawal_with_open_partial_work_claim_keeps_hold(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch);voids=fake_void(monkeypatch)
    with db.engine().begin() as conn:
        row=db.one(conn,db.projects,db.projects.c.id==pid);p=copy.deepcopy(row['payload'])
        p['withdrawal']={'status':'accepted','reason':'Cannot finish remaining work.'}
        p['support_cases']=[{'id':'claim','milestone_id':m['id'],'status':'awaiting_support'}]
        conn.execute(update(db.projects).where(db.projects.c.id==pid).values(payload=p))
    asyncio.run(w.scan(pid))
    assert not voids
    assert c.get(root).json()['payments'][0]['status']=='authorized'
