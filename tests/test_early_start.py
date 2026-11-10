import copy
from datetime import date,datetime,timedelta,timezone
import pytest
from sqlalchemy import insert,update
from app import db,assurance as a,workflows as w,early_start,negotiation as n
from app.agreement_validation import digest
from test_step1_auth_validation import portals
from test_paid_amendments import paid_first,change_second
from test_step1_negotiation import call,sign_both
from test_workflows import propose,vote,base


def schedule(portals,monkeypatch):
    c,f,room,payment,payout=paid_first(portals,monkeypatch)
    return c,f,room,payment,payout,f"/api/client/projects/{room['project']['id']}/assurance"


def test_two_acceptances_update_schedule_agreement_and_exports_without_manual_edit(portals,monkeypatch):
    c,f,room,payment,payout,root=schedule(portals,monkeypatch);pid=room['project']['id']
    original=copy.deepcopy(room['agreement']);version=payment['agreement_id']
    preview=c.get(base(pid)+'/early-start/M2');assert preview.status_code==200,preview.text
    plan=preview.json();shift=plan['shift_days'];assert shift>0
    proposal=propose(c,pid,'early_start',milestone_id='M2')
    assert proposal['schedule']==plan
    vote(c,pid,proposal)
    assert c.get(root).json()['operational_agreement']==original
    vote(f,pid,proposal,'freelancer')
    current=c.get(root).json()['operational_agreement']
    assert current['timeline']['milestones'][0]==original['timeline']['milestones'][0]
    assert current['timeline']['milestones'][1]['due_date']==str(date.fromisoformat(original['timeline']['milestones'][1]['due_date'])-timedelta(days=shift))
    assert current['timeline']['deadline']==plan['deadline']
    assert current['payment']==original['payment']
    assert current['scope']['deliverables']==original['scope']['deliverables']
    document=c.get(f'/api/client/projects/{pid}/export/json').json()
    assert document['agreement']==current and document['sha256']==digest(current)
    assert document['original_sha256']==digest(original)
    assert len(document['schedule_addenda'][0]['votes'])==2
    original_export=c.get(f'/api/client/projects/{pid}/export/json?version_id={version}').json()
    assert original_export['agreement']==original
    assert c.get(f'/api/client/projects/{pid}/export/pdf').content.startswith(b'%PDF')
    assert c.get(f'/api/client/projects/{pid}/contract-summary').json()['terms']==current
    detail=c.get(f'/api/client/projects/{pid}').json()
    assert detail['active_rendered']['agreement']==current
    assert 'Approved schedule' in detail['active_rendered']['html']
    with db.engine().connect() as conn:
        assert db.one(conn,db.agreements,db.agreements.c.id==version)['payload']==original
        assert db.one(conn,db.milestone_payments,db.milestone_payments.c.id==payment['id'])==payment
        assert db.one(conn,db.milestone_payouts,db.milestone_payouts.c.id==payout['id'])==payout


def test_all_later_dates_dependencies_and_duration_shift_consistently(portals,monkeypatch):
    c,f,room,payment,payout,root=schedule(portals,monkeypatch);pid=room['project']['id']
    with db.engine().connect() as conn:
        p=db.one(conn,db.projects,db.projects.c.id==pid)['payload'];v=copy.deepcopy(n.sent(conn,p))
        terms=v['payload'];previous=terms['timeline']['milestones'][0]['due_date']
        terms['timeline']['milestones'].append({**terms['timeline']['milestones'][1],'id':'M3','title':'Final delivery','due_date':str(date.today()+timedelta(days=24))})
        terms['scope']['dependencies'][0]['due_date']=previous
        terms['timeline']['deadline']=str(date.today()+timedelta(days=29))
        holidays=copy.deepcopy(terms['timeline'].get('blackout_dates',[]))
        plan=early_start.plan(conn,p,v,'M2')
    assert len(plan['milestones'])==2
    for row in plan['milestones']:
        assert (date.fromisoformat(row['old_due'])-date.fromisoformat(row['due_date'])).days==plan['shift_days']
        assert (date.fromisoformat(row['old_due'])-date.fromisoformat(row['old_start'])).days==(date.fromisoformat(row['due_date'])-date.fromisoformat(row['start_date'])).days
    updated=copy.deepcopy(terms);early_start.apply(updated,{'schedule':plan})
    assert updated['scope']['dependencies'][0]['due_date']==str(date.fromisoformat(previous)-timedelta(days=plan['shift_days']))
    assert updated['timeline'].get('blackout_dates',[])==holidays


def test_financial_change_after_first_acceptance_blocks_application(portals,monkeypatch):
    c,f,room,payment,payout,root=schedule(portals,monkeypatch);pid=room['project']['id']
    r=propose(c,pid,'early_start',milestone_id='M2');vote(c,pid,r)
    with db.engine().begin() as conn:conn.execute(update(db.milestone_payouts).where(db.milestone_payouts.c.id==payout['id']).values(status='returned'))
    vote(f,pid,r,'freelancer',status=409)
    assert c.get(root).json()['operational_agreement']==room['agreement']


def test_unsettled_previous_payment_or_funded_target_cannot_move(portals,monkeypatch):
    c,f,room,payment,payout=paid_first(portals,monkeypatch,payout_status='attention');pid=room['project']['id']
    response=c.get(base(pid)+'/early-start/M2');assert response.status_code==409 and 'previous milestone' in response.text
    with db.engine().begin() as conn:
        conn.execute(update(db.milestone_payouts).where(db.milestone_payouts.c.id==payout['id']).values(status='paid'))
        conn.execute(insert(db.milestone_payments).values(**{**payment,'id':db.uid(),'milestone_id':'M2','status':'authorized','payload':{'authorization_id':'EXISTING'}}))
    response=c.get(base(pid)+'/early-start/M2');assert response.status_code==409 and 'funding activity' in response.text


