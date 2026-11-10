"""Upfront-funded milestones. Legacy rows never use this adapter.

Capture, seller disbursement and refunds are separate persisted operations.
Unknown writes are reconciled by reads, never replaced by a fresh operation.
"""
import copy
import os
from datetime import datetime, timedelta, timezone, date
import re
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import insert,select,update
from . import db, negotiation as n, platform_paypal as provider, paypal
from .agreement_schema import at
from .agreement_validation import digest

MODEL = 'paypal_funded_v1'
POLICY = ('Each new milestone is collected before work starts using PayPal delayed disbursement. '
          'Only a verified completed capture with delayed disbursement marks work funded. '
          'Delivery starts the signed 1-5 day review; without a timely objection FairFlow requests seller disbursement automatically. '
          'PayPal deducts seller processing fees, so the agreed amount is gross. '
          'Funding, delivery, corrections and review must fit within 26 days; PayPal automatically disburses after 28 days, even if an internal case is unresolved. '
          'An internal dispute cannot extend that limit. Both parties receive deadline warnings and must resolve or agree a refund before it. '
          'Accepted withdrawal or cancellation refunds unsubmitted funded work unless an open partial-work claim requires resolution. '
          'Unsubmitted work is refunded by day 26 after funding unless a partial-work claim is open. Previously submitted work retains its review rules. Provider reversals, disputes and account restrictions can still affect payment. '
          'This is Sandbox delayed disbursement, not escrow or a 100% payment guarantee. Existing payment attempts retain their original terms.')


def is_funded(row):
    return bool(row and row['payload'].get('funding_model') == MODEL)


def enabled(p, v):
    # Operator-selected default for every new attempt. Existing rows dispatch
    # by their persisted funding_model and never switch credential families.
    return os.getenv('PAYPAL_PAYMENT_FLOW')=='platform' or provider.configured() or agreed(p,v)


def agreed(p,v,mid=None):
    if p.get('funding_policy',{}).get('version')==MODEL:return True
    if at(v['payload'],'payment.funding_model')==MODEL and at(v['payload'],'payment.funding_rules')==POLICY:return True
    return bool(mid and any('CO-'+r['id']==mid and r['kind']=='change' and r['status']=='applied'
        and r.get('funding_model')==MODEL and r.get('funding_rules')==POLICY and {vote['role'] for vote in r['votes']}=={'client','freelancer'}
        for r in p.get('workflow_requests',[])))


def prepare_draft(conn,p,payload):
    """Include the default funding terms in unsigned proposals, never versions."""
    if not provider.configured() and os.getenv('PAYPAL_PAYMENT_FLOW')!='platform':return
    payload.setdefault('payment',{}).update(funding_model=MODEL,funding_rules=POLICY)
    protected={row['milestone_id'] for row in db.rows(conn,db.milestone_payments,db.milestone_payments.c.project_id==p['id'])}
    for milestone in at(payload,'timeline.milestones',[]):
        if milestone.get('id') not in protected:
            milestone['auto_accept']=True
            milestone['auto_accept_days']=milestone.get('review_days',3)


def assert_app(row):
    n.require(row['payload'].get('platform_fingerprint') == provider.fingerprint(), 'Restore the original platform credentials before processing this payment.')


def window(agreement, milestone, today):
    from .payment_policy import reserve_days
    reserve = reserve_days(agreement, milestone)
    try:
        due = date.fromisoformat(milestone['due_date'])
        end = due + timedelta(days=reserve)
        return dict(available=0 <= (due-today).days and (end-today).days <= 26,
                    earliest_checkout=str(end-timedelta(days=26)), delivery_date=str(due), reserve_days=reserve, capture_by=str(end))
    except (ValueError, TypeError, KeyError):
        return dict(available=False)


def inherit_seller(conn,p):
    """Reuse only this freelancer's verified connection to the current platform."""
    if p.get('platform_seller'):return
    member=db.one(conn,db.members,(db.members.c.project_id==p['id'])&(db.members.c.role=='freelancer'))
    if not member or not provider.configured():return
    uid=member['user_id'];fingerprint=provider.fingerprint()
    profile=db.one(conn,db.freelancer_profiles,db.freelancer_profiles.c.user_id==uid)
    candidate=(profile or {}).get('payload',{}).get('platform_seller',{})
    if candidate.get('status')!='verified' or candidate.get('fingerprint')!=fingerprint:
        candidates=[]
        for row in db.rows(conn,db.projects):
            seller=row['payload'].get('platform_seller',{})
            if seller.get('user_id')==uid and seller.get('status')=='verified' and seller.get('fingerprint')==fingerprint:
                if db.one(conn,db.members,(db.members.c.project_id==row['id'])&(db.members.c.user_id==uid)&(db.members.c.role=='freelancer')):candidates.append(seller)
        candidate=max(candidates,key=lambda x:x.get('verified_at') or '') if candidates else {}
    if candidate.get('status')=='verified' and candidate.get('user_id')==uid and candidate.get('fingerprint')==fingerprint:p['platform_seller']=copy.deepcopy(candidate)


