"""Explicit Sandbox transfers from the configured merchant to a freelancer.

Each capture can fund one payout intent. The recipient and amount are frozen
before submission; only item-level PayPal SUCCESS is reported as received.
"""
from typing import Literal
from datetime import datetime,timedelta,timezone
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,ConfigDict,EmailStr,Field
from sqlalchemy import insert,update
from . import db,paypal,negotiation as n,assurance as a

class Recipient(BaseModel):
    model_config=ConfigDict(extra='forbid')
    email:EmailStr=Field(max_length=127)
    sandbox_account_confirmed:Literal[True]

class Consent(BaseModel):
    model_config=ConfigDict(extra='forbid')
    recipient_email:EmailStr=Field(max_length=127)
    sandbox_payout_confirmed:Literal[True]

def destination(conn,user_id):
    return db.one(conn,db.sandbox_recipients,db.sandbox_recipients.c.user_id==user_id)

def project_recipient(conn,pid):
    member=db.one(conn,db.members,(db.members.c.project_id==pid)&(db.members.c.role=='freelancer'))
    return destination(conn,member['user_id']) if member else None


def setup_error(conn):
    for row in db.rows(conn,db.milestone_payouts,db.milestone_payouts.c.status=='attention'):
        error=row['payload'].get('attention','')
        if any(code in error for code in ('PAYOUT_NOT_AVAILABLE','AUTHORIZATION_ERROR','NOT_AUTHORIZED')):
            return 'PayPal has rejected freelancer payouts for this Sandbox sender. Client checkout and authorization are available for valid agreements. Capture and release need the sender Payouts setup fixed first. Check sender country and Payouts access, then recover the original payout.'
    return None

def visible(row):
    if not row:return None
    return {k:row[k] for k in ('id','recipient_email','amount_minor','currency','status','created_at')}|{
        k:row['payload'].get(k) for k in ('batch_id','item_id','transaction_id','provider_status','checked_at','attention')}

def view_for_payment(conn,payment_id):
    payment=db.one(conn,db.milestone_payments,db.milestone_payments.c.id==payment_id)
    if payment and payment['payload'].get('funding_model')=='paypal_funded_v1':
        return {'kind':'delayed_disbursement','id':payment['id'],'status':'paid' if payment['status']=='captured' else 'refunded' if payment['status']=='refunded' else 'pending',
                'recipient_email':payment['payload'].get('seller_email'),'amount_minor':payment['payload'].get('settlement_amount_minor',payment['amount_minor']),
                'currency':payment['currency'],'gross':True,'created_at':payment['created_at']}
    return visible(db.one(conn,db.milestone_payouts,db.milestone_payouts.c.payment_id==payment_id))

def project_completed(conn,project):
    version_id=project.get('active_agreement_id')
    if not version_id:return False
    version=db.one(conn,db.agreements,db.agreements.c.id==version_id)
    if version:
        from .workflows import effective_terms
        version=effective_terms(project,version)
    milestones=version['payload'].get('timeline',{}).get('milestones',[]) if version else []
    if not milestones:return False
    for milestone in milestones:
        payment=db.one(conn,db.milestone_payments,(db.milestone_payments.c.project_id==project['id'])&(db.milestone_payments.c.milestone_id==milestone['id']))
        if not payment or payment['status']!='captured':return False
        payout=db.one(conn,db.milestone_payouts,db.milestone_payouts.c.payment_id==payment['id'])
        from .funded_payments import is_funded
        if not is_funded(payment) and (not payout or payout['status']!='paid'):return False
    return True

def save(conn,row,status,**fields):
    if status!=row['status']:fields['state_changed_at']=db.now()
    row.update(status=status,payload={**row['payload'],**fields})
    conn.execute(update(db.milestone_payouts).where(db.milestone_payouts.c.id==row['id']).values(status=status,payload=row['payload']))

