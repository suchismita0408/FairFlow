import copy,os
from datetime import date, timedelta
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import update,text
import main
from app import db
from app.agreement_examples import example_agreement
from app.agreement_schema import CATALOG, clean_draft, leaves
from app.agreement_validation import validate, digest

@pytest.fixture
def portals(tmp_path,monkeypatch):
    target=os.getenv('FAIRFLOW_TEST_DATABASE_URL')
    monkeypatch.setenv('DATABASE_URL',target or 'sqlite:///'+str(tmp_path/'step1.db'))
    monkeypatch.setenv('AUTH_ENABLED','true')
    monkeypatch.setenv('EMAIL_MODE','local')
    monkeypatch.setenv('TOKEN_ENCRYPTION_KEY',Fernet.generate_key().decode())
    if target:
        assert db.engine().url.database.startswith('_qa_step1_'),'Refuse to reset a non-QA database.'
        with db.engine().begin() as conn:
            conn.execute(text('DROP SCHEMA public CASCADE'));conn.execute(text('CREATE SCHEMA public'))
        from alembic.config import Config
        from alembic import command
        command.upgrade(Config('alembic.ini'),'head')
    else:db.metadata.create_all(db.engine())
    clients=[]
    def signup(role,email=None,verify=True):
        c=TestClient(main.app);clients.append(c)
        data=dict(name='Alice Baker' if role=='client' else 'Rahul Kumar',email=email or role+'@example.com',password='Step1-password-123!',country='IN',time_zone='Asia/Kolkata')
        if role=='freelancer':data.update(professional_title='Developer',primary_category='Frontend',skills=['HTML','CSS'])
        r=c.post(f'/api/{role}/signup',json=data)
        assert r.status_code==201,r.text
        c.headers['x-csrf-token']=r.json()['csrf_token']
        if verify:
            token=next(m['body'].split('#verify=')[1] for m in c.get('/api/auth/mail').json() if '#verify=' in m['body'])
            assert c.post('/api/auth/verify',json={'token':token}).status_code==200
        return c
    yield signup
    for c in clients:c.close()

def test_fixed_roles_sessions_audit_and_idle(portals):
    c=portals('client');f=portals('freelancer')
    assert c.get('/api/auth/me').json()['user']['role']=='client'
    assert c.post('/api/freelancer/login',json={'email':'client@example.com','password':'Step1-password-123!'}).status_code==403
    assert c.post('/api/freelancer/signup',json=dict(email='client@example.com',name='Other',password='Step1-password-123!',country='IN',time_zone='Asia/Kolkata',professional_title='Dev',primary_category='Frontend',skills=['HTML'])).status_code==409
    assert c.get('/api/auth/sessions').json()[0]['current']
    assert c.post('/api/agile/draft',json={}).status_code==403
    with db.engine().begin() as conn:
        conn.execute(update(db.sessions).where(db.sessions.c.user_id==c.get('/api/auth/me').json()['user']['id']).values(last_active_at='2000-01-01T00:00:00+00:00'))
    assert c.get('/api/auth/me').status_code==401
    assert f.post('/api/auth/logout-everywhere',json={}).status_code==200
    assert f.get('/api/auth/me').status_code==401

def test_failed_login_lockout_persists_audit(portals):
    portals('client');c=TestClient(main.app)
    for _ in range(5):assert c.post('/api/client/login',json={'email':'client@example.com','password':'wrong'}).status_code==401
    assert c.post('/api/client/login',json={'email':'client@example.com','password':'Step1-password-123!'}).status_code==429
    with db.engine().connect() as conn:assert len(db.rows(conn,db.audit,db.audit.c.kind=='failed_login'))==5

def test_valid_schema_and_pure_validation():
    a=clean_draft(example_agreement(date(2030,1,1)))
    before=copy.deepcopy(a)
    assert validate(a,today=date(2030,1,1))==[]
    assert a==before
    assert digest(a)==digest(dict(reversed(list(a.items()))))

def mandatory_paths():
    def visit(fields,prefix):
        for key,spec in fields.items():
            path=prefix+'.'+key
            if spec['required']:yield path
            if spec['type']=='repeat':yield from visit(spec['children'],path+'.0')
    return [p for section,fields in CATALOG.items() for p in visit(fields,section)]

def change(a,path,value):
    parts=path.split('.');obj=a
    for part in parts[:-1]:obj=obj[int(part)] if isinstance(obj,list) else obj[part]
    obj[int(parts[-1]) if isinstance(obj,list) else parts[-1]]=value

@pytest.mark.parametrize('path',mandatory_paths())
def test_every_mandatory_field(path):
    a=example_agreement();a['attachments']['links']=[{'label':'Design','url':'https://example.com/design','binding':False}];change(a,path,None)
    assert any(f['field_path']==path and f['severity']=='BLOCKER' for f in validate(a)),path