def seller_view(p,user=None):
    seller = p.get('platform_seller', {})
    view={k: seller.get(k) for k in ('email', 'merchant_id', 'status', 'verified_at', 'attention')}
    if user and user.get('role')=='freelancer' and seller.get('user_id')==user['id']:view['action_url']=seller.get('action_url')
    return view


async def verify_seller(pid, user):
    from . import assurance as a
    with db.engine().connect() as conn:
        p, _ = a.current(conn, pid, user)
        seller = copy.deepcopy(p.get('platform_seller', {}))
    n.require(seller.get('tracking_id') and seller.get('partner_id'), 'Create the seller connection first.')
    n.require(seller.get('fingerprint') == provider.fingerprint(), 'Seller onboarding belongs to a different platform app.')
    lookup = await provider.call('GET', f"/v1/customer/partners/{seller['partner_id']}/merchant-integrations?tracking_id={seller['tracking_id']}")
    merchant = lookup.get('merchant_id')
    n.require(merchant and re.fullmatch(r'[A-Za-z0-9]+', merchant), 'Seller onboarding has not completed.')
    n.require(merchant != seller['partner_id'], 'The platform account was connected as its own seller. Open the connection in a private window and sign in with a separate freelancer Business Sandbox account.')
    result = await provider.call('GET', f"/v1/customer/partners/{seller['partner_id']}/merchant-integrations/{merchant}")
    n.require(result.get('merchant_id') == merchant and result.get('payments_receivable') is True and result.get('primary_email_confirmed') is True,
              'The seller must confirm its email and be able to receive payments.')
    ident = provider.config()['PAYPAL_PLATFORM_CLIENT_ID']
    permission = any(x.get('partner_client_id') == ident and provider.DELAY_SCOPE in x.get('scopes', [])
                     for integration in result.get('oauth_integrations', []) for x in integration.get('oauth_third_party', []))
    n.require(permission, 'Seller has not granted delayed-disbursement permission to this platform app.')
    with n.transaction() as conn:
        p, revision = n.project(conn, pid, user, True)
        n.require(p.get('platform_seller', {}).get('tracking_id') == seller['tracking_id'], 'Seller connection changed; refresh.')
        p['platform_seller'].update(merchant_id=merchant, status='verified', verified_at=db.now(), attention=None)
        profile=conn.execute(select(db.freelancer_profiles).where(db.freelancer_profiles.c.user_id==p['platform_seller']['user_id']).with_for_update()).mappings().first()
        if profile:
            profile_data=copy.deepcopy(profile['payload']);profile_data['platform_seller']=copy.deepcopy(p['platform_seller'])
            conn.execute(update(db.freelancer_profiles).where(db.freelancer_profiles.c.user_id==p['platform_seller']['user_id']).values(payload=profile_data))
        n.write(conn, p, revision)
        db.log(conn,'platform_seller_verified',user['id'],pid,merchant_id=merchant)
        return seller_view(p,user)


def unit(row, order):
    units = order.get('purchase_units', [])
    n.require(order.get('id') == row['payload']['order_id'] and order.get('intent') == 'CAPTURE' and len(units) == 1,
              'Platform order identity differs from saved funding.', 502)
    item = units[0]
    n.require(item.get('custom_id') == row['id'] and item.get('payee', {}).get('merchant_id') == row['payload']['seller_merchant_id']
              and paypal.step1_same_amount(item.get('amount'), row['amount_minor'], row['currency']), 'Platform seller, amount or milestone reference mismatch.', 502)
    return item


def capture(row, order):
    item = unit(row, order)
    captures = item.get('payments', {}).get('captures', [])
    n.require(len(captures) == 1, 'Expected one exact capture; reconcile the platform order.', 502)
    result = captures[0]
    n.require(result.get('id') and paypal.step1_same_amount(result.get('amount'), row['amount_minor'], row['currency']), 'Platform capture amount differs.', 502)
    return result


def hold_deadline(result):
    stamp = result.get('create_time')
    n.require(stamp, 'PayPal did not provide capture time; do not start work.', 502)
    try:
        instant=datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        n.require(instant.tzinfo is not None,'Capture timestamp must include a timezone.',502)
        return (instant + timedelta(days=28)).isoformat()
    except (ValueError,TypeError):raise HTTPException(502,'Invalid provider capture timestamp; reconcile before starting work.')


