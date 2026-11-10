import hashlib,json,copy,secrets,os
from datetime import datetime,timedelta,timezone
from fastapi import APIRouter,HTTPException
from sqlalchemy import select,insert,update
from . import db,auth,agile,store,decisions
from . import ai
from .platform_models import CreateWorkspace,AcceptVersion,EvidenceInput,MessageInput,DecisionInput,JointProposal,Vote,ClientVisionInput,SmartProposalData,SmartDraftInput,TwoSidedQuickstartInput
from .auth import Token
from .agreement_checks import agreement_issues
from .locking import project_lock

def hash_payload(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def assert_ok(condition,message):
    if not condition:raise HTTPException(409,message)
def get(pid):
    auth.role(pid)
    p=store.get(pid)
    if not p or p.get('_archived'):raise HTTPException(404,'Project not found.')
    return p

def agreement(conn,p):
    return db.one(conn,db.agreements,db.agreements.c.id==p.get('agreement_id'))

def write_version(conn,p,payload,user):
    existing=db.rows(conn,db.agreements,db.agreements.c.project_id==p['id'])
    number=max([x['number'] for x in existing],default=0)+1
    version={'id':db.uid(),'project_id':p['id'],'number':number,'digest':hash_payload(payload),'payload':payload,'status':'proposed','created_by':user['id'],'created_at':db.now()}
    conn.execute(insert(db.agreements).values(**version))
    for clause in payload['clauses']:conn.execute(insert(db.clauses).values(id=db.uid(),agreement_id=version['id'],**clause))
    p['agreement_id']=version['id'];p['accepted']=[]
    return version

def notification(conn,p,subject,body):
    for member in db.rows(conn,db.members,db.members.c.project_id==p['id']):
        user=db.one(conn,db.users,db.users.c.id==member['user_id'])
        auth.mail(conn,user['id'],user['email'],subject,body)

def schedule(conn,pid,kind,payload,run_at=None):
    jid=db.uid()
    conn.execute(insert(db.jobs).values(id=jid,project_id=pid,kind=kind,payload=payload,status='queued',attempts=0,run_at=run_at or db.now()))
    return jid

def room(pid):
    p=get(pid);user=auth.current()
    if 'smart_proposal' not in p or not p['smart_proposal']:
        m = p['milestones'][0] if p.get('milestones') else {}
        contract = p.get('contract', {})
        duration = min(max(1, m.get('duration_days', 16)), 21)
        target = datetime.now(timezone.utc) + timedelta(days=duration)
        delivery_date = f"{target.strftime('%b %d, %Y')} ({duration} days from today - safely within the 21-day platform limit)"
        p['smart_proposal'] = {
            'section1': {
                'project_title': contract.get('title', 'Bakery Landing Page'),
                'milestone_phase': m.get('title', 'Frontend Design & Build'),
                'goal_summary': contract.get('task_description', 'I need a landing page that captures emails for my new bakery.')
            },
            'section2': {
                'deliverables': contract.get('deliverables') or [
                    '3-Section Landing Page (Home, About, Contact)',
                    'Mobile and Tablet Responsive Design',
                    'Integration with Mailchimp for the email form'
                ]
            },
            'section3': {
                'revisions_included': contract.get('allowed_revisions', 2),
                'revision_rules': 'Tweaking colors, changing images, or adjusting text sizes. New pages or new buttons are extra.',
                'out_of_scope': contract.get('exclusions') or [
                    'No logo design.',
                    'No backend database setup.',
                    'Does not include writing the marketing text.',
                    'Does not include buying the domain name.'
                ]
            },
            'section4': {
                'price_cents': m.get('amount_cents') or contract.get('price_cents', 45000),
                'duration_days': duration,
                'delivery_date': delivery_date
            },
            'client_vision': {
                'goal': contract.get('task_description', 'I need a landing page that captures emails for my new bakery.'),
                'assets_inspiration': ''
            },
            'status': 'hold_authorized' if (p.get('payments') and any(x.get('status') == 'authorized' for x in p['payments'])) else ('sent_to_client' if p.get('accepted') else 'draft')
        }
    with db.engine().connect() as conn:
        versions=db.rows(conn,db.agreements,db.agreements.c.project_id==pid)
        for v in versions:
            v['acceptances']=db.rows(conn,db.acceptances,db.acceptances.c.agreement_id==v['id'])
            v['validation_issues']=agreement_issues(v['payload'])
        data={'project':agile.public(p),'role':auth.role(pid),'agreements':versions,
            'evidence':db.rows(conn,db.evidence,db.evidence.c.project_id==pid),
            'decisions':db.rows(conn,db.decisions,db.decisions.c.project_id==pid),
            'messages':db.rows(conn,db.messages,db.messages.c.project_id==pid),
            'actions':db.rows(conn,db.actions,db.actions.c.project_id==pid),
            'ledger':db.rows(conn,db.ledger,db.ledger.c.project_id==pid),
            'jobs':db.rows(conn,db.jobs,db.jobs.c.project_id==pid),
            'members':db.rows(conn,db.members,db.members.c.project_id==pid)}
        for a in data['actions']:a['votes']=db.rows(conn,db.votes,db.votes.c.action_id==a['id'])
        for job in data['jobs']:job.pop('lease_token',None)
        member_map={m['user_id']:m['role'] for m in data['members']}
        for m_item in data['messages']:
            m_item['role']=member_map.get(m_item['user_id'],'participant')
        data['project']['smart_proposal'] = p['smart_proposal']
    return data

def mount(app):
    router=APIRouter(prefix='/api/platform',tags=['Authenticated project rooms'])
    @router.get('/agreement-fields')
    def agreement_fields():
        auth.verify_required()
        from .agreement_checks import POLICY_FIELDS,example_details
        return {'fields':POLICY_FIELDS,'example':example_details() if agile.core.MODE=='demo' else None}

    @router.post('/agreements/validate')
    async def validate_agreement(payload:dict):
        auth.verify_required()
        from pydantic import ValidationError
        issues=[]
        try:CreateWorkspace.model_validate(payload)
        except ValidationError as exc:
            issues=[{'field':'.'.join(map(str,e['loc'])),'message':e['msg']} for e in exc.errors()]
        if issues:
            try:
                for message in agreement_issues(payload):
                    issues.append({'field':'agreement','message':message})
            except (TypeError,AttributeError,ValueError):
                pass  # Schema errors already identify malformed field types.
        if str(payload.get('counterparty_email','')).lower()==auth.current()['email']:
            issues.append({'field':'counterparty_email','message':'Client and freelancer must use different accounts.'})
        if not issues:
            from .semantic_review import check
            review=await check(payload)
            for issue in review['issues']:
                issues.append({'field':', '.join(issue['fields']),'message':issue['explanation']+' '+issue['question']})
            return {'valid':not issues,'issues':issues,'semantic_review':review}
        return {'valid':False,'issues':issues}

    @router.get('/projects')
    def list_projects():
        user=auth.verify_required()
        with db.engine().connect() as conn:ids=[m['project_id'] for m in db.rows(conn,db.members,db.members.c.user_id==user['id'])]
        return [agile.public(store.get(pid)) for pid in ids if store.get(pid) and not store.get(pid).get('_archived')]

    
    @router.post('/projects/two-sided-quickstart', status_code=201)
    async def create_two_sided_quickstart(data: TwoSidedQuickstartInput):
        user = auth.verify_required()
        counterparty = str(data.counterparty_email).strip().lower()
        assert_ok(counterparty != user['email'], 'Client and freelancer must be different accounts.')
        from .agreement_checks import example_details
        pid = db.uid()
        duration = 16
        target_date = datetime.now(timezone.utc) + timedelta(days=duration)
        delivery_str = f"{target_date.strftime('%b %d, %Y')} ({duration} days from today - safely within the 21-day platform limit)"
        
        contract = {
            'title': data.title,
            'task_description': data.client_goal,
            'price_cents': 45000,
            'allowed_revisions': 2,
            'hourly_rate_cents': 5000,
            'deliverables': ['3-Section Landing Page (Home, About, Contact)'],
            'exclusions': ['No logo design.', 'No backend database setup.']
        }
        milestones = [{
            'title': 'Frontend Design & Build',
            'scope': data.client_goal,
            'amount_cents': 45000,
            'duration_days': duration
        }]
        clauses = [
            {'reference': 'D1', 'kind': 'deliverable', 'text': 'Deliver the 3-section landing page with email capture form.'},
            {'reference': 'A1', 'kind': 'acceptance', 'text': 'Mobile and tablet responsive design.'},
            {'reference': 'B1', 'kind': 'defect_policy', 'text': 'Defects fixed without consuming revisions.'},
            {'reference': 'R1', 'kind': 'revision_policy', 'text': '2 revision rounds included.'},
            {'reference': 'E1', 'kind': 'exclusion', 'text': 'No logo design. No backend database setup.'}
        ]
        work_items = [[
            {'title': 'Frontend Design & Build', 'amount_cents': 45000, 'acceptance': 'Deliver the 3-section landing page.'}
        ]]
        terms = {
            'contract': contract,
            'milestones': milestones,
            'clauses': clauses,
            'work_items': work_items,
            'currency': data.currency,
            'source_conversation': data.client_goal,
            'details': example_details()
        }
        p = {
            'id': pid,
            'contract': contract,
            'client_name': user.get('name', 'Client'),
            'freelancer_name': counterparty.split('@')[0].capitalize(),
            'engine': 'agile_v2',
            'mode': agile.core.MODE,
            'status': 'draft',
            'plan_hash': hash_payload(terms),
            'original_plan': milestones,
            'accepted': [],
            'consents': [],
            'milestones': [agile.make_milestone(milestones[0], items=[{'id': db.uid(), 'title': 'Frontend Design & Build', 'amount_cents': 45000, 'acceptance': 'Deliver the 3-section landing page.'}])],
            'payments': [],
            'events': [],
            'changes': [],
            'deliveries': [],
            'revisions_used': 0,
            'review_deadline': None,
            'emergency_paused': False,
            'vault': {'status': 'not_connected'},
            'created_at': db.now(),
            'demo_clock_days': 0,
            'currency': data.currency,
            'smart_proposal': {
                'section1': {
                    'project_title': data.title,
                    'milestone_phase': 'Phase 1: Frontend Design & Build',
                    'goal_summary': data.client_goal
                },
                'section2': {
                    'deliverables': [
                        '3-Section Landing Page (Home, About, Contact)',
                        'Mobile and Tablet Responsive Design',
                        'Integration with Mailchimp for the email form'
                    ]
                },
                'section3': {
                    'revisions_included': 2,
                    'revision_rules': 'Tweaking colors, changing images, or adjusting text sizes. New pages or new buttons are extra.',
                    'out_of_scope': [
                        'No logo design.',
                        'No backend database setup.'
                    ]
                },
                'section4': {
                    'price_cents': 45000,
                    'duration_days': duration,
                    'delivery_date': delivery_str
                },
                'client_vision': {
                    'goal': data.client_goal,
                    'assets_inspiration': data.assets_inspiration
                },
                'status': 'draft'
            }
        }
        raw = secrets.token_urlsafe(32)
        with db.engine().begin() as conn:
            db.save_project(p, conn)
            conn.execute(insert(db.members).values(id=db.uid(), project_id=pid, user_id=user['id'], role='client'))
            write_version(conn, p, terms, user)
            conn.execute(insert(db.invitations).values(id=auth.digest(raw), project_id=pid, email=counterparty, role='freelancer', expires_at=auth.until(10080)))
            target = db.one(conn, db.users, db.users.c.email == counterparty)
            base = os.getenv('APP_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
            auth.mail(conn, target['id'] if target else None, counterparty, f'FairFlow Invitation: {data.title}', base + '/#invite=' + raw)
            conn.execute(insert(db.messages).values(id=db.uid(), project_id=pid, user_id=user['id'], body='Client created collaborative workspace.\nGoal: ' + data.client_goal, created_at=db.now()))
            db.log(conn, 'two_sided_quickstart_created', user['id'], pid)
            db.save_project(p, conn)
        return room(pid)

    @router.post('/projects',status_code=201)
    async def create(data:CreateWorkspace):
        user=auth.verify_required();payload=data.model_dump(mode='json')
        assert_ok(str(data.counterparty_email).lower()!=user['email'],'Client and freelancer must be different accounts.')
        from .semantic_review import require_ready
        await require_ready(payload)
        pid=db.uid();terms={k:payload[k] for k in ('contract','milestones','clauses','work_items','currency','source_conversation','details')}
        p={'id':pid,'contract':payload['contract'],'client_name':data.client_name,'freelancer_name':data.freelancer_name,'engine':'agile_v2','mode':agile.core.MODE,'status':'draft','plan_hash':hash_payload(terms),'original_plan':payload['milestones'],'accepted':[],'consents':[],'milestones':[],'payments':[],'events':[],'changes':[],'deliveries':[],'revisions_used':0,'review_deadline':None,'emergency_paused':False,'vault':{'status':'not_connected'},'created_at':db.now(),'demo_clock_days':0,'currency':data.currency}
        for i,m in enumerate(payload['milestones']):
            items=payload['work_items'][i] if payload['work_items'] else [{'title':m['title'],'acceptance':m['scope'],'amount_cents':m['amount_cents']}]
            p['milestones'].append(agile.make_milestone(m,items=[{'id':db.uid(),**item} for item in items]))
        raw=secrets.token_urlsafe(32);invite_role='client' if data.creator_role=='freelancer' else 'freelancer'
        with db.engine().begin() as conn:
            db.save_project(p,conn)
            conn.execute(insert(db.members).values(id=db.uid(),project_id=pid,user_id=user['id'],role=data.creator_role))
            write_version(conn,p,terms,user)
            conn.execute(insert(db.invitations).values(id=auth.digest(raw),project_id=pid,email=str(data.counterparty_email).lower(),role=invite_role,expires_at=auth.until(10080)))
            target=db.one(conn,db.users,db.users.c.email==str(data.counterparty_email).lower())
            base=__import__('os').getenv('APP_BASE_URL','http://127.0.0.1:8000').rstrip('/')
            auth.mail(conn,target['id'] if target else None,str(data.counterparty_email).lower(),'FairFlow project invitation',base+'/#invite='+raw)
            db.log(conn,'project_created',user['id'],pid,agreement_id=p['agreement_id'])
            db.save_project(p,conn)
        return room(pid)

    @router.post('/invitations/accept')
    async def accept_invite(data:Token):
        user=auth.verify_required()
        with db.engine().begin() as conn:
            invite=conn.execute(select(db.invitations).where(db.invitations.c.id==auth.digest(data.token)).with_for_update()).mappings().first()
            assert_ok(invite and invite['email']==user['email'] and invite['expires_at']>db.now(),'Invalid, expired, or wrong-recipient invitation.')
            if invite['accepted_by']:
                assert_ok(invite['accepted_by']==user['id'],'Invitation has already been used.')
            else:
                assert_ok(not db.one(conn,db.members,(db.members.c.project_id==invite['project_id'])&(db.members.c.user_id==user['id'])),'One person cannot occupy both project roles.')
                conn.execute(insert(db.members).values(id=db.uid(),project_id=invite['project_id'],user_id=user['id'],role=invite['role']))
                conn.execute(update(db.invitations).where(db.invitations.c.id==invite['id']).values(accepted_by=user['id']))
                db.log(conn,'invitation_accepted',user['id'],invite['project_id'],role=invite['role'])
        return room(invite['project_id'])

    @router.get('/projects/{pid}')
    def detail(pid:str):return room(pid)

    @router.post('/projects/{pid}/agreements')
    async def revise(pid:str,data:CreateWorkspace):
        user=auth.verify_required()
        get(pid)
        from .semantic_review import require_ready
        await require_ready(data.model_dump(mode='json'))
        async with project_lock(pid):
            p=get(pid)
            assert_ok(not p['payments'],'Funded agreements are immutable. Use a separately accepted change order for additions.')
            assert_ok(data.creator_role==auth.role(pid),'A revised agreement cannot change your project role.')
            with db.engine().connect() as conn:
                other=[m for m in db.rows(conn,db.members,db.members.c.project_id==pid) if m['user_id']!=user['id']]
                if other:
                    target=db.one(conn,db.users,db.users.c.id==other[0]['user_id'])
                    assert_ok(str(data.counterparty_email).lower()==target['email'],'A revised agreement cannot replace the other participant.')
            payload=data.model_dump(mode='json');terms={k:payload[k] for k in ('contract','milestones','clauses','work_items','currency','source_conversation','details')}
            p['currency']=data.currency;p['contract']=terms['contract'];p['plan_hash']=hash_payload(terms);p['original_plan']=terms['milestones']
            p['milestones']=[agile.make_milestone(m,items=[{'id':db.uid(),**x} for x in (terms['work_items'][i] if terms['work_items'] else [{'title':m['title'],'acceptance':m['scope'],'amount_cents':m['amount_cents']}])]) for i,m in enumerate(terms['milestones'])]
            p['status']='draft'
            # A saved-method mandate belongs to the exact accepted plan, including currency.
            p['vault']={'status':'not_connected'}
            with db.engine().begin() as conn:
                write_version(conn,p,terms,user);db.save_project(p,conn)
                db.log(conn,'agreement_proposed',user['id'],pid,agreement_id=p['agreement_id'])
                notification(conn,p,'Agreement changed','Review and accept the new agreement version before funding.')
        return room(pid)

    @router.post('/projects/{pid}/agreements/{vid}/accept')
    async def accept(pid:str,vid:str,data:AcceptVersion):
        user=auth.verify_required()
        snapshot=get(pid)
        with db.engine().connect() as conn:
            current_version=agreement(conn,snapshot)
        assert_ok(current_version and current_version['id']==vid and current_version['digest']==data.digest,'Review the current agreement; this acceptance is stale.')
        issues=agreement_issues(current_version['payload'])
        assert_ok(not issues,'Agreement needs clarification: '+' '.join(issues))
        from .semantic_review import require_ready
        await require_ready(current_version['payload'])
        async with project_lock(pid):
            p=get(pid);own_role=auth.role(pid)
            with db.engine().begin() as conn:
                v=agreement(conn,p)
                assert_ok(v and v['id']==vid and v['digest']==data.digest,'Review the current agreement; this acceptance is stale.')
                issues=agreement_issues(v['payload'])
                assert_ok(not issues,'Agreement needs clarification: '+' '.join(issues))
                existing=db.one(conn,db.acceptances,(db.acceptances.c.agreement_id==vid)&(db.acceptances.c.user_id==user['id']))
                if not existing:conn.execute(insert(db.acceptances).values(id=db.uid(),agreement_id=vid,user_id=user['id'],role=own_role,digest=v['digest'],created_at=db.now()))
                accepted=db.rows(conn,db.acceptances,db.acceptances.c.agreement_id==vid)
                p['accepted']=list({a['role'] for a in accepted})
                if len(p['accepted'])==2:
                    conn.execute(update(db.agreements).where(db.agreements.c.id==vid).values(status='accepted'))
                    p['status']='ready'
                db.log(conn,'agreement_accepted',user['id'],pid,agreement_id=vid,digest=v['digest'])
                db.save_project(p,conn)
        return room(pid)

    @router.post('/projects/{pid}/messages')
    def message(pid:str,data:MessageInput):
        get(pid);user=auth.current()
        with db.engine().begin() as conn:conn.execute(insert(db.messages).values(id=db.uid(),project_id=pid,user_id=user['id'],body=data.body,created_at=db.now()))
        return room(pid)

    @router.post('/projects/{pid}/evidence')
    def add_evidence(pid:str,data:EvidenceInput):
        p=get(pid);agile.milestone(p,data.milestone_id);user=auth.current()
        payload=data.model_dump(mode='json')
        with db.engine().begin() as conn:
            conn.execute(insert(db.evidence).values(id=db.uid(),project_id=pid,user_id=user['id'],**payload,digest=hash_payload(payload),created_at=db.now()))
            db.log(conn,'evidence_added',user['id'],pid,milestone_id=data.milestone_id)
        return room(pid)

    @router.post('/projects/{pid}/decisions')
    async def request_decision(pid:str,data:DecisionInput):
        user=auth.verify_required();auth.role(pid,('client',))
        # Snapshot under a short lock. No model call runs while holding the project lock.
        async with project_lock(pid):
            p=get(pid);m=agile.milestone(p,data.milestone_id)
            assert_ok(m['status'] in ('active','in_review','revision_requested','disputed'),'Requests require active funded work.')
            with db.engine().connect() as conn:
                v=agreement(conn,p)
                assert_ok(v and v['status']=='accepted','Both parties must accept the agreement first.')
                clauses=db.rows(conn,db.clauses,db.clauses.c.agreement_id==v['id'])
                evidence=db.rows(conn,db.evidence,(db.evidence.c.project_id==pid)&(db.evidence.c.id.in_(data.evidence_ids))) if data.evidence_ids else []
                assert_ok(len(evidence)==len(set(data.evidence_ids)),'Some evidence does not belong to this project.')
                for extra in p['milestones']:
                    if extra.get('change_id') and extra['accepted']:clauses.append({'reference':'A_'+extra['change_id'][:20],'kind':'deliverable','text':extra['scope']})
            if data.dispute_current_work:
                m.update(status='disputed',dispute_open=True,review_deadline=None)
                agile.log(p,'Client disputed current delivery while requesting an evidence-based review.');agile.sync(p)
            version=v['id'];scope=m['scope']
        result,trace=await decisions.recommend(clauses,evidence,data.request,scope)
        async with project_lock(pid):
            p=get(pid)
            assert_ok(p.get('agreement_id')==version,'Agreement changed during review. Request a fresh decision.')
            with db.engine().begin() as conn:
                conn.execute(insert(db.decisions).values(id=db.uid(),project_id=pid,milestone_id=data.milestone_id,agreement_id=version,requested_by=user['id'],request=data.request,result=result.model_dump(),model_trace=trace,status='awaiting_review',created_at=db.now()))
                db.log(conn,'ai_recommendation_created',user['id'],pid,category=result.category,agreement_id=version)
        return room(pid)

    @router.post('/projects/{pid}/actions')
    async def propose(pid:str,data:JointProposal):
        user=auth.verify_required()
        async with project_lock(pid):
            p=get(pid);m=agile.milestone(p,data.milestone_id)
            from .currencies import validate
            try:validate(p.get('currency','USD'),data.amount_cents)
            except ValueError as exc:raise HTTPException(422,str(exc))
            body=data.model_dump();body['agreement_id']=p.get('agreement_id')
            with db.engine().begin() as conn:
                if data.kind=='decision':
                    d=db.one(conn,db.decisions,(db.decisions.c.id==data.decision_id)&(db.decisions.c.project_id==pid))
                    assert_ok(d and d['status']=='awaiting_review' and d['milestone_id']==m['id'] and d['agreement_id']==p.get('agreement_id'),'Decision is missing or stale.')
                    assert_ok(data.category is not None,'Choose the reviewed classification.')
                    assert_ok(data.category=='additional_scope' or data.amount_cents==0,'Only additional scope may have a new quoted amount.')
                    assert_ok(data.category!='additional_scope' or data.amount_cents>0,'Enter the explicit proposed price for additional scope.')
                    body['request']=d['request']
                if data.kind=='dispute_resolution':assert_ok(m['status']=='disputed','No open dispute for this milestone.')
                if data.kind=='reconciliation':assert_ok(m['status']=='payment_attention','No uncertain operation to reconcile.')
                if data.kind=='refund':
                    assert_ok(data.amount_cents>0 and data.capture_id,'Specify a capture and positive refund amount.')
                    body['payment_version']=p.get('_db_version')
                if data.kind=='partial_items':
                    items=[x for x in m.get('items',[]) if x['id'] in data.item_ids]
                    assert_ok(items and len(items)==len(set(data.item_ids)),'Select priced items belonging to this milestone.')
                    proof=db.one(conn,db.evidence,(db.evidence.c.id==data.evidence_id)&(db.evidence.c.project_id==pid)&(db.evidence.c.milestone_id==m['id']))
                    assert_ok(proof,'Provide project evidence for the submitted work items.')
                    body['amount_cents']=sum(x['amount_cents'] for x in items)
                    body['completed_work']='; '.join(x['title'] for x in items)
                    body['remaining_work']='; '.join(x['title']+': '+x['acceptance'] for x in m['items'] if x['id'] not in data.item_ids)
                    body['evidence_url']=proof['url']
                aid=db.uid();digest=hash_payload(body)
                conn.execute(insert(db.actions).values(id=aid,project_id=pid,kind=data.kind,payload=body,digest=digest,status='proposed',created_by=user['id'],created_at=db.now()))
                conn.execute(insert(db.votes).values(id=db.uid(),action_id=aid,user_id=user['id'],digest=digest,created_at=db.now()))
                db.log(conn,'joint_action_proposed',user['id'],pid,action_id=aid,kind_proposed=data.kind)
                notification(conn,p,'Review requested','Review the proposed '+data.kind+' in the project room. No support override is available.')
        return room(pid)

    @router.post('/projects/{pid}/actions/{aid}/accept')
    async def vote(pid:str,aid:str,data:Vote):
        user=auth.verify_required()
        async with project_lock(pid):
            p=get(pid)
            with db.engine().begin() as conn:
                action=db.one(conn,db.actions,(db.actions.c.id==aid)&(db.actions.c.project_id==pid))
                assert_ok(action and action['digest']==data.digest,'Proposal changed or was not found.')
                assert_ok(action['payload']['agreement_id']==p.get('agreement_id'),'Proposal refers to a previous agreement version.')
                if action['status']!='proposed':return room(pid)
                if not db.one(conn,db.votes,(db.votes.c.action_id==aid)&(db.votes.c.user_id==user['id'])):conn.execute(insert(db.votes).values(id=db.uid(),action_id=aid,user_id=user['id'],digest=action['digest'],created_at=db.now()))
                voters=db.rows(conn,db.votes,db.votes.c.action_id==aid)
                if len(voters)==2:
                    conn.execute(update(db.actions).where(db.actions.c.id==aid).values(status='queued'))
                    schedule(conn,pid,'joint_action',{'action_id':aid})
                db.log(conn,'joint_action_accepted',user['id'],pid,action_id=aid)
        return room(pid)

    @router.post('/projects/{pid}/actions/{aid}/reject')
    async def reject(pid:str,aid:str):
        user=auth.verify_required()
        async with project_lock(pid):
            get(pid)
            with db.engine().begin() as conn:
                a=db.one(conn,db.actions,(db.actions.c.id==aid)&(db.actions.c.project_id==pid))
                assert_ok(a and a['status']=='proposed','Only a pending proposal can be rejected.')
                conn.execute(update(db.actions).where(db.actions.c.id==aid).values(status='rejected'))
                db.log(conn,'joint_action_rejected',user['id'],pid,action_id=aid)
        return room(pid)

    @router.post('/projects/{pid}/reconcile')
    async def reconcile(pid:str):
        get(pid)
        with db.engine().begin() as conn:schedule(conn,pid,'reconcile',{})
        return room(pid)

    @router.post('/projects/{pid}/client-vision')
    async def update_client_vision(pid:str,data:ClientVisionInput):
        user=auth.verify_required()
        auth.role(pid,('client',))
        async with project_lock(pid):
            p=get(pid)
            p.setdefault('smart_proposal',{}).setdefault('client_vision',{})
            p['smart_proposal']['client_vision']=data.model_dump()
            agile.log(p,f"Client updated project vision: {data.goal[:100]}")
            with db.engine().begin() as conn:
                db.save_project(p,conn)
                db.log(conn,'client_vision_updated',user['id'],pid)
                conn.execute(insert(db.messages).values(id=db.uid(),project_id=pid,user_id=user['id'],body=f"💡 Client updated project vision: \"{data.goal}\"",created_at=db.now()))
        return room(pid)

    @router.post('/projects/{pid}/smart-draft')
    async def generate_smart_draft(pid:str,data:SmartDraftInput):
        user=auth.verify_required()
        auth.role(pid,('freelancer',))
        p=get(pid)
        with db.engine().connect() as conn:
            msgs=db.rows(conn,db.messages,db.messages.c.project_id==pid)
        chat_str='\n'.join([f"Message: {m['body']}" for m in msgs]) if msgs else data.chat_history
        cv=p.get('smart_proposal',{}).get('client_vision',{})
        goal=data.client_goal or cv.get('goal','')
        assets=data.assets_inspiration or cv.get('assets_inspiration','')
        draft=await ai.smart_proposal_draft(chat_str,goal,assets,p.get('currency','USD'))
        async with project_lock(pid):
            p=get(pid)
            p['smart_proposal']=draft
            with db.engine().begin() as conn:
                db.save_project(p,conn)
                db.log(conn,'smart_proposal_ai_drafted',user['id'],pid)
        return room(pid)

    @router.post('/projects/{pid}/proposal/submit')
    async def submit_proposal(pid:str,data:SmartProposalData):
        user=auth.verify_required()
        auth.role(pid,('freelancer',))
        async with project_lock(pid):
            p=get(pid)
            assert_ok(not (p.get('payments') and any(x.get('status') in ('authorized','captured') for x in p['payments'])),'Cannot modify proposal after payment authorization.')
            p['smart_proposal']=data.model_dump()
            p['smart_proposal']['status']='sent_to_client'
            contract={
                'title':data.section1.project_title,
                'task_description':data.section1.goal_summary,
                'price_cents':data.section4.price_cents,
                'allowed_revisions':data.section3.revisions_included,
                'hourly_rate_cents':p['contract'].get('hourly_rate_cents',5000),
                'deliverables':data.section2.deliverables,
                'exclusions':data.section3.out_of_scope
            }
            clauses=[
                {'reference':'DELIVERY','kind':'deliverable','text':'\n'.join(data.section2.deliverables)},
                {'reference':'ACCEPTANCE','kind':'acceptance','text':'Delivered work satisfies each item in the agreed deliverables checklist.'},
                {'reference':'DEFECTS','kind':'defect_policy','text':'Failures to meet accepted criteria must be corrected without consuming presentation revisions.'},
                {'reference':'REVISIONS','kind':'revision_policy','text':f"{data.section3.revisions_included} presentation revisions. {data.section3.revision_rules}"},
                {'reference':'EXCLUSIONS','kind':'exclusion','text':'\n'.join(data.section3.out_of_scope)}
            ]
            mid=p['milestones'][0]['id'] if p.get('milestones') else db.uid()
            item_cnt=max(1,len(data.section2.deliverables))
            part,rem=divmod(data.section4.price_cents,item_cnt)
            items=[{'id':db.uid(),'title':d,'amount_cents':part+(rem if idx==0 else 0),'acceptance':d} for idx,d in enumerate(data.section2.deliverables)]
            p['milestones']=[agile.make_milestone({
                'title':f"Phase 1: {data.section1.milestone_phase}",
                'scope':data.section1.goal_summary,
                'amount_cents':data.section4.price_cents,
                'duration_days':data.section4.duration_days
            },id=mid,items=items)]
            from .agreement_checks import example_details
            details=p.get('details') or example_details()
            terms={
                'contract':contract,
                'milestones':[{'title':p['milestones'][0]['title'],'scope':p['milestones'][0]['scope'],'amount_cents':p['milestones'][0]['amount_cents'],'duration_days':p['milestones'][0]['duration_days']}],
                'clauses':clauses,
                'work_items':[items],
                'currency':p.get('currency','USD'),
                'source_conversation':p.get('source_conversation',''),
                'details':details
            }
            p['contract']=contract
            p['status']='draft'
            with db.engine().begin() as conn:
                v=write_version(conn,p,terms,user)
                conn.execute(insert(db.acceptances).values(id=db.uid(),agreement_id=v['id'],user_id=user['id'],role='freelancer',digest=v['digest'],created_at=db.now()))
                p['accepted']=['freelancer']
                db.save_project(p,conn)
                db.log(conn,'smart_proposal_sent',user['id'],pid,agreement_id=v['id'])
                conn.execute(insert(db.messages).values(id=db.uid(),project_id=pid,user_id=user['id'],body=f"📋 Smart Proposal sent to client: {data.section1.project_title} - ${data.section4.price_cents/100:.2f}. Delivery: {data.section4.delivery_date}. Ready for Client PayPal hold authorization.",created_at=db.now()))
        return room(pid)

    @router.post('/projects/{pid}/proposal/authorize-hold')
    async def authorize_hold(pid:str):
        user=auth.verify_required()
        auth.role(pid,('client',))
        async with project_lock(pid):
            p=get(pid)
            proposal=p.get('smart_proposal')
            assert_ok(proposal and proposal.get('status')=='sent_to_client','Proposal must be sent to client before hold authorization.')
            with db.engine().begin() as conn:
                v=agreement(conn,p)
                assert_ok(v,'Agreement version not found.')
                existing=db.one(conn,db.acceptances,(db.acceptances.c.agreement_id==v['id'])&(db.acceptances.c.user_id==user['id']))
                if not existing:
                    conn.execute(insert(db.acceptances).values(id=db.uid(),agreement_id=v['id'],user_id=user['id'],role='client',digest=v['digest'],created_at=db.now()))
                conn.execute(update(db.agreements).where(db.agreements.c.id==v['id']).values(status='accepted'))
                p['accepted']=['client','freelancer']

            m=p['milestones'][0]
            pay=agile.payment(p,m)
            if not pay:
                pay=agile.new_payment(p,m)

            if agile.core.MODE=='demo':
                auth_id=f"DEMO-AUTH-{pay['id']}"
                pay.update(status='authorized',authorization_id=auth_id,authorized_at=agile.stamp(p),expires_at=(agile.clock(p)+timedelta(days=29)).isoformat())
                m.update(status='active',work_allowed=True,funding_signal='green')
                p['status']='active'
                proposal['status']='hold_authorized'
                p['smart_proposal']=proposal
                agile.log(p,f"Client authorized PayPal escrow hold of ${m['amount_cents']/100:.2f}. Funds safely held; milestone is active.")
                with db.engine().begin() as conn:
                    db.save_project(p,conn)
                    db.log(conn,'paypal_hold_authorized',user['id'],pid,amount_cents=m['amount_cents'])
                    conn.execute(insert(db.messages).values(id=db.uid(),project_id=pid,user_id=user['id'],body=f"✅ Client authorized PayPal Hold for ${m['amount_cents']/100:.2f}. Funds are safely held in escrow. Work is officially underway!",created_at=db.now()))
            else:
                from . import paypal
                res=await paypal.milestone_order(pay)
                link=next((x['href'] for x in res.get('links',[]) if x['rel'] in ('approve','payer-action')),None)
                pay.update(order_id=res['id'],checkout_url=link,status='awaiting_payment')
                store.save(p)
                return {'checkout_url':link,'room':room(pid)}
        return room(pid)

    app.include_router(router)

async def apply_joint(action):
    """Only durable worker calls this, after verifying both authenticated project members voted."""
    pid=action['project_id'];body=action['payload'];p=store.get(pid)
    assert_ok(p and body['agreement_id']==p.get('agreement_id'),'Agreement changed; proposal must be reviewed again.')
    m=agile.milestone(p,body['milestone_id'])
    with db.engine().connect() as conn:
        voters={x['user_id'] for x in db.rows(conn,db.votes,db.votes.c.action_id==action['id'])}
        participants={x['user_id'] for x in db.rows(conn,db.members,db.members.c.project_id==pid)}
        assert_ok(len(participants)==2 and voters==participants,'Both current project participants must accept.')
    if action['id'] in p.get('applied_joint_actions',[]):return
    if action['kind']=='decision':
        assert_ok(body['decision_id'] not in p.get('applied_decisions',[]),'This decision has already been applied.')
        p.setdefault('applied_decisions',[]).append(body['decision_id'])
        category=body['category']
        if category in ('defect','included_revision'):
            assert_ok(m['status'] in ('active','revision_requested','in_review','disputed'),'Milestone is no longer open for correction.')
            if category=='included_revision':
                assert_ok(m['revisions_used']<p['contract']['allowed_revisions'],'Included revision allowance exhausted. Propose an explicit paid addition instead.')
                m['revisions_used']+=1
            m.update(status='revision_requested',review_deadline=None,dispute_open=False)
        elif category=='additional_scope':
            assert_ok(not any(x.get('change_id')==action['id'] for x in p['milestones']),'Addition already exists.')
            p['milestones'].append(agile.make_milestone({'title':'Addition: '+body['request'][:100],'scope':body['request'],'amount_cents':body['amount_cents'],'duration_days':14},change_id=action['id'],addition_consents=['client','freelancer'],items=[{'id':db.uid(),'title':body['request'][:200],'acceptance':body['request'],'amount_cents':body['amount_cents']}]))
            p['changes'].append({'id':action['id'],'request':body['request'],'verdict':'out_of_scope','amount_cents':body['amount_cents'],'status':'accepted','reasoning':body['reason']})
        else:
            m.update(status='disputed',dispute_open=True,review_deadline=None)
    elif action['kind']=='dispute_resolution':
        assert_ok(m['status']=='disputed','Dispute has already changed.')
        pay=agile.payment(p,m)
        m.update(dispute_open=False,status='active' if pay and pay['status']=='authorized' and agile.parse(pay['expires_at'])>agile.clock(p) else 'authorization_expired',review_deadline=None)
    elif action['kind']=='partial_items':
        amount=body['amount_cents']
        assert_ok(m['status'] in ('active','revision_requested','settling_partial','rolled_over','completed'),'Milestone cannot be settled in this state.')
        if amount==m['amount_cents']:
            if m['status']!='completed':m.update(status='in_review',review_deadline=agile.stamp(p));store.save(p);await agile.finish(p,m)
        else:
            m['progress']={'id':action['id'],'percent':amount*100//m['amount_cents'],'approved_amount_cents':amount,'evidence_url':body['evidence_url'],'completed_work':body['completed_work'],'remaining_work':body['remaining_work'],'accepted_at':db.now()}
            store.save(p);await agile.settle_partial(p,m)
    elif action['kind']=='reconciliation':
        from . import reconciliation
        await reconciliation.retry_agreed(p,m)
    elif action['kind']=='refund':
        from . import reconciliation
        await reconciliation.refund(p,body,action['id'])
    p=store.get(pid) if action['kind'] in ('partial_items','reconciliation','refund') else p
    p.setdefault('applied_joint_actions',[]).append(action['id'])
    agile.log(p,'Both participants approved '+action['kind']+': '+body['reason'])
    if action['kind']=='decision':
        with db.engine().begin() as conn:
            db.save_project(p,conn)
            conn.execute(update(db.decisions).where(db.decisions.c.id==body['decision_id']).values(status='reviewed'))
    else:agile.sync(p)
