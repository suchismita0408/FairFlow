"""Milestone engine. One API/worker process; all mutations use the shared lock.
External operations are journaled before network calls and reused on explicit retries.
"""
import copy
import hashlib
import json
import os
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException
from . import ai, paypal, store, auth, db
from .models import ChangeInput, Delivery
from .agile_models import (AgileDraft, AgileProject, Plan, MilestoneSpec, Consent,
                           VaultConsent, Progress, ProgressConsent, DemoClock, DemoBilling, Retry)

core = None
REVIEW_DAYS = 5
TERMINAL = {'completed', 'rolled_over'}
OPEN = {'active', 'in_review', 'revision_requested', 'disputed', 'payment_attention', 'settling_partial'}

def clock(p):
    return core.now() + timedelta(days=p.get('demo_clock_days', 0) if p['mode']=='demo' else 0)

def stamp(p): return clock(p).isoformat()
def parse(value): return datetime.fromisoformat(value.replace('Z', '+00:00'))
def log(p, message): p['events'].append({'at':stamp(p), 'message':message})
def require(condition, message):
    if not condition: raise HTTPException(409, message)

def get(pid):
    p = core.project(pid)
    require(p.get('engine')=='agile_v2', 'Use the legacy project API for this project.')
    return p

def milestone(p, mid):
    m = next((m for m in p['milestones'] if m['id']==mid), None)
    if not m: raise HTTPException(404, 'Milestone not found.')
    return m

def payment(p, m):
    return next((x for x in reversed(p['payments']) if x['milestone_id']==m['id']), None)

def make_milestone(spec, **extra):
    return {**spec, 'id':core.uid(), 'status':'queued', 'accepted':True, 'revisions_used':0,
            'deliveries':[], 'review_deadline':None, 'progress':None, **extra}

def sync(p):
    if p.get('emergency_paused'): p['status']='paused_emergency'
    elif any(m['status'] in ('payment_attention','billing_failed','authorization_expired','awaiting_payment') for m in p['milestones']): p['status']='billing_paused'
    elif any(m['status']=='disputed' for m in p['milestones']): p['status']='disputed'
    elif any(m['status']=='in_review' for m in p['milestones']): p['status']='in_review'
    elif any(m['status'] in ('active','revision_requested') for m in p['milestones']): p['status']='active'
    elif all(m['status'] in TERMINAL or m['status']=='proposed' for m in p['milestones']) and len(p['accepted'])==2: p['status']='completed'
    elif len(p['accepted'])<2: p['status']='draft'
    else: p['status']='ready'
    p['revisions_used'] = sum(m['revisions_used'] for m in p['milestones'])
    store.save(p)

def public(p):
    if p.get('engine') != 'agile_v2': return p
    result = copy.deepcopy(p)
    result.pop('_db_version',None)
    result.pop('_archived',None)
    vault = result['vault']
    for key in ('token_id', 'setup_id', 'setup_key', 'exchange_key'): vault.pop(key, None)
    result['server_time'] = stamp(p)
    for m in result['milestones']:
        pay = payment(p, m)
        remaining = (pay['amount_cents']-sum(c['amount_cents'] for c in pay.get('captures',[]))) if pay else 0
        authorized = bool(pay and pay['status']=='authorized' and parse(pay['expires_at']) > clock(p))
        can_work = authorized and m['status'] in ('active','revision_requested') and not p.get('emergency_paused') and not p.get('external_payment_issue')
        m['work_allowed'] = can_work
        m['funding_signal'] = ('amber' if clock(p)>parse(pay['authorized_at'])+timedelta(days=3) else 'green') if can_work else ('complete' if m['status'] in TERMINAL else 'red')
        m['authorized_remaining_cents'] = remaining if authorized else 0
        m['captured_cents'] = sum(c['amount_cents'] for x in p['payments'] if x['milestone_id']==m['id'] for c in x.get('captures',[]))
        m['authorization_expires_at'] = pay.get('expires_at') if pay else None
    return result