def test_changed_schedule_and_expired_start_require_fresh_approval(portals,monkeypatch):
    c,f,room,payment,payout,root=schedule(portals,monkeypatch);pid=room['project']['id']
    r=propose(c,pid,'early_start',milestone_id='M2');vote(c,pid,r)
    later=w.clock()+timedelta(days=2)
    monkeypatch.setattr(w,'clock',lambda:later)
    response=vote(f,pid,r,'freelancer',status=409)
    assert 'passed' in response.text


def test_declined_early_start_preserves_the_agreement(portals,monkeypatch):
    c,f,room,payment,payout,root=schedule(portals,monkeypatch);pid=room['project']['id']
    r=propose(c,pid,'early_start',milestone_id='M2');vote(c,pid,r);vote(f,pid,r,'freelancer',decision='decline')
    assert c.get(root).json()['operational_agreement']==room['agreement']


def test_later_manual_amendment_inherits_approved_early_dates(portals,monkeypatch):
    c,f,room,payment,payout,root=schedule(portals,monkeypatch);pid=room['project']['id']
    r=propose(c,pid,'early_start',milestone_id='M2');vote(c,pid,r);vote(f,pid,r,'freelancer')
    expected=c.get(root).json()['operational_agreement']
    room=c.get(f'/api/client/projects/{pid}').json();room=call(c,'client',room,'amend')
    assert room['agreement']['timeline']==expected['timeline']
    room=change_second(c,room);room=call(c,'client',room,'send');room=sign_both(c,f,room)
    expected['timeline']['milestones'][1]['amount_minor']=1500
    assert room['agreement']['timeline']==expected['timeline']


def test_early_completion_opens_next_funding_window_today(portals,monkeypatch):
    from app import platform_paypal
    c,f,room,payment,payout=paid_first(portals,monkeypatch,first_due_days=20,second_due_days=40,deadline_days=44)
    pid=room['project']['id'];root=f'/api/client/projects/{pid}/assurance'
    monkeypatch.setattr(platform_paypal,'configured',lambda:True)
    assert not c.get(root).json()['funding_windows']['M2']['available']
    r=propose(c,pid,'early_start',milestone_id='M2')
    assert r['schedule']['shift_days']==20
    vote(c,pid,r);vote(f,pid,r,'freelancer')
    current=c.get(root).json()
    assert current['funding_windows']['M2']['available']
    m=current['operational_agreement']['timeline']['milestones'][1]
    assert (date.fromisoformat(m['due_date'])-date.fromisoformat(m['start_date'])).days==20
    assert current['operational_agreement']['timeline']['milestones'][0]==room['agreement']['timeline']['milestones'][0]


def test_actual_completed_work_can_precede_its_old_planned_due_date(portals,monkeypatch):
    from app.agreement_validation import validate
    c,f,room,payment,payout=paid_first(portals,monkeypatch,first_due_days=20,second_due_days=25,deadline_days=29)
    pid=room['project']['id']
    r=propose(c,pid,'early_start',milestone_id='M2');vote(c,pid,r);vote(f,pid,r,'freelancer')
    current=c.get(f'/api/client/projects/{pid}/assurance').json()['operational_agreement']
    assert current['timeline']['milestones'][1]['due_date']<current['timeline']['milestones'][0]['due_date']
    assert not [flag for flag in validate(current,today=date.today(),completed_milestones=['M1']) if flag['severity']=='BLOCKER']


def test_checkout_uses_approved_early_schedule_and_retains_original_signature_hash(portals,monkeypatch):
    from app import funded_payments as fp,platform_paypal as pp
    c,f,room,payment,payout=paid_first(portals,monkeypatch,first_due_days=20,second_due_days=40,deadline_days=44)
    pid=room['project']['id']
    r=propose(c,pid,'early_start',milestone_id='M2');vote(c,pid,r);vote(f,pid,r,'freelancer')
    monkeypatch.setattr(pp,'configured',lambda:True)
    monkeypatch.setattr(pp,'fingerprint',lambda:'early-platform-test')
    async def verified(*args):return None
    monkeypatch.setattr(fp,'verify_seller',verified)
    with db.engine().begin() as conn:
        project=db.one(conn,db.projects,db.projects.c.id==pid)['payload']
        project['funding_policy']={'version':fp.MODEL,'text':fp.POLICY}
        project['platform_seller']={'status':'verified','merchant_id':'SELLER','email':'seller@example.com'}
        conn.execute(update(db.projects).where(db.projects.c.id==pid).values(payload=project))
    requests=[]
    async def provider(method,path,body=None,key=None,seller=None):
        requests.append((method,path,body,seller))
        return {'id':'EARLY-ORDER','links':[{'rel':'approve','href':'https://www.sandbox.paypal.com/checkoutnow?token=EARLY-ORDER'}]}
    monkeypatch.setattr(pp,'call',provider)
    result=c.post(f'/api/client/projects/{pid}/assurance/start',json={'milestone_id':'M2'})
    assert result.status_code==200,result.text
    assert requests[0][2]['intent']=='CAPTURE'
    assert requests[0][2]['purchase_units'][0]['payment_instruction']['disbursement_mode']=='DELAYED'
    with db.engine().connect() as conn:
        saved=a.payment(conn,pid,'M2')
        assert saved['payload']['agreement_hash']==payment['payload']['agreement_hash']
        assert saved['payload']['funded_milestone_terms']['due_date']==r['schedule']['milestones'][0]['due_date']
