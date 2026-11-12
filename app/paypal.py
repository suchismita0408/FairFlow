import os,re
import httpx
import hashlib
from pathlib import Path
from dotenv import dotenv_values
from decimal import Decimal
from babel.numbers import get_currency_precision
from fastapi import HTTPException
from .currencies import amount as currency_amount

BASE = 'https://api-m.sandbox.paypal.com'

def payout_credentials():
    path=Path(os.getenv('PAYPAL_PAYOUT_CREDENTIALS_FILE',str(Path(__file__).resolve().parent.parent/'.env.paypal-us')))
    if path.is_file():
        values=dotenv_values(path)
        ident=values.get('PAYPAL_CLIENT_ID');secret=values.get('PAYPAL_CLIENT_SECRET')
        if ident or secret:
            if not ident or not secret:raise HTTPException(503,'Complete both US Sandbox payout credential fields.')
            return ident,secret
    return os.getenv('PAYPAL_CLIENT_ID',''),os.getenv('PAYPAL_CLIENT_SECRET','')

def payout_sender():
    return hashlib.sha256(payout_credentials()[0].encode()).hexdigest()

def check_payout_sender(row):
    saved=row.get('payload',{}).get('sender_fingerprint')
    if saved and saved!=payout_sender():raise HTTPException(409,'This payout belongs to a different sender app. Restore its original payout credentials before reconciliation.')

async def call(method, path, body=None, key=None):
    from .judge_demo import require_sandbox
    require_sandbox()
    try:
        return await _call(method, path, body, key)
    except (httpx.HTTPError,ValueError,KeyError,TypeError):
        raise HTTPException(502, 'PayPal connection failed or timed out. Reconcile before retrying; the request may have succeeded remotely.')

async def _call(method, path, body=None, key=None):
    async with httpx.AsyncClient(timeout=30) as client:
        credentials=payout_credentials() if path.startswith('/v1/payments/payouts') else (os.getenv('PAYPAL_CLIENT_ID',''),os.getenv('PAYPAL_CLIENT_SECRET',''))
        auth = await client.post(BASE+'/v1/oauth2/token', auth=credentials, data={'grant_type':'client_credentials'})
        if auth.status_code != 200:
            raise HTTPException(502, 'PayPal Sandbox authentication failed.')
        headers = {'Authorization':'Bearer '+auth.json()['access_token'], 'Content-Type':'application/json'}
        if key:
            headers['PayPal-Request-Id'] = key
        result = await client.request(method, BASE+path, headers=headers, json=body)
    if result.status_code >= 400:
        try:problem=result.json()
        except ValueError:problem={}
        codes=[d.get('issue','') for d in problem.get('details',[]) if isinstance(d,dict)]
        codes.append(problem.get('name',''))
        codes=[v for v in codes if isinstance(v,str) and re.fullmatch(r'[A-Z0-9_]{1,80}',v)]
        if method=='POST' and path=='/v1/payments/payouts' and 'DUPLICATE_BATCH_ID' in codes:
            # Follow only a batch resource on this Sandbox provider, never an
            # arbitrary remote URL carrying our credentials.
            from urllib.parse import urlparse
            links=problem.get('links',[])
            for detail in problem.get('details',[]):
                if isinstance(detail,dict):links=links+detail.get('links',[])
            for link in links:
                parsed=urlparse(link.get('href',''))
                if parsed.scheme=='https' and parsed.netloc in ('api-m.sandbox.paypal.com','api.sandbox.paypal.com') and re.fullmatch(r'/v1/payments/payouts/[A-Za-z0-9]+',parsed.path) and not parsed.query and not parsed.fragment:
                    return await call('GET',parsed.path)
        debug=problem.get('debug_id','')
        suffix=(' Code: '+', '.join(codes[:3])) if codes else ''
        hints={'INSUFFICIENT_FUNDS':'Fund the Sandbox business balance to cover the payout and fees.',
               'PAYOUT_NOT_AVAILABLE':'This Sandbox sender cannot send the payout. Check its registered country and Payouts eligibility with PayPal; USD currency alone does not enable Payouts.',
               'AUTHORIZATION_ERROR':'Enable Payouts access for this Sandbox business app.',
               'NOT_AUTHORIZED':'Enable Payouts access for this Sandbox business app.',
               'RECEIVER_ACCOUNT_INVALID':'Check the frozen Sandbox receiving account with PayPal.'}
        suffix+=' '+next((hints[c] for c in codes if c in hints),'')
        if isinstance(debug,str) and re.fullmatch(r'[A-Za-z0-9-]{1,80}',debug):suffix+=' Reference: '+debug
        raise HTTPException(502, 'PayPal request failed. Reconcile before retrying.'+suffix)
    return result.json() if result.content else {}