async def draft_plan(data):
    contract = await ai.extract(data.chat_history)
    if os.getenv('AI_MODE','demo')=='gemini':
        plan = await ai.structured(Plan, 'Split this accepted draft scope into the requested number of sequential milestones. Each duration is 14 to 21 days. Amounts are integer USD cents and MUST sum exactly to contract price_cents. Do not invent additional scope. State concrete acceptance criteria in scope. This remains a proposal.', {'contract':contract.model_dump(), 'count':data.milestone_count})
        require(len(plan.milestones)==data.milestone_count, 'AI returned the wrong milestone count. Retry planning.')
    else:
        require(contract.price_cents >= data.milestone_count, 'Price is too small for this many milestones.')
        part, remainder = divmod(contract.price_cents, data.milestone_count)
        labels = ['Plan & acceptance criteria', 'Build agreed deliverables', 'Review & handoff']
        plan = Plan(milestones=[MilestoneSpec(title=labels[i] if data.milestone_count==3 else f'Milestone {i+1}',
            scope=f'Phase {i+1} of {data.milestone_count} for the agreed scope: '+contract.task_description,
            amount_cents=part+(1 if i<remainder else 0), duration_days=21) for i in range(data.milestone_count)])
    if sum(m.amount_cents for m in plan.milestones) != contract.price_cents:
        raise HTTPException(502, 'AI milestone amounts do not match the contract. Review and retry.')
    return {'contract':contract.model_dump(), **plan.model_dump(), 'source_conversation':data.chat_history}

def new_payment(p, m):
    pay = {'id':core.uid(), 'milestone_id':m['id'], 'currency':p.get('currency','USD'), 'amount_cents':m['amount_cents'], 'status':'creating',
           'mode':p['mode'], 'order_key':core.uid(), 'authorize_key':core.uid(), 'captures':[], 'operations':[],
           'stage':'order', 'created_at':stamp(p)}
    p['payments'].append(pay)
    store.save(p)
    return pay

def attention(p, m, pay, message):
    m['status']='payment_attention'
    m['last_error']=message
    pay['last_error']=message
    log(p, 'RED LIGHT: '+m['title']+'. '+message)
    sync(p)

async def fund(p, m, confirmed=False):
    from .currencies import funding_available
    if db.secure() and p.get('agreement_id'):
        from .agreement_checks import agreement_issues
        with db.engine().connect() as conn:
            version=db.one(conn,db.agreements,db.agreements.c.id==p['agreement_id'])
        require(version is not None, 'Current agreement was not found.')
        issues=agreement_issues(version['payload'])
        require(not issues, 'Agreement needs clarification before funding: '+' '.join(issues))
        from .semantic_review import require_ready
        await require_ready(version['payload'])
    funding_available(p)
    require(not p.get('external_payment_issue'), 'An external payment issue must be reconciled before funding.')
    require(len(p['accepted'])==2, 'Both parties must accept the exact plan first.')
    require(m['accepted'], 'This addition requires separate client and freelancer acceptance.')
    require(not m.get('dispute_open'), 'Resolve the dispute before requesting new funding.')
    require(not p['emergency_paused'], 'Resume the project before authorizing new work.')
    require(not any(x['id']!=m['id'] and x['status'] in OPEN for x in p['milestones']), 'Finish or pause the current milestone first.')
    earlier = p['milestones'][:p['milestones'].index(m)]
    require(all(x['status'] in TERMINAL or x['status']=='proposed' for x in earlier), 'Milestones must be funded in sequence.')
    require(m['status'] in ('queued','awaiting_payment','billing_failed','payment_attention','paused_emergency','authorization_expired'), 'Milestone cannot be funded in this state.')
    pay = payment(p, m)
    if pay and pay['status'] in ('voided','expired','failed'): pay=None
    if not pay: pay=new_payment(p,m)
    require(pay['status'] not in ('authorized','captured','partially_captured'), 'Existing funds must be resolved before another authorization.')
    token = p['vault'].get('token_id') if p['vault']['status']=='active' else None
    if token:
        require(p['vault'].get('plan_hash')==p['plan_hash'], 'Saved-method consent belongs to an older plan. Save the method again for the current agreement.')
    try:
        if pay['stage']=='order':
            if p['mode']=='demo':
                if token and p['vault'].get('fail_next'):
                    p['vault']['fail_next']=False
                    pay['status']='failed'
                    m['status']='billing_failed'
                    m['last_error']='Demo payment method declined. Update billing and explicitly retry; do not continue work.'
                    log(p,'RED LIGHT: demo funding declined for '+m['title'])
                    sync(p)
                    return
                result={'id':'DEMO-'+pay['id'], 'status':'APPROVED' if token else 'PAYER_ACTION_REQUIRED', 'links':[]}
            else: result=await paypal.milestone_order(pay, token)
            pay['order_id']=result['id']
            pay['checkout_url']=next((x['href'] for x in result.get('links',[]) if x['rel'] in ('approve','payer-action')), None)
            pay['stage']='authorize'
            pay['status']='awaiting_approval'
            pay['requires_approval']=not token or result.get('status')=='PAYER_ACTION_REQUIRED'
            store.save(p)
        if pay.get('requires_approval') and not confirmed:
            m['status']='awaiting_payment'
            sync(p)
            return
        if p['mode']=='demo':
            auth={'id':'DEMO-AUTH-'+pay['id'], 'expiration_time':(clock(p)+timedelta(days=29)).isoformat()}
        else: auth=await paypal.milestone_authorize(pay)
        pay.update(status='authorized', authorization_id=auth['id'], authorized_at=stamp(p),
            expires_at=auth.get('expiration_time') or (clock(p)+timedelta(days=29)).isoformat(), stage='funded')
        m.update(status='active', started_at=stamp(p), due_at=(clock(p)+timedelta(days=m['duration_days'])).isoformat(), review_deadline=None)
        if parse(pay['expires_at']) < clock(p)+timedelta(days=m['duration_days']+REVIEW_DAYS+1):
            attention(p,m,pay,'Authorization validity is too short for the work and review window; pause and reconcile.')
            return
        m.pop('last_error',None)
        log(p, f"Milestone authorized: {m['title']}. {m['duration_days']} work days + 5 review days; capture remains conditional.")
        sync(p)
    except HTTPException as exc:
        attention(p,m,pay,str(exc.detail))
        # Persist failed/uncertain state; do not silently create a new order or retry money movement.

