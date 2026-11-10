import asyncio
import httpx
import pytest
from fastapi import HTTPException
from app import paypal, ai
from app.models import Contract

PAY={'id':'test-payment','order_id':'ORDER-1','authorization_id':'AUTH-1','amount_cents':80000}

def test_order_intent_amount_return_and_idempotency(monkeypatch):
    recorded={}
    async def fake(method,path,body=None,key=None):
        recorded.update(method=method,path=path,body=body,key=key)
        return {'id':'ORDER-1','links':[]}
    monkeypatch.setattr(paypal,'call',fake)
    monkeypatch.setenv('APP_BASE_URL','https://fairflow.example')
    asyncio.run(paypal.create(PAY))
    assert recorded['body']['intent']=='AUTHORIZE'
    assert recorded['body']['purchase_units'][0]['amount']=={'currency_code':'USD','value':'800.00'}
    assert recorded['body']['application_context']['return_url']=='https://fairflow.example/?payment=test-payment'
    assert recorded['key']=='test-payment-create'

@pytest.mark.parametrize('status,currency,amount',[('CREATED','USD','800.00'),('CREATED','EUR','800.00'),('CREATED','USD','1.00'),('DENIED','USD','800.00')])
def test_authorization_verification(monkeypatch,status,currency,amount):
    async def fake(*args,**kwargs):
        return {'purchase_units':[{'payments':{'authorizations':[{'id':'AUTH-1','status':status,'amount':{'currency_code':currency,'value':amount}}]}}]}
    monkeypatch.setattr(paypal,'call',fake)
    if (status,currency,amount)==('CREATED','USD','800.00'):
        assert asyncio.run(paypal.authorize(PAY))=='AUTH-1'
    else:
        with pytest.raises(HTTPException): asyncio.run(paypal.authorize(PAY))

@pytest.mark.parametrize('status',['PENDING','DECLINED','COMPLETED'])
def test_capture_requires_completed(monkeypatch,status):
    async def fake(method,path,body,key):
        assert key=='test-payment-capture'
        assert body=={'final_capture':True}
        return {'id':'CAP-1','status':status}
    monkeypatch.setattr(paypal,'call',fake)
    if status=='COMPLETED': assert asyncio.run(paypal.capture(PAY))=='CAP-1'
    else:
        with pytest.raises(HTTPException): asyncio.run(paypal.capture(PAY))

def test_gemini_structured_response(monkeypatch):
    real_client=httpx.AsyncClient
    def handler(request):
        assert request.headers['x-goog-api-key']=='test-key'
        import json
        payload=json.loads(request.content)
        assert payload['generationConfig']['responseMimeType']=='application/json'
        assert 'price_cents' in payload['generationConfig']['responseJsonSchema']['properties']
        contract={'title':'Landing page','task_description':'One static landing page','price_cents':80000,'allowed_revisions':2,'deliverables':['HTML'],'exclusions':['Backend']}
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps(contract)}]}}]})
    monkeypatch.setenv('GEMINI_API_KEY','test-key')
    monkeypatch.setattr(ai.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    result=asyncio.run(ai.structured(Contract,'Extract contract',{'chat':'test'}))
    assert result.price_cents==80000

def test_gemini_invalid_response_fails_closed(monkeypatch):
    real_client=httpx.AsyncClient
    monkeypatch.setenv('GEMINI_API_KEY','test-key')
    monkeypatch.setattr(ai.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'candidates':[]})),**kwargs))
    with pytest.raises(HTTPException) as error: asyncio.run(ai.structured(Contract,'Extract',{}))
    assert error.value.status_code==502

def test_vault_setup_and_order_payloads(monkeypatch):
    calls=[]
    async def fake(method,path,body=None,key=None):
        calls.append((method,path,body,key));return {'id':'ID','status':'CREATED'}
    monkeypatch.setattr(paypal,'call',fake)
    asyncio.run(paypal.vault_setup('PROJECT','SETUP-KEY'))
    body=calls[-1][2]
    assert body['payment_source']['paypal']['usage_type']=='MERCHANT'
    assert body['payment_source']['paypal']['usage_pattern']=='UNSCHEDULED_PREPAID'
    payment={'id':'PAY','amount_cents':12345,'order_key':'ORDER-KEY'}
    asyncio.run(paypal.milestone_order(payment,'TOKEN-123'))
    body=calls[-1][2]
    assert body['intent']=='AUTHORIZE'
    assert body['purchase_units'][0]['amount']['value']=='123.45'
    assert body['payment_source']['paypal']['vault_id']=='TOKEN-123'
    assert body['payment_source']['paypal']['stored_credential']['payment_initiator']=='MERCHANT'

