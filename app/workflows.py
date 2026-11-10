"""Consent-based delivery/default/settlement workflows for the signed portals.

Proposals never change money or suspend an existing review. Final acceptance is
serialized with payment transitions. Unknown provider effects require reads,
not a new operation ID. Signed agreement snapshots are never edited.
"""
import base64
import copy
import hashlib
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select
from . import db, auth, negotiation as n, paypal
from .agreement_schema import at
from .agreement_validation import digest, safe_url
from .workflow_policy import POLICY_VERSION, POLICY_TEXT

UTC=timezone.utc
OPEN={'proposed','accepted_by_one'}
CASE_CATEGORIES={'quality','defect','missing_work','access','scope','delay','partial_work','ip','confidentiality','payment','other'}

def clock():return datetime.now(UTC)

def reviewer(user):
    allowed={x.strip().casefold() for x in os.getenv('FAIRFLOW_SUPPORT_EMAILS','').split(',') if x.strip()}
    return user['email'].casefold() in allowed and auth.mailbox_verified(user)

def support_available():return bool(os.getenv('FAIRFLOW_SUPPORT_EMAILS','').strip())

def enabled(p):return p.get('workflow_policy',{}).get('version')==POLICY_VERSION

def requests(p):return p.setdefault('workflow_requests',[])

def find(p,rid):
    r=next((x for x in requests(p) if x['id']==rid),None)
    n.require(r,'Request not found.',404)
    return r

def proposal_digest(r):
    return digest({k:v for k,v in r.items() if k not in ('digest','votes','status','operations','applied_at')})

def effective_terms(p,v):
    """Operational addenda; never persist this over the signed version."""
    if v.get('_operational'):return v
    result=copy.deepcopy(v);result['_operational']=True
    a=result['payload']
    for r in p.get('workflow_requests',[]):
        if r['status']!='applied':continue
        if r['kind']=='early_start' and r['agreement_id']==v['id']:
            from .early_start import apply
            apply(a,r)
        if r['kind'] in ('extension','dependency') and (r['agreement_id']==v['id'] or r['milestone_id'].startswith('CO-')):
            for m in at(a,'timeline.milestones',[]):
                if m['id']==r['milestone_id']:m['due_date']=r['due_date']
            a['timeline']['deadline']=max(a['timeline']['deadline'],r.get('completion_date',r['due_date']))
        if r['kind']=='change':
            mid='CO-'+r['id'];did='D-'+r['id']
            # Each paid addition is separately and exactly consented to.
            m={'id':mid,'title':r['title'],'due_date':r['due_date'],'amount_minor':r['amount_minor'],
               'currency':r.get('currency',at(a,'payment.currency')),'review_days':r['review_days'],
               'auto_accept':True,'auto_accept_days':r['review_days'],'deliverable_ids':[did]}
            a['timeline']['milestones'].append(m)
            a['scope']['deliverables'].append({'id':did,'name':r['title'],'description':r['reason'],
                'specification':r['reason'],'acceptance_criteria':r['criteria'],'milestone_id':mid,'quantity':1,'revision_rounds':0})
            a['payment']['total_minor']+=r['amount_minor']
            a['timeline']['deadline']=max(a['timeline']['deadline'],r.get('completion_date',r['due_date']))
    return result

def milestone(v,mid):
    m=next((m for m in at(v['payload'],'timeline.milestones',[]) if m['id']==mid),None)
    n.require(m,'Milestone not found.',404)
    return m

def cutoff(p,v,m):
    # End of the date in the agreed timezone (exclusive), with signed grace.
    zone=ZoneInfo(at(v['payload'],'timeline.time_zone','UTC'))
    return datetime.combine(date.fromisoformat(m['due_date'])+timedelta(days=1),time(),zone).astimezone(UTC)

def grace_end(p,v,m):return cutoff(p,v,m)+timedelta(days=max(0,at(v['payload'],'timeline.grace_days',0) or 0))

def snapshot(conn,p,mid=''):
    from . import assurance as a
    rows=[a.payment(conn,p['id'],mid)] if mid else db.rows(conn,db.milestone_payments,db.milestone_payments.c.project_id==p['id'])
    values=[]
    for row in rows:
        if not row:continue
        payout=db.one(conn,db.milestone_payouts,db.milestone_payouts.c.payment_id==row['id'])
        values.append([row['id'],row['milestone_id'],row['status'],
            {k:row['payload'].get(k) for k in ('authorization_id','capture_id','delivery_url','delivered_at','review_deadline','objection','settlement_amount_minor')},
            [payout['id'],payout['status']] if payout else None])
    return digest(sorted(values,key=lambda x:x[0]))

def event(conn,p,kind,body,user=None,**data):
    n.system(conn,p,kind,body,user['id'] if user else None,**data)
    for role in ('client','freelancer'):
        n.notify(conn,p,kind,body+' Open project: '+os.getenv('APP_BASE_URL','http://127.0.0.1:8000').rstrip('/')+'/'+role+'/projects/'+p['id']+'?tab=Resolution',role)

def closed(p,mid):return bool(p.get('work_closed') or mid in p.get('closed_milestones',[]))

def payment_case(conn,pid,mid,reason,user):
    """Open one auditable support case for an adverse provider event."""
    p,revision=n.project(conn,pid,user,True)
    existing=next((c for c in p.get('support_cases',[]) if c['category']=='payment' and c['milestone_id']==mid and c['status']!='closed'),None)
    if existing:
        if not any(m['body']==reason for m in existing['messages']):existing['messages'].append({'by':None,'role':'system','body':reason,'at':db.now()})
        else:return
    else:
        p.setdefault('support_cases',[]).append({'id':db.uid(),'milestone_id':mid,'category':'payment','reason':reason,'created_by':None,
            'created_at':db.now(),'response_due_at':db.now(),'status':'awaiting_support' if support_available() else 'support_unconfigured',
            'messages':[{'by':None,'role':'system','body':reason,'at':db.now()}]})
    event(conn,p,'payment_case',reason);n.write(conn,p,revision)

def evidence(conn,p,user,mid,title,url,description,files=(),checklist=()):
    n.require(not url or safe_url(url),'Use an HTTP(S) evidence link.',422)
    n.require(len(files)<=5,'Attach at most five files.',422)
    artifacts=[]
    for fid in files:
        row=db.one(conn,db.delivery_files,(db.delivery_files.c.id==fid)&(db.delivery_files.c.project_id==p['id']))
        n.require(row and row['user_id']==user['id'],'Evidence file not found for this author.',404)
        artifacts.append({'id':fid,'filename':row['filename'],'sha256':row['digest']})
    body={'url':url,'description':description,'files':artifacts,'checklist':list(checklist)}
    row=dict(id=db.uid(),project_id=p['id'],milestone_id=mid,user_id=user['id'],title=title,
        url=url or '',description=__import__('json').dumps(body),digest=digest(body),created_at=db.now())
    conn.execute(insert(db.evidence).values(**row))
    return row

def record_delivery(conn,p,v,row,user,data,now=None):
    now=now or clock();m=milestone(v,row['milestone_id'])
    n.require(not closed(p,m['id']) or row['payload'].get('delivery_url'),'Remaining work is closed.')
    required=set(m.get('deliverable_ids',[]))
    if enabled(p):
        n.require(set(data.checklist)==required,'Confirm every signed deliverable in the submission checklist.',422)
        n.require(data.file_ids or len(data.note.strip())>=30,'Attach work or describe the completed deliverables in at least 30 characters.',422)
    e=evidence(conn,p,user,m['id'],'Milestone delivery',data.url,data.note,data.file_ids,data.checklist)
    prior=row['payload'].get('delivery_history',[])
    history=prior+[{'evidence_id':e['id'],'sha256':e['digest'],'submitted_at':e['created_at']}]
    first=not row['payload'].get('delivery_url')
    late=enabled(p) and first and now>=grace_end(p,v,m)
    return {'delivery_history':history,'delivery_evidence_id':e['id'],'late':now>=cutoff(p,v,m),'late_pending':late}