async def advance(p):
    if p['emergency_paused'] or any(m['status'] in OPEN for m in p['milestones']): return
    next_m = next((m for m in p['milestones'] if m['status'] not in TERMINAL and m['accepted']),None)
    if next_m and next_m['status']=='queued' and p['vault']['status']=='active': await fund(p,next_m)
    else: sync(p)

def operation(p, pay, kind, amount=0, final=True):
    op=next((x for x in pay['operations'] if x['kind']==kind),None)
    if not op:
        op={'key':core.uid(),'kind':kind,'amount_cents':amount,'final_capture':final,'status':'pending'}
        pay['operations'].append(op)
        store.save(p)
    require(op['amount_cents']==amount and op['final_capture']==final, 'Saved payment operation differs. Reconcile before retry.')
    return op

async def capture(p,m,amount,kind,final):
    pay=payment(p,m)
    require(pay is not None, 'Missing payment.')
    op=operation(p,pay,kind,amount,final)
    if op['status']=='done': return
    require(pay['status'] in ('authorized','partially_captured'), 'Payment is not authorized.')
    require(parse(pay['expires_at'])>clock(p), 'Authorization expired. No capture attempted.')
    require(0<amount<=pay['amount_cents']-sum(x['amount_cents'] for x in pay['captures']), 'Capture exceeds the unpaid authorization balance.')
    try:
        cid='DEMO-CAP-'+op['key'] if p['mode']=='demo' else await paypal.milestone_capture(pay,op)
        op.update(status='done',provider_id=cid)
        pay['captures'].append({'id':cid,'amount_cents':amount,'at':stamp(p),'operation_key':op['key']})
        pay['status']='captured' if final else 'partially_captured'
        log(p,f"{'Final' if final else 'Partial'} capture confirmed: {amount} hundredths of {p.get('currency','USD')} for {m['title']}.")
        store.save(p)
    except HTTPException as exc:
        attention(p,m,pay,str(exc.detail))
        raise