async def start(pid, data, user):
    from . import assurance as a, workflows as w
    with db.engine().connect() as conn:
        project,version=a.current(conn,pid,user)
        from .chat_terms import require_current
        require_current(conn,project,version['payload'],data.milestone_id)
        n.require(agreed(project,version,data.milestone_id), 'Use the updated agreement with upfront funding before collection.')
    await verify_seller(pid, user)
    with n.transaction() as conn:
        p, v = a.current(conn, pid, user, True)
        require_current(conn,p,v['payload'],data.milestone_id)
        n.require(agreed(p,v,data.milestone_id), 'The active agreement must include upfront funding before collection. Update the normal agreement or amendment; no separate funding setup approval is needed.')
        n.require(not p.get('withdrawal') and not p.get('amendment') and not w.closed(p, data.milestone_id), 'Remaining work is closed or being amended.')
        milestone = w.milestone(v, data.milestone_id)
        n.require(milestone.get('auto_accept') is True and milestone.get('auto_accept_days') == milestone.get('review_days'), 'Agree automatic release and a review period first.')
        blockers=[] if data.milestone_id.startswith('CO-') else [f for f in n.current_flags(p,v['payload'],conn=conn) if f['severity']=='BLOCKER']
        n.require(not blockers,'Resolve signed agreement issues before collecting funding.',422)
        n.require(milestone['amount_minor'] > 0 and milestone['currency'] == at(v['payload'], 'payment.currency'), 'Signed amount or currency is invalid.')
        n.require(at(v['payload'], 'payment.deposit_percent', 0) in (0, None), 'Resolve separate deposit terms before milestone funding.')
        n.require(window(v['payload'], milestone, date.today())['available'], 'Delivery plus all revisions, review and buffer must fit within 26 days of funding. Shorten or split this milestone.')
        existing = a.payment(conn, pid, data.milestone_id, True)
        n.require(not existing, 'This milestone already has a payment attempt; reconcile it instead of charging again.')
        seller = p['platform_seller']
        n.require(seller.get('status') == 'verified', 'Verify the separate seller account before funding.')
        row = dict(id=db.uid(), project_id=pid, agreement_id=v['id'], milestone_id=data.milestone_id,
                   amount_minor=milestone['amount_minor'], currency=milestone['currency'], status='creating', created_at=db.now(), payload={
                       'funding_model': MODEL, 'platform_fingerprint': provider.fingerprint(), 'seller_merchant_id': seller['merchant_id'],
                       'seller_email': seller['email'], 'auto_release': True, 'agreement_hash': v['digest'],
                       'effective_agreement_hash':digest(v['payload']),
                       'funded_milestone_terms':copy.deepcopy(milestone),
                       'order_key': db.uid(), 'capture_key': db.uid(), 'release_key': db.uid(), 'state_changed_at': db.now()})
        conn.execute(insert(db.milestone_payments).values(**row))
        db.log(conn, 'platform_funding_started', user['id'], pid, milestone_id=data.milestone_id)
    import os
    base = os.getenv('APP_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
    body = {'intent': 'CAPTURE', 'purchase_units': [{'custom_id': row['id'], 'invoice_id': row['payload']['order_key'],
            'amount': paypal.step1_amount(row['amount_minor'], row['currency']), 'payee': {'merchant_id': seller['merchant_id']},
            'payment_instruction': {'disbursement_mode': 'DELAYED'}}],
            'application_context': {'return_url': base + '/client/projects/' + pid + '?tab=Payments&paypal_return=1',
                                    'cancel_url': base + '/client/projects/' + pid + '?tab=Payments', 'shipping_preference': 'NO_SHIPPING', 'user_action': 'PAY_NOW'}}
    try:
        result = await provider.call('POST', '/v2/checkout/orders', body, row['payload']['order_key'], seller=seller['merchant_id'])
        checkout = next((x['href'] for x in result.get('links', []) if x['rel'] in ('approve', 'payer-action') and urlparse(x['href']).hostname == 'www.sandbox.paypal.com' and urlparse(x['href']).scheme == 'https'), None)
        n.require(result.get('id') and checkout, 'PayPal did not return a Sandbox checkout link.', 502)
    except HTTPException as error:
        with n.transaction() as conn:
            saved = a.payment(conn, pid, data.milestone_id, True)
            a.save(conn, saved, 'attention', attention=str(error.detail), interrupted_operation='create')
        raise
    with n.transaction() as conn:
        saved = a.payment(conn, pid, data.milestone_id, True)
        n.require(saved['status'] == 'creating', 'Funding state changed.')
        a.save(conn, saved, 'awaiting_approval', order_id=result['id'], checkout_url=checkout, attention=None)
        return a.visible(saved)


def record_capture(conn, row, result):
    from . import assurance as a, step1_ledger
    n.require(result.get('status') == 'COMPLETED' and result.get('disbursement_mode') == 'DELAYED', 'Capture is not confirmed completed with delayed disbursement. Do not start work.', 502)
    deadline = hold_deadline(result)
    a.save(conn, row, 'funded', capture_id=result['id'], captured_at=result['create_time'], collection_verified_at=db.now(), provider_disburse_by=deadline, provider_disbursement_mode=result['disbursement_mode'], provider_checked_at=db.now(), attention=None)
    step1_ledger.capture(conn, row)


def buyer_issue(order):
    expected=provider.config().get('PAYPAL_SANDBOX_BUYER_EMAIL','').strip().casefold()
    buyer=(order.get('payer',{}).get('email_address') or order.get('payment_source',{}).get('paypal',{}).get('email_address') or '').strip().casefold()
    if expected and buyer!=expected:
        return 'Wrong PayPal Sandbox buyer account. Sign out of PayPal, open this existing checkout again, and approve using '+expected+'. No money was collected.'
    return None


async def collect(pid, mid, user):
    from . import assurance as a
    with n.transaction() as conn:
        p, v = a.current(conn, pid, user, True); row = a.bound(a.payment(conn, pid, mid, True), v, conn)
        from .chat_terms import require_current
        require_current(conn,p,v['payload'],mid)
        n.require(is_funded(row) and row['status'] == 'awaiting_approval', 'Funding is not awaiting approval.')
        from .workflows import closed
        n.require(not p.get('withdrawal') and not closed(p, mid), 'Work is closed; no new collection is permitted.')
        n.require(window(v['payload'], next(m for m in at(v['payload'], 'timeline.milestones') if m['id']==mid), date.today())['available'], 'The agreed schedule no longer fits; update it before collection.')
        assert_app(row)
        a.save(conn, row, 'collecting', interrupted_operation='capture')
    try:
        order = await provider.call('GET', '/v2/checkout/orders/' + row['payload']['order_id'], seller=row['payload']['seller_merchant_id'])
        item=unit(row, order)
        n.require(item.get('payment_instruction',{}).get('disbursement_mode')=='DELAYED', 'PayPal has not confirmed delayed disbursement on this order. Collection blocked; verify platform and seller setup before proceeding.',502)
        n.require(order.get('status') == 'APPROVED', 'Approve the upfront charge in PayPal first.')
        issue=buyer_issue(order)
        n.require(not issue, issue or '',409)
        result = await provider.call('POST', '/v2/checkout/orders/' + row['payload']['order_id'] + '/capture', {}, row['payload']['capture_key'], seller=row['payload']['seller_merchant_id'])
        # Capture responses can omit order intent/payee. Verify the complete
        # original order before accepting capture evidence; never recapture.
        result = capture(row, await provider.call('GET', '/v2/checkout/orders/' + row['payload']['order_id'], seller=row['payload']['seller_merchant_id']))
        with n.transaction() as conn:
            p, _ = a.current(conn, pid, user, True); saved = a.payment(conn, pid, mid, True)
            n.require(saved['status']=='collecting', 'Funding state changed during collection.')
            record_capture(conn, saved, result)
            n.notify(conn, p, 'milestone_funded', 'Upfront funding confirmed for '+mid+'. Work may begin. Seller fees apply; disbursement follows review.')
            db.log(conn, 'platform_capture_verified', user['id'], pid, milestone_id=mid, capture_id=result['id'])
            return a.visible(saved)
    except HTTPException as error:
        with n.transaction() as conn:
            saved=a.payment(conn,pid,mid,True)
            if saved['status']=='collecting':a.save(conn,saved,'awaiting_approval' if error.status_code==409 else 'attention',attention=str(error.detail))
        raise


def coverage(row, reserve, now=None):
    now=now or datetime.now(timezone.utc)
    end=row['payload'].get('provider_disburse_by')
    return reserve is not None and end and datetime.fromisoformat(end)-timedelta(days=1)>=now+timedelta(days=reserve)


async def release(pid, mid, user, now=None, *, client_approved=False):
    from . import assurance as a
    now=now or datetime.now(timezone.utc)
    with n.transaction() as conn:
        p,v=a.current(conn,pid,user,True);row=a.bound(a.payment(conn,pid,mid,True),v,conn)
        n.require(is_funded(row), 'This payment does not use delayed disbursement.')
        assert_app(row)
        if client_approved:n.require(user['role']=='client','Only the client may approve submitted work.',403)
        if row['status'] in ('captured','disbursing'):
            return {'local':a.visible(row),'message':'Payment already released.' if row['status']=='captured' else 'Release is already processing. Check provider status; do not submit again.'}
        n.require(row['status']=='in_review' and row['payload'].get('auto_release') and not row['payload'].get('objection'), 'Payment is not releasable. Refresh the payment status and resolve any active objection before approval.')
        n.require(row['payload'].get('delivery_url') and row['payload'].get('review_deadline'),'Delivery and an active review are required.')
        n.require(client_approved or datetime.fromisoformat(row['payload']['review_deadline'])<=now,'Full review must end before disbursement.')
        a.save(conn,row,'disbursing',interrupted_operation='release',release_reason='client_approval' if client_approved else 'review_timeout')
        if client_approved:db.log(conn,'milestone_approved_for_release',user['id'],pid,milestone_id=mid,amount_minor=row['amount_minor'])
    try:
        # Check the capture first; provider may have auto-disbursed already.
        remote=await provider.call('GET','/v2/payments/captures/'+row['payload']['capture_id'],seller=row['payload']['seller_merchant_id'])
        verify_remote(row,remote)
        if remote.get('disbursement_mode')!='INSTANT':
            n.require(remote.get('status')=='COMPLETED' and remote.get('disbursement_mode')=='DELAYED','Capture cannot be disbursed.',502)
            response=await provider.call('POST','/v1/payments/referenced-payouts-items',{'reference_id':row['payload']['capture_id'],'reference_type':'TRANSACTION_ID'},row['payload']['release_key'])
            with n.transaction() as conn:
                saved=a.payment(conn,pid,mid,True)
                a.save(conn,saved,saved['status'],release_response=response,release_requested_at=db.now())
        # A POST success alone is never treated as seller receipt.
        return await reconcile(pid,mid,user)
    except HTTPException as error:
        with n.transaction() as conn:
            saved=a.payment(conn,pid,mid,True)
            if saved['status']=='disbursing':a.save(conn,saved,'attention',attention=str(error.detail),interrupted_operation='release')
        raise


def verify_remote(row, remote):
    n.require(remote.get('id')==row['payload'].get('capture_id') and paypal.step1_same_amount(remote.get('amount'),row['amount_minor'],row['currency']), 'Capture reference or gross amount mismatch.',502)


async def refund_operation(row, op):
    assert_app(row)
    seller=row['payload']['seller_merchant_id']
    original=await provider.call('GET','/v2/payments/captures/'+op['capture_id'],seller=seller)
    verify_remote(row,original)
    n.require(original.get('status') in ('COMPLETED','PARTIALLY_REFUNDED') and original.get('disbursement_mode')=='DELAYED', 'Capture is not held for refund; check PayPal before proceeding.',502)
    result=await provider.call('POST','/v2/payments/captures/'+op['capture_id']+'/refund',
                               {'amount':paypal.step1_amount(op['amount_minor'],row['currency']),'invoice_id':op['key']},op['key'],seller=seller)
    n.require(result.get('id') and paypal.step1_same_amount(result.get('amount'),op['amount_minor'],row['currency']), 'Refund response mismatched; reconcile.',502)
    if result.get('status')=='COMPLETED':
        original=await provider.call('GET','/v2/payments/captures/'+op['capture_id'],seller=seller)
        verify_remote(row,original)
        n.require(original.get('status')==('REFUNDED' if op['amount_minor']==row['amount_minor'] else 'PARTIALLY_REFUNDED'),'Refund not yet verified on original capture.',502)
    return result


async def reconcile_refund(row,op,reference=''):
    assert_app(row)
    reference=op.get('provider_id') or reference
    n.require(reference and re.fullmatch(r'[A-Za-z0-9]+',reference),'Supply the original refund reference; no second refund was sent.')
    seller=row['payload']['seller_merchant_id']
    result=await provider.call('GET','/v2/payments/refunds/'+reference,seller=seller)
    n.require(result.get('id')==reference and result.get('status')=='COMPLETED' and paypal.step1_same_amount(result.get('amount'),op['amount_minor'],row['currency']), 'Refund is not completed with the exact expected amount.')
    n.require(any(x.get('rel')=='up' and urlparse(x.get('href','')).hostname in ('api-m.sandbox.paypal.com','api.sandbox.paypal.com') and urlparse(x.get('href','')).path=='/v2/payments/captures/'+op['capture_id'] for x in result.get('links',[])), 'Refund must link to the original capture.')
    original=await provider.call('GET','/v2/payments/captures/'+op['capture_id'],seller=seller)
    verify_remote(row,original)
    n.require(original.get('status')==('REFUNDED' if op['amount_minor']==row['amount_minor'] else 'PARTIALLY_REFUNDED'),'Original capture refund is not verified.')
    return result


async def settlement_release(row,op,read_only=False):
    assert_app(row)
    seller=row['payload']['seller_merchant_id']
    remote=await provider.call('GET','/v2/payments/captures/'+op['capture_id'],seller=seller)
    verify_remote(row,remote)
    n.require(remote.get('status') in ('COMPLETED','PARTIALLY_REFUNDED'),'Capture is unavailable for the agreed partial settlement.',502)
    if not read_only and remote.get('disbursement_mode')=='DELAYED':
        await provider.call('POST','/v1/payments/referenced-payouts-items',{'reference_id':op['capture_id'],'reference_type':'TRANSACTION_ID'},op['key'])
        remote=await provider.call('GET','/v2/payments/captures/'+op['capture_id'],seller=seller)
        verify_remote(row,remote)
    n.require(remote.get('disbursement_mode')=='INSTANT','Provider disbursement is pending; reconcile the original operation without resubmitting.',502)
    return {'id':op['capture_id'],'status':'COMPLETED','amount':paypal.step1_amount(op['amount_minor'],row['currency'])}


async def reconcile(pid,mid,user):
    from . import assurance as a, step1_ledger
    with db.engine().connect() as conn:
        p,v=a.current(conn,pid,user);row=a.bound(a.payment(conn,pid,mid),v,conn)
    assert_app(row)
    n.require(row['payload'].get('order_id'), 'No order ID saved. Recover the original platform order; do not create another charge.')
    order=await provider.call('GET','/v2/checkout/orders/'+row['payload']['order_id'],seller=row['payload']['seller_merchant_id'])
    item=unit(row,order)
    captures=item.get('payments',{}).get('captures',[])
    if not captures:
        n.require(not row['payload'].get('capture_id'), 'Previously captured order now lacks capture evidence; investigate.',502)
        with n.transaction() as conn:
            saved=a.payment(conn,pid,mid,True)
            n.require(saved['status'] in ('attention','awaiting_approval','collecting'), 'Funding cannot be reset in this state.')
            # Capture is retryable only with the original key and a verified uncaptured order.
            issue=buyer_issue(order) if order.get('status')=='APPROVED' else None
            a.save(conn,saved,'awaiting_approval',attention=issue,provider_checked_at=db.now())
            message=issue or ('Correct buyer approval verified. Confirm approval & collect funding to complete this existing order.' if order.get('status')=='APPROVED' else 'Original order has no capture. Use its existing approval/collection action.')
            return {'local':a.visible(saved),'provider_status':order.get('status'),'message':message}
    result=capture(row,order)
    release_check=None
    response=row['payload'].get('release_response') or {}
    # Follow only the original provider's read-only resource link, never a
    # caller-supplied URL or a new payout request.
    for link in response.get('links',[]):
        target=urlparse(link.get('href',''))
        if (link.get('rel')=='self' and link.get('method')=='GET'
                and target.scheme=='https' and target.hostname in ('api-m.sandbox.paypal.com','api.sandbox.paypal.com')
                and re.fullmatch(r'/v1/payments/referenced-payouts-items/[A-Za-z0-9_=-]+',target.path)
                and not target.query and not target.fragment and not target.username and not target.password):
            release_check=await provider.call('GET',target.path)
            n.require(release_check.get('reference_id')==row['payload']['capture_id'], 'PayPal release reference does not match the original capture.',502)
            break
    released=False
    if release_check and release_check.get('processing_state',{}).get('status')=='SUCCESS':
        detail=await provider.call('GET','/v2/payments/captures/'+result['id'],seller=row['payload']['seller_merchant_id'])
        verify_remote(row,detail)
        net=detail.get('seller_receivable_breakdown',{}).get('net_amount')
        n.require(release_check.get('reference_type')=='TRANSACTION_ID' and release_check.get('payout_destination')==row['payload']['seller_merchant_id'] and release_check.get('payout_transaction_id') and net and release_check.get('payout_amount')==net,'PayPal payout destination or net amount does not match this payment.',502)
        released=True
    with n.transaction() as conn:
        p,v=a.current(conn,pid,user,True);saved=a.payment(conn,pid,mid,True)
        a.save(conn,saved,saved['status'],provider_disbursement_mode=result.get('disbursement_mode'))
        if release_check is not None:
            a.save(conn,saved,saved['status'],release_check_response=release_check,payout_transaction_id=release_check.get('payout_transaction_id'),seller_net_amount=release_check.get('payout_amount'),release_provider_status=release_check.get('processing_state',{}).get('status'))
        if not saved['payload'].get('capture_id'):
            n.require(saved['status'] in ('attention','collecting'), 'Unexpected capture state.')
            record_capture(conn,saved,result)
        else:
            verify_remote(saved,result)
            mode=result.get('disbursement_mode');status=result.get('status')
            if (status=='COMPLETED' or status=='PARTIALLY_REFUNDED' and saved['payload'].get('refunded_minor') and saved['payload'].get('settlement_amount_minor')) and (mode=='INSTANT' or released):
                unexpected=not saved['payload'].get('release_reason') and not saved['payload'].get('release_requested_at') and not saved['payload'].get('release_response') and saved['status']!='captured'
                warning='PayPal reports seller release without a recorded FairFlow release request. The payment is no longer awaiting release; check PayPal before funding more work.' if unexpected or saved['payload'].get('unexpected_disbursement') else None
                a.save(conn,saved,'captured',disbursed_at=saved['payload'].get('disbursed_at') or db.now(),provider_checked_at=db.now(),attention=warning,unexpected_disbursement=unexpected or saved['payload'].get('unexpected_disbursement',False))
                if unexpected:
                    db.log(conn,'unexpected_platform_disbursement',user['id'],pid,milestone_id=mid,capture_id=result['id'])
                    n.notify(conn,p,'payment_attention',warning)
                # Gross provider accounting; net seller receipt excludes PayPal fees.
                step1_ledger.post(conn,{**saved,'amount_minor':saved['payload'].get('settlement_amount_minor',saved['amount_minor'])},'platform_disbursement',result['id'],[('provider_receivable',-1),('project_liability',1)])
                n.notify(conn,p,'seller_disbursed','PayPal confirms seller disbursement for '+mid+'. Seller processing fees apply.') if row['status']!='captured' else None
            elif status=='REFUNDED':
                a.save(conn,saved,'refunded',provider_checked_at=db.now(),attention=None)
                step1_ledger.post(conn,saved,'refund',result['id'],[('provider_receivable',-1),('project_liability',1)])
            elif status=='COMPLETED' and mode not in ('DELAYED','INSTANT'):
                a.save(conn,saved,'attention',provider_checked_at=db.now(),attention='PayPal did not confirm the current disbursement mode. Check provider status before proceeding.')
            elif status!='COMPLETED':
                a.save(conn,saved,'attention',provider_checked_at=db.now(),attention='PayPal capture status: '+str(status))
                from .workflows import payment_case
                payment_case(conn,pid,mid,'PayPal funded capture status: '+str(status),user)
            elif release_check and release_check.get('processing_state',{}).get('status') in ('FAILED','DENIED','CANCELED','CANCELLED','RETURNED','BLOCKED'):
                a.save(conn,saved,'attention',provider_checked_at=db.now(),attention='PayPal payment release '+release_check['processing_state']['status'].lower()+'. Client funding was collected; freelancer receipt is not confirmed. Check the original payout before retrying.')
            elif saved['status']=='disbursing' or saved['status']=='attention' and saved['payload'].get('interrupted_operation')=='release':
                # A completed capture confirms collection, not seller receipt.
                # Older releases discarded their response; never replay a payout
                # simply to recover its missing reference.
                missing=not saved['payload'].get('release_response')
                message=('Buyer payment collected. Seller receipt is unconfirmed. The original release reference was not saved; check this capture in the PayPal Sandbox account before any retry.' if missing else 'Buyer payment collected. Payment release requested; seller receipt is not yet confirmed by PayPal.')
                a.save(conn,saved,'attention' if missing else 'disbursing',provider_checked_at=db.now(),attention=message)
            else:
                a.save(conn,saved,saved['status'],provider_checked_at=db.now())
        message=saved['payload'].get('attention') or ('PayPal confirms payment released. PayPal fees apply.' if saved['status']=='captured' else 'Buyer payment collection checked. This does not confirm seller receipt.')
        return {'local':a.visible(saved),'provider_status':result.get('status'),'provider_id':result['id'],'message':message+' No additional charge or payout was created.'}


async def scan(pid,user,rows,now):
    from . import assurance as a, workflows as w
    for row in rows:
        if not is_funded(row):continue
        if row['status']=='settling':continue
        mid=row['milestone_id'];payload=row['payload']
        if row['status'] in ('creating','collecting'):
            changed=datetime.fromisoformat(payload.get('state_changed_at',row['created_at']))
            if changed+timedelta(minutes=5)<now:
                with n.transaction() as conn:
                    saved=a.payment(conn,pid,mid,True)
                    if saved['status']==row['status']:a.save(conn,saved,'attention',attention='Interrupted platform operation. Check provider status before proceeding.')
        if row['status']=='in_review' and payload.get('review_deadline') and datetime.fromisoformat(payload['review_deadline'])<=now and not payload.get('objection'):
            try:await release(pid,mid,user,now)
            except HTTPException:pass
        if payload.get('capture_id'):
            end=datetime.fromisoformat(payload['provider_disburse_by'])
            if now>=end-timedelta(days=3) and row['status'] not in ('captured','refunded'):
                with n.transaction() as conn:
                    p,rev=n.project(conn,pid,user,True);saved=a.payment(conn,pid,mid,True)
                    if not saved['payload'].get('hold_warned'):
                        a.save(conn,saved,saved['status'],hold_warned=True)
                        n.notify(conn,p,'funding_limit','Milestone '+mid+': PayPal automatic seller disbursement is approaching. An internal dispute cannot extend the hold. Resolve this case now.')
                        w.payment_case(conn,pid,mid,'Delayed disbursement reaches its provider limit on '+payload['provider_disburse_by'],user)
            checked=payload.get('provider_checked_at')
            if row['status'] not in ('refunded',) and (not checked or datetime.fromisoformat(checked)+timedelta(minutes=5)<now):
                try:await reconcile(pid,mid,user)
                except HTTPException:pass


class SellerInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    email:EmailStr


class OrderReference(BaseModel):
    model_config=ConfigDict(extra='forbid')
    order_id:str=Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9]+$')