class Input(BaseModel):model_config=ConfigDict(extra='forbid')
class Proposal(Input):
    revision:int=Field(ge=1)
    kind:Literal['early_start','funding','protections','cancel','settlement','extension','dependency','change','reopen']
    milestone_id:str=Field(default='',max_length=64)
    title:str=Field(default='',max_length=150)
    reason:str=Field(min_length=10,max_length=3000)
    amount_minor:int=Field(default=0,ge=0,le=10**12,strict=True)
    due_date:str=Field(default='',max_length=10)
    handover_url:str=Field(default='',max_length=2000)
    criteria:list[str]=Field(default_factory=list,max_length=20)
    review_days:int=Field(default=3,ge=1,le=5)
    outcome:Literal['close','partial','refund']='close'
class Vote(Input):
    revision:int=Field(ge=1)
    digest:str=Field(min_length=64,max_length=64)
    decision:Literal['accept','decline','withdraw']
    typed_name:str=Field(default='',max_length=100)
class FileInput(Input):
    filename:str=Field(min_length=1,max_length=180)
    content_base64:str=Field(min_length=1,max_length=7_000_000)
class CaseInput(Input):
    revision:int=Field(ge=1)
    milestone_id:str=Field(max_length=64)
    category:Literal['quality','defect','missing_work','access','scope','delay','partial_work','ip','confidentiality','payment','other']
    reason:str=Field(min_length=10,max_length=3000)
    evidence_url:str=Field(default='',max_length=2000)
class CaseReply(Input):
    revision:int=Field(ge=1)
    body:str=Field(min_length=10,max_length=3000)
    evidence_url:str=Field(default='',max_length=2000)
    escalate:bool=False
    close:bool=False
    file_ids:list[str]=Field(default_factory=list,max_length=5)
class SupportReview(Input):
    revision:int=Field(ge=1)
    stage:Literal['mediation','support_review','recommendation']
    body:str=Field(min_length=20,max_length=4000)
class LateDecision(Input):accept:bool
class RevisionNumber(Input):revision:int=Field(ge=1)
class ReconcileInput(Input):refund_id:str=Field(default='',max_length=100,pattern=r'^[A-Za-z0-9]*$')
class RecoverOrder(Input):order_id:str=Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9-]+$')
class Handover(Input):
    revision:int=Field(ge=1)
    milestone_id:str=Field(min_length=1,max_length=64)
    url:str=Field(default='',max_length=2000)
    note:str=Field(min_length=20,max_length=3000)
    file_ids:list[str]=Field(default_factory=list,max_length=5)

def request_valid(conn,p,v,r):
    from . import assurance as a
    kind=r['kind'];mid=r['milestone_id']
    if kind=='early_start':
        from . import early_start
        n.require(not any(x['status'] in OPEN and x['id']!=r['id'] for x in requests(p)),'Finish other schedule proposals before starting early.')
        plan=early_start.plan(conn,p,v,mid,start=r['schedule']['start_date'],request_id=r['id'])
        n.require(plan==r['schedule'],'The schedule changed. Create a fresh early-start proposal.')
        return
    if kind=='funding':
        from . import funded_payments as funded, platform_paypal
        n.require(platform_paypal.configured(),'Configure the Sandbox platform app first.')
        n.require(not funded.agreed(p,v),'Upfront funding is already agreed.')
        return
    if kind in ('protections','reopen','cancel'):return
    m=milestone(v,mid) if kind!='change' else None
    row=a.payment(conn,p['id'],mid) if mid else None
    if kind in ('extension','dependency','change'):
        try:due=date.fromisoformat(r['due_date'])
        except ValueError:raise HTTPException(422,'Choose a valid delivery date.')
        n.require(due>=clock().date(),'Choose a current or future delivery date.',422)
        n.require(not row or row['status'] in ('authorized','funded','revision_requested'),'Only unfunded or authorized work can receive a deadline exception.')
        n.require(not closed(p,mid) and not p.get('withdrawal'),'Remaining work is closed.')
        if kind=='change':
            n.require(not p.get('amendment'),'Finish the agreement amendment first.')
            n.require(r['amount_minor']>0 and r['title'].strip() and r['criteria'] and all(x.strip() for x in r['criteria']),'Specify change title, price and acceptance criteria.',422)
            n.require(at(v['payload'],'scope.out_of_scope_policy')!='new_project','The signed terms require a new project for out-of-scope work.')
        if row:
            from .payment_policy import reserve_days
            reserve=reserve_days(v['payload'],m,row['payload'].get('revision_counts'))
            expiry=row['payload'].get('provider_disburse_by') or row['payload'].get('expires_at')
            n.require(expiry and reserve is not None and datetime.fromisoformat(expiry.replace('Z','+00:00')).date()>=due+timedelta(days=reserve),'Replace or renew funding before extending beyond its coverage.')
    if kind=='settlement':
        n.require(row,'Funded milestone not found.',404);a.bound(row,v,conn)
        from . import funded_payments
        if funded_payments.is_funded(row):
            n.require(row['status'] in ('funded','in_review','revision_requested','disputed','late_pending'),'Reconcile provider funding before settlement.')
            n.require(r['amount_minor']<=row['amount_minor'],'Settlement exceeds the milestone cap.')
            n.require((r['outcome']=='partial' and r['amount_minor']>0 and safe_url(r['handover_url'])) or (r['outcome'] in ('close','refund') and r['amount_minor'] in (0,row['amount_minor'])),'Specify a partial award with handover, or a full refund.')
            return
        n.require(not db.one(conn,db.milestone_payouts,db.milestone_payouts.c.payment_id==row['id']),
            'A payout intent already exists. Reconcile it before discussing any refund; completed payouts are not automatically reversed.')
        n.require(row['status'] in ('authorized','in_review','revision_requested','disputed','late_pending','captured'),'Reconcile or replace funding before settlement.')
        n.require(r['amount_minor']<=row['amount_minor'],'Settlement exceeds the signed milestone cap.',422)
        if r['outcome']=='refund':
            n.require(row['status']=='captured' and r['amount_minor']>0,'A refund requires a capture with no payout intent.')
            n.require(r['amount_minor']==row['payload'].get('settlement_amount_minor',row['amount_minor']),'Only a full unsettled-capture refund is supported; settle partial work before capture.')
        else:
            n.require(row['status']!='captured','A captured charge requires the explicit refund outcome.')
            n.require((r['outcome']=='close' and r['amount_minor']==0) or (r['outcome']=='partial' and r['amount_minor']>0),'Choose zero-payment closure or a positive partial-work payment.',422)
            n.require(not r['amount_minor'] or safe_url(r['handover_url']),'Partial settlement requires a handover link.',422)
            expiry=row['payload'].get('provider_disburse_by') or row['payload'].get('expires_at')
            n.require(not r['amount_minor'] or expiry and datetime.fromisoformat(expiry.replace('Z','+00:00'))>clock(),'Authorization has expired; replace funding first.')