def verified_state(row,result):
    header=result.get('batch_header',{})
    a.require(header.get('payout_batch_id')==row['payload']['batch_id'],'Payout batch ID differs from the saved request.',502)
    a.require(header.get('sender_batch_header',{}).get('sender_batch_id')==row['id'],'Payout reference differs from the saved request.',502)
    items=result.get('items',[])
    if not items:
        return ('failed' if header.get('batch_status') in ('DENIED','CANCELED') else 'pending'),{'provider_status':header.get('batch_status'),'checked_at':db.now()}
    a.require(len(items)==1,'Expected exactly one payout recipient.',502)
    item=items[0];intent=item.get('payout_item',{});amount=intent.get('amount',{})
    a.require(intent.get('sender_item_id')==row['id'] and intent.get('recipient_type')=='EMAIL' and intent.get('receiver','').casefold()==row['recipient_email'].casefold(),
              'PayPal payout recipient or item reference differs from the saved request.',502)
    a.require(paypal.step1_same_amount({'currency_code':amount.get('currency'),'value':amount.get('value')},row['amount_minor'],row['currency']),
              'PayPal payout amount differs from the captured milestone.',502)
    provider=item.get('transaction_status')
    states={'SUCCESS':'paid','FAILED':'failed','PENDING':'pending','UNCLAIMED':'unclaimed','RETURNED':'returned','ONHOLD':'on_hold','BLOCKED':'blocked','REFUNDED':'refunded','REVERSED':'reversed'}
    a.require(provider in states and item.get('payout_item_id'),'PayPal returned an unrecognized payout status.',502)
    return states[provider],{'provider_status':provider,'item_id':item['payout_item_id'],'transaction_id':item.get('transaction_id'),'checked_at':db.now(),'attention':None}

async def send_payment(portal,pid,milestone_id,data,user,*,automatic=False):
    a.require(portal=='client','Only the client can release a captured milestone to the freelancer.',403)
    a.ensure_provider()
    with n.transaction() as conn:
        p,v=a.current(conn,pid,user,True);payment=a.bound(a.payment(conn,pid,milestone_id,True),v,conn)
        from .funded_payments import is_funded
        a.require(not is_funded(payment),'This payment uses seller disbursement; a separate Payouts transfer is blocked.')
        a.require(payment['status']=='captured' and payment['payload'].get('capture_id'),'PayPal must confirm capture before freelancer payout.')
        recipient=payment['payload'].get('payout_recipient') or project_recipient(conn,pid)
        a.require(recipient,'The freelancer must save their PayPal Sandbox recipient first.')
        a.require(recipient['email']==str(data.recipient_email).casefold(),'The freelancer recipient changed. Review the latest email before paying.')
        a.require(not db.one(conn,db.milestone_payouts,db.milestone_payouts.c.payment_id==payment['id']),
                  'This milestone already has a payout intent. Check its status; a second payout is blocked.')
        row={'id':db.uid(),'payment_id':payment['id'],'recipient_user_id':recipient['user_id'],'recipient_email':recipient['email'],
             'amount_minor':a.captured_amount(payment),'currency':payment['currency'],'status':'verifying','created_at':db.now(),
             'payload':{'capture_id':payment['payload']['capture_id'],'agreement_hash':v['digest'],'sender_fingerprint':paypal.payout_sender()}}
        conn.execute(insert(db.milestone_payouts).values(**row))
        db.log(conn,'sandbox_payout_requested',None if automatic else user['id'],pid,payout_id=row['id'],recipient_email=row['recipient_email'],amount_minor=row['amount_minor'],currency=row['currency'])
    try:
        capture=await paypal.step1_get_capture(row['payload']['capture_id'])
        a.require(capture.get('id')==row['payload']['capture_id'] and capture.get('status')=='COMPLETED' and paypal.step1_same_amount(capture.get('amount'),row['amount_minor'],row['currency']),
                  'PayPal capture is no longer completed or its amount differs. No payout was sent.',502)
        with n.transaction() as conn:
            from . import step1_ledger
            step1_ledger.capture(conn,payment)
            save(conn,row,'submitting')
        result=await paypal.step1_create_payout(row)
        batch=result.get('batch_header',{}).get('payout_batch_id')
        a.require(batch,'PayPal did not return a payout batch ID. Reconcile in the merchant dashboard before any retry.',502)
        with n.transaction() as conn:save(conn,row,'pending',batch_id=batch,provider_status=result.get('batch_header',{}).get('batch_status'))
        # A successful batch submission is not proof that the recipient received it.
        return visible(row)
    except HTTPException as exc:
        with n.transaction() as conn:save(conn,row,'attention',attention=str(exc.detail))
        raise