async def create(payment):
    base = os.getenv('APP_BASE_URL','http://127.0.0.1:8000').rstrip('/')
    return await call('POST', '/v2/checkout/orders', {
        'intent':'AUTHORIZE', 'purchase_units':[{'custom_id':payment['id'], 'amount':{'currency_code':'USD','value':f"{payment['amount_cents']/100:.2f}"}}],
        'application_context':{'return_url':base+'/?payment='+payment['id'], 'cancel_url':base+'/?cancelled=1', 'user_action':'PAY_NOW'}}, payment['id']+'-create')

async def authorize(payment):
    result = await call('POST', '/v2/checkout/orders/'+payment['order_id']+'/authorize', {}, payment['id']+'-authorize')
    try:
        item = result['purchase_units'][0]['payments']['authorizations'][0]
        if item['status'] != 'CREATED' or item['amount']['currency_code'] != 'USD' or item['amount']['value'] != f"{payment['amount_cents']/100:.2f}":
            raise ValueError()
        return item['id']
    except (KeyError, IndexError, ValueError):
        raise HTTPException(502, 'PayPal authorization could not be verified.')

async def capture(payment):
    result = await call('POST', '/v2/payments/authorizations/'+payment['authorization_id']+'/capture', {'final_capture':True}, payment['id']+'-capture')
    if result.get('status') != 'COMPLETED':
        raise HTTPException(502, 'Capture is not completed. Manual reconciliation is required.')
    return result['id']

# Agile-engine adapters. Hosted PayPal Wallet vaulting keeps raw card data out of FairFlow.
def usd(cents):
    return f'{cents // 100}.{cents % 100:02d}'