def apply_request(conn,p,v,r,user):
    from . import assurance as a
    kind=r['kind'];mid=r['milestone_id']
    if kind=='funding':
        from .funded_payments import MODEL,POLICY
        p['funding_policy']={'version':MODEL,'text':POLICY,'request_id':r['id'],'accepted_at':db.now()}
    elif kind=='protections':p['workflow_policy']={'version':POLICY_VERSION,'text':POLICY_TEXT,'request_id':r['id'],'accepted_at':db.now(),'agreement_id':v['id']}
    elif kind=='reopen':
        n.require(not any(x['status'] in ('executing','needs_attention') for x in requests(p) if x['id']!=r['id']),'Resolve unsettled operations before reopening.')
        p.setdefault('closure_history',[]).append({'withdrawal':p.pop('withdrawal',None),'work_closed':p.pop('work_closed',None),'closed_milestones':p.pop('closed_milestones',[]),'at':db.now()})
    elif kind=='early_start':r['applied_at']=db.now()
    elif kind in ('extension','dependency','change'):pass
    elif kind in ('cancel','settlement'):
        if kind=='cancel':
            # Only jointly close work with no recorded submission or claim.
            ids=[m['id'] for m in at(v['payload'],'timeline.milestones',[])]
            rows=[a.payment(conn,p['id'],i,True) for i in ids]
            for row in rows:
                if row and not row['payload'].get('delivery_url'):
                    a.bound(row,v,conn)
                    n.require(row['status'] in ('authorized','funded','expired','voided','refunded'),'Resolve pending checkout/payment activity before cancelling.')
                    n.require(not any(c['milestone_id']==row['milestone_id'] and c['status']!='closed' for c in p.get('support_cases',[])),'Resolve the partial-work claim before closing this milestone.')
            targets=[row['milestone_id'] for row in rows if row and not row['payload'].get('delivery_url') and row['status'] in ('authorized','funded')]
            p['work_closed']={'reason':r['reason'],'request_id':r['id'],'at':db.now()}
        else:targets=[mid]
        r['operations']=[]
        for target in targets:
            row=a.payment(conn,p['id'],target,True)
            from .funded_payments import is_funded
            platform=is_funded(row)
            if platform and kind=='settlement' and r['outcome']=='partial':
                refund=row['amount_minor']-r['amount_minor']
                for operation,amount in ([('platform_refund',refund)] if refund else [])+[('platform_release',r['amount_minor'])]:
                    r['operations'].append({'milestone_id':target,'kind':operation,'key':db.uid(),'status':'ready','payment_id':row['id'],'amount_minor':amount,'capture_id':row['payload']['capture_id']})
                a.save(conn,row,'settling',settlement_request_id=r['id'],settlement_previous_status=row['status'],settlement_amount_minor=r['amount_minor'])
                p.setdefault('closed_milestones',[]).append(target)
                continue
            op='platform_refund' if platform else 'refund' if r.get('outcome')=='refund' else 'capture' if kind=='settlement' and r['amount_minor'] else 'void'
            r['operations'].append({'milestone_id':target,'kind':op,'key':db.uid(),'status':'ready','payment_id':row['id'],
                'amount_minor':row['amount_minor'] if platform else r['amount_minor'] if kind=='settlement' else 0,'authorization_id':row['payload'].get('authorization_id'),'capture_id':row['payload'].get('capture_id')})
            a.save(conn,row,'settling',settlement_request_id=r['id'],settlement_previous_status=row['status'])
            p.setdefault('closed_milestones',[]).append(target)
        r['status']='executing' if r['operations'] else 'applied'
        return
    r['status']='applied'

async def execute(pid,rid,user):
    """Only ready effects are dispatched. Claimed/unknown effects are read-only reconciled."""
    from . import assurance as a
    while True:
        with n.transaction() as conn:
            p,revision=n.project(conn,pid,user,True);r=find(p,rid)
            op=next((o for o in r.get('operations',[]) if o['status']=='ready'),None)
            if not op:break
            op['status']='submitting';r['status']='executing';n.write(conn,p,revision)
            row=a.payment(conn,pid,op['milestone_id']);a.bound(row,a.signed_terms(conn,p),conn)
        try:
            result=await provider_operation(row,op)
            # Persist an exact provider reference even when completion is pending.
            if op['kind']!='void' and result.get('id') and paypal.step1_same_amount(result.get('amount'),op['amount_minor'],row['currency']):
                with n.transaction() as conn:
                    saved_p,rev=n.project(conn,pid,user,True);saved_r=find(saved_p,rid)
                    saved_op=next(o for o in saved_r['operations'] if o['key']==op['key']);saved_op['provider_id']=result['id'];n.write(conn,saved_p,rev)
            n.require(result.get('status') in ('COMPLETED','VOIDED','EXPIRED'),'Provider operation is pending. Check its status; do not resubmit.',502)
            await finish_operation(pid,rid,op['key'],result,user)
        except HTTPException as exc:
            with n.transaction() as conn:
                p,revision=n.project(conn,pid,user,True);r=find(p,rid);saved=next(o for o in r['operations'] if o['key']==op['key'])
                if saved['status']=='submitting':saved.update(status='unknown',error=str(exc.detail));r['status']='needs_attention'
                n.write(conn,p,revision)
            raise
    with n.transaction() as conn:
        p,revision=n.project(conn,pid,user,True);r=find(p,rid)
        if r.get('operations') and all(o['status']=='completed' for o in r['operations']):
            r['status']='awaiting_payout' if any(o['kind']=='capture' for o in r['operations']) else 'applied'
            r['applied_at']=db.now();event(conn,p,'settlement_completed','Settlement operations verified'+('; freelancer receipt still awaits verification.' if r['status']=='awaiting_payout' else '.'),user,request_id=rid);n.write(conn,p,revision)

async def provider_operation(row,op):
    if op['kind']=='platform_release':
        from .funded_payments import settlement_release
        return await settlement_release(row,op)
    if op['kind']=='platform_refund':
        from .funded_payments import refund_operation
        return await refund_operation(row,op)
    if op['kind']=='void':
        await paypal.call('POST','/v2/payments/authorizations/'+op['authorization_id']+'/void',{},op['key'])
        result=await paypal.step1_get_authorization(op['authorization_id'])
        n.require(result.get('id')==op['authorization_id'] and result.get('status') in ('VOIDED','EXPIRED'),'Authorization void is not verified.',502)
        return result
    if op['kind']=='capture':
        result=await paypal.call('POST','/v2/payments/authorizations/'+op['authorization_id']+'/capture',
            {'amount':paypal.step1_amount(op['amount_minor'],row['currency']),'final_capture':True,'invoice_id':op['key']},op['key'])
    else:
        result=await paypal.call('POST','/v2/payments/captures/'+op['capture_id']+'/refund',
            {'amount':paypal.step1_amount(op['amount_minor'],row['currency']),'invoice_id':op['key']},op['key'])
    n.require(result.get('id') and paypal.step1_same_amount(result.get('amount'),op['amount_minor'],row['currency']),'Settlement response is mismatched. Reconcile before proceeding.',502)
    if op['kind']=='refund' and result.get('status')=='COMPLETED':
        capture=await paypal.step1_get_capture(op['capture_id'])
        n.require(capture.get('id')==op['capture_id'] and capture.get('status')=='REFUNDED','Capture refund is not verified.',502)
    return result