async def refresh_payment(pid,milestone_id,user,*,automatic=False):
    with db.engine().connect() as conn:
        p,v=a.current(conn,pid,user);payment=a.bound(a.payment(conn,pid,milestone_id),v,conn)
        row=db.one(conn,db.milestone_payouts,db.milestone_payouts.c.payment_id==payment['id'])
        a.require(row and row['payload'].get('batch_id'),'No payout batch ID is available. Reconcile in the merchant dashboard.')
    paypal.check_payout_sender(row)
    result=await paypal.step1_get_payout(row['payload']['batch_id'])
    status,fields=verified_state(row,result)
    with n.transaction() as conn:
        n.project(conn,pid,user,True)
        saved=db.one(conn,db.milestone_payouts,db.milestone_payouts.c.id==row['id'])
        # An earlier concurrent lookup cannot overwrite a newer one.
        a.require(saved['payload'].get('checked_at')==row['payload'].get('checked_at'),'Another status check completed. Refresh the payment.')
        save(conn,saved,status,**fields)
        from . import step1_ledger
        step1_ledger.payout(conn,payment,saved,status)
        if status!=row['status']:
            db.log(conn,'sandbox_payout_status_verified',None if automatic else user['id'],pid,payout_id=row['id'],status=status,provider_status=fields['provider_status'])
            n.notify(conn,p,'freelancer_payout','Freelancer Sandbox payout status: '+status)
            if status in ('returned','refunded','reversed','blocked','failed'):
                from .workflows import payment_case
                payment_case(conn,pid,milestone_id,'PayPal payout '+saved['id']+' status is '+status+'. Resolve the original transfer before any replacement.',user)
        return visible(saved)