@pytest.mark.parametrize('path,value,rule',[
    ('payment.total_minor',99999,'Milestone total'),('timeline.milestones.0.currency','USD','one currency'),
    ('timeline.milestones.0.due_date','2099-01-01','outside'),('timeline.deadline','2000-01-01','after the start'),
    ('scope.deliverables.0.milestone_id','missing','exactly one'),('timeline.milestones.0.deliverable_ids',['missing'],'unknown'),
    ('scope.exclusions',['Responsive landing page'],'overlaps'),('scope.deliverables.0.revision_rounds','unlimited','integer'),
    ('revisions.turnaround_days',100,'do not fit'),('payment.kill_fee_percent',101,'between'),
    ('payment.deposit_percent',101,'between'),('ip.source_files',False,'Source code'),
    ('revisions.change_rate_minor',None,'mandatory'),('termination.jurisdiction','Atlantis','Unrecognized'),
    ('revisions.definition','Includes 9 revisions','conflicts'),
])
def test_contradiction_rules(path,value,rule):
    a=example_agreement();a['scope']['deliverables'][0]['type']='source_code'
    change(a,path,value)
    assert any(f['severity']=='BLOCKER' and rule.lower() in f['message'].lower() for f in validate(a)),validate(a)

def test_calendar_deposit_ip_ai_and_chronology():
    a=example_agreement(date(2030,1,1));a['timeline']['working_days']=[]
    assert any('non-working' in f['message'] for f in validate(a))
    a['timeline']['milestones'][0]['bumped_date']=True
    assert not any('non-working' in f['message'] for f in validate(a))
    assert any('today or later' in f['message'] for f in validate(a,today=date(2031,1,1)))
    a['payment'].update(due_trigger='days_after_delivery',due_days=30);a['ip']['transfer_timing']='delivery_exception'
    assert any('precedes' in f['message'] for f in validate(a))
    a['ip']['transfer_exception']='Both parties agree ownership transfers before payment.'
    assert any(f['severity']=='WARNING' for f in validate(a))
    a['confidentiality']['ai_policy']='prohibited';a['basics']['skills']=['Gemini']
    assert any('AI tool' in f['message'] for f in validate(a))
    b=example_agreement();b['timeline']['milestones'][0]['amount_minor']=10000;b['payment']['deposit_percent']=20
    assert any('Deposit exceeds' in f['message'] for f in validate(b))
    m=copy.deepcopy(b['timeline']['milestones'][0]);m.update(id='M2',due_date=b['timeline']['start_date']);b['timeline']['milestones'].append(m)
    assert any('chronological' in f['message'] for f in validate(b))

@pytest.mark.parametrize('word',['etc.','and so on','as needed','ASAP','reasonable','unlimited','similar to','standard','minor changes','good quality','approximately','TBD'])
def test_every_ambiguity_word_and_hash_bound_acknowledgments(word):
    a=example_agreement();a['basics']['background']=word
    flags=validate(a);warning=next(f for f in flags if f['severity']=='WARNING')
    a['acknowledgments']={warning['id']:{'client':'Explicit limit reviewed.','freelancer':'Agreed after discussion.'}}
    assert next(f for f in validate(a) if f['id']==warning['id'])['status']=='acknowledged'
    a['scope']['deliverables'][0]['specification']=word
    assert any(f['severity']=='BLOCKER' and f['field_path'].endswith('specification') for f in validate(a))
    a['basics']['background']=word+' changed'
    assert any(f['severity']=='WARNING' and f['status']=='open' for f in validate(a))


@pytest.mark.parametrize('review',[0,6,30])
def test_paypal_review_window_blocks_outside_one_to_five_days(review):
    a=example_agreement();a['timeline']['milestones'][0]['review_days']=review
    assert any(f['severity']=='BLOCKER' and '1-5' in f['message'] for f in validate(a))


def test_paypal_29_day_boundary_includes_review_revision_and_buffer():
    start=date(2030,1,1);a=example_agreement(start)
    a['timeline']['start_date']=str(start)
    a['timeline']['deadline']=str(start+timedelta(days=29))
    a['revisions']['turnaround_days']=1
    a['timeline']['milestones'][0].update(due_date=str(start+timedelta(days=17)),review_days=3)
    assert not [f for f in validate(a) if '29-day' in f['message']]
    a['timeline']['milestones'][0]['due_date']=str(start+timedelta(days=18))
    assert any('29-day' in f['message'] and f['field_path'].endswith('due_date') for f in validate(a))
    a['timeline']['deadline']=str(start+timedelta(days=30))
    assert any('29-day' in f['message'] and f['field_path']=='timeline.deadline' for f in validate(a))


def test_extreme_dates_and_numbers_return_flags_instead_of_overflow():
    a=example_agreement();a['revisions']['turnaround_days']=10**12
    a['timeline']['deadline']='9999-12-31'
    assert any(f['severity']=='BLOCKER' for f in validate(a))


@pytest.mark.parametrize('review',[1,2,3,4,5])
def test_paypal_review_accepts_one_to_five_days(review):
    a=example_agreement();a['timeline']['milestones'][0].update(review_days=review,auto_accept_days=review)
    assert not any(f['field_path'].endswith('.review_days') and f['severity']=='BLOCKER' for f in validate(a))