async def finish_operation(pid,rid,key,result,user):
    from . import assurance as a, step1_ledger
    with n.transaction() as conn:
        p,revision=n.project(conn,pid,user,True);r=find(p,rid);op=next(o for o in r['operations'] if o['key']==key)
        n.require(op['status'] in ('submitting','unknown'),'Operation was already processed.')
        row=a.payment(conn,pid,op['milestone_id'],True)
        n.require(row['status']=='settling' and row['payload'].get('settlement_request_id')==rid,'Settlement payment state changed.')
        op.update(status='completed',provider_id=result['id'],verified_at=db.now())
        if op['kind']=='platform_release':
            a.save(conn,row,'captured',disbursed_at=db.now(),attention=None,delivery_url=r['handover_url'],delivery_note=r['reason'])
            step1_ledger.post(conn,{**row,'amount_minor':op['amount_minor']},'platform_disbursement',result['id'],[('provider_receivable',-1),('project_liability',1)])
        elif op['kind']=='platform_refund' and r.get('outcome')=='partial':
            a.save(conn,row,'settling',refund_id=result['id'],refunded_minor=op['amount_minor'],attention=None)
            step1_ledger.post(conn,{**row,'amount_minor':op['amount_minor']},'refund',result['id'],[('provider_receivable',-1),('project_liability',1)])
        elif op['kind']=='capture':
            a.save(conn,row,'captured',capture_id=result['id'],captured_at=db.now(),settlement_amount_minor=op['amount_minor'],
                release_reason='joint_partial_settlement',delivery_url=r['handover_url'],delivery_note=r['reason'],capture_key=key,attention=None)
        elif op['kind']=='void':a.save(conn,row,'voided',voided_at=db.now(),attention=None)
        else:
            a.save(conn,row,'refunded',refund_id=result['id'],refunded_at=db.now(),attention=None)
            step1_ledger.post(conn,{**row,'amount_minor':op['amount_minor']},'refund',result['id'],[('provider_receivable',-1),('project_liability',1)])
        db.log(conn,'settlement_operation_verified',user['id'],pid,request_id=rid,operation=copy.deepcopy(op))
        n.write(conn,p,revision)

async def reconcile(pid,rid,user,refund_id=''):
    from . import assurance as a
    with db.engine().connect() as conn:
        p,_=n.project(conn,pid,user);r=find(p,rid);ops=copy.deepcopy(r.get('operations',[]))
    for op in ops:
        if op['status'] not in ('submitting','unknown'):continue
        with db.engine().connect() as conn:
            p,_=n.project(conn,pid,user);row=a.payment(conn,pid,op['milestone_id']);a.bound(row,a.signed_terms(conn,p),conn)
        if op['kind']=='platform_release':
            from .funded_payments import settlement_release
            result=await settlement_release(row,op,read_only=True)
        elif op['kind']=='platform_refund':
            from .funded_payments import reconcile_refund
            result=await reconcile_refund(row,op,refund_id)
        elif op['kind']=='void':
            result=await paypal.step1_get_authorization(op['authorization_id'])
            n.require(result.get('id')==op['authorization_id'] and result.get('status') in ('VOIDED','EXPIRED'),'Void has not been confirmed; no operation was retried.')
        elif op['kind']=='capture':
            order=await paypal.call('GET','/v2/checkout/orders/'+row['payload']['order_id'])
            units=order.get('purchase_units',[])
            n.require(order.get('id')==row['payload']['order_id'] and len(units)==1 and units[0].get('custom_id')==row['id'],'Order reference mismatch.',502)
            captures=units[0].get('payments',{}).get('captures',[])
            found=[c for c in captures if c.get('invoice_id')==op['key'] and c.get('status')=='COMPLETED' and paypal.step1_same_amount(c.get('amount'),op['amount_minor'],row['currency'])]
            n.require(len(found)==1,'Exact settlement capture not found. No new transfer was sent.');result=found[0]
        else:
            reference=op.get('provider_id') or refund_id
            n.require(reference,'Refund response is uncertain. Enter the refund ID from the merchant dashboard to verify it.')
            result=await paypal.call('GET','/v2/payments/refunds/'+reference)
            n.require(result.get('id')==reference and paypal.step1_same_amount(result.get('amount'),op['amount_minor'],row['currency']),'Refund amount/reference is not verified.')
            if not op.get('provider_id'):
                from urllib.parse import urlparse
                linked=any(x.get('rel')=='up' and urlparse(x.get('href','')).path=='/v2/payments/captures/'+op['capture_id'] and urlparse(x.get('href','')).hostname in ('api-m.sandbox.paypal.com','api.sandbox.paypal.com') for x in result.get('links',[]))
                n.require(linked,'The supplied refund is not linked to the original capture.',502)
                with n.transaction() as conn:
                    saved_p,rev=n.project(conn,pid,user,True);saved_r=find(saved_p,rid)
                    saved_op=next(o for o in saved_r['operations'] if o['key']==op['key']);saved_op['provider_id']=reference;n.write(conn,saved_p,rev)
            n.require(result.get('status')=='COMPLETED','Refund is not complete yet.')
            capture=await paypal.step1_get_capture(op['capture_id'])
            n.require(capture.get('id')==op['capture_id'] and capture.get('status')=='REFUNDED','Capture refund is not verified.')
        await finish_operation(pid,rid,op['key'],result,user)
    await execute(pid,rid,user)

def view(conn,p,v,user):
    from . import assurance as a
    now=clock();milestones=[]
    for m in at(v['payload'],'timeline.milestones',[]):
        row=a.payment(conn,p['id'],m['id']);due=cutoff(p,v,m);end=grace_end(p,v,m)
        milestones.append({'id':m['id'],'title':m['title'],'cutoff':due.isoformat(),'grace_end':end.isoformat(),
            'overdue':now>=due and not (row and row['payload'].get('delivery_url')),'closed':closed(p,m['id']),
            'status':row['status'] if row else 'unfunded','amount_minor':m['amount_minor']})
    files=db.rows(conn,db.delivery_files,db.delivery_files.c.project_id==p['id'])
    return {'policy_text':POLICY_TEXT,'enabled':enabled(p),'support_available':support_available(),'is_reviewer':reviewer(user),
        'requests':p.get('workflow_requests',[]),'cases':p.get('support_cases',[]),'milestones':milestones,
        'files':[{k:f[k] for k in ('id','filename','digest','size','created_at')} for f in files],
        'evidence':db.rows(conn,db.evidence,db.evidence.c.project_id==p['id']),
        'email_mode':os.getenv('EMAIL_MODE','local'),'work_closed':p.get('work_closed'),
        'operational_agreement':v['payload']}

