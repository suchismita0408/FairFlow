import asyncio
import copy
from datetime import datetime,timedelta,timezone
import pytest
from sqlalchemy import update
from fastapi import HTTPException
from app import db,assurance as a,funded_payments as fp,platform_paypal as pp,payment_scan,workflows as w
from test_step1_auth_validation import portals
from test_workflows import authorized,propose,vote


def setup(portals,monkeypatch,status='funded'):
    c,f,pid,m,root,fr,legacy=authorized(portals,monkeypatch)
    monkeypatch.setattr(pp,'fingerprint',lambda:'platform-test')
    monkeypatch.setattr(pp,'configured',lambda:True)
    monkeypatch.setattr(pp,'config',lambda:{'PAYPAL_PLATFORM_CLIENT_ID':'PLATFORM'})
    stamp=datetime.now(timezone.utc)
    with db.engine().begin() as conn:
        project=db.one(conn,db.projects,db.projects.c.id==pid);p=copy.deepcopy(project['payload'])
        p['funding_policy']={'version':fp.MODEL,'text':fp.POLICY}
        p['platform_seller']={'user_id':db.one(conn,db.members,(db.members.c.project_id==pid)&(db.members.c.role=='freelancer'))['user_id'],
                              'email':'seller@example.com','merchant_id':'SELLER','partner_id':'PARTNER','tracking_id':'track','fingerprint':'platform-test','status':'verified'}
        conn.execute(update(db.projects).where(db.projects.c.id==pid).values(payload=p))
        row=a.payment(conn,pid,m['id'])
        payload={'funding_model':fp.MODEL,'platform_fingerprint':'platform-test','seller_merchant_id':'SELLER','seller_email':'seller@example.com',
                 'auto_release':True,'order_id':'ORDER','order_key':'CREATE','capture_key':'CAPTURE','release_key':'RELEASE',
                 'state_changed_at':stamp.isoformat()}
        if status not in ('awaiting_approval',):payload.update(capture_id='CAP',captured_at=stamp.isoformat(),provider_disburse_by=(stamp+timedelta(days=28)).isoformat())
        conn.execute(update(db.milestone_payments).where(db.milestone_payments.c.id==row['id']).values(status=status,payload=payload))
        row=a.payment(conn,pid,m['id'])
        if payload.get('capture_id'):
            from app.step1_ledger import capture
            capture(conn,row)
    events=[];remote={'mode':'DELAYED','status':'COMPLETED','refunded':False}
    def cap():return {'id':'CAP','status':'REFUNDED' if remote['refunded'] else remote['status'],'amount':{'currency_code':'USD','value':f"{m['amount_minor']/100:.2f}"},'disbursement_mode':remote['mode'],'create_time':stamp.isoformat()}
    def order():
        with db.engine().connect() as conn:saved=a.payment(conn,pid,m['id'])
        item={'custom_id':saved['id'],'amount':cap()['amount'],'payee':{'merchant_id':'SELLER'},'payment_instruction':{'disbursement_mode':remote.get('order_mode','DELAYED')}}
        if remote.get('has_capture',True):item['payments']={'captures':[cap()]}
        return {'id':'ORDER','intent':'CAPTURE','status':'APPROVED','purchase_units':[item]}
    async def call(method,path,body=None,key=None,seller=None):
        events.append((method,path,key))
        if '/merchant-integrations?' in path:return {'merchant_id':remote.get('seller','SELLER')}
        if '/merchant-integrations/' in path:return {'merchant_id':'SELLER','payments_receivable':True,'primary_email_confirmed':True,'oauth_integrations':[{'oauth_third_party':[{'partner_client_id':'PLATFORM','scopes':[pp.DELAY_SCOPE]}]}]}
        if method=='GET' and path=='/v2/checkout/orders/ORDER':return order()
        if method=='POST' and path=='/v2/checkout/orders/ORDER/capture':remote['has_capture']=True;return order()
        if method=='GET' and path=='/v2/payments/captures/CAP':return cap()
        if method=='POST' and path=='/v1/payments/referenced-payouts-items':
            remote['mode']='INSTANT';return {'id':'DISBURSEMENT'}
        if method=='POST' and path=='/v2/payments/captures/CAP/refund':
            remote['refunded']=body['amount']==cap()['amount']
            if not remote['refunded']:remote['status']='PARTIALLY_REFUNDED'
            return {'id':'REFUND','status':'COMPLETED','amount':body['amount']}
        raise AssertionError((method,path))
    monkeypatch.setattr(pp,'call',call)
    return c,f,pid,m,root,fr,events,remote


