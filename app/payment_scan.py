"""Durable Step 1 scans. Signed consent, not AI or a session, authorizes timers."""
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from . import db, negotiation as n, assurance as a, payouts, paypal


async def scan(pid, *, now=None):
    clock=now or datetime.now(timezone.utc)
    from . import workflows
    await workflows.scan(pid,now=clock)
    with db.engine().connect() as conn:
        member=db.one(conn,db.members,(db.members.c.project_id==pid)&(db.members.c.role=='client'))
        if not member:return
        # Reuse membership checks; scheduled events are audited with actor=None.
        user=db.one(conn,db.users,db.users.c.id==member['user_id'])
        p,v=a.current(conn,pid,user)
        rows=db.rows(conn,db.milestone_payments,db.milestone_payments.c.project_id==pid)
    from . import funded_payments
    await funded_payments.scan(pid,user,rows,clock)
    for row in rows:
        if funded_payments.is_funded(row):continue
        mid=row['milestone_id'];payload=row['payload']
        if payload.get('merchant_retired'):continue
        if row['status'] in ('creating','authorizing','capturing','reauthorizing','replacing'):
            changed=payload.get('state_changed_at',row['created_at'])
            if datetime.fromisoformat(changed)<clock-timedelta(minutes=5):
                with n.transaction() as conn:
                    saved=a.payment(conn,pid,mid,True)
                    if saved['status']==row['status']:
                        a.save(conn,saved,'attention',attention='Interrupted provider operation. Check provider status before retrying.')
                        db.log(conn,'interrupted_payment_operation',None,pid,milestone_id=mid,operation=row['status'])
            continue
        expiry=payload.get('expires_at')
        if row['status'] in ('authorized','in_review','revision_requested','disputed','late_pending') and expiry:
            expires=datetime.fromisoformat(expiry.replace('Z','+00:00'))
            with n.transaction() as conn:
                saved=a.payment(conn,pid,mid,True)
                if saved['status']!=row['status']:continue
                if expires<=clock:
                    a.save(conn,saved,'expired',attention='Authorization expired; work and automatic release are paused.')
                    db.log(conn,'sandbox_authorization_expired',None,pid,milestone_id=mid)
                    n.notify(conn,p,'authorization_expired','Milestone '+mid+' authorization expired. Payment is paused.')
                    continue
                if expires<=clock+timedelta(days=2) and not payload.get('expiry_reminded'):
                    a.save(conn,saved,saved['status'],expiry_reminded=True)
                    n.notify(conn,p,'authorization_expiring','Milestone '+mid+' authorization expires within 48 hours. Review its funding status.')
        if row['status']=='in_review' and payload.get('review_deadline'):
            deadline=datetime.fromisoformat(payload['review_deadline'])
            if deadline>clock and deadline<=clock+timedelta(days=1):
                with n.transaction() as conn:
                    saved=a.payment(conn,pid,mid,True)
                    if saved['status']=='in_review' and not saved['payload'].get('review_reminded'):
                        a.save(conn,saved,'in_review',review_reminded=True)
                        n.notify(conn,p,'review_reminder','Milestone '+mid+' review ends within 24 hours. Automatic release: '+('enabled' if payload.get('auto_release') else 'not agreed')+'.','client')
            if deadline<=clock and payload.get('auto_release'):
                try:await a.release_payment(pid,mid,user,automatic=True,now=clock)
                except HTTPException as exc:
                    # Concurrent approval/objection wins atomically. Provider failures
                    # are persisted as attention by release_payment, never retried here.
                    if exc.status_code not in (409,502,503):raise
        if row['status']=='captured' and payload.get('capture_id'):
            with db.engine().connect() as conn:
                payout=db.one(conn,db.milestone_payouts,db.milestone_payouts.c.payment_id==row['id'])
            if not payout and payload.get('release_reason') and payload.get('payout_recipient'):
                try:
                    await payouts.send_payment('client',pid,mid,payouts.Consent(recipient_email=payload['payout_recipient']['email'],sandbox_payout_confirmed=True),user,automatic=True)
                except HTTPException as exc:
                    if exc.status_code not in (409,502,503):raise
            elif payout and payout['payload'].get('batch_id'):
                checked=payout['payload'].get('checked_at')
                if not checked or datetime.fromisoformat(checked)<clock-timedelta(minutes=5):
                    await payouts.refresh_payment(pid,mid,user,automatic=True)
            elif payout and payout['status'] in ('verifying','submitting','recovering'):
                changed=payout['payload'].get('state_changed_at',payout['created_at'])
                if datetime.fromisoformat(changed)<clock-timedelta(minutes=5):
                    with n.transaction() as conn:
                        saved=db.one(conn,db.milestone_payouts,db.milestone_payouts.c.id==payout['id'])
                        if saved['status']==payout['status']:
                            payouts.save(conn,saved,'attention',attention='Interrupted payout. Reconcile in the merchant dashboard; no new transfer was submitted.')
            # Capture reversals/refunds must not continue to look funded.
            checked=payload.get('capture_checked_at')
            if not checked or datetime.fromisoformat(checked)<clock-timedelta(minutes=5):
                remote=await paypal.step1_get_capture(payload['capture_id'])
                a.require(remote.get('id')==payload['capture_id'] and paypal.step1_same_amount(remote.get('amount'),a.captured_amount(row),row['currency']),'Capture reconciliation reference/amount mismatch.',502)
                state=remote.get('status')
                with n.transaction() as conn:
                    saved=a.payment(conn,pid,mid,True)
                    if saved['status']!='captured':continue
                    if state in ('REFUNDED','PARTIALLY_REFUNDED','REVERSED','DECLINED','FAILED'):
                        a.save(conn,saved,'attention',capture_checked_at=clock.isoformat(),attention='PayPal capture status is '+state+'. Payment needs review.')
                        db.log(conn,'capture_external_change',None,pid,milestone_id=mid,provider_status=state)
                        n.notify(conn,p,'payment_attention','PayPal capture changed to '+state+' for milestone '+mid+'.')
                        workflows.payment_case(conn,pid,mid,'PayPal capture '+payload['capture_id']+' changed to '+state+'. Review provider evidence; no replacement transfer or automatic clawback is authorized.',user)
                        if state=='REFUNDED':
                            from . import step1_ledger
                            step1_ledger.post(conn,{**saved,'amount_minor':a.captured_amount(saved)},'external_full_refund',payload['capture_id'],[('provider_receivable',-1),('project_liability',1)])
                    else:a.save(conn,saved,'captured',capture_checked_at=clock.isoformat())