async def vault_setup(project_id, operation_id):
    base = os.getenv('APP_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
    return await call('POST', '/v3/vault/setup-tokens', {
        'customer': {'merchant_customer_id': project_id},
        'payment_source': {'paypal': {
            'description': 'FairFlow: approved milestone plan and separately accepted additions',
            'usage_type': 'MERCHANT', 'customer_type': 'CONSUMER',
            'usage_pattern': 'UNSCHEDULED_PREPAID',
            'permit_multiple_payment_tokens': True,
            'experience_context': {'return_url': base+'/?vault_project='+project_id,
                                   'cancel_url': base+'/?cancelled=1', 'shipping_preference': 'NO_SHIPPING'}
        }}}, operation_id)

async def vault_exchange(setup_id, operation_id):
    setup = await call('GET', '/v3/vault/setup-tokens/'+setup_id)
    if setup.get('status') != 'APPROVED':
        raise HTTPException(409, 'Approve saving the payment method in PayPal first.')
    result = await call('POST', '/v3/vault/payment-tokens',
        {'payment_source': {'token': {'id': setup_id, 'type': 'SETUP_TOKEN'}}}, operation_id)
    if not result.get('id'):
        raise HTTPException(502, 'PayPal did not return a saved payment token.')
    return result['id']

async def milestone_order(payment, token_id=None):
    base = os.getenv('APP_BASE_URL','http://127.0.0.1:8000').rstrip('/')
    body = {'intent':'AUTHORIZE', 'purchase_units':[{'custom_id':payment['id'],
        'amount':currency_amount(payment)}]}
    if token_id:
        body['payment_source'] = {'paypal': {'vault_id':token_id,
            'stored_credential': {'payment_initiator':'MERCHANT', 'usage_pattern':'UNSCHEDULED_PREPAID', 'usage':'SUBSEQUENT'}}}
    else:
        body['payment_source'] = {'paypal': {'experience_context': {
            'return_url':base+'/?agile_payment='+payment['id'], 'cancel_url':base+'/?cancelled=1',
            'shipping_preference':'NO_SHIPPING', 'user_action':'PAY_NOW'}}}
    return await call('POST','/v2/checkout/orders',body,payment['order_key'])

async def milestone_authorize(payment):
    result = await call('POST', '/v2/checkout/orders/'+payment['order_id']+'/authorize', {}, payment['authorize_key'])
    try:
        auth = result['purchase_units'][0]['payments']['authorizations'][0]
        if auth['status'] != 'CREATED' or auth['amount'] != currency_amount(payment):
            raise ValueError()
        return auth
    except (KeyError, IndexError, ValueError):
        raise HTTPException(502, 'Milestone authorization was not confirmed; reconcile provider state.')

async def milestone_capture(payment, operation):
    result = await call('POST', '/v2/payments/authorizations/'+payment['authorization_id']+'/capture',
        {'amount':currency_amount(payment,operation['amount_cents']),
         'final_capture':operation['final_capture'], 'invoice_id':operation['key']}, operation['key'])
    if result.get('status') != 'COMPLETED' or result.get('amount') != currency_amount(payment,operation['amount_cents']):
        raise HTTPException(502, 'Capture is pending or unverified. Reconcile before continuing.')
    return result['id']

async def milestone_void(payment, operation):
    await call('POST', '/v2/payments/authorizations/'+payment['authorization_id']+'/void', {}, operation['key'])

# Signed Step 1 milestone orders use actual ISO minor units, unlike legacy cents.
def step1_amount(amount_minor,currency):
    digits=get_currency_precision(currency)
    return {'currency_code':currency,'value':f'{Decimal(amount_minor)/(10**digits):.{digits}f}'}

def step1_same_amount(remote,amount_minor,currency):
    expected=step1_amount(amount_minor,currency)
    return isinstance(remote,dict) and remote.get('currency_code')==expected['currency_code'] and remote.get('value')==expected['value']

async def step1_create_order(payment,project_id):
    base=os.getenv('APP_BASE_URL','http://127.0.0.1:8000').rstrip('/')
    return await call('POST','/v2/checkout/orders',{
        'intent':'AUTHORIZE',
        'purchase_units':[{'custom_id':payment['id'],'reference_id':payment['milestone_id'],
                           'amount':step1_amount(payment['amount_minor'],payment['currency'])}],
        'payment_source':{'paypal':{'experience_context':{
            'return_url':base+'/client/projects/'+project_id+'?paypal_return=1',
            'cancel_url':base+'/client/projects/'+project_id+'?paypal_cancel=1',
            'shipping_preference':'NO_SHIPPING','user_action':'PAY_NOW','landing_page':'LOGIN'}}}},payment['payload']['order_key'])

async def step1_authorize(payment):
    order=await call('GET','/v2/checkout/orders/'+payment['payload']['order_id'])
    if order.get('status')!='APPROVED':raise HTTPException(409,'The buyer has not approved this PayPal Sandbox order.')
    units=order.get('purchase_units') or []
    if len(units)!=1 or units[0].get('custom_id')!=payment['id'] or not step1_same_amount(units[0].get('amount'),payment['amount_minor'],payment['currency']):
        raise HTTPException(502,'PayPal order amount or project reference differs. Reconcile before authorizing.')
    result=await call('POST','/v2/checkout/orders/'+payment['payload']['order_id']+'/authorize',{},payment['payload']['authorize_key'])
    try:authorization=result['purchase_units'][0]['payments']['authorizations'][0]
    except (KeyError,IndexError,TypeError):raise HTTPException(502,'PayPal did not return an authorization. Reconcile before retrying.')
    if authorization.get('status')!='CREATED' or not step1_same_amount(authorization.get('amount'),payment['amount_minor'],payment['currency']):
        raise HTTPException(502,'PayPal authorization status or amount differs. Reconcile before retrying.')
    return authorization

async def step1_capture(payment):
    result=await call('POST','/v2/payments/authorizations/'+payment['payload']['authorization_id']+'/capture',
        {'amount':step1_amount(payment['amount_minor'],payment['currency']),'final_capture':True,'invoice_id':payment['payload']['capture_key']},payment['payload']['capture_key'])
    if not result.get('id') or result.get('status')!='COMPLETED' or not step1_same_amount(result.get('amount'),payment['amount_minor'],payment['currency']):
        raise HTTPException(502,'PayPal capture is pending or differs in amount. Reconcile before retrying.')
    return result

async def step1_get_authorization(authorization_id):
    return await call('GET','/v2/payments/authorizations/'+authorization_id)

async def step1_reauthorize(payment):
    result=await call('POST','/v2/payments/authorizations/'+payment['payload']['authorization_id']+'/reauthorize',
        {'amount':step1_amount(payment['amount_minor'],payment['currency'])},payment['payload']['reauthorize_key'])
    if not result.get('id') or result.get('status')!='CREATED' or not step1_same_amount(result.get('amount'),payment['amount_minor'],payment['currency']):
        raise HTTPException(502,'Renewed authorization amount or status differs. Reconcile before continuing.')
    return result

async def step1_get_capture(capture_id):
    return await call('GET','/v2/payments/captures/'+capture_id)

async def step1_create_payout(payout):
    check_payout_sender(payout)
    amount=step1_amount(payout['amount_minor'],payout['currency'])
    return await call('POST','/v1/payments/payouts',{
        'sender_batch_header':{'sender_batch_id':payout['id'],'email_subject':'FairFlow Sandbox milestone payment'},
        'items':[{'recipient_type':'EMAIL','receiver':payout['recipient_email'],
                  'amount':{'currency':amount['currency_code'],'value':amount['value']},
                  'sender_item_id':payout['id'],'note':'FairFlow Sandbox milestone '+payout['payment_id']}]},payout['id'])

async def step1_get_payout(batch_id):
    from urllib.parse import quote
    return await call('GET','/v1/payments/payouts/'+quote(batch_id,safe=''))