async def recover_payment(pid,milestone_id,data,user):
    """Resume the original intent within PayPal's duplicate-protection window."""
    a.require(user['role']=='client','Only the client can recover the approved payout.',403)
    with n.transaction() as conn:
        p,v=a.current(conn,pid,user,True);payment=a.bound(a.payment(conn,pid,milestone_id,True),v,conn)
        row=db.one(conn,db.milestone_payouts,db.milestone_payouts.c.payment_id==payment['id'])
        a.require(payment['status']=='captured' and row,'A confirmed capture and original payout intent are required.')
        a.require(row['status']=='attention' and not row['payload'].get('batch_id'),'Check the existing payout status; this intent cannot be resubmitted.')
        a.require(row['recipient_email']==str(data.recipient_email).casefold(),'Confirm the original frozen recipient; recovery cannot redirect money.')
        created=datetime.fromisoformat(row['created_at'])
        a.require(created<=datetime.now(timezone.utc)<created+timedelta(days=29),'The safe payout recovery window has ended. Investigate with PayPal; a new transfer is blocked.')
        a.require(row['amount_minor']==a.captured_amount(payment) and row['currency']==payment['currency'] and row['payload']['capture_id']==payment['payload'].get('capture_id'),'Payout evidence differs from the captured milestone.')
        paypal.check_payout_sender(row)
        save(conn,row,'recovering',sender_fingerprint=paypal.payout_sender())
        db.log(conn,'sandbox_payout_recovery_started',user['id'],pid,payout_id=row['id'])
    try:
        capture=await paypal.step1_get_capture(row['payload']['capture_id'])
        a.require(capture.get('id')==row['payload']['capture_id'] and capture.get('status')=='COMPLETED' and paypal.step1_same_amount(capture.get('amount'),row['amount_minor'],row['currency']),'Capture is no longer completed for the exact amount. No payout recovery was sent.',502)
        result=await paypal.step1_create_payout(row)
        header=result.get('batch_header',{})
        a.require(header.get('payout_batch_id'),'No payout batch ID returned. Keep the original intent for investigation.',502)
        a.require(header.get('sender_batch_header',{}).get('sender_batch_id',row['id'])==row['id'],'Recovered payout reference differs from the original intent.',502)
        with n.transaction() as conn:
            n.project(conn,pid,user,True)
            saved=db.one(conn,db.milestone_payouts,db.milestone_payouts.c.id==row['id'])
            a.require(saved['status']=='recovering','Payout state changed during recovery.')
            from . import step1_ledger
            step1_ledger.capture(conn,payment)
            save(conn,saved,'pending',batch_id=header['payout_batch_id'],provider_status=header.get('batch_status'),attention=None)
            db.log(conn,'sandbox_payout_recovered',user['id'],pid,payout_id=row['id'],batch_id=header['payout_batch_id'])
            return visible(saved)
    except HTTPException as exc:
        with n.transaction() as conn:
            n.project(conn,pid,user,True)
            saved=db.one(conn,db.milestone_payouts,db.milestone_payouts.c.id==row['id'])
            if saved['status']=='recovering':save(conn,saved,'attention',attention=str(exc.detail))
        raise


def mount(app):
    router=APIRouter(prefix='/api/{portal}',tags=['Freelancer Sandbox payouts'],dependencies=[Depends(n.portal_user)])

    @router.get('/payout-recipient')
    def own_recipient(portal:n.Portal,user=Depends(n.portal_user)):
        a.require(portal=='freelancer','Only freelancers configure a payout recipient.',403)
        with db.engine().connect() as conn:
            row=destination(conn,user['id'])
            return {'email':row['email'] if row else None,'sandbox':True}

    @router.put('/payout-recipient')
    def set_recipient(portal:n.Portal,data:Recipient,user=Depends(n.portal_user)):
        a.require(portal=='freelancer','Only freelancers configure a payout recipient.',403)
        with n.transaction() as conn:
            # Serialize destination changes for this user, independently of any project.
            from sqlalchemy import select
            conn.execute(select(db.users.c.id).where(db.users.c.id==user['id']).with_for_update()).first()
            row=destination(conn,user['id']);email=str(data.email).casefold()
            if row:conn.execute(update(db.sandbox_recipients).where(db.sandbox_recipients.c.id==row['id']).values(email=email,updated_at=db.now()))
            else:conn.execute(insert(db.sandbox_recipients).values(id=db.uid(),user_id=user['id'],email=email,updated_at=db.now()))
            db.log(conn,'sandbox_payout_recipient_updated',user['id'],recipient_email=email)
        return {'email':email,'sandbox':True}

    @router.post('/projects/{pid}/assurance/{milestone_id}/payout')
    async def send(portal:n.Portal,pid:str,milestone_id:str,data:Consent,user=Depends(n.portal_user)):
        return await send_payment(portal,pid,milestone_id,data,user)

    @router.post('/projects/{pid}/assurance/{milestone_id}/payout-status')
    async def refresh(portal:n.Portal,pid:str,milestone_id:str,user=Depends(n.portal_user)):
        return await refresh_payment(pid,milestone_id,user)

    @router.post('/projects/{pid}/assurance/{milestone_id}/payout-recover')
    async def recover(portal:n.Portal,pid:str,milestone_id:str,data:Consent,user=Depends(n.portal_user)):
        return await recover_payment(pid,milestone_id,data,user)

    app.include_router(router)