def mount(app):
    from . import assurance as a
    router=APIRouter(prefix='/api/{portal}/projects/{pid}/assurance/platform',dependencies=[Depends(n.portal_user)])

    @router.post('/connect')
    async def connect(portal:n.Portal,pid:str,data:SellerInput,user=Depends(n.portal_user)):
        n.require(portal=='freelancer','The freelancer must connect their own receiving account.',403)
        with n.transaction() as conn:
            p,v=a.current(conn,pid,user,True)
            _,revision=n.project(conn,pid,user,True)
            n.require(not any(is_funded(row) for row in db.rows(conn,db.milestone_payments,db.milestone_payments.c.project_id==pid)), 'Seller is frozen after a platform payment attempt.')
            tracking='ff-'+db.uid()
            p['platform_seller']={'tracking_id':tracking,'email':str(data.email).casefold(),'fingerprint':provider.fingerprint(),'status':'connecting','user_id':user['id']}
            n.write(conn,p,revision)
            db.log(conn,'platform_seller_onboarding_started',user['id'],pid,tracking_id=tracking)
        body={'tracking_id':tracking,'email':str(data.email),'products':['EXPRESS_CHECKOUT'],'operations':[{'operation':'API_INTEGRATION','api_integration_preference':{'rest_api_integration':{'integration_method':'PAYPAL','integration_type':'THIRD_PARTY','third_party_details':{'features':['PAYMENT','REFUND','DELAY_FUNDS_DISBURSEMENT']}}}}]}
        result=await provider.call('POST','/v2/customer/partner-referrals',body,tracking)
        referral=next((x['href'] for x in result.get('links',[]) if x.get('rel')=='self'),None)
        parsed=urlparse(referral or '')
        n.require(parsed.scheme=='https' and parsed.hostname in ('api-m.sandbox.paypal.com','api.sandbox.paypal.com') and parsed.path.startswith('/v2/customer/partner-referrals/'),'Invalid referral response.',502)
        info=await provider.call('GET',parsed.path)
        action=next((x['href'] for x in result.get('links',[]) if x.get('rel')=='action_url'),None)
        n.require(action and urlparse(action).scheme=='https' and urlparse(action).hostname=='www.sandbox.paypal.com' and info.get('submitter_payer_id'),'Invalid seller onboarding response.',502)
        with n.transaction() as conn:
            p,rev=n.project(conn,pid,user,True)
            n.require(p.get('platform_seller',{}).get('tracking_id')==tracking,'Onboarding request was superseded.')
            p['platform_seller'].update(partner_id=info['submitter_payer_id'],action_url=action,status='awaiting_seller')
            n.write(conn,p,rev);return seller_view(p,user)

    @router.post('/verify')
    async def verify(portal:n.Portal,pid:str,user=Depends(n.portal_user)):
        n.require(portal=='freelancer','Only the freelancer verifies their account.',403)
        return await verify_seller(pid,user)

    @router.post('/{mid}/reconcile')
    async def check(portal:n.Portal,pid:str,mid:str,user=Depends(n.portal_user)):
        with db.engine().connect() as conn:
            a.current(conn,pid,user)
            n.require(is_funded(a.payment(conn,pid,mid)), 'Not an upfront-funded payment.')
        return await reconcile(pid,mid,user)

    @router.post('/{mid}/recover-order')
    async def recover(portal:n.Portal,pid:str,mid:str,data:OrderReference,user=Depends(n.portal_user)):
        n.require(portal=='client','Only the client can recover a checkout reference.',403)
        with db.engine().connect() as conn:
            p,v=a.current(conn,pid,user);row=a.bound(a.payment(conn,pid,mid),v,conn)
            n.require(is_funded(row) and row['status']=='attention' and not row['payload'].get('capture_id'),'This payment is not awaiting order recovery.')
            n.require(not row['payload'].get('order_id') or row['payload']['order_id']==data.order_id,'The saved order reference cannot be replaced.')
        candidate=copy.deepcopy(row);candidate['payload']['order_id']=data.order_id
        order=await provider.call('GET','/v2/checkout/orders/'+data.order_id,seller=row['payload']['seller_merchant_id'])
        item=unit(candidate,order)
        checkout=next((x['href'] for x in order.get('links',[]) if x.get('rel') in ('approve','payer-action') and urlparse(x.get('href','')).scheme=='https' and urlparse(x['href']).hostname=='www.sandbox.paypal.com'),None)
        with n.transaction() as conn:
            p,v=a.current(conn,pid,user,True);saved=a.payment(conn,pid,mid,True)
            n.require(saved['status']=='attention' and not saved['payload'].get('capture_id'),'Payment changed during recovery.')
            a.save(conn,saved,'attention',order_id=data.order_id,checkout_url=checkout)
            if item.get('payments',{}).get('captures'):
                record_capture(conn,saved,capture(candidate,order))
            else:a.save(conn,saved,'awaiting_approval',attention=None)
            db.log(conn,'platform_order_recovered',user['id'],pid,order_id=data.order_id)
            return a.visible(saved)

    app.include_router(router)