def test_unapproved_vault_setup_is_not_exchanged(monkeypatch):
    calls=[]
    async def fake(method,path,body=None,key=None):calls.append(method);return {'status':'PAYER_ACTION_REQUIRED'}
    monkeypatch.setattr(paypal,'call',fake)
    with pytest.raises(HTTPException):asyncio.run(paypal.vault_exchange('SETUP-ID','EXCHANGE-KEY'))
    assert calls==['GET']

def test_approved_vault_setup_exchange(monkeypatch):
    async def fake(method,path,body=None,key=None):
        if method=='GET':return {'status':'APPROVED'}
        assert body=={'payment_source':{'token':{'id':'SETUP-ID','type':'SETUP_TOKEN'}}}
        assert key=='EXCHANGE-KEY'
        return {'id':'PAYMENT-TOKEN'}
    monkeypatch.setattr(paypal,'call',fake)
    assert asyncio.run(paypal.vault_exchange('SETUP-ID','EXCHANGE-KEY'))=='PAYMENT-TOKEN'

def test_partial_capture_and_void_payloads(monkeypatch):
    calls=[]
    async def fake(method,path,body=None,key=None):
        calls.append((method,path,body,key))
        return {'id':'CAP','status':'COMPLETED','amount':{'currency_code':'USD','value':'80.00'}}
    monkeypatch.setattr(paypal,'call',fake)
    pay={'authorization_id':'AUTH'}
    op={'amount_cents':8000,'final_capture':False,'key':'CAP-KEY'}
    assert asyncio.run(paypal.milestone_capture(pay,op))=='CAP'
    assert calls[-1][2]['final_capture'] is False
    assert calls[-1][2]['amount']['value']=='80.00'
    asyncio.run(paypal.milestone_void(pay,{'key':'VOID-KEY'}))
    assert calls[-1]==('POST','/v2/payments/authorizations/AUTH/void',{},'VOID-KEY')

def test_milestone_capture_validates_amount(monkeypatch):
    async def fake(*args,**kwargs):return {'id':'CAP','status':'COMPLETED','amount':{'currency_code':'USD','value':'1.00'}}
    monkeypatch.setattr(paypal,'call',fake)
    with pytest.raises(HTTPException):asyncio.run(paypal.milestone_capture({'authorization_id':'AUTH'},{'amount_cents':8000,'final_capture':False,'key':'KEY'}))


@pytest.mark.parametrize('currency,cents,wire',[('EUR',12345,'123.45'),('JPY',12300,'123'),('HUF',12300,'123'),('TWD',12300,'123')])
def test_currency_survives_provider_order_authorization_and_capture(monkeypatch,currency,cents,wire):
    amount={'currency_code':currency,'value':wire}
    pay={**PAY,'currency':currency,'amount_cents':cents,'order_key':'ORDER-KEY','authorize_key':'AUTH-KEY'}
    async def fake(method,path,body=None,key=None):
        if path.endswith('/authorize'):
            return {'purchase_units':[{'payments':{'authorizations':[{'id':'AUTH','status':'CREATED','amount':amount}]}}]}
        if path.endswith('/capture'):
            assert body['amount']==amount
            return {'id':'CAP','status':'COMPLETED','amount':amount}
        assert body['purchase_units'][0]['amount']==amount
        return {'id':'ORDER'}
    monkeypatch.setattr(paypal,'call',fake)
    assert asyncio.run(paypal.milestone_order(pay))['id']=='ORDER'
    assert asyncio.run(paypal.milestone_authorize(pay))['id']=='AUTH'
    assert asyncio.run(paypal.milestone_capture(pay,{'amount_cents':cents,'final_capture':True,'key':'CAP-KEY'}))=='CAP'