async def void(p,m,kind='void'):
    pay=payment(p,m)
    if not pay or pay['status'] in ('voided','expired','failed','captured'): return
    require(pay['status'] in ('authorized','partially_captured'), 'An uncertain or unapproved payment must be reconciled before voiding.')
    op=operation(p,pay,kind)
    if op['status']=='done': return
    try:
        if p['mode']!='demo': await paypal.milestone_void(pay,op)
        op['status']='done'
        pay['status']='voided'
        log(p,'Remaining authorization voided for '+m['title']+'. Issuer controls when available balance updates.')
        store.save(p)
    except HTTPException as exc:
        attention(p,m,pay,str(exc.detail))
        raise

async def finish(p,m,automatic=False):
    require(not p.get('external_payment_issue'), 'Provider reconciliation has blocked settlement.')
    if m['status']=='completed': return
    require(not p['emergency_paused'], 'Project is paused.')
    require(m['status']=='in_review', 'Only an undisputed delivered milestone can be captured.')
    require(m.get('settlement') in (None,'full'), 'Partial settlement already started.')
    m['settlement']='full'
    store.save(p)
    await capture(p,m,m['amount_cents'],'full_capture',True)
    m.update(status='completed',review_deadline=None,completed_at=stamp(p))
    log(p, 'Milestone completed after '+('five-day review deadline' if automatic else 'client approval'))
    sync(p)
    await advance(p)