async def scan(pid,now=None):
    """Durable reminders; only explicitly consented no-delivery defaults can close."""
    from . import assurance as a
    now=now or clock();execute_ids=[]
    with n.transaction() as conn:
        source=db.one(conn,db.projects,db.projects.c.id==pid)
        if not source or not source['payload'].get('active_agreement_id'):return
        member=db.one(conn,db.members,(db.members.c.project_id==pid)&(db.members.c.role=='client'))
        user=db.one(conn,db.users,db.users.c.id==member['user_id'])
        p,revision=n.project(conn,pid,user,True);v=effective_terms(p,a.signed_terms(conn,p));changed=False
        marks=p.setdefault('workflow_reminders',[])
        for m in at(v['payload'],'timeline.milestones',[]):
            mid=m['id'];row=a.payment(conn,pid,mid);due=cutoff(p,v,m)
            if closed(p,mid):continue
            if not row or not row['payload'].get('delivery_url'):
                phase='overdue' if now>=due else 'due_soon' if due-now<=timedelta(days=1) else ''
                key=f'{mid}:{m["due_date"]}:{phase}'
                if phase and key not in marks:
                    marks.append(key);event(conn,p,'delivery_'+phase,f'Milestone {mid} is '+phase.replace('_',' ')+'.');changed=True
                limit=grace_end(p,v,m)+timedelta(days=max(0,at(v['payload'],'termination.notice_days',7) or 0))
                claims=any(c['milestone_id']==mid and c['status']!='closed' for c in p.get('support_cases',[]))
                withdrawal_accepted=p.get('withdrawal',{}).get('status')=='accepted'
                from .funded_payments import is_funded
                if is_funded(row) and row['payload'].get('provider_disburse_by'):
                    limit=min(limit,datetime.fromisoformat(row['payload']['provider_disburse_by'])-timedelta(days=2))
                default_due=(enabled(p) or is_funded(row)) and now>=limit
                if (withdrawal_accepted or default_due) and not claims and (not row or row['status'] in ('authorized','funded','expired','voided','refunded')):
                    rid=db.uid();r={'id':rid,'kind':'withdrawal_close' if withdrawal_accepted else 'default','milestone_id':mid,'reason':'Accepted withdrawal from unsubmitted work.' if withdrawal_accepted else 'No submission after signed grace and termination notice.',
                        'status':'applied','created_at':db.now(),'votes':[],'operations':[]}
                    p.setdefault('closed_milestones',[]).append(mid)
                    if row and row['status'] in ('authorized','funded'):
                        a.bound(row,v,conn)
                        from .funded_payments import is_funded
                        platform=is_funded(row)
                        r['status']='executing';r['operations']=[{'milestone_id':mid,'kind':'platform_refund' if platform else 'void','key':db.uid(),
                            'status':'ready','payment_id':row['id'],'amount_minor':row['amount_minor'] if platform else 0,'capture_id':row['payload'].get('capture_id'),'authorization_id':row['payload'].get('authorization_id')}]
                        a.save(conn,row,'settling',settlement_request_id=rid,settlement_previous_status=row['status'])
                        execute_ids.append(rid)
                    requests(p).append(r);event(conn,p,'withdrawal_funding_closed' if withdrawal_accepted else 'delivery_default',f'Milestone {mid}: unsubmitted work closed following accepted withdrawal.' if withdrawal_accepted else f'Milestone {mid}: unperformed work closed after signed notice.');changed=True
            if row and row['status']=='revision_requested' and row['payload'].get('revision_due_at') and now>=datetime.fromisoformat(row['payload']['revision_due_at']):
                key=mid+':revision:'+row['payload']['revision_due_at']
                if key not in marks:marks.append(key);event(conn,p,'revision_overdue',f'Milestone {mid} correction is overdue. Payment remains paused; open a support case.');changed=True
        for case in p.get('support_cases',[]):
            if (case['status'] in ('negotiation','mediation') and now>=datetime.fromisoformat(case['response_due_at'])) or (case['status']=='support_unconfigured' and support_available()):
                case['status']='awaiting_support' if support_available() else 'support_unconfigured'
                event(conn,p,'case_escalated','Case '+case['id'][:8]+' needs human review. No payment decision was inferred.');changed=True
        for r in requests(p):
            if r['status']=='awaiting_payout':
                ops=[o for o in r.get('operations',[]) if o['kind']=='capture']
                if ops and all((db.one(conn,db.milestone_payouts,db.milestone_payouts.c.payment_id==o['payment_id']) or {}).get('status')=='paid' for o in ops):
                    r['status']='applied';event(conn,p,'settlement_received','Partial-work payout receipt verified.');changed=True
        if changed:n.write(conn,p,revision)
    for rid in execute_ids:
        try:await execute(pid,rid,user)
        except HTTPException:pass