def test_upfront_collection_is_required_before_submission(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch,'awaiting_approval');remote['has_capture']=False
    delivery={'milestone_id':m['id'],'url':'https://example.com/work','note':'All the agreed work is ready for client review.'}
    assert f.post(fr+'/deliver',json=delivery).status_code==409
    result=c.post(root+'/'+m['id']+'/authorize',json={})
    assert result.status_code==200,result.text
    assert result.json()['status']=='funded'
    assert c.post(root+'/'+m['id']+'/authorize',json={}).status_code==409
    assert len([e for e in events if e[0]=='POST' and e[1].endswith('/capture')])==1
    assert f.post(fr+'/deliver',json=delivery).status_code==200


def test_timeout_releases_existing_capture_without_legacy_transfer(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    result=f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'All the agreed deliverables have been completed.'})
    assert result.status_code==200,result.text
    deadline=datetime.fromisoformat(result.json()['review_deadline'])
    asyncio.run(payment_scan.scan(pid,now=deadline-timedelta(seconds=1)))
    assert not [e for e in events if e[0]=='POST']
    asyncio.run(payment_scan.scan(pid,now=deadline));asyncio.run(payment_scan.scan(pid,now=deadline))
    assert len([e for e in events if e[1]=='/v1/payments/referenced-payouts-items'])==1
    assert c.get(root).json()['payments'][0]['status']=='captured'
    assert not [e for e in events if e[1].endswith('/capture') or e[1]=='/v1/payments/payouts']


def test_objection_prevents_automatic_platform_release(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    delivery=f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'Completed deliverables submitted for review.'}).json()
    assert c.post(root+'/'+m['id']+'/object',json={'kind':'dispute','reason':'Please investigate the missing agreed functionality.'}).status_code==200
    asyncio.run(payment_scan.scan(pid,now=datetime.fromisoformat(delivery['review_deadline'])))
    assert not [e for e in events if e[0]=='POST']


