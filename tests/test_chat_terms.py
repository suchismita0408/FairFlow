import copy,asyncio
from datetime import date,timedelta,datetime,timezone
import pytest
from fastapi import HTTPException
from app import chat_terms,db,negotiation as n,payment_scan
from test_step1_auth_validation import portals
from test_step1_negotiation import start,call,sign_both,approve_both
from test_step1_assurance import signed_short_project
from test_workflows import propose,vote,authorized
from test_step1_automation import prepared


def chat(c,role,pid,text):
    r=c.post(f'/api/{role}/projects/{pid}/chat',json={'body':text})
    assert r.status_code==200,r.text
    return r.json()


def room(c,pid,role='client'):return c.get(f'/api/{role}/projects/{pid}').json()


def accept_price(c,f,r):
    pid=r['project']['id'];m=r['agreement']['timeline']['milestones'][0];amount=m['amount_minor']+5000;currency=r['agreement']['payment']['currency']
    chat(f,'freelancer',pid,f'Change milestone 1 price to {currency} {amount/100:.2f}.')
    chat(c,'client',pid,'Agreed.')
    return amount


def test_accepted_price_cannot_be_bypassed_by_old_amount_confirmations(portals):
    c,f,r=start(portals);pid=r['project']['id'];accept_price(c,f,r)
    r=room(c,pid);sha=r['versions'][-1]['digest']
    assert len(r['chat_term_changes'])==1 and not r['chat_term_changes'][0]['resolved']
    issue=r['chat_price_conflicts'][0]
    r=call(c,'client',r,'confirm-chat-price',flag_id=issue['id'],version_hash=sha,reason='The old amount is final.')
    r=call(f,'freelancer',r,'confirm-chat-price',flag_id=issue['id'],version_hash=sha,reason='The old amount is final.')
    result=c.post(f'/api/client/projects/{pid}/actions',json={'revision':r['revision'],'action':'approve','version_hash':sha})
    assert result.status_code==409 and 'Accepted chat changes' in result.text


def test_matching_updated_version_can_be_approved_and_signed(portals):
    c,f,r=start(portals);pid=r['project']['id'];expected=accept_price(c,f,r)
    r=room(f,pid,'freelancer');terms=copy.deepcopy(r['agreement']);old=terms['timeline']['milestones'][0]['amount_minor']
    terms['timeline']['milestones'][0]['amount_minor']=expected;terms['payment']['total_minor']+=expected-old
    saved=f.put(f'/api/freelancer/projects/{pid}/draft',json={'revision':r['revision'],'agreement':terms});assert saved.status_code==200,saved.text
    r=call(f,'freelancer',saved.json(),'send');r=sign_both(c,f,r)
    assert r['chat_term_changes'][0]['resolved'] and r['agreement']['timeline']['milestones'][0]['amount_minor']==expected


def test_offer_alone_or_rejected_offer_does_not_become_accepted(portals):
    c,f,r=start(portals);pid=r['project']['id']
    chat(f,'freelancer',pid,'Can we change milestone 1 price to USD 50?')
    assert not room(c,pid)['chat_term_changes']
    chat(c,'client',pid,'No, keep the current price.')
    chat(f,'freelancer',pid,'Okay.')
    assert not room(c,pid)['chat_term_changes']


def test_self_agreement_and_unrelated_acknowledgment_do_not_accept_other_offer(portals):
    c,f,r=start(portals);pid=r['project']['id']
    chat(f,'freelancer',pid,'Change milestone 1 price to USD 50.')
    chat(f,'freelancer',pid,'Agreed.')
    assert not room(c,pid)['chat_term_changes']


def test_accepted_relative_deadline_blocks_delivery_until_applied_addendum(portals,monkeypatch):
    c,f,pid,m,root,fr,calls=authorized(portals,monkeypatch)
    chat(f,'freelancer',pid,'Extend milestone 1 deadline by 2 days.')
    chat(c,'client',pid,'Okay, agreed.')
    result=f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'Completed all agreed outputs.'})
    assert result.status_code==409 and 'Accepted chat changes' in result.text
    new_due=str(date.fromisoformat(m['due_date'])+timedelta(days=2))
    r=propose(c,pid,'extension',milestone_id=m['id'],due_date=new_due);vote(c,pid,r);vote(f,pid,r,'freelancer')
    assert room(c,pid)['chat_term_changes'][0]['resolved']
    result=f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'Completed all agreed outputs.'})
    assert result.status_code==200,result.text