def mount(app):
    from . import assurance as a
    router=APIRouter(prefix='/api/{portal}/projects/{pid}/workflow',dependencies=[Depends(n.portal_user)],tags=['Delivery and settlement'])

    @router.get('')
    def get(portal:n.Portal,pid:str,user=Depends(n.portal_user)):
        with db.engine().connect() as conn:
            p,v=a.current(conn,pid,user);return view(conn,p,v,user)

    @router.get('/early-start/{mid}')
    def preview_early(portal:n.Portal,pid:str,mid:str,user=Depends(n.portal_user)):
        from . import early_start
        with db.engine().connect() as conn:
            p,v=a.current(conn,pid,user)
            return early_start.plan(conn,p,v,mid)

    @router.post('/requests')
    def propose(portal:n.Portal,pid:str,data:Proposal,user=Depends(n.portal_user)):
        with n.transaction() as conn:
            p,revision=n.project(conn,pid,user,True);n.require(revision==data.revision,'Project changed. Refresh before proposing.')
            raw=a.signed_terms(conn,p);v=effective_terms(p,raw)
            n.require(len(requests(p))<300,'Request history is full; contact support.')
            r=data.model_dump(exclude={'revision'});r.update(id=db.uid(),status='proposed',created_by=user['id'],created_at=db.now(),
                agreement_id=raw['id'],agreement_digest=raw['digest'],financial_snapshot=snapshot(conn,p,'' if data.kind=='early_start' else data.milestone_id),votes=[])
            n.require(not any(x['kind']==r['kind'] and x['milestone_id']==r['milestone_id'] and x['status'] in OPEN|{'executing','needs_attention'} for x in requests(p)),'A request of this kind is already open.')
            if r['kind']=='early_start':
                from . import early_start
                r['schedule']=early_start.plan(conn,p,v,r['milestone_id'])
            if r['kind']=='funding':
                from .funded_payments import POLICY,MODEL
                r['policy_text']=POLICY;r['policy_version']=MODEL
            if r['kind']=='protections':
                n.require(not enabled(p),'Delivery protections already enabled.');r['policy_text']=POLICY_TEXT;r['policy_version']=POLICY_VERSION
            if r['kind']=='change':
                r['currency']=at(v['payload'],'payment.currency')
                from . import platform_paypal
                if platform_paypal.configured():
                    from .funded_payments import MODEL,POLICY
                    r.update(funding_model=MODEL,funding_rules=POLICY)
            request_valid(conn,p,v,r)
            if r['kind'] in ('extension','dependency','change'):
                from .payment_policy import reserve_days
                planned=milestone(v,r['milestone_id']) if r['kind']!='change' else {'id':'new','review_days':r['review_days']}
                reserve=reserve_days(v['payload'],planned)
                r['completion_date']=str(date.fromisoformat(r['due_date'])+timedelta(days=reserve))
            r['digest']=proposal_digest(r);requests(p).append(r)
            event(conn,p,'workflow_proposed',r['kind'].title()+' proposed: '+r['reason'],user,request=copy.deepcopy(r))
            n.write(conn,p,revision);return {'request':r,'revision':revision+1}

    @router.post('/requests/{rid}/vote')
    async def vote(portal:n.Portal,pid:str,rid:str,data:Vote,request:Request,user=Depends(n.portal_user)):
        execute_it=False
        with n.transaction() as conn:
            p,revision=n.project(conn,pid,user,True);n.require(revision==data.revision,'Project changed. Refresh before accepting.')
            r=find(p,rid);n.require(r['status'] in OPEN and r['digest']==data.digest,'Request changed or is no longer open.')
            n.require(r['digest']==proposal_digest(r),'Proposal integrity check failed. No acceptance was recorded.')
            if data.decision=='withdraw':n.require(r['created_by']==user['id'],'Only the proposer can withdraw.',403);r['status']='withdrawn'
            elif data.decision=='decline':r['status']='declined'
            else:
                n.require(data.typed_name.strip().casefold()==user['name'].strip().casefold(),'Type your account name to accept.',422)
                n.require(r['agreement_id']==p['active_agreement_id'],'The active agreement changed; propose again.')
                n.require(r['financial_snapshot']==snapshot(conn,p,'' if r['kind']=='early_start' else r['milestone_id']),'Payment or review state changed. Withdraw this proposal and create a fresh one.')
                n.require(not any(x['user_id']==user['id'] for x in r['votes']),'You already accepted this request.')
                v=effective_terms(p,a.signed_terms(conn,p));request_valid(conn,p,v,r)
                r['votes'].append({'user_id':user['id'],'role':portal,'typed_name':data.typed_name.strip(),'digest':r['digest'],'at':db.now(),
                    'ip':request.client.host if request.client else '', 'user_agent':request.headers.get('user-agent','')[:500]})
                r['status']='accepted_by_one'
                if {x['role'] for x in r['votes']}=={'client','freelancer'}:
                    apply_request(conn,p,v,r,user);execute_it=r['status']=='executing'
            event(conn,p,'workflow_response',r['kind'].title()+': '+r['status'].replace('_',' '),user,request=copy.deepcopy(r))
            n.write(conn,p,revision)
        if execute_it:await execute(pid,rid,user)
        return {'ok':True}

    @router.post('/requests/{rid}/reconcile')
    async def check(portal:n.Portal,pid:str,rid:str,data:ReconcileInput,user=Depends(n.portal_user)):
        await reconcile(pid,rid,user,data.refund_id);return {'ok':True}

    @router.get('/requests/{rid}/export')
    def export_request(portal:n.Portal,pid:str,rid:str,user=Depends(n.portal_user)):
        import json
        with db.engine().connect() as conn:
            p,_=n.project(conn,pid,user);r=find(p,rid)
            return Response(json.dumps(r,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="agreement-addendum-'+rid+'.json"','Cache-Control':'private, no-store'})

    @router.post('/{mid}/late')
    def late(portal:n.Portal,pid:str,mid:str,data:LateDecision,user=Depends(n.portal_user)):
        n.require(portal=='client','Only the client accepts a late timing exception.',403)
        with n.transaction() as conn:
            p,v=a.current(conn,pid,user,True);row=a.bound(a.payment(conn,pid,mid,True),v,conn)
            n.require(row['status']=='late_pending','No late submission is pending.')
            m=milestone(v,mid)
            if data.accept:
                from .payment_policy import reserve_days
                reserve=reserve_days(v['payload'],m,row['payload'].get('revision_counts'))
                from . import funded_payments
                n.require(funded_payments.coverage(row,reserve) if funded_payments.is_funded(row) else reserve is not None and row['payload'].get('expires_at') and datetime.fromisoformat(row['payload']['expires_at'].replace('Z','+00:00'))>=clock()+timedelta(days=reserve),'Funding cannot cover the late review period.')
                a.save(conn,row,'in_review',late_accepted_at=db.now(),review_deadline=(clock()+timedelta(days=m['review_days'])).isoformat(),objection=None,review_reminded=False)
            else:a.save(conn,row,'disputed',objection={'kind':'dispute','reason':'Late delivery timing rejected; cancellation or settlement required.','at':db.now()})
            event(conn,p,'late_delivery_response','Late delivery timing '+('accepted; full review started.' if data.accept else 'rejected; settlement required.'),user)
            return a.visible(row)

    @router.post('/files')
    def upload(portal:n.Portal,pid:str,data:FileInput,user=Depends(n.portal_user)):
        try:raw=base64.b64decode(data.content_base64,validate=True)
        except ValueError:raise HTTPException(422,'Invalid file encoding.')
        n.require(0<len(raw)<=5*1024*1024,'Files must be between 1 byte and 5 MiB.',422)
        filename=data.filename.replace('\\','/').split('/')[-1]
        n.require(filename and all(ord(c)>=32 for c in filename),'Invalid filename.',422)
        with n.transaction() as conn:
            p,_=a.current(conn,pid,user,True)
            existing=db.rows(conn,db.delivery_files,db.delivery_files.c.project_id==pid)
            n.require(sum(f['size'] for f in existing)+len(raw)<=50*1024*1024,'Project evidence limit is 50 MiB.')
            row=dict(id=db.uid(),project_id=pid,user_id=user['id'],filename=filename,digest=hashlib.sha256(raw).hexdigest(),
                size=len(raw),content=base64.b64encode(raw).decode(),created_at=db.now())
            conn.execute(insert(db.delivery_files).values(**row));db.log(conn,'delivery_file_stored',user['id'],pid,file_id=row['id'],sha256=row['digest'],size=len(raw))
            return {k:row[k] for k in ('id','filename','digest','size')}

    @router.post('/handover')
    def handover(portal:n.Portal,pid:str,data:Handover,user=Depends(n.portal_user)):
        n.require(portal=='freelancer','Only the freelancer records work handover.',403)
        n.require(data.url or data.file_ids,'Provide a handover link or evidence file.',422)
        with n.transaction() as conn:
            p,revision=n.project(conn,pid,user,True);n.require(revision==data.revision,'Project changed. Refresh before recording handover.')
            v=effective_terms(p,a.signed_terms(conn,p));milestone(v,data.milestone_id)
            e=evidence(conn,p,user,data.milestone_id,'Partial work / handover',data.url,data.note,data.file_ids)
            case=next((c for c in p.get('support_cases',[]) if c['milestone_id']==data.milestone_id and c['category']=='partial_work' and c['status']!='closed'),None)
            message={'by':user['id'],'role':portal,'body':data.note,'evidence_url':data.url,'evidence_id':e['id'],'at':db.now()}
            if case:case['messages'].append(message)
            else:
                case={'id':db.uid(),'milestone_id':data.milestone_id,'category':'partial_work','reason':data.note,'created_by':user['id'],
                    'created_at':db.now(),'response_due_at':(clock()+timedelta(hours=max(24,at(v['payload'],'communication.response_hours',24) or 24))).isoformat(),
                    'status':'negotiation','messages':[message]}
                p.setdefault('support_cases',[]).append(case)
            event(conn,p,'handover_recorded','Partial-work handover recorded. This is evidence, not a full delivery or payment approval.',user,evidence_id=e['id'])
            n.write(conn,p,revision);return {'evidence':e,'case':case}

    @router.get('/files/{fid}')
    def download(portal:n.Portal,pid:str,fid:str,user=Depends(n.portal_user)):
        with db.engine().connect() as conn:
            n.project(conn,pid,user)
            row=db.one(conn,db.delivery_files,(db.delivery_files.c.id==fid)&(db.delivery_files.c.project_id==pid));n.require(row,'File not found.',404)
        from urllib.parse import quote
        raw=base64.b64decode(row['content']);n.require(hashlib.sha256(raw).hexdigest()==row['digest'],'Evidence hash mismatch.',409)
        return Response(raw,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row['filename']),
            'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store','X-Content-SHA256':row['digest']})

    @router.post('/cases')
    def create_case(portal:n.Portal,pid:str,data:CaseInput,user=Depends(n.portal_user)):
        with n.transaction() as conn:
            p,revision=n.project(conn,pid,user,True);n.require(revision==data.revision,'Project changed. Refresh first.')
            v=effective_terms(p,a.signed_terms(conn,p));milestone(v,data.milestone_id)
            n.require(not data.evidence_url or safe_url(data.evidence_url),'Use an HTTP(S) evidence link.',422)
            n.require(len(p.get('support_cases',[]))<100,'Case limit reached.')
            case={'id':db.uid(),'milestone_id':data.milestone_id,'category':data.category,'reason':data.reason,'created_by':user['id'],
                'created_at':db.now(),'response_due_at':(clock()+timedelta(hours=max(24,at(v['payload'],'communication.response_hours',24) or 24))).isoformat(),
                'status':'negotiation','messages':[{'by':user['id'],'role':portal,'body':data.reason,'evidence_url':data.evidence_url,'at':db.now()}]}
            if data.category=='defect':
                n.require(portal=='client','Only the client requests a warranty correction.',403)
                row=a.payment(conn,pid,data.milestone_id)
                delivered=row and row['payload'].get('delivered_at')
                n.require(delivered,'A recorded submission is required for a defect claim.')
                n.require(clock()<=datetime.fromisoformat(delivered)+timedelta(days=at(v['payload'],'revisions.defect_days',0) or 0),'The signed free-defect reporting period has ended.')
                case['correction_due_at']=(clock()+timedelta(days=at(v['payload'],'revisions.turnaround_days',2))).isoformat()
                case['payment_effect']='none; paid work is not reversed and revision counters are unchanged'
            # A case is evidence/escalation. It cannot bypass the signed objection cutoff.
            p.setdefault('support_cases',[]).append(case);event(conn,p,'support_case_opened',data.category+' case opened: '+data.reason,user)
            n.write(conn,p,revision);return case

    @router.post('/cases/{cid}/reply')
    def reply(portal:n.Portal,pid:str,cid:str,data:CaseReply,user=Depends(n.portal_user)):
        with n.transaction() as conn:
            p,revision=n.project(conn,pid,user,True);n.require(revision==data.revision,'Project changed. Refresh first.')
            c=next((x for x in p.get('support_cases',[]) if x['id']==cid),None);n.require(c,'Case not found.',404)
            n.require(not data.evidence_url or safe_url(data.evidence_url),'Use an HTTP(S) evidence link.',422)
            c['messages'].append({'by':user['id'],'role':portal,'body':data.body,'evidence_url':data.evidence_url,'at':db.now()})
            if data.file_ids or data.evidence_url:
                e=evidence(conn,p,user,c['milestone_id'],'Case evidence',data.evidence_url,data.body,data.file_ids)
                c['messages'][-1]['evidence_id']=e['id']
            if data.close:
                if c.get('conclusion',{}).get('body')!=data.body:
                    n.require(c['status']!='closed','A closed conclusion cannot be rewritten.')
                    c['conclusion']={'body':data.body,'roles':[]}
                conclusion=c['conclusion']
                conclusion['roles']=sorted(set(conclusion['roles']+[portal]))
                if conclusion['roles']==['client','freelancer']:c['status']='closed'
            if data.escalate:
                n.require(clock()>=datetime.fromisoformat(c['response_due_at']),'Allow the agreed response period before escalating.')
                c['status']='awaiting_support' if support_available() else 'support_unconfigured'
            event(conn,p,'support_case_response','A response was added to case '+cid[:8]+'.',user);n.write(conn,p,revision);return c

    @router.post('/{mid}/replace-funding')
    async def replace(portal:n.Portal,pid:str,mid:str,user=Depends(n.portal_user)):
        n.require(portal=='client','Only the client can replace buyer funding.',403)
        # Read provider state first; claim locally before any void. Never replace a capture.
        with n.transaction() as conn:
            p,v=a.current(conn,pid,user,True);row=a.bound(a.payment(conn,pid,mid,True),v,conn)
            n.require(not row['payload'].get('funding_model'),'Use platform provider reconciliation for upfront-funded work.')
            n.require(not p.get('withdrawal') and not closed(p,mid),'Remaining work is closed. Agree reopening before replacement funding.')
            n.require(row['status'] in ('expired','authorized','revision_requested','disputed','attention','awaiting_approval'),'This payment cannot be replaced.')
            n.require(not row['payload'].get('capture_id') and not db.one(conn,db.milestone_payouts,db.milestone_payouts.c.payment_id==row['id']),'Captured work cannot be replaced.')
            previous=copy.deepcopy(row);key=db.uid();a.save(conn,row,'replacing',replacement_key=key)
        try:
            order=await paypal.call('GET','/v2/checkout/orders/'+row['payload']['order_id'])
            units=order.get('purchase_units',[])
            n.require(order.get('id')==row['payload']['order_id'] and len(units)==1 and units[0].get('custom_id')==row['id'] and paypal.step1_same_amount(units[0].get('amount'),row['amount_minor'],row['currency']),'Original order reference/amount mismatch.',502)
            payments=units[0].get('payments',{});n.require(not payments.get('captures'),'A capture exists. Reconcile before replacing funding.')
            auths=payments.get('authorizations',[])
            aid=row['payload'].get('authorization_id')
            if not aid and auths:
                n.require(len(auths)==1 and auths[0].get('id') and paypal.step1_same_amount(auths[0].get('amount'),row['amount_minor'],row['currency']),'An unrecognized authorization exists. Reconcile before replacement.')
                aid=auths[0]['id']
            # Wallet approval of an AUTHORIZE order alone cannot capture money.
            # Claiming `replacing` above prevents the app authorizing the old order.
            n.require(aid or not auths and order.get('status') in ('CREATED','APPROVED','PAYER_ACTION_REQUIRED','VOIDED'),'Original checkout state cannot be safely retired.')
            if aid:
                n.require(any(x.get('id')==aid for x in auths),'Original authorization reference not present.',502)
                remote=await paypal.step1_get_authorization(aid)
                n.require(remote.get('id')==aid and paypal.step1_same_amount(remote.get('amount'),row['amount_minor'],row['currency']),'Authorization amount/reference mismatch.',502)
                if remote.get('status')=='CREATED':
                    await paypal.call('POST','/v2/payments/authorizations/'+aid+'/void',{},key);remote=await paypal.step1_get_authorization(aid)
                n.require(remote.get('status') in ('VOIDED','EXPIRED'),'Original authorization remains active or has a capture. Replacement blocked.')
        except HTTPException as exc:
            with n.transaction() as conn:
                saved=a.payment(conn,pid,mid,True);a.save(conn,saved,'attention',attention=str(exc.detail),replacement_previous=previous)
            raise
        with n.transaction() as conn:
            p,revision=n.project(conn,pid,user,True);row=a.payment(conn,pid,mid,True);n.require(row['status']=='replacing','Funding state changed.')
            history=copy.deepcopy(previous);history['payload'].pop('previous_attempts',None);history['payload'].pop('replacement_previous',None)
            histories=row['payload'].get('previous_attempts',[])+[history]
            kept={k:previous['payload'][k] for k in ('merchant_fingerprint','payout_recipient','agreement_hash','auto_release','delivery_url','delivery_note','delivered_at','delivery_history','delivery_evidence_id','objection','revision_counts') if k in previous['payload']}
            # Historical delivery cannot resume payment review without a fresh full period.
            kept.update(previous_attempts=histories,order_key=db.uid(),authorize_key=db.uid(),capture_key=db.uid(),replacement_requires_review=True)
            row['payload']=kept;a.save(conn,row,'creating')
            event(conn,p,'funding_replaced','Original unused funding retired; any authorization was verified closed. New buyer approval is required.',user)
            n.write(conn,p,revision)
        try:
            result=await paypal.step1_create_order(row,pid)
            link=next((x['href'] for x in result.get('links',[]) if x.get('rel') in ('approve','payer-action') and x.get('href','').startswith('https://www.sandbox.paypal.com/')),None)
            n.require(result.get('id') and link,'No verified Sandbox checkout link.',502)
            with n.transaction() as conn:
                saved=a.payment(conn,pid,mid,True);n.require(saved['status']=='creating','Payment changed during replacement.')
                a.save(conn,saved,'awaiting_approval',order_id=result['id'],checkout_url=link)
            return a.visible(saved)
        except HTTPException as exc:
            with n.transaction() as conn:
                saved=a.payment(conn,pid,mid,True);a.save(conn,saved,'attention',attention=str(exc.detail))
            raise

    @router.post('/{mid}/recover-order')
    async def recover_order(portal:n.Portal,pid:str,mid:str,data:RecoverOrder,user=Depends(n.portal_user)):
        n.require(portal=='client','Only the client can recover a checkout reference.',403)
        with db.engine().connect() as conn:
            p,v=a.current(conn,pid,user);row=a.bound(a.payment(conn,pid,mid),v,conn)
            n.require(row['status']=='attention' and not row['payload'].get('capture_id'),'Only an uncertain uncaptured checkout can be recovered.')
            n.require(not row['payload'].get('order_id') or row['payload']['order_id']==data.order_id,'Recover the saved checkout reference; a different existing order cannot replace it.')
            retired={x['payload'].get('order_id') for x in row['payload'].get('previous_attempts',[])}
            n.require(data.order_id not in retired,'That order belongs to a retired attempt.')
        remote=await paypal.call('GET','/v2/checkout/orders/'+data.order_id)
        units=remote.get('purchase_units',[])
        n.require(remote.get('id')==data.order_id and len(units)==1 and units[0].get('custom_id')==row['id'] and paypal.step1_same_amount(units[0].get('amount'),row['amount_minor'],row['currency']),'Order reference or amount does not match this milestone.',502)
        n.require(not units[0].get('payments',{}).get('captures'),'A capture exists. Use capture reconciliation instead.')
        auths=units[0].get('payments',{}).get('authorizations',[])
        link=next((x.get('href') for x in remote.get('links',[]) if x.get('rel') in ('approve','payer-action') and x.get('href','').startswith('https://www.sandbox.paypal.com/')),None)
        fields={'order_id':data.order_id,'checkout_url':link,'attention':None};status='awaiting_approval'
        if auths:
            n.require(len(auths)==1 and auths[0].get('id'),'Authorization reference is ambiguous.',502)
            authz=await paypal.step1_get_authorization(auths[0]['id'])
            n.require(authz.get('id')==auths[0]['id'] and authz.get('status')=='CREATED' and paypal.step1_same_amount(authz.get('amount'),row['amount_minor'],row['currency']),'Authorization could not be verified.',502)
            from .payment_policy import reserve_days
            reserve=reserve_days(v['payload'],milestone(v,mid),row['payload'].get('revision_counts'))
            expiry=authz.get('expiration_time')
            n.require(expiry and reserve is not None and datetime.fromisoformat(expiry.replace('Z','+00:00'))>=clock()+timedelta(days=reserve),'Authorization does not cover a complete review. Reconcile replacement funding.')
            fields.update(authorization_id=authz['id'],expires_at=expiry,authorized_at=authz.get('create_time',db.now()))
            # Existing delivery must be resubmitted; old objections are retained.
            status='authorized'
        else:n.require(remote.get('status')=='APPROVED' or link,'Order is not available for buyer approval.')
        with n.transaction() as conn:
            p,rev=n.project(conn,pid,user,True);saved=a.payment(conn,pid,mid,True)
            n.require(saved['status']=='attention' and saved['payload']==row['payload'],'Payment changed during recovery.')
            a.save(conn,saved,status,**fields)
            event(conn,p,'checkout_recovered','The original provider checkout reference was verified. No new charge was submitted.',user)
            n.write(conn,p,rev);return a.visible(saved)

    app.include_router(router)

    support=APIRouter(prefix='/api/support',tags=['Human support review'])
    def support_project(conn,pid,user):
        n.require(reviewer(user),'Support reviewer access required.',403)
        row=db.one(conn,db.projects,db.projects.c.id==pid)
        n.require(row and row['payload'].get('engine')==n.ENGINE,'Project not found.',404)
        n.require(not db.one(conn,db.members,(db.members.c.project_id==pid)&(db.members.c.user_id==user['id'])),'Participants cannot review their own disputes.',403)
        n.require(any(c['status'] in ('awaiting_support','mediation','support_review','recommendation') for c in row['payload'].get('support_cases',[])),'Case has not reached support.',404)
        return row

    @support.get('/projects/{pid}')
    def support_evidence(pid:str,user=Depends(auth.verify_required)):
        with db.engine().connect() as conn:
            row=support_project(conn,pid,user);p=row['payload'];v=a.signed_terms(conn,p)
            files=db.rows(conn,db.delivery_files,db.delivery_files.c.project_id==pid)
            return {'agreement':v['payload'],'cases':p.get('support_cases',[]),'evidence':db.rows(conn,db.evidence,db.evidence.c.project_id==pid),
                'operational_agreement':effective_terms(p,v)['payload'],'addenda':p.get('workflow_requests',[]),
                'files':[{k:f[k] for k in ('id','filename','digest','size')} for f in files],
                'payments':[a.visible(x) for x in db.rows(conn,db.milestone_payments,db.milestone_payments.c.project_id==pid)]}

    @support.get('/projects/{pid}/files/{fid}')
    def support_file(pid:str,fid:str,user=Depends(auth.verify_required)):
        with db.engine().connect() as conn:
            support_project(conn,pid,user)
            row=db.one(conn,db.delivery_files,(db.delivery_files.c.id==fid)&(db.delivery_files.c.project_id==pid));n.require(row,'File not found.',404)
        raw=base64.b64decode(row['content']);n.require(hashlib.sha256(raw).hexdigest()==row['digest'],'Evidence hash mismatch.')
        from urllib.parse import quote
        return Response(raw,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row['filename']),'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'})

    @support.get('/cases')
    def queue(user=Depends(auth.verify_required)):
        n.require(reviewer(user),'Support reviewer access required.',403)
        result=[]
        with db.engine().connect() as conn:
            for row in db.rows(conn,db.projects,db.projects.c.archived==False):
                p=row['payload']
                if p.get('engine')!=n.ENGINE:continue
                # A participant can never review their own case, even if allowlisted.
                if db.one(conn,db.members,(db.members.c.project_id==p['id'])&(db.members.c.user_id==user['id'])):continue
                for c in p.get('support_cases',[]):
                    if c['status'] in ('awaiting_support','mediation','support_review','recommendation'):
                        result.append({'project_id':p['id'],'title':at(a.signed_terms(conn,p)['payload'],'basics.title'),
                            'revision':row['version'],'case':c})
        return result

    @support.post('/projects/{pid}/cases/{cid}')
    def review(pid:str,cid:str,data:SupportReview,user=Depends(auth.verify_required)):
        n.require(reviewer(user),'Support reviewer access required.',403)
        with n.transaction() as conn:
            row=conn.execute(select(db.projects).where(db.projects.c.id==pid).with_for_update()).mappings().first()
            n.require(row and row['payload'].get('engine')==n.ENGINE,'Project not found.',404)
            n.require(not db.one(conn,db.members,(db.members.c.project_id==pid)&(db.members.c.user_id==user['id'])),'Participants cannot review their own disputes.',403)
            n.require(row['version']==data.revision,'Case changed. Refresh first.')
            p=copy.deepcopy(row['payload']);c=next((x for x in p.get('support_cases',[]) if x['id']==cid),None)
            n.require(c and c['status'] in ('awaiting_support','mediation','support_review','recommendation'),'Case has not reached support.',404)
            c['status']=data.stage;c['messages'].append({'by':user['id'],'role':'support','body':data.body,'at':db.now()})
            if data.stage=='mediation':c['response_due_at']=(clock()+timedelta(days=2)).isoformat()
            event(conn,p,'human_support_review','Human support '+data.stage.replace('_',' ')+': '+data.body,user)
            n.write(conn,p,row['version']);return c
    app.include_router(support)
