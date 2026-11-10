"""Idempotent local-only demo data through the authenticated Step 1 API.

python -m scripts.seed_step1
All signatures belong to explicitly labeled demo accounts, with no real funds.
"""
import os
from datetime import date,timedelta
from fastapi.testclient import TestClient
from sqlalchemy import select
from dotenv import load_dotenv
load_dotenv()
import main
from app import db
from app.guided_agreement import owner
from app.agreement_examples import example_agreement

def run():
    if os.getenv('APP_ENV','development')=='production' or os.getenv('EMAIL_MODE','local')!='local':raise RuntimeError('Seed only a local development database with local email previews.')
    password=os.getenv('FAIRFLOW_DEMO_PASSWORD','Demo-terms-only-123!')
    accounts={}
    for role,name in [('client','Alice Baker'),('freelancer','Rahul Kumar')]:
        c=TestClient(main.app);email=f'step1-{role}@example.com'
        response=c.post(f'/api/{role}/login',json=dict(email=email,password=password))
        if response.status_code==401:
            response=c.post(f'/api/{role}/signup',json=dict(name=name,email=email,password=password,country='IN',time_zone='Asia/Kolkata',professional_title='Frontend developer',primary_category='Frontend',skills=['HTML','CSS']))
        if response.status_code not in (200,201):raise RuntimeError(response.text)
        c.headers['x-csrf-token']=response.json()['csrf_token']
        if not response.json()['user']['verified']:
            token=next(m['body'].split('#verify=')[1] for m in c.get('/api/auth/mail').json() if '#verify=' in m['body'])
            response=c.post('/api/auth/verify',json={'token':token});response.raise_for_status()
        accounts[role]=c
    c=accounts['client'];f=accounts['freelancer']
    for state in ('Contradictory draft','Invitation','Signed agreement','Sandbox payment demo'):
        title='DEMO Step 1 — '+state
        if any(p['title']==title for p in c.get('/api/client/projects').json()):continue
        terms=example_agreement();terms['basics']['title']=title
        if state=='Sandbox payment demo':
            today=date.today();terms['payment']['currency']='USD';terms['timeline']['milestones'][0]['currency']='USD'
            terms['timeline']['start_date']=str(today+timedelta(days=2))
            terms['timeline']['milestones'][0]['due_date']=str(today+timedelta(days=10))
            terms['timeline']['deadline']=str(today+timedelta(days=24))
            terms['scope']['dependencies'][0]['due_date']=str(today+timedelta(days=2))
        if state=='Contradictory draft':terms['payment']['total_minor']+=5000
        client_terms={section:{key:value for key,value in fields.items() if owner(section+'.'+key) in ('both','client')} for section,fields in terms.items()};response=c.post('/api/client/projects',json={'agreement':client_terms});response.raise_for_status();room=response.json();pid=room['project']['id']
        def act(role,action,**kw):
            nonlocal room
            response=accounts[role].post(f'/api/{role}/projects/{pid}/actions',json=dict(revision=room['revision'],action=action,**kw));response.raise_for_status();room=response.json()
        if state!='Contradictory draft':act('client','invite',recipient='step1-freelancer@example.com')
        if state in ('Signed agreement','Sandbox payment demo'):
            act('freelancer','open')
            proposal=room['agreement']
            for section,fields in terms.items():
                for key,value in fields.items():
                    if owner(section+'.'+key) in ('both','freelancer'):proposal.setdefault(section,{})[key]=value
            response=f.put(f'/api/freelancer/projects/{pid}/draft',json=dict(revision=room['revision'],agreement=proposal));response.raise_for_status();room=response.json()
            act('freelancer','send');sha=room['versions'][-1]['digest']
            for role in accounts:act(role,'approve',version_hash=sha)
            act('client','sign',version_hash=sha,typed_name='Alice Baker');act('freelancer','sign',version_hash=sha,typed_name='Rahul Kumar')
        print(title+': '+room['project']['status'])
    print('Local demo emails: step1-client@example.com and step1-freelancer@example.com')
    print('Password: FAIRFLOW_DEMO_PASSWORD, or the documented local demo default.')

if __name__=='__main__':run()