def test_new_funding_is_blocked_but_chat_does_not_pause_submitted_payment(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,delivery=prepared(portals,monkeypatch)
    chat(f,'freelancer',pid,'Add a mobile app to the agreed scope.')
    chat(c,'client',pid,'Agreed.')
    assert room(c,pid)['chat_term_changes'][0]['field_path']=='scope'
    result=c.post(root+'/start',json={'milestone_id':m['id'],'recipient_email':'seller@business.example.com'})
    assert result.status_code==409 and 'Accepted chat changes' in result.text
    asyncio.run(payment_scan.scan(pid,now=datetime.fromisoformat(delivery['review_deadline'])))
    assert c.get(root).json()['payments'][0]['status']=='captured'


def test_withdrawing_accepted_change_requires_both_names_and_reasons(portals):
    c,f,r=start(portals);pid=r['project']['id'];accept_price(c,f,r)
    change=room(c,pid)['chat_term_changes'][0]
    def withdraw(client,role,name):
        r=room(client,pid,role)
        return client.post(f'/api/{role}/projects/{pid}/chat-term-changes/{change["id"]}/withdraw',json={'revision':r['revision'],'typed_name':name,'reason':'We jointly abandon the proposed change and keep the existing terms.'})
    assert withdraw(c,'client','Wrong Name').status_code==422
    assert withdraw(c,'client','Alice Baker').status_code==200
    assert not room(c,pid)['chat_term_changes'][0]['resolved']
    assert withdraw(f,'freelancer','Rahul Kumar').status_code==200
    assert room(c,pid)['chat_term_changes'][0]['resolved']


def test_explicit_record_covers_other_fields_and_requires_both_confirmations(portals):
    c,f,r=start(portals);pid=r['project']['id'];message=chat(f,'freelancer',pid,'We will deliver the source in a zip archive.')
    path='ip.source_format';expected='ZIP archive containing the agreed original source.'
    def record(client,role,name):
        r=room(client,pid,role)
        return client.post(f'/api/{role}/projects/{pid}/chat/{message["id"]}/term-change',json={'revision':r['revision'],'field_path':path,'expected':expected,'typed_name':name})
    assert record(f,'freelancer','Rahul Kumar').status_code==200
    assert not room(c,pid)['chat_term_changes']
    assert record(c,'client','Alice Baker').status_code==200
    change=room(c,pid)['chat_term_changes'][0]
    assert change['expected']==expected and not change['resolved']
    r=room(c,pid)
    result=c.post(f'/api/client/projects/{pid}/actions',json={'revision':r['revision'],'action':'approve','version_hash':r['versions'][-1]['digest']})
    assert result.status_code==409


def test_one_accepted_message_tracks_both_price_and_date(portals):
    c,f,r=start(portals);pid=r['project']['id']
    amount=r['agreement']['timeline']['milestones'][0]['amount_minor']+5000
    currency=r['agreement']['payment']['currency'];due=str(date.fromisoformat(r['agreement']['timeline']['milestones'][0]['due_date'])+timedelta(days=2))
    chat(f,'freelancer',pid,f'Change milestone 1 price to {currency} {amount/100:.2f} and extend deadline to {due}.')
    chat(c,'client',pid,'Yes, agreed.')
    checks=room(c,pid)['chat_term_changes']
    assert {x['field_path'] for x in checks}=={'timeline.milestones.0.amount_minor','timeline.milestones.0.due_date'}
    assert all(not x['resolved'] for x in checks)


def test_from_old_to_new_price_checks_new_amount(portals):
    c,f,r=start(portals);pid=r['project']['id'];old=r['agreement']['timeline']['milestones'][0]['amount_minor'];currency=r['agreement']['payment']['currency']
    chat(f,'freelancer',pid,f'Change milestone 1 price from {currency} {old/100:.2f} to {currency} {(old+5000)/100:.2f}.')
    chat(c,'client',pid,'Agreed.')
    assert room(c,pid)['chat_term_changes'][0]['expected']==old+5000


def test_additional_price_requires_separate_matching_change_order(portals):
    c,f,r=signed_short_project(portals);pid=r['project']['id'];currency=r['agreement']['payment']['currency']
    chat(f,'freelancer',pid,f'I charge {currency} 5.00 for this additional work.')
    chat(c,'client',pid,'Agreed.')
    check=room(c,pid)['chat_term_changes'][0]
    assert check['addition_amount']==500 and not check['resolved']
    request=propose(c,pid,'change',title='Additional work',amount_minor=500,due_date=str(date.today()+timedelta(days=15)),criteria=['Extra tab matches the agreed new requirements.'])
    vote(c,pid,request);vote(f,pid,request,'freelancer')
    assert room(c,pid)['chat_term_changes'][0]['resolved']


def test_chat_agreement_after_approvals_blocks_signature(portals):
    c,f,r=start(portals);r,sha=approve_both(c,f,r);pid=r['project']['id']
    accept_price(c,f,r);r=room(c,pid)
    result=c.post(f'/api/client/projects/{pid}/actions',json={'revision':r['revision'],'action':'sign','version_hash':sha,'typed_name':'Alice Baker','payment_rules_confirmed':True})
    assert result.status_code==409 and not room(c,pid)['versions'][-1]['signatures']


@pytest.mark.parametrize('operation',['start','authorize'])
def test_platform_funding_checks_chat_before_any_provider_call(portals,monkeypatch,operation):
    from test_funded_payments import setup
    c,f,pid,m,root,fr,events,remote=setup(portals,monkeypatch,'awaiting_approval')
    chat(f,'freelancer',pid,'Change milestone 1 price to USD 50.')
    chat(c,'client',pid,'Agreed.')
    result=c.post(root+'/start',json={'milestone_id':m['id']}) if operation=='start' else c.post(root+'/'+m['id']+'/authorize',json={})
    assert result.status_code==409 and 'Accepted chat changes' in result.text
    assert not events
