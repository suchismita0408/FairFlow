"""PayPal Sandbox milestone assurance for signed Step 1 agreements.

The configured merchant receives captures. This is not escrow or a freelancer payout.
Remote mutations have persisted operation IDs before the network request; uncertain
responses pause instead of reporting success.
"""
import os,hashlib
from datetime import date,datetime,timedelta,timezone
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field,EmailStr,ConfigDict
from sqlalchemy import insert,update,select
from . import db,paypal,negotiation as n
from .agreement_schema import at
from .agreement_validation import digest,safe_url,validate
from .currencies import funding_available
from .payment_policy import reserve_days,checkout_window,automatic_release_agreed

Portal=Literal['client','freelancer']

class MilestoneRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    milestone_id:str=Field(min_length=1,max_length=64)

class Checkout(MilestoneRequest):
    recipient_email:EmailStr|None=None

class Objection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['revision','dispute','defect']
    reason:str=Field(min_length=10,max_length=3000)
    deliverable_id:str|None=None

class Resolution(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reason:str=Field(min_length=10,max_length=3000)

class Approval(BaseModel):
    model_config=ConfigDict(extra='forbid')
    recipient_email:EmailStr
    sandbox_payout_confirmed:Literal[True]

class Delivery(MilestoneRequest):
    url:str=Field(max_length=2000)
    note:str=Field(min_length=10,max_length=3000)
    file_ids:list[str]=Field(default_factory=list,max_length=5)
    checklist:list[str]=Field(default_factory=list,max_length=100)

def require(condition,message,status=409):
    if not condition:raise HTTPException(status,message)

def payment(conn,pid,milestone_id,lock=False):
    if lock:
        # Keep the same project -> payment order in API and worker completion
        # transactions. Audit/ledger foreign keys also acquire project locks.
        conn.execute(select(db.projects.c.id).where(db.projects.c.id==pid).with_for_update()).first()
    query=select(db.milestone_payments).where((db.milestone_payments.c.project_id==pid)&(db.milestone_payments.c.milestone_id==milestone_id))
    if lock:query=query.with_for_update()
    row=conn.execute(query).mappings().first()
    return dict(row) if row else None

def save(conn,row,status,**fields):
    payload={**row['payload'],**fields}
    if status!=row['status']:payload['state_changed_at']=db.now()
    conn.execute(update(db.milestone_payments).where(db.milestone_payments.c.id==row['id']).values(status=status,payload=payload))
    row.update(status=status,payload=payload)
    if status=='captured':
        from . import step1_ledger
        step1_ledger.capture(conn,row)

def visible(row):
    return {k:row[k] for k in ('id','milestone_id','amount_minor','currency','status','created_at')}|{
        k:row['payload'].get(k) for k in ('checkout_url','order_id','authorization_id','capture_id','expires_at','delivery_url','delivery_note','delivered_at','captured_at','review_deadline','attention','auto_release','objection','revision_counts','payout_recipient','authorized_at','reauthorized_at','resolution','delivery_history','delivery_evidence_id','late','late_accepted_at','revision_due_at','defect_requests','settlement_amount_minor','refund_id','previous_attempts','funding_model','provider_disburse_by','seller_email','seller_merchant_id','disbursed_at','provider_checked_at','payout_transaction_id','seller_net_amount','release_provider_status','provider_disbursement_mode','collection_verified_at','release_requested_at','release_reason','unexpected_disbursement')}

def captured_amount(row):return row['payload'].get('settlement_amount_minor',row['amount_minor'])

def signed_terms(conn,p):
    require(p.get('active_agreement_id') and p['status'] not in n.TERMINAL,'Both signatures are required before payment.')
    v=db.one(conn,db.agreements,db.agreements.c.id==p['active_agreement_id'])
    require(v and digest(v['payload'])==v['digest'],'Signed agreement hash mismatch.')
    return v

def current(conn,pid,user,lock=False):
    p,_=n.project(conn,pid,user,lock)
    from .workflows import effective_terms
    return p,effective_terms(p,signed_terms(conn,p))

def bound(row,v,conn=None):
    require(row,'Payment not found.',404)
    if row['agreement_id']!=v['id']:
        require(conn is not None and row.get('project_id')==v.get('project_id'),'This payment belongs to a different signed version.',404)
        original=db.one(conn,db.agreements,(db.agreements.c.id==row['agreement_id'])&(db.agreements.c.project_id==v['project_id']))
        require(original and digest(original['payload'])==original['digest'] and len(n.signed(conn,original['id']))==2,'Historical payment agreement evidence is invalid.')
        from .workflows import effective_terms
        project_terms=db.one(conn,db.projects,db.projects.c.id==row['project_id'])['payload']
        historical=effective_terms(project_terms,original)
        old=next((m for m in at(historical['payload'],'timeline.milestones',[]) or [] if m.get('id')==row['milestone_id']),None)
        signed_active=db.one(conn,db.agreements,db.agreements.c.id==v['id']) if v.get('_operational') else v
        active=next((m for m in at(signed_active['payload'],'timeline.milestones',[]) or [] if m.get('id')==row['milestone_id']),None)
        if row['milestone_id'].startswith('CO-'):
            project=db.one(conn,db.projects,db.projects.c.id==row['project_id'])['payload']
            addition=next((r for r in project.get('workflow_requests',[]) if 'CO-'+r['id']==row['milestone_id'] and r['kind']=='change' and r['status']=='applied'),None)
            require(addition and addition['agreement_id']==original['id'] and len(addition['votes'])==2 and row['amount_minor']==addition['amount_minor'],'Change-order consent is invalid.')
        else:
            require(old is not None and old==active,'An amended agreement cannot change a previously funded milestone.')
            require(row['currency']==at(original['payload'],'payment.currency')==at(v['payload'],'payment.currency') and row['amount_minor']==old.get('amount_minor'),'Historical payment amount or currency differs.')
    require(not row['payload'].get('merchant_retired'),'This historical payment belongs to the removed merchant account. Its records are preserved; manage its funds in that merchant’s PayPal dashboard.')
    from . import funded_payments
    if funded_payments.is_funded(row):
        funded_payments.assert_app(row)
        return row
    expected=row['payload'].get('merchant_fingerprint')
    require(not expected or expected==hashlib.sha256(os.getenv('PAYPAL_CLIENT_ID','').encode()).hexdigest(),'This payment belongs to a different merchant app. Do not retry it under another account.')
    return row

def ensure_provider():
    require(bool(os.getenv('PAYPAL_CLIENT_ID') and os.getenv('PAYPAL_CLIENT_SECRET')),'Configure PayPal Sandbox credentials on the server.',503)

async def release_payment(pid,milestone_id,user,data=None,*,automatic=False,now=None):
    from . import payouts, funded_payments
    with db.engine().connect() as conn:
        candidate=payment(conn,pid,milestone_id)
    if funded_payments.is_funded(candidate):
        require(automatic,'Disbursement follows the agreed automatic review period.')
        return await funded_payments.release(pid,milestone_id,user,now)
    require(automatic,'Payments are released by the worker after review; manual approval is unavailable.')
    require(user['role']=='client','Only the client can approve delivery and capture.',403)
    clock=now or datetime.now(timezone.utc)
    actor=None if automatic else user['id']
    with n.transaction() as conn:
        p,v=current(conn,pid,user,True);row=bound(payment(conn,pid,milestone_id,True),v,conn)
        require(not payouts.setup_error(conn),payouts.setup_error(conn),503)
        require(row['status']=='in_review','A delivered milestone must be in review before capture.')
        require(row['payload'].get('delivery_url'),'Delivery evidence is required.')
        require(row['payload'].get('expires_at') and datetime.fromisoformat(row['payload']['expires_at'].replace('Z','+00:00'))>clock,'Authorization expired. Reauthorize before capture.')
        if automatic:
            milestone=next(m for m in at(v['payload'],'timeline.milestones') if m['id']==milestone_id)
            require(milestone.get('auto_accept') is True and row['payload'].get('auto_release') is True,'Automatic release was not agreed and enabled at checkout.')
            require(row['payload'].get('review_deadline') and datetime.fromisoformat(row['payload']['review_deadline'])<=clock,'The review period has not ended.')
        require(not row['payload'].get('objection'),'An objection pauses payment.')
        recipient=row['payload'].get('payout_recipient') or payouts.project_recipient(conn,pid)
        require(recipient,'The freelancer must configure a Sandbox receiving account before approval and payment.')
        require(automatic or recipient['email']==str(data.recipient_email).casefold(),'Recipient changed. Refresh and review the receiving email before approving.')
        save(conn,row,'capturing',release_reason='review_timeout' if automatic else 'client_approval',payout_recipient={'user_id':recipient['user_id'],'email':recipient['email']})
        db.log(conn,'milestone_auto_release_started' if automatic else 'milestone_approved_for_capture',actor,pid,milestone_id=milestone_id,amount_minor=row['amount_minor'])
    try:capture=await paypal.step1_capture(row)
    except HTTPException as exc:
        with n.transaction() as conn:
            saved=payment(conn,pid,milestone_id,True);save(conn,saved,'attention',attention=str(exc.detail))
        raise
    with n.transaction() as conn:
        saved=payment(conn,pid,milestone_id,True)
        require(saved['status']=='capturing','Payment state changed during capture.')
        save(conn,saved,'captured',capture_id=capture['id'],captured_at=db.now(),attention=None)
        db.log(conn,'sandbox_capture_confirmed',actor,pid,milestone_id=milestone_id,capture_id=capture['id'],amount_minor=saved['amount_minor'],currency=saved['currency'])
        n.notify(conn,p,'milestone_captured','PayPal Sandbox capture completed for milestone '+milestone_id)
    # Capture is committed first; a payout failure must never erase it or recapture.
    try:
        payout=await payouts.send_payment('client',pid,milestone_id,payouts.Consent(recipient_email=recipient['email'],sandbox_payout_confirmed=True),user,automatic=automatic)
    except HTTPException as exc:
        return {**visible(saved),'payout_error':str(exc.detail)}
    return {**visible(saved),'payout':payout}


def mount(app):
    router=APIRouter(prefix='/api/{portal}/projects/{pid}/assurance',tags=['Signed milestone payments'],dependencies=[Depends(n.portal_user)])

    @router.get('/ledger')
    def settlement_ledger(portal:Portal,pid:str,user=Depends(n.portal_user)):
        with db.engine().connect() as conn:
            current(conn,pid,user)
            entries=db.rows(conn,db.ledger,(db.ledger.c.project_id==pid)&db.ledger.c.external_id.startswith('step1:'))
            return {'unit':'hundredths_of_currency','sandbox':True,'entries':entries,
                    'limitations':'Gross recorded settlement, including verified full-refund postings. Excludes fees and taxes; uncertain partial refunds and historical records require provider reconciliation.'}

    @router.get('')
    def list_payments(portal:Portal,pid:str,user=Depends(n.portal_user)):
        from . import payouts
        with db.engine().connect() as conn:
            p,v=current(conn,pid,user)
            rows=db.rows(conn,db.milestone_payments,db.milestone_payments.c.project_id==pid)
            enabled={code.strip().upper() for code in os.getenv('PAYPAL_ENABLED_CURRENCIES','USD').split(',') if code.strip()}
            recipient=payouts.project_recipient(conn,pid)
            job=db.one(conn,db.jobs,db.jobs.c.id=='scan-'+pid)
            live=job and job['attempts']>0 and datetime.fromisoformat(job.get('lease_until') or job['run_at'])>datetime.now(timezone.utc)-timedelta(minutes=2)
            from . import funded_payments as funded, platform_paypal
            platform_mode=funded.enabled(p,v)
            from . import early_start
            early={}
            for milestone in at(v['payload'],'timeline.milestones',[]):
                try:
                    early[milestone['id']]={'available':True,'plan':early_start.plan(conn,p,v,milestone['id'])}
                except HTTPException as exc:early[milestone['id']]={'available':False,'reason':str(exc.detail)}
            return {'platform':{'configured':platform_paypal.configured(),'enabled':platform_mode,'terms_agreed':funded.agreed(p,v),'policy_text':funded.POLICY,'buyer_email':platform_paypal.config().get('PAYPAL_SANDBOX_BUYER_EMAIL',''),'seller':funded.seller_view(p,user)},'worker_status':'attention' if job and job.get('last_error') else 'running' if live else 'needs_worker','agreement_hash':v['digest'],'sandbox':True,'enabled_currencies':sorted(enabled),
                    'early_start':early,'operational_agreement':v['payload'],
                    'payout_recipient':recipient['email'] if recipient else None,'payout_setup_error':payouts.setup_error(conn),
                    'funding_blockers':[f for f in n.current_flags(p,signed_terms(conn,p)['payload'],conn=conn) if f['severity']=='BLOCKER'],
                    'funding_windows':{m['id']:(funded.window if platform_mode else checkout_window)(v['payload'],m,datetime.now(timezone.utc).date()) for m in at(v['payload'],'timeline.milestones',[])},
                    'payments':[dict(**visible(row),agreement_id=row['agreement_id'],payout=payouts.view_for_payment(conn,row['id'])) for row in rows]}

    @router.post('/start')
    async def start(portal:Portal,pid:str,data:Checkout,user=Depends(n.portal_user)):
        require(portal=='client','Only the client can start PayPal checkout.',403)
        from . import funded_payments
        with db.engine().connect() as conn:
            project,version=current(conn,pid,user)
            platform_mode=funded_payments.enabled(project,version)
        if platform_mode:return await funded_payments.start(pid,data,user)
        ensure_provider()
        with n.transaction() as conn:
            p,v=current(conn,pid,user,True)
            from .chat_terms import require_current
            require_current(conn,p,v['payload'],data.milestone_id)
            from .workflows import closed
            require(not closed(p,data.milestone_id),'Remaining work has been closed. Agree reopening before funding.')
            require(not p.get('amendment'),'Finish signing or reject the proposed amendment before new funding.')
            require(not p.get('withdrawal'),'New funding is stopped because the freelancer has withdrawn. Discuss remaining work in project chat.')
            require(automatic_release_agreed(v['payload'],n.amendments.protected_ids(conn,p)),'This signed version does not include automatic release after review. Both parties must sign updated terms before new funding.')
            from . import payouts
            blockers=[] if data.milestone_id.startswith('CO-') else [f for f in n.current_flags(p,signed_terms(conn,p)['payload'],conn=conn) if f['severity']=='BLOCKER']
            require(not blockers,{'message':'The signed schedule or price is incompatible with payment. Resolve it in an amendment before funding.','flags':blockers},422)
            require(at(v['payload'],'payment.due_trigger')=='milestone_approval','This payment integration supports milestone approval or agreed automatic acceptance only. Deferred payment terms need an amendment before checkout.')
            require(at(v['payload'],'payment.deposit_percent',0) in (None,0),'Upfront deposit collection is not implemented. Do not fund this agreement through the milestone flow until its deposit terms are resolved.')
            milestones=at(v['payload'],'timeline.milestones',[]) or []
            index=next((i for i,m in enumerate(milestones) if m.get('id')==data.milestone_id),None)
            require(index is not None,'Milestone not in the signed agreement.',404)
            m=milestones[index]
            require(type(m.get('amount_minor')) is int and m['amount_minor']>0,'Signed milestone amount is invalid.')
            require(m.get('currency')==at(v['payload'],'payment.currency'),'Signed milestone currency differs from the total.')
            funding_available({'mode':'sandbox','currency':m['currency']})
            for previous in ([] if data.milestone_id.startswith('CO-') else [m for m in milestones[:index] if not m['id'].startswith('CO-')]):
                prior=payment(conn,pid,previous['id'])
                require(prior and prior['status']=='captured','Finish the previous milestone before funding this one.')
                prior_payout=db.one(conn,db.milestone_payouts,db.milestone_payouts.c.payment_id==prior['id'])
                require(prior_payout and prior_payout['status']=='paid','Verify the previous milestone payout before funding the next one.')
            existing=payment(conn,pid,data.milestone_id,True)
            if existing:
                bound(existing,v,conn)
                require(False,'This milestone already has a payment attempt. Reconcile it before retrying.')
            due=date.fromisoformat(m['due_date'])
            reserve=reserve_days(v['payload'],m)
            window=checkout_window(v['payload'],m,datetime.now(timezone.utc).date())
            require(due>=datetime.now(timezone.utc).date(),'Milestone due date has passed. Agree an amendment before funding it.')
            require(reserve is not None and reserve<=29 and due+timedelta(days=reserve)<=datetime.now(timezone.utc).date()+timedelta(days=29),
                    f"Checkout for this milestone opens on {window.get('earliest_checkout','a valid schedule')}. Delivery is {m['due_date']}; {reserve} days are reserved afterward, through {window.get('capture_by','completion')}. Authorizing today would expire before that date.")
            from . import payouts
            recipient=payouts.project_recipient(conn,pid)
            require(recipient and data.recipient_email and recipient['email']==str(data.recipient_email).casefold(),'Configure and confirm the freelancer Sandbox receiving account before checkout.')
            ident=db.uid();row=dict(id=ident,project_id=pid,agreement_id=v['id'],milestone_id=data.milestone_id,
                amount_minor=m['amount_minor'],currency=m['currency'],status='creating',created_at=db.now(),payload={
                    'agreement_hash':v['digest'],'auto_release':bool(m.get('auto_accept')),'merchant_fingerprint':hashlib.sha256(os.getenv('PAYPAL_CLIENT_ID','').encode()).hexdigest(),
                    'payout_recipient':{'user_id':recipient['user_id'],'email':recipient['email']},'order_key':db.uid(),'authorize_key':db.uid(),'capture_key':db.uid()})
            conn.execute(insert(db.milestone_payments).values(**row))
            db.log(conn,'sandbox_payment_started',user['id'],pid,milestone_id=data.milestone_id,amount_minor=row['amount_minor'],currency=row['currency'],agreement_hash=v['digest'])
        try:
            result=await paypal.step1_create_order(row,pid)
            checkout=next((link['href'] for link in result.get('links',[]) if link.get('rel') in ('approve','payer-action') and link.get('href','').startswith('https://www.sandbox.paypal.com/')),None)
            require(result.get('id') and checkout,'PayPal returned no Sandbox approval link.',502)
        except HTTPException as exc:
            with n.transaction() as conn:
                saved=payment(conn,pid,data.milestone_id,True)
                save(conn,saved,'attention',attention=str(exc.detail))
            raise
        with n.transaction() as conn:
            saved=payment(conn,pid,data.milestone_id,True)
            require(saved['status']=='creating','Payment state changed during order creation.')
            save(conn,saved,'awaiting_approval',order_id=result['id'],checkout_url=checkout)
            return visible(saved)

    @router.post('/{milestone_id}/authorize')
    async def authorize(portal:Portal,pid:str,milestone_id:str,user=Depends(n.portal_user)):
        require(portal=='client','Only the client can confirm buyer approval.',403)
        from . import funded_payments
        with db.engine().connect() as conn:
            current(conn,pid,user);candidate=payment(conn,pid,milestone_id)
        if funded_payments.is_funded(candidate):return await funded_payments.collect(pid,milestone_id,user)
        with n.transaction() as conn:
            p,v=current(conn,pid,user,True);row=bound(payment(conn,pid,milestone_id,True),v,conn)
            from .chat_terms import require_current
            require_current(conn,p,v['payload'],milestone_id)
            require(not p.get('withdrawal'),'New authorization is stopped following freelancer withdrawal.')
            from .workflows import closed
            require(not closed(p,milestone_id),'Remaining work has been closed.')
            require(row['status']=='awaiting_approval','This checkout is not awaiting buyer approval.')
            save(conn,row,'authorizing')
        try:authorization=await paypal.step1_authorize(row)
        except HTTPException as exc:
            with n.transaction() as conn:
                saved=payment(conn,pid,milestone_id,True)
                save(conn,saved,'awaiting_approval' if exc.status_code==409 else 'attention',attention=str(exc.detail))
            raise
        with n.transaction() as conn:
            saved=payment(conn,pid,milestone_id,True)
            require(saved['status']=='authorizing','Payment state changed during authorization.')
            expires=authorization.get('expiration_time')
            if expires:
                expires=min(datetime.fromisoformat(expires.replace('Z','+00:00')),datetime.now(timezone.utc)+timedelta(days=29)).isoformat()
            milestone=next(m for m in at(v['payload'],'timeline.milestones') if m['id']==milestone_id)
            reserve=reserve_days(v['payload'],milestone)
            require(reserve is not None and reserve<=29,'Signed revision schedule does not fit the 29-day window.')
            horizon=date.fromisoformat(milestone['due_date'])+timedelta(days=reserve)
            expiry_date=datetime.fromisoformat(expires.replace('Z','+00:00')).date() if expires else datetime.now(timezone.utc).date()
            status='authorized' if expiry_date>=horizon else 'attention'
            save(conn,saved,status,authorization_id=authorization['id'],expires_at=expires,authorized_at=db.now(),attention=None if status=='authorized' else 'Authorization expires before the agreed review window. Do not start work; reconcile or reauthorize.')
            db.log(conn,'sandbox_authorization_confirmed',user['id'],pid,milestone_id=milestone_id,authorization_id=authorization['id'],status=status)
            n.notify(conn,p,'milestone_authorized','PayPal Sandbox authorization confirmed for milestone '+milestone_id)
            return visible(saved)

    @router.post('/deliver')
    def deliver(portal:Portal,pid:str,data:Delivery,user=Depends(n.portal_user)):
        require(portal=='freelancer','Only the freelancer can submit milestone delivery.',403)
        require(safe_url(data.url),'Use a valid HTTP(S) delivery link. FairFlow never fetches it.',422)
        with n.transaction() as conn:
            p,v=current(conn,pid,user,True);row=bound(payment(conn,pid,data.milestone_id,True),v,conn)
            from .chat_terms import require_current
            require_current(conn,p,v['payload'],data.milestone_id)
            require(not p.get('withdrawal') or row['payload'].get('delivery_url'),'Withdrawal stops new deliveries. Previously submitted work can still be revised and reviewed.')
            require(row['status'] in ('authorized','funded','revision_requested'),'Confirmed funding or a requested revision is required before delivery.')
            milestone=next(m for m in at(v['payload'],'timeline.milestones') if m['id']==data.milestone_id)
            review=milestone['review_days']
            require(type(review) is int and 1<=review<=5,'Agree 1-5 review days before delivery.')
            review_end=datetime.now(timezone.utc)+timedelta(days=review)
            from . import funded_payments
            expiry=row['payload'].get('expires_at')
            reserve=reserve_days(v['payload'],milestone,row['payload'].get('revision_counts'))
            require(funded_payments.coverage(row,reserve) if funded_payments.is_funded(row) else reserve is not None and reserve<=29 and expiry and datetime.fromisoformat(expiry.replace('Z','+00:00'))>=datetime.now(timezone.utc)+timedelta(days=reserve),
                    'Delivery is too late to preserve the agreed review, revision and capture buffer. Renew funding before work is submitted.')
            from .workflows import record_delivery
            record=record_delivery(conn,p,v,row,user,data)
            pending=record.pop('late_pending')
            save(conn,row,'late_pending' if pending else 'in_review',delivery_url=data.url,delivery_note=data.note.strip(),delivered_at=db.now(),review_deadline=None if pending else review_end.isoformat(),objection=None,review_reminded=False,**record)
            db.log(conn,'milestone_delivered',user['id'],pid,milestone_id=data.milestone_id,url=data.url,note=data.note.strip(),review_deadline=review_end.isoformat())
            n.notify(conn,p,'milestone_delivery','Milestone '+data.milestone_id+(' arrived after the signed grace period. Accept or reject the late timing in Resolution.' if pending else ' is ready for client review.'),'client')
            return visible(row)

    @router.post('/{milestone_id}/object')
    def object_to_delivery(portal:Portal,pid:str,milestone_id:str,data:Objection,user=Depends(n.portal_user)):
        require(portal=='client','Only the client can object to delivery.',403)
        require(len(data.reason.strip())>=10,'Explain the objection in at least 10 characters.',422)
        with n.transaction() as conn:
            p,v=current(conn,pid,user,True);row=bound(payment(conn,pid,milestone_id,True),v,conn)
            require(row['status']=='in_review','Only a delivery in review can be challenged.')
            if row['payload'].get('auto_release'):
                require(datetime.fromisoformat(row['payload']['review_deadline'])>datetime.now(timezone.utc),'The agreed review period has ended.')
            counts=dict(row['payload'].get('revision_counts',{}))
            if data.kind=='revision':
                deliverable=next((d for d in at(v['payload'],'scope.deliverables',[]) if d['id']==data.deliverable_id and d['milestone_id']==milestone_id),None)
                require(deliverable,'Choose a deliverable from this milestone.',422)
                used=counts.get(data.deliverable_id,0)
                require(used<deliverable['revision_rounds'],'Included revision rounds are exhausted. Discuss a change or raise a dispute.')
                counts[data.deliverable_id]=used+1
            objection={'kind':data.kind,'reason':data.reason.strip(),'deliverable_id':data.deliverable_id,'at':db.now()}
            if data.kind=='defect':
                from .workflows import enabled
                require(enabled(p),'Both participants must activate delivery protections before using the separate defect workflow.')
                require(any(d['id']==data.deliverable_id and d['milestone_id']==milestone_id for d in at(v['payload'],'scope.deliverables',[])),'Choose the affected deliverable.',422)
            correcting=data.kind in ('revision','defect')
            save(conn,row,'revision_requested' if correcting else 'disputed',objection=objection,revision_counts=counts,resolution=None,
                 revision_due_at=(datetime.now(timezone.utc)+timedelta(days=at(v['payload'],'revisions.turnaround_days',2))).isoformat() if correcting else None,
                 defect_requests=row['payload'].get('defect_requests',[])+([objection] if data.kind=='defect' else []))
            db.log(conn,'milestone_objection',user['id'],pid,milestone_id=milestone_id,objection=objection)
            n.notify(conn,p,'milestone_objection','Payment paused: '+data.reason.strip())
            return visible(row)

    @router.post('/{milestone_id}/approve-work')
    async def approve_work(portal:Portal,pid:str,milestone_id:str,user=Depends(n.portal_user)):
        require(portal=='client','Only the client can approve work.',403)
        from . import funded_payments
        with db.engine().connect() as conn:
            p,v=current(conn,pid,user);row=bound(payment(conn,pid,milestone_id),v,conn)
            require(funded_payments.is_funded(row),'Early approval is available for upfront-funded milestones.')
        return await funded_payments.release(pid,milestone_id,user,client_approved=True)

    @router.post('/{milestone_id}/approve')
    async def approve(portal:Portal,pid:str,milestone_id:str,data:Approval,user=Depends(n.portal_user)):
        require(portal=='client','Only the client can approve delivery and capture.',403)
        # Retain a clear compatibility response for old browser tabs. Only the
        # worker executes release after the signed review period has elapsed.
        with db.engine().connect() as conn:current(conn,pid,user)
        raise HTTPException(409,'Payment releases automatically after the agreed review period. Manual approval is no longer available.')

    @router.post('/{milestone_id}/resolve')
    def resolve(portal:Portal,pid:str,milestone_id:str,data:Resolution,user=Depends(n.portal_user)):
        require(len(data.reason.strip())>=10,'Describe the agreed resolution.',422)
        with n.transaction() as conn:
            p,v=current(conn,pid,user,True);row=bound(payment(conn,pid,milestone_id,True),v,conn)
            require(row['status']=='disputed' and row['payload'].get('objection',{}).get('kind')=='dispute','Only an internal delivery dispute can be resolved here.')
            resolution=row['payload'].get('resolution') or {'reason':data.reason.strip(),'roles':[]}
            require(resolution['reason']==data.reason.strip(),'Both parties must confirm the same resolution text.')
            roles=sorted(set(resolution['roles']+[portal]));resolution['roles']=roles
            fields={'resolution':resolution};state='disputed'
            if roles==['client','freelancer']:
                m=next(m for m in at(v['payload'],'timeline.milestones') if m['id']==milestone_id)
                deadline=datetime.now(timezone.utc)+timedelta(days=m['review_days'])
                from . import funded_payments
                expiry=row['payload'].get('expires_at')
                reserve=reserve_days(v['payload'],m,row['payload'].get('revision_counts'))
                require(funded_payments.coverage(row,reserve) if funded_payments.is_funded(row) else reserve is not None and reserve<=29 and expiry and datetime.fromisoformat(expiry.replace('Z','+00:00'))>=datetime.now(timezone.utc)+timedelta(days=reserve),'Authorization cannot cover all remaining revisions and review periods. Payment remains paused.')
                state='in_review';fields.update(objection=None,review_deadline=deadline.isoformat(),review_reminded=False)
            save(conn,row,state,**fields)
            db.log(conn,'delivery_resolution_confirmed',user['id'],pid,milestone_id=milestone_id,reason=data.reason.strip(),roles=roles)
            n.notify(conn,p,'delivery_resolution','Delivery resolution confirmed by '+portal+'. Status: '+state)
            return visible(row)

    @router.post('/{milestone_id}/reauthorize')
    async def reauthorize(portal:Portal,pid:str,milestone_id:str,user=Depends(n.portal_user)):
        require(portal=='client','Only the client can request reauthorization.',403)
        with n.transaction() as conn:
            p,v=current(conn,pid,user,True);row=bound(payment(conn,pid,milestone_id,True),v,conn)
            require(row['status'] in ('authorized','in_review','revision_requested','disputed'),'This payment cannot be reauthorized.')
            clock=datetime.now(timezone.utc);expires=row['payload'].get('expires_at')
            require(expires and datetime.fromisoformat(expires.replace('Z','+00:00'))>clock,'Authorization expired. A new funding agreement is required.')
            last=row['payload'].get('reauthorized_at') or row['payload'].get('authorized_at')
            require(last and datetime.fromisoformat(last.replace('Z','+00:00'))+timedelta(days=3)<=clock,'Reauthorization is available after the three-day honor period.')
            previous=row['status'];original_expiry=expires
            save(conn,row,'reauthorizing',reauthorize_key=db.uid())
        try:
            remote=await paypal.step1_reauthorize(row)
            require(remote.get('expiration_time'),'PayPal did not supply the renewed expiry.',502)
            expiry=min(datetime.fromisoformat(original_expiry.replace('Z','+00:00')),datetime.fromisoformat(remote['expiration_time'].replace('Z','+00:00'))).isoformat()
        except HTTPException as exc:
            with n.transaction() as conn:
                saved=payment(conn,pid,milestone_id,True);save(conn,saved,'attention',attention=str(exc.detail))
            raise
        with n.transaction() as conn:
            saved=payment(conn,pid,milestone_id,True)
            require(saved['status']=='reauthorizing','Reauthorization state changed.')
            save(conn,saved,previous,authorization_id=remote['id'],expires_at=expiry,reauthorized_at=db.now(),attention=None)
            db.log(conn,'authorization_renewed',user['id'],pid,milestone_id=milestone_id,old_authorization=row['payload']['authorization_id'],authorization_id=remote['id'])
            return visible(saved)

    @router.post('/{milestone_id}/reconcile')
    async def reconcile(portal:Portal,pid:str,milestone_id:str,user=Depends(n.portal_user)):
        require(portal=='client','Only the client can request provider reconciliation.',403)
        with db.engine().connect() as conn:
            p,v=current(conn,pid,user);row=bound(payment(conn,pid,milestone_id),v,conn)
            require(row['status']=='attention','Reconciliation is available for paused payments.')
        from . import funded_payments
        if funded_payments.is_funded(row):return await funded_payments.reconcile(pid,milestone_id,user)
        if row['payload'].get('order_id'):
            order=await paypal.call('GET','/v2/checkout/orders/'+row['payload']['order_id'])
            units=order.get('purchase_units') or []
            if len(units)==1 and units[0].get('custom_id')==row['id']:
                captures=units[0].get('payments',{}).get('captures',[])
                if len(captures)==1 and captures[0].get('status')=='COMPLETED' and paypal.step1_same_amount(captures[0].get('amount'),row['amount_minor'],row['currency']):
                    capture=captures[0]
                    with n.transaction() as conn:
                        p,v=current(conn,pid,user,True);saved=bound(payment(conn,pid,milestone_id,True),v,conn)
                        require(saved['status']=='attention','Payment state changed during reconciliation.')
                        save(conn,saved,'captured',capture_id=capture['id'],captured_at=capture.get('create_time') or db.now(),attention=None)
                        db.log(conn,'sandbox_capture_reconciled',user['id'],pid,capture_id=capture['id'],amount_minor=saved['amount_minor'])
                    result={'local':visible(saved),'provider_status':'COMPLETED','provider_id':capture['id'],'message':'PayPal confirmed the exact capture amount. The payment record is now reconciled.'}
                    recipient=saved['payload'].get('payout_recipient')
                    if recipient:
                        from . import payouts
                        try:
                            result['payout']=await payouts.send_payment(portal,pid,milestone_id,payouts.Consent(recipient_email=recipient['email'],sandbox_payout_confirmed=True),user)
                        except HTTPException as exc:result['payout_error']=str(exc.detail)
                    return result
        auth_id=row['payload'].get('authorization_id');capture_id=row['payload'].get('capture_id')
        require(auth_id or capture_id,'No provider transaction ID is available. Check the PayPal merchant dashboard before retrying.')
        provider=await (paypal.step1_get_capture(capture_id) if capture_id else paypal.step1_get_authorization(auth_id))
        return {'local':visible(row),'provider_status':provider.get('status'),'provider_id':provider.get('id'),
                'message':'Provider evidence was read. No money state was changed automatically; reconcile any uncertain capture before retrying.'}

    app.include_router(router)