def test_accepted_withdrawal_refunds_funded_unsubmitted_work_once(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    with db.engine().begin() as conn:
        p=db.one(conn,db.projects,db.projects.c.id==pid)['payload'];p['withdrawal']={'status':'accepted'}
        conn.execute(update(db.projects).where(db.projects.c.id==pid).values(payload=p))
    asyncio.run(w.scan(pid));asyncio.run(w.scan(pid))
    assert c.get(root).json()['payments'][0]['status']=='refunded'
    assert len([e for e in events if e[1].endswith('/refund')])==1


def test_joint_cancel_refunds_upfront_funding(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    r=propose(c,pid,'cancel');vote(c,pid,r);vote(f,pid,r,'freelancer')
    assert c.get(root).json()['payments'][0]['status']=='refunded'


def test_same_account_seller_is_rejected(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch);remote['seller']='PARTNER'
    result=f.post(fr+'/platform/verify',json={})
    assert result.status_code==409 and 'own seller' in result.text


def test_changed_platform_credentials_cannot_read_or_release_payment(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    monkeypatch.setattr(pp,'fingerprint',lambda:'another-app')
    result=c.post(root+'/platform/'+m['id']+'/reconcile',json={})
    assert result.status_code==409 and not events


def test_unexpected_immediate_capture_never_marks_work_funded(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch,'awaiting_approval');remote.update(mode='INSTANT',has_capture=False)
    result=c.post(root+'/'+m['id']+'/authorize',json={})
    assert result.status_code==502
    assert c.get(root).json()['payments'][0]['status']=='attention'


def test_uncertain_capture_reconciles_without_recollecting(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch,'awaiting_approval')
    with db.engine().begin() as conn:a.save(conn,a.payment(conn,pid,m['id']),'attention')
    result=c.post(root+'/'+m['id']+'/reconcile',json={})
    assert result.status_code==200,result.text
    assert result.json()['local']['status']=='funded'
    assert not [e for e in events if e[0]=='POST']


def test_new_funding_terms_require_both_votes(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    with db.engine().begin() as conn:
        p=db.one(conn,db.projects,db.projects.c.id==pid)['payload'];p.pop('funding_policy')
        conn.execute(update(db.projects).where(db.projects.c.id==pid).values(payload=p))
    r=propose(c,pid,'funding');vote(c,pid,r)
    assert not c.get(root).json()['platform']['terms_agreed']
    vote(f,pid,r,'freelancer')
    assert c.get(root).json()['platform']['terms_agreed']


def test_full_funded_refund_is_joint_and_does_not_release(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    r=propose(c,pid,'settlement',milestone_id=m['id'],outcome='refund',amount_minor=m['amount_minor'])
    vote(c,pid,r);assert not [e for e in events if e[0]=='POST']
    vote(f,pid,r,'freelancer');assert c.get(root).json()['payments'][0]['status']=='refunded'


def test_partial_funded_settlement_refunds_difference_before_release(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    award=m['amount_minor']//2
    r=propose(c,pid,'settlement',milestone_id=m['id'],outcome='partial',amount_minor=award,handover_url='https://example.com/partial-work')
    vote(c,pid,r);vote(f,pid,r,'freelancer')
    result=c.get(root).json()['payments'][0]
    assert result['status']=='captured' and result['settlement_amount_minor']==award
    mutations=[e[1] for e in events if e[0]=='POST']
    assert mutations==['/v2/payments/captures/CAP/refund','/v1/payments/referenced-payouts-items']
    entries=c.get(root+'/ledger').json()['entries']
    assert sum(e['amount_cents'] for e in entries)==0
    assert c.post(root+'/platform/'+m['id']+'/reconcile',json={}).status_code==200
    assert c.get(root).json()['payments'][0]['status']=='captured'


def test_day_26_unsubmitted_funded_work_refunds_without_ghost_payment(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    asyncio.run(w.scan(pid,now=datetime.now(timezone.utc)+timedelta(days=26,hours=1)))
    assert c.get(root).json()['payments'][0]['status']=='refunded'
    assert not any(e[1]=='/v1/payments/referenced-payouts-items' for e in events)


def test_platform_order_creation_uses_delayed_capture_intent_and_frozen_seller(portals,monkeypatch):
    from sqlalchemy import delete
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    with db.engine().begin() as conn:
        conn.execute(delete(db.milestone_payments).where(db.milestone_payments.c.project_id==pid))
    original=pp.call;created=[]
    async def provider(method,path,body=None,key=None,seller=None):
        if method=='POST' and path=='/v2/checkout/orders':
            created.append((copy.deepcopy(body),key,seller))
            return {'id':'NEWORDER','links':[{'rel':'approve','href':'https://www.sandbox.paypal.com/checkoutnow?token=NEWORDER'}]}
        return await original(method,path,body,key,seller)
    monkeypatch.setattr(pp,'call',provider)
    result=c.post(root+'/start',json={'milestone_id':m['id']})
    assert result.status_code==200,result.text
    body,key,seller=created[0]
    assert body['intent']=='CAPTURE' and key and seller=='SELLER'
    assert body['purchase_units'][0]['payment_instruction']['disbursement_mode']=='DELAYED'
    assert body['purchase_units'][0]['payee']=={'merchant_id':'SELLER'}
    assert c.post(root+'/start',json={'milestone_id':m['id']}).status_code==409
    assert len(created)==1


def test_platform_hold_warning_opens_case_once(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    # Delivered but disputed work must not be auto-refunded by a no-delivery rule.
    response=f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'The completed deliverables are ready for review.'})
    assert response.status_code==200
    assert c.post(root+'/'+m['id']+'/object',json={'kind':'dispute','reason':'Please investigate this scope disagreement.'}).status_code==200
    with db.engine().connect() as conn:user=db.one(conn,db.users,db.users.c.id==db.one(conn,db.members,(db.members.c.project_id==pid)&(db.members.c.role=='client'))['user_id'])
    now=datetime.now(timezone.utc)+timedelta(days=25,hours=1)
    for _ in range(2):
        with db.engine().connect() as conn:rows=db.rows(conn,db.milestone_payments,db.milestone_payments.c.project_id==pid)
        asyncio.run(fp.scan(pid,user,rows,now))
    with db.engine().connect() as conn:p=db.one(conn,db.projects,db.projects.c.id==pid)['payload']
    assert len([case for case in p['support_cases'] if case['category']=='payment'])==1
    assert c.get(root).json()['payments'][0]['status']=='disputed'
    assert not [e for e in events if e[0]=='POST']


def test_client_cannot_obtain_freelancer_onboarding_token(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    with db.engine().begin() as conn:
        p=db.one(conn,db.projects,db.projects.c.id==pid)['payload']
        uid=db.one(conn,db.members,(db.members.c.project_id==pid)&(db.members.c.role=='freelancer'))['user_id']
        p['platform_seller'].update(user_id=uid,action_url='https://www.sandbox.paypal.com/private-onboarding-token')
        conn.execute(update(db.projects).where(db.projects.c.id==pid).values(payload=p))
    assert 'action_url' not in c.get(root).json()['platform']['seller']
    assert 'action_url' not in c.get(f'/api/client/projects/{pid}').json()['project']['platform_seller']
    assert f.get(fr).json()['platform']['seller']['action_url'].endswith('private-onboarding-token')


def test_uncertain_platform_order_recovery_checks_frozen_recipient_and_reference(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch,'awaiting_approval')
    with db.engine().begin() as conn:
        row=a.payment(conn,pid,m['id']);payload={k:v for k,v in row['payload'].items() if k!='order_id'}
        conn.execute(update(db.milestone_payments).where(db.milestone_payments.c.id==row['id']).values(status='attention',payload=payload))
    result=c.post(root+'/platform/'+m['id']+'/recover-order',json={'order_id':'ORDER'})
    assert result.status_code==200,result.text
    assert result.json()['status']=='funded'
    assert not [event for event in events if event[0]=='POST']


def test_new_guided_agreement_discloses_upfront_funding_when_configured(portals,monkeypatch):
    monkeypatch.setattr(pp,'configured',lambda:True)
    client=portals('client')
    result=client.post('/api/client/projects',json={'agreement':{}})
    assert result.status_code==201,result.text
    terms=result.json()['agreement']['payment']
    assert terms['funding_model']==fp.MODEL and terms['funding_rules']==fp.POLICY


def test_default_platform_flow_never_falls_back_to_legacy_for_unsigned_funding_terms(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    with db.engine().begin() as conn:
        p=db.one(conn,db.projects,db.projects.c.id==pid)['payload'];p.pop('funding_policy')
        conn.execute(update(db.projects).where(db.projects.c.id==pid).values(payload=p))
    response=c.post(root+'/start',json={'milestone_id':m['id']})
    assert response.status_code==409 and 'updated agreement' in response.text
    assert c.get(root).json()['platform']['enabled'] is True
    assert not c.get(root).json()['platform']['terms_agreed']
    assert not events


def test_normal_amendment_includes_default_funding_terms_without_extra_opt_in(portals,monkeypatch):
    from test_paid_amendments import paid_first,change_second
    from test_step1_negotiation import call,sign_both
    c,f,r,payment,payout=paid_first(portals,monkeypatch,payout_status='attention')
    monkeypatch.setattr(pp,'configured',lambda:True)
    r=call(c,'client',r,'amend')
    assert r['agreement']['payment']['funding_model']==fp.MODEL
    assert r['agreement']['payment']['funding_rules']==fp.POLICY
    r=change_second(c,r);r=call(c,'client',r,'send');r=sign_both(c,f,r)
    assert c.get(f"/api/client/projects/{r['project']['id']}/assurance").json()['platform']['terms_agreed']
    with db.engine().connect() as conn:assert db.one(conn,db.milestone_payments,db.milestone_payments.c.id==payment['id'])==payment


def test_paid_change_request_carries_new_funding_policy_in_ordinary_acceptance(portals,monkeypatch):
    from datetime import date
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    with db.engine().begin() as conn:
        p=db.one(conn,db.projects,db.projects.c.id==pid)['payload'];p.pop('funding_policy')
        conn.execute(update(db.projects).where(db.projects.c.id==pid).values(payload=p))
    r=propose(c,pid,'change',title='Extra page',amount_minor=500,due_date=str(date.today()+timedelta(days=5)),criteria=['The added tab opens its agreed content.'])
    assert r['funding_model']==fp.MODEL and r['funding_rules']==fp.POLICY
    vote(c,pid,r);vote(f,pid,r,'freelancer')
    with db.engine().connect() as conn:
        p=db.one(conn,db.projects,db.projects.c.id==pid)['payload'];v={'payload':{}}
        assert fp.agreed(p,v,'CO-'+r['id'])


def test_reuse_verified_freelancer_connection_without_replacing_existing(monkeypatch):
    from app import funded_payments as funded
    seller={'status':'verified','user_id':'freelancer-a','fingerprint':'current-app','merchant_id':'SELLER-A'}
    monkeypatch.setattr(funded.provider,'configured',lambda:True)
    monkeypatch.setattr(funded.provider,'fingerprint',lambda:'current-app')
    monkeypatch.setattr(db,'one',lambda conn,table,condition:{'user_id':'freelancer-a'} if table is db.members else {'payload':{'platform_seller':seller}})
    p={'id':'new-project'};funded.inherit_seller(None,p)
    assert p['platform_seller']==seller and p['platform_seller'] is not seller
    existing={'id':'old-project','platform_seller':{'merchant_id':'ORIGINAL'}}
    funded.inherit_seller(None,existing)
    assert existing['platform_seller']['merchant_id']=='ORIGINAL'
    seller['user_id']='different-freelancer'
    p={'id':'another-project'};funded.inherit_seller(None,p)
    assert 'platform_seller' not in p


def test_client_can_release_early_but_freelancer_and_disputed_work_cannot(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    delivery=f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'Completed agreed work for the client to review.'})
    assert delivery.status_code==200
    assert f.post(fr+'/'+m['id']+'/approve-work',json={}).status_code==403
    result=c.post(root+'/'+m['id']+'/approve-work',json={})
    assert result.status_code==200,result.text
    assert c.get(root).json()['payments'][0]['status']=='captured'
    assert len([e for e in events if e[1]=='/v1/payments/referenced-payouts-items'])==1
    repeated=c.post(root+'/'+m['id']+'/approve-work',json={})
    assert repeated.status_code==200 and repeated.json()['message']=='Payment already released.'
    assert len([e for e in events if e[1]=='/v1/payments/referenced-payouts-items'])==1


def test_client_cannot_release_disputed_work(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    assert f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'Completed agreed work for the client to review.'}).status_code==200
    assert c.post(root+'/'+m['id']+'/object',json={'kind':'dispute','reason':'The agreed functionality is missing from the submission.'}).status_code==200
    assert c.post(root+'/'+m['id']+'/approve-work',json={}).status_code==409
    assert not [e for e in events if e[1]=='/v1/payments/referenced-payouts-items']


def test_pending_release_keeps_response_and_status_checks_never_repay(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    original=pp.call
    response={'item_id':'ITEM','links':[{'rel':'self','method':'GET','href':'https://api-m.sandbox.paypal.com/v1/payments/referenced-payouts-items/ITEM'}]}
    async def pending(method,path,body=None,key=None,seller=None):
        if method=='POST' and path=='/v1/payments/referenced-payouts-items':
            events.append((method,path,key));return response
        if method=='GET' and path=='/v1/payments/referenced-payouts-items/ITEM':
            events.append((method,path,key));return {'item_id':'ITEM','reference_id':'CAP','processing_state':{'status':'PROCESSING'}}
        return await original(method,path,body,key,seller)
    monkeypatch.setattr(pp,'call',pending)
    assert f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'Completed agreed work for review.'}).status_code==200
    assert c.post(root+'/'+m['id']+'/approve-work',json={}).status_code==200
    for _ in range(2):
        r=c.post(root+'/platform/'+m['id']+'/reconcile',json={})
        assert r.status_code==200,r.text
        assert r.json()['local']['status']=='disbursing'
        assert 'not yet confirmed' in r.json()['message']
    with db.engine().connect() as conn:
        row=a.payment(conn,pid,m['id'])
        assert row['payload']['release_response']==response
        assert row['payload']['release_check_response']['reference_id']=='CAP'
    assert len([e for e in events if e[0]=='POST' and e[1]=='/v1/payments/referenced-payouts-items'])==1


def test_old_release_without_reference_explains_uncertainty_without_repaying(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch,'attention')
    with db.engine().begin() as conn:
        row=a.payment(conn,pid,m['id'])
        a.save(conn,row,'attention',interrupted_operation='release')
    r=c.post(root+'/platform/'+m['id']+'/reconcile',json={})
    assert r.status_code==200,r.text
    assert r.json()['local']['status']=='attention'
    assert 'original release reference was not saved' in r.json()['message']
    assert not [e for e in events if e[0]=='POST']


def test_successful_referenced_payout_confirms_release_with_delayed_capture(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch,'disbursing')
    original=pp.call
    amount={'currency_code':'USD','value':f"{m['amount_minor']/100:.2f}"}
    response={'links':[{'rel':'self','method':'GET','href':'https://api.sandbox.paypal.com/v1/payments/referenced-payouts-items/ITEM='}]}
    with db.engine().begin() as conn:
        row=a.payment(conn,pid,m['id']);a.save(conn,row,'disbursing',release_response=response)
    async def checked(method,path,body=None,key=None,seller=None):
        if path.endswith('/ITEM='):
            return {'reference_id':'CAP','reference_type':'TRANSACTION_ID','processing_state':{'status':'SUCCESS'},'payout_destination':'SELLER','payout_transaction_id':'TRANSFER','payout_amount':amount}
        r=await original(method,path,body,key,seller)
        if path=='/v2/payments/captures/CAP':r['seller_receivable_breakdown']={'net_amount':amount}
        return r
    monkeypatch.setattr(pp,'call',checked)
    for _ in range(2):
        r=c.post(root+'/platform/'+m['id']+'/reconcile',json={})
        assert r.status_code==200,r.text
        assert r.json()['local']['status']=='captured'
        assert r.json()['local']['payout_transaction_id']=='TRANSFER'
    assert not [e for e in events if e[0]=='POST']


def test_reconcile_clears_stale_buyer_warning_without_capture(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch,'awaiting_approval');remote['has_capture']=False
    original=pp.call
    async def call(method,path,*args,**kwargs):
        result=await original(method,path,*args,**kwargs)
        if method=='GET' and path=='/v2/checkout/orders/ORDER':result['payer']={'email_address':' Buyer@example.com '}
        return result
    monkeypatch.setattr(pp,'call',call)
    monkeypatch.setattr(pp,'config',lambda:{'PAYPAL_SANDBOX_BUYER_EMAIL':'buyer@example.com'})
    with db.engine().begin() as conn:
        row=a.payment(conn,pid,m['id']);a.save(conn,row,'awaiting_approval',attention='Wrong PayPal Sandbox buyer account.')
    result=c.post(root+'/platform/'+m['id']+'/reconcile',json={})
    assert result.status_code==200,result.text
    assert result.json()['local']['attention'] is None
    assert result.json()['local']['status']=='awaiting_approval'
    assert not [e for e in events if e[0]=='POST']


def test_reconcile_preserves_wrong_buyer_warning(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch,'awaiting_approval');remote['has_capture']=False
    monkeypatch.setattr(pp,'config',lambda:{'PAYPAL_SANDBOX_BUYER_EMAIL':'buyer@example.com'})
    result=c.post(root+'/platform/'+m['id']+'/reconcile',json={})
    assert result.status_code==200,result.text
    assert 'Wrong PayPal Sandbox buyer' in result.json()['local']['attention']
    assert not [e for e in events if e[0]=='POST']


def test_repeated_release_does_not_send_another_provider_request(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch,'captured')
    result=c.post(root+'/'+m['id']+'/approve-work',json={})
    assert result.status_code==200,result.text
    assert result.json()['message']=='Payment already released.'
    assert events==[]
    assert f.post(fr+'/'+m['id']+'/approve-work',json={}).status_code==403


def test_inflight_release_is_not_repeated(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch,'disbursing')
    result=c.post(root+'/'+m['id']+'/approve-work',json={})
    assert result.status_code==200,result.text
    assert result.json()['local']['status']=='disbursing'
    assert events==[]


@pytest.mark.parametrize('mode',['INSTANT',None])
def test_collection_blocked_before_charge_without_delayed_order(portals,monkeypatch,mode):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch,'awaiting_approval');remote.update(has_capture=False,order_mode=mode)
    result=c.post(root+'/'+m['id']+'/authorize',json={})
    assert result.status_code==502,result.text
    assert not [e for e in events if e[0]=='POST']
    assert c.get(root).json()['payments'][0]['status']=='attention'


def test_wrong_platform_cannot_collect_or_release(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch,'awaiting_approval');remote['has_capture']=False
    monkeypatch.setattr(pp,'fingerprint',lambda:'wrong-app')
    assert c.post(root+'/'+m['id']+'/authorize',json={}).status_code==409
    assert c.post(root+'/'+m['id']+'/approve-work',json={}).status_code==409
    assert events==[]


def test_unexpected_provider_release_is_visible_and_audited(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch)
    remote['mode']='INSTANT'
    result=c.post(root+'/platform/'+m['id']+'/reconcile',json={})
    assert result.status_code==200,result.text
    payment=result.json()['local']
    assert payment['status']=='captured' and payment['unexpected_disbursement']
    assert 'without a recorded FairFlow release request' in payment['attention']
    assert not [e for e in events if e[0]=='POST']


def test_unexpected_release_warning_survives_rechecks(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch);remote['mode']='INSTANT'
    for _ in range(2):
        result=c.post(root+'/platform/'+m['id']+'/reconcile',json={})
        assert result.status_code==200,result.text
        assert result.json()['local']['unexpected_disbursement']
        assert result.json()['local']['attention']
    assert not [e for e in events if e[0]=='POST']


def test_unknown_capture_mode_pauses_work(portals,monkeypatch):
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch);remote['mode']=None
    result=c.post(root+'/platform/'+m['id']+'/reconcile',json={})
    assert result.status_code==200,result.text
    assert result.json()['local']['status']=='attention'
    assert 'did not confirm' in result.json()['local']['attention']
    assert not [e for e in events if e[0]=='POST']
