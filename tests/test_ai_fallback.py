import asyncio
import json
import httpx
import pytest
from fastapi import HTTPException
from app import ai
from app.models import Contract

VALID={'title':'Landing page','task_description':'Build one static landing page.','price_cents':80000,'allowed_revisions':2,'deliverables':['HTML and CSS'],'exclusions':['Backend']}

def good():
    return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(VALID)}]}}]})

@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','test-key')
    monkeypatch.setenv('GEMINI_MODEL','primary')
    monkeypatch.setenv('GEMINI_FALLBACK_MODELS','secondary,third')
    monkeypatch.setenv('GEMINI_ATTEMPT_TIMEOUT_SECONDS','1')
    monkeypatch.setenv('GEMINI_TOTAL_TIMEOUT_SECONDS','5')
    real_client=httpx.AsyncClient
    calls=[]
    delays=[]
    async def sleep(delay):delays.append(delay)
    monkeypatch.setattr(ai.asyncio,'sleep',sleep)
    def install(handler):
        def wrapped(request):
            model=request.url.path.split('/')[-1].split(':')[0]
            calls.append(model)
            return handler(model,request)
        monkeypatch.setattr(ai.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(wrapped),**kwargs))
    return install,calls,delays

@pytest.mark.parametrize('status',[404,408,429,500,502,503,504])
def test_failover_on_recoverable_http(harness,status):
    install,calls,delays=harness
    install(lambda model,r: httpx.Response(status) if model=='primary' else good())
    result=asyncio.run(ai.structured(Contract,'Extract',{}))
    assert result.price_cents==80000
    assert calls==['primary','secondary']
    assert bool(delays)==(status!=404)

@pytest.mark.parametrize('status',[400,401,403])
def test_no_fallback_for_bad_request_or_credentials(harness,status):
    install,calls,_=harness
    install(lambda model,r:httpx.Response(status))
    with pytest.raises(HTTPException):asyncio.run(ai.structured(Contract,'Extract',{}))
    assert calls==['primary']

def test_network_timeout_fallback(harness):
    install,calls,_=harness
    def handler(model,request):
        if model=='primary':raise httpx.ReadTimeout('simulated')
        return good()
    install(handler)
    assert asyncio.run(ai.structured(Contract,'Extract',{})).price_cents==80000
    assert calls==['primary','secondary']

def test_invalid_json_fallback_is_still_validated(harness):
    install,calls,_=harness
    install(lambda model,r:httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'{"price_cents":-5}'}]}}]}) if model=='primary' else good())
    assert asyncio.run(ai.structured(Contract,'Extract',{})).price_cents==80000
    assert calls==['primary','secondary']

@pytest.mark.parametrize('body',[{'promptFeedback':{'blockReason':'SAFETY'}},{'candidates':[{'finishReason':'SAFETY'}]}])
def test_safety_block_not_bypassed(harness,body):
    install,calls,_=harness
    install(lambda model,r:httpx.Response(200,json=body))
    with pytest.raises(HTTPException) as error:asyncio.run(ai.structured(Contract,'Extract',{}))
    assert error.value.status_code==422
    assert calls==['primary']

def test_retry_after_larger_than_budget_stops(harness):
    install,calls,delays=harness
    install(lambda model,r:httpx.Response(429,headers={'Retry-After':'120'}))
    with pytest.raises(HTTPException):asyncio.run(ai.structured(Contract,'Extract',{}))
    assert calls==['primary'] and not delays

def test_exhaustion_never_returns_demo(harness):
    install,calls,_=harness
    install(lambda model,r:httpx.Response(503))
    with pytest.raises(HTTPException) as error:asyncio.run(ai.structured(Contract,'Extract',{}))
    assert calls==['primary','secondary','third']
    assert 'No demo output' in error.value.detail

def test_primary_success_reports_used_model_in_context(harness):
    install,calls,_=harness
    install(lambda model,r:good())
    async def run():
        result=await ai.structured(Contract,'Extract',{})
        return result,ai.request_trace.get()
    result,trace=asyncio.run(run())
    assert trace['model']=='primary' and len(trace['attempts'])==1

def test_deduplication_and_opt_out(monkeypatch):
    monkeypatch.setenv('GEMINI_MODEL','models/primary')
    monkeypatch.setenv('GEMINI_FALLBACK_MODELS','primary, secondary,models/secondary')
    assert ai.model_chain()==['primary','secondary']
    monkeypatch.setenv('GEMINI_FALLBACK_MODELS','')
    assert ai.model_chain()==['primary']

def test_invalid_model_configuration(monkeypatch):
    monkeypatch.setenv('GEMINI_MODEL','../other?key=secret')
    with pytest.raises(HTTPException):ai.model_chain()

def test_total_deadline_cancels_slow_request(harness,monkeypatch):
    real_client=httpx.AsyncClient
    monkeypatch.setenv('GEMINI_TOTAL_TIMEOUT_SECONDS','0.02')
    monkeypatch.setenv('GEMINI_ATTEMPT_TIMEOUT_SECONDS','1')
    calls=[]
    async def handler(request):
        calls.append(request.url.path)
        await asyncio.Event().wait()
    monkeypatch.setattr(ai.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    with pytest.raises(HTTPException):asyncio.run(ai.structured(Contract,'Extract',{}))
    assert len(calls)==1