async def settle_partial(p,m):
    if m['status']=='rolled_over': return
    require(not p['emergency_paused'], 'Project is paused.')
    require(m['status'] in ('active','revision_requested','settling_partial'), 'Partial settlement is unavailable in this state.')
    progress=m.get('progress')
    require(progress and progress.get('accepted_at'), 'Client-approved progress and remainder are required.')
    require(m.get('settlement') in (None,'partial'), 'Another settlement is already in progress.')
    pay=payment(p,m)
    amount=progress.get('approved_amount_cents',m['amount_cents']*progress['percent']//100)
    require(0<amount<m['amount_cents'], 'Partial amount rounds to zero or full price.')
    m.update(settlement='partial',status='settling_partial')
    store.save(p)
    await capture(p,m,amount,'partial_capture',False)
    await void(p,m,'rollover_void')
    remainder=m['amount_cents']-amount
    # Merge only into an unfunded, already-agreed next milestone; never duplicate captured cents.
    following=p['milestones'][p['milestones'].index(m)+1:]
    target=next((x for x in following if x['status']=='queued' and x['accepted'] and payment(p,x) is None),None)
    if target:
        target['amount_cents']+=remainder
        target['scope']+='\nApproved carry-over: '+progress['remaining_work']
        target.setdefault('carryovers',[]).append({'from':m['id'],'amount_cents':remainder})
    else:
        target=make_milestone({'title':'Carry-over: '+m['title'][:100], 'scope':progress['remaining_work'],
            'amount_cents':remainder,'duration_days':14},carryovers=[{'from':m['id'],'amount_cents':remainder}])
        p['milestones'].insert(p['milestones'].index(m)+1,target)
    if m.get('items'):
        target.setdefault('items',[]).append({'id':db.uid(),'title':'Approved remaining work','acceptance':progress['remaining_work'],'amount_cents':remainder})
    m.update(status='rolled_over',review_deadline=None,rolled_into=target['id'], settled_cents=amount)
    log(p,f"Approved remainder {remainder} cents moved to {target['title']}. Prior captured work is excluded.")
    sync(p)
    await advance(p)

async def scan(p):
    if p['emergency_paused'] or p.get('external_payment_issue'): return
    for m in p['milestones']:
        if m['status'] not in ('active','revision_requested','in_review','disputed','settling_partial'): continue
        pay=payment(p,m)
        # Recover the local half of a confirmed financial operation after a process restart.
        # Never replay an uncertain provider call automatically.
        if pay and m.get('settlement')=='full' and m['status']=='in_review' and any(o['kind']=='full_capture' and o['status']=='done' for o in pay['operations']):
            await finish(p,m)
            continue
        if pay and m.get('settlement')=='partial' and m['status']=='settling_partial' and any(o['kind']=='partial_capture' and o['status']=='done' for o in pay['operations']):
            if parse(pay['expires_at'])<=clock(p) and pay['status']=='partially_captured':
                pay['status']='expired'
                store.save(p)
            if pay['status'] in ('voided','expired'):
                await settle_partial(p,m)
                continue
        if not pay or pay['status'] not in ('authorized','partially_captured'): continue
        current=clock(p)
        if current>=parse(pay['expires_at']):
            pay['status']='expired'
            m.update(status='disputed' if m.get('dispute_open') else 'authorization_expired',review_deadline=None,last_error='Authorization expired. Work is paused; resolve any dispute and reauthorize.')
            log(p,'RED LIGHT: authorization expired for '+m['title'])
            sync(p)
            continue
        if m['status']=='disputed': continue
        try:
            if m['status']=='in_review' and parse(m['review_deadline'])<=current:
                await finish(p,m,True)
            elif current>=parse(pay['authorized_at'])+timedelta(days=28) or current>=parse(pay['expires_at'])-timedelta(days=1):
                if m['status'] in ('active','revision_requested','settling_partial') and (m.get('progress') or {}).get('accepted_at'):
                    await settle_partial(p,m)
                elif m['status'] in ('active','revision_requested'):
                    await void(p,m,'expiry_void')
                    m.update(status='authorization_expired',last_error='Hold safely voided: no approved progress settlement before expiry. Reauthorize before work resumes.')
                    log(p,'No unapproved pro-rata charge was made. Work paused before authorization expiry.')
                    sync(p)
            elif m['status']=='settling_partial':
                await settle_partial(p,m)
        except HTTPException:
            # Capture/void failures persist payment_attention and require an operator reconciliation.
            pass


def mount(app, runtime):
    global core
    core=runtime
    router=APIRouter(prefix='/api/agile',tags=['Agile milestone engine'])

    @router.post('/draft')
    async def draft(data:AgileDraft): return await draft_plan(data)

    @router.post('/projects',status_code=201)
    async def create(data:AgileProject):
        async with core.lock:
            payload=data.model_dump()
            plan_hash=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
            p={'id':core.uid(),**payload,'engine':'agile_v2','mode':core.MODE,'status':'draft',
               'plan_hash':plan_hash,'original_plan':payload['milestones'],'accepted':[],'consents':[],
               'milestones':[make_milestone(m) for m in payload['milestones']], 'payments':[], 'events':[],
               'changes':[],'deliveries':[],'revisions_used':0,'review_deadline':None,'emergency_paused':False,
               'vault':{'status':'not_connected'},'created_at':core.timestamp(),'demo_clock_days':0}
            log(p,'Agile plan drafted: 14–21 day milestones, five-day reviews, explicit future-payment consent.')
            store.save(p)
            return public(p)

    @router.post('/projects/{pid}/accept')
    async def accept(pid:str,data:Consent):
        async with core.project_lock(pid):
            p=get(pid)
            require(data.plan_hash==p['plan_hash'],'The plan changed. Review the current plan before accepting.')
            if data.role not in p['accepted']:
                p['accepted'].append(data.role)
                p['consents'].append({**data.model_dump(),'at':stamp(p),'method':'operator_recorded'})
                log(p,data.role.title()+' accepted exact milestone plan '+p['plan_hash'][:12])
            sync(p)
            return public(p)

    @router.post('/projects/{pid}/vault/setup')
    async def setup(pid:str,data:VaultConsent):
        async with core.project_lock(pid):
            p=get(pid)
            require(data.plan_hash==p['plan_hash'] and len(p['accepted'])==2,'Accept the current plan before saving a payment method.')
            if p['vault']['status']=='active': return public(p)
            if p['mode']=='sandbox' and os.getenv('PAYPAL_VAULT_ENABLED','false').lower()!='true':
                raise HTTPException(503,'Enable PAYPAL_VAULT_ENABLED only after Sandbox Vault eligibility is configured. Manual checkout remains available.')
            vault=p['vault']
            if vault['status'] not in ('setup_pending','approval_required'):
                vault.update(status='setup_pending',setup_key=core.uid(),exchange_key=core.uid(),consented_at=stamp(p),plan_hash=p['plan_hash'])
                store.save(p)
            if vault['status']=='setup_pending':
                result={'id':'DEMO-SETUP-'+p['id'],'links':[]} if p['mode']=='demo' else await paypal.vault_setup(p['id'],vault['setup_key'])
                vault.update(status='approval_required',setup_id=result['id'],approval_url=next((x['href'] for x in result.get('links',[]) if x['rel']=='approve'),None))
                store.save(p)
            log(p,'Saving payment method requested with explicit consent for the agreed plan. Additions require separate acceptance.')
            sync(p)
            return public(p)

    @router.post('/projects/{pid}/vault/confirm')
    async def confirm_vault(pid:str):
        async with core.project_lock(pid):
            p=get(pid); v=p['vault']
            require(len(p['accepted'])==2 and v.get('plan_hash')==p['plan_hash'],'Accept the current plan and start its save-payment-method flow first.')
            if v['status']=='active': return public(p)
            require(v['status']=='approval_required','Start the save-payment-method flow first.')
            token='DEMO-TOKEN-'+core.uid() if p['mode']=='demo' else await paypal.vault_exchange(v['setup_id'],v['exchange_key'])
            v.update(token_id=token,status='active',approved_at=stamp(p),fail_next=False)
            log(p,'Saved payment method confirmed. Future authorizations are restricted to accepted milestones.')
            sync(p)
            return public(p)

    @router.post('/projects/{pid}/vault/revoke')
    async def revoke(pid:str):
        async with core.project_lock(pid):
            p=get(pid)
            p['vault'].update(status='revoked',revoked_at=stamp(p))
            log(p,'Consent to future saved-method authorizations revoked. Existing authorized obligations remain unchanged.')
            sync(p)
            return public(p)

    @router.post('/projects/{pid}/milestones/{mid}/fund')
    async def funding(pid:str,mid:str):
        async with core.project_lock(pid):
            p=get(pid); m=milestone(p,mid)
            require(m['status']!='payment_attention','Reconcile the saved operation and use its retry action.')
            if m['status']=='active': return public(p)
            await fund(p,m)
            return public(p)

    @router.post('/projects/{pid}/milestones/{mid}/confirm-payment')
    async def confirm_payment(pid:str,mid:str):
        async with core.project_lock(pid):
            p=get(pid); m=milestone(p,mid)
            if m['status']=='active': return public(p)
            require(m['status']=='awaiting_payment','No buyer approval is pending.')
            await fund(p,m,True)
            return public(p)

    @router.post('/projects/{pid}/milestones/{mid}/deliver')
    async def deliver(pid:str,mid:str,data:Delivery):
        async with core.project_lock(pid):
            p=get(pid); m=milestone(p,mid); pay=payment(p,m)
            require(m['status'] in ('active','revision_requested') and not p['emergency_paused'] and not p.get('external_payment_issue'),'Only funded active work can be delivered.')
            require(pay and pay['status']=='authorized','Confirmed authorization is required.')
            deadline=clock(p)+timedelta(days=REVIEW_DAYS)
            require(deadline<=parse(pay['expires_at'])-timedelta(days=1),'Insufficient hold validity for a full five-day review plus safety buffer. Use approved progress rollover or pause and reauthorize.')
            m['deliveries'].append({**data.model_dump(mode='json'),'at':stamp(p)})
            m.update(status='in_review',review_deadline=deadline.isoformat())
            log(p,'Delivery submitted for '+m['title']+'. Five-day review started.')
            sync(p)
            return public(p)

    @router.post('/projects/{pid}/milestones/{mid}/approve')
    async def approve(pid:str,mid:str):
        async with core.project_lock(pid):
            p=get(pid); await finish(p,milestone(p,mid)); return public(p)

    @router.post('/projects/{pid}/milestones/{mid}/progress')
    async def progress(pid:str,mid:str,data:Progress):
        async with core.project_lock(pid):
            p=get(pid); m=milestone(p,mid)
            require(m['status'] in ('active','revision_requested') and not m.get('settlement'),'Progress can only be recorded before settlement on active work.')
            require(not p['emergency_paused'],'Project is paused.')
            require(payment(p,m)['status']=='authorized' and parse(payment(p,m)['expires_at'])>clock(p),'Authorization must be valid.')
            m['progress']={**data.model_dump(),'id':core.uid(),'proposed_at':stamp(p),'accepted_at':None}
            log(p,'Progress proposed for client review: '+str(data.percent)+'%. No partial capture is authorized yet.')
            sync(p)
            return public(p)

    @router.post('/projects/{pid}/milestones/{mid}/progress/accept')
    async def progress_accept(pid:str,mid:str,data:ProgressConsent):
        async with core.project_lock(pid):
            p=get(pid); m=milestone(p,mid); report=m.get('progress')
            require(m['status'] in ('active','revision_requested') and report and report['id']==data.progress_id,'Review the latest progress report on an active milestone.')
            require(not p['emergency_paused'] and not m.get('settlement'),'Settlement is paused or already started.')
            report['accepted_at']=stamp(p)
            log(p,'Client accepted progress evidence, proportional settlement, and unpaid-scope carry-over (operator recorded).')
            sync(p)
            return public(p)

    @router.post('/projects/{pid}/milestones/{mid}/partial-settle')
    async def partial(pid:str,mid:str):
        async with core.project_lock(pid):
            p=get(pid); await settle_partial(p,milestone(p,mid)); return public(p)

    @router.post('/projects/{pid}/pause')
    async def pause(pid:str,data:ChangeInput):
        async with core.project_lock(pid):
            p=get(pid)
            require(not any(m.get('settlement') and m['status'] not in TERMINAL for m in p['milestones']), 'A settlement already started. Reconcile it before emergency pause.')
            p['emergency_paused']=True
            p['pause_reason']=data.request
            log(p,'Emergency pause: '+data.request)
            sync(p)  # Stop new work and scheduler activity before making void requests.
            for m in p['milestones']:
                pay=payment(p,m)
                if pay and pay['status'] in ('authorized','partially_captured'):
                    require(not m.get('settlement'),'A settlement already started. Reconcile it before emergency voiding.')
                    await void(p,m,'emergency_void')
                    m.update(status='paused_emergency',review_deadline=None,progress=None)
                elif pay and pay['status']=='awaiting_approval':
                    # Approval alone never authorizes: the server refuses funding while paused.
                    m.update(status='paused_emergency',review_deadline=None)
            sync(p)
            return public(p)

    @router.post('/projects/{pid}/resume')
    async def resume(pid:str):
        async with core.project_lock(pid):
            p=get(pid)
            require(p['emergency_paused'],'Project is not paused.')
            require(not any(m['status']=='payment_attention' for m in p['milestones']),'Reconcile unfinished payment operations before resuming.')
            p['emergency_paused']=False
            for m in p['milestones']:
                if m['status']=='paused_emergency': m['status']='disputed' if m.get('dispute_open') else 'queued'
            log(p,'Emergency pause ended. Work stays blocked until a new authorization succeeds.')
            sync(p); await advance(p)
            return public(p)

    @router.post('/projects/{pid}/milestones/{mid}/changes')
    async def changes(pid:str,mid:str,data:ChangeInput):
        async with core.project_lock(pid):
            p=get(pid); m=milestone(p,mid)
            require(m['status'] in ('active','revision_requested','in_review') and not p['emergency_paused'],'Changes require an active milestone.')
            if m['review_deadline']: require(clock(p)<parse(m['review_deadline']),'Review deadline has passed.')
            decision=await ai.classify({**p['contract'],'current_milestone':m['scope'],
                'accepted_additions':[x['scope'] for x in p['milestones'] if x.get('change_id') and x['accepted']]},data.request)
            if decision.confidence<.75: decision.verdict='needs_review'
            if decision.verdict=='in_scope' and m['revisions_used']>=p['contract']['allowed_revisions']:
                decision.verdict='out_of_scope';decision.estimated_hours=1
                decision.reasoning='This milestone has used its included revision allowance. An additional revision requires acceptance of a new quote.'
            amount=max(2000,decision.estimated_hours*p['contract']['hourly_rate_cents']) if decision.verdict=='out_of_scope' else 0
            c={'id':core.uid(),'milestone_id':mid,'request':data.request,**decision.model_dump(),'amount_cents':amount,'status':'pending'}
            p['changes'].append(c)
            if decision.verdict=='in_scope':
                m.update(status='revision_requested',review_deadline=None,progress=None)
                m['revisions_used']+=1;c['status']='accepted'
            elif decision.verdict=='needs_review': m.update(status='disputed',review_deadline=None,dispute_open=True)
            else:
                addition=make_milestone({'title':'Addition: '+data.request[:100], 'scope':data.request+'\nAcceptance: deliver the separately agreed addition.', 'amount_cents':amount, 'duration_days':14},status='proposed',accepted=False,change_id=c['id'],addition_consents=[])
                p['milestones'].append(addition);c['addition_id']=addition['id']
            log(p,'Revision firewall: '+decision.verdict+'. '+decision.reasoning)
            sync(p)
            return public(p)

    @router.post('/projects/{pid}/milestones/{mid}/accept-addition')
    async def accept_addition(pid:str,mid:str,data:Consent):
        async with core.project_lock(pid):
            p=get(pid); m=milestone(p,mid)
            auth.validate_role(pid,data.role)
            require(m.get('change_id') and data.plan_hash==p['plan_hash'],'Review the addition against the accepted baseline plan.')
            require(m['status'] in ('proposed','queued'),'Addition already started.')
            if data.role not in m['addition_consents']: m['addition_consents'].append(data.role)
            if len(m['addition_consents'])==2:
                m.update(accepted=True,status='queued')
                next(c for c in p['changes'] if c['id']==m['change_id'])['status']='accepted'
                log(p,'Both parties accepted additional scope and price: '+m['title'])
            sync(p)
            await advance(p)
            return public(p)

    @router.post('/projects/{pid}/milestones/{mid}/dispute')
    async def dispute(pid:str,mid:str,data:ChangeInput):
        async with core.project_lock(pid):
            p=get(pid);m=milestone(p,mid)
            require(m['status'] in ('active','revision_requested','in_review') and not m.get('settlement'),'This milestone cannot be disputed in its current state.')
            m.update(status='disputed',review_deadline=None,progress=None,dispute_open=True)
            log(p,'Dispute blocks capture: '+data.request)
            sync(p)
            return public(p)

    @router.post('/projects/{pid}/milestones/{mid}/resolve')
    async def resolve(pid:str,mid:str,data:ChangeInput):
        async with core.project_lock(pid):
            p=get(pid); m=milestone(p,mid);pay=payment(p,m)
            require(m['status']=='disputed','No dispute to resolve.')
            m['dispute_open']=False
            m['status']='active' if pay['status']=='authorized' and parse(pay['expires_at'])>clock(p) else 'authorization_expired'
            log(p,'Mutual dispute resolution recorded: '+data.request+'. Fresh delivery required.')
            sync(p)
            return public(p)

    @router.post('/projects/{pid}/milestones/{mid}/retry')
    async def retry(pid:str,mid:str,data:Retry):
        async with core.project_lock(pid):
            p=get(pid);m=milestone(p,mid);pay=payment(p,m)
            require(m['status']=='payment_attention','Only uncertain payment operations require this retry.')
            log(p,'Operator confirmed provider reconciliation; retrying saved operation IDs.')
            if m.get('settlement')=='partial':
                m['status']='settling_partial';await settle_partial(p,m)
            elif m.get('settlement')=='full':
                m['status']='in_review';await finish(p,m)
            elif p['emergency_paused'] and pay['status'] in ('authorized','partially_captured'):
                await void(p,m,'emergency_void');m.update(status='paused_emergency',review_deadline=None,progress=None);sync(p)
            else:
                await fund(p,m,True)
            return public(p)

    @router.post('/projects/{pid}/demo/advance')
    async def demo_advance(pid:str,data:DemoClock):
        if core.MODE!='demo': raise HTTPException(403,'Demo clock is disabled in Sandbox.')
        async with core.project_lock(pid):
            p=get(pid);p['demo_clock_days']+=data.days
            log(p,'Demo clock advanced '+str(data.days)+' days. No real financial time changed.')
            store.save(p);await scan(p)
            return public(get(pid))

    @router.post('/projects/{pid}/demo/billing')
    async def demo_billing(pid:str,data:DemoBilling):
        if core.MODE!='demo': raise HTTPException(403,'Simulated billing failures are disabled in Sandbox.')
        async with core.project_lock(pid):
            p=get(pid);p['vault']['fail_next']=data.fail_next
            log(p,'Demo: '+('next saved-method authorization will decline' if data.fail_next else 'payment method updated; retry funding explicitly'))
            sync(p)
            return public(p)

    app.include_router(router)
