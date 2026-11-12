import asyncio, contextlib, json, os, secrets, uuid
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.models import ChatInput, NewProject, ChangeInput, Delivery, Accept
from app import ai, paypal, store, agile, db, auth, workspace
from app.locking import project_lock
from app.currencies import SUPPORTED

load_dotenv()
os.environ.setdefault('AI_MODE','gemini' if os.getenv('GEMINI_API_KEY') else 'demo')
if os.environ['AI_MODE'] not in ('demo','gemini'):
    raise RuntimeError('AI_MODE must be gemini or explicitly selected demo.')
MODE = os.getenv('FAIRFLOW_MODE', 'demo')
from app import judge_demo
judge_demo.configure()
if MODE not in ('demo','sandbox'):
    raise RuntimeError('Only demo and sandbox modes are supported. Live money is deliberately disabled.')
if MODE == 'sandbox' and not db.secure() and len(os.getenv('FAIRFLOW_ADMIN_TOKEN','')) < 24:
    raise RuntimeError('Sandbox requires FAIRFLOW_ADMIN_TOKEN of at least 24 characters.')
if os.getenv('APP_ENV','development')=='production':
    raise RuntimeError('Public production launch is disabled pending real email, marketplace onboarding, payout and jurisdiction validation.')
ROOT = Path(__file__).parent
lock = asyncio.Lock()  # Legacy demo only; authenticated milestones use PostgreSQL advisory locks.
def now(): return datetime.now(timezone.utc)
def uid(): return uuid.uuid4().hex
def timestamp(): return now().isoformat()
def event(p, message): p['events'].append({'at':timestamp(), 'message':message})
def project(pid):
    p = store.get(pid)
    if not p or p.get('mode') != MODE: raise HTTPException(404, 'Project not found in this payment environment.')
    return p

def ensure(p, *states):
    if p.get('engine') == 'agile_v2': raise HTTPException(409, 'Use the milestone API for this Agile project.')
    if p['status'] not in states: raise HTTPException(409, 'Action is unavailable in the current project state.')

def payment_for(pid):
    for p in store.all_projects():
        if p.get('mode') != MODE or p.get('_archived'): continue
        for payment in p['payments']:
            if payment['id'] == pid: return p, payment
    raise HTTPException(404, 'Payment not found.')

async def new_payment(p, amount, change_id=None):
    payment = {'id':uid(), 'amount_cents':amount, 'status':'creating', 'change_id':change_id, 'mode':MODE}
    p['payments'].append(payment)
    store.save(p)  # Persist request ID before remote mutation, allowing safe retries.
    return await complete_order(p, payment)

async def complete_order(p, payment):
    if payment['status'] != 'creating': return payment
    if MODE == 'demo':
        payment.update(order_id='DEMO-'+payment['id'], checkout_url=None, status='awaiting_approval')
    else:
        result = await paypal.create(payment)
        link = next((x['href'] for x in result.get('links',[]) if x['rel'] in ('approve','payer-action')), None)
        if not link: raise HTTPException(502, 'PayPal returned no approval link.')
        payment.update(order_id=result['id'], checkout_url=link, status='awaiting_approval')
    event(p, 'Checkout created for '+('change order' if payment['change_id'] else 'baseline contract'))
    store.save(p)
    return payment

async def release(p, automatic=False):
    ensure(p, 'in_review', 'payment_attention')
    if any(c['verdict'] == 'needs_review' and c['status'] == 'pending' for c in p['changes']):
        raise HTTPException(409, 'Resolve the disputed scope decision before release.')
    for payment in p['payments']:
        if payment['status'] == 'authorized':
            try:
                capture_id = 'DEMO-CAP-'+payment['id'] if MODE == 'demo' else await paypal.capture(payment)
            except HTTPException:
                p['status'] = 'payment_attention'
                event(p, 'Capture needs reconciliation. Automatic retries paused; operator can retry safely.')
                store.save(p)
                raise
            payment.update(status='captured', capture_id=capture_id)
            store.save(p)
    if not any(x['status'] == 'captured' for x in p['payments']):
        raise HTTPException(409, 'No funded payment to capture.')
    p['status'] = 'completed'
    p['review_deadline'] = None
    event(p, 'Payment captured after '+('72-hour review deadline' if automatic else 'client approval'))
    store.save(p)
    return p

async def run_due():
    if db.secure():
        from app.worker import tick
        return await tick()
    count = 0
    async with lock:
        for p in store.all_projects():
            if p.get('mode') != MODE or p.get('_archived'): continue
            if p.get('engine') == 'agile_v2':
                await agile.scan(p)
                continue
            if p['status'] == 'in_review' and p['review_deadline'] and datetime.fromisoformat(p['review_deadline']) <= now():
                try:
                    await release(p, True)
                    count += 1
                except HTTPException:
                    pass
    return count

async def scheduler():
    while True:
        await asyncio.sleep(15)
        try: await run_due()
        except Exception:
            import logging
            logging.exception('Review scheduler failed; will retry next scan')

@asynccontextmanager
async def lifespan(app):
    store.all_projects()
    task = asyncio.create_task(scheduler()) if not db.secure() or os.getenv('RUN_EMBEDDED_WORKER','false')=='true' else None
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError): await task

app = FastAPI(title='FairFlow', version='2.0.0', lifespan=lifespan)

@app.middleware('http')
async def boundary(request: Request, call_next):
    identity_token=None
    try:
        if request.url.path.startswith('/api/') and request.method not in ('GET','HEAD','OPTIONS'):
            parts=request.url.path.strip("/").split("/")
            assistant_upload=len(parts)==5 and parts[0]=="api" and parts[1] in ("client","freelancer") and parts[2]=="projects" and parts[4]=="assistant"
            maximum=(8 if assistant_upload else 1)*1024*1024
            body=bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body)>maximum:raise HTTPException(413,f'Request exceeds the {maximum//(1024*1024)} MiB limit.')
            request._body=bytes(body)
        if db.secure():
            user=await auth.guard(request)
            identity_token=auth.actor.set(user)
            auth.authorize_path(request)
        elif request.url.path.startswith('/api/') and request.url.path not in ('/api/health','/api/webhooks/paypal'):
            if MODE == 'sandbox' and not secrets.compare_digest(request.headers.get('x-admin-token',''), os.getenv('FAIRFLOW_ADMIN_TOKEN','')):
                raise HTTPException(401,'Operator token required.')
            origin=request.headers.get('origin')
            if request.method!='GET' and origin and origin!=str(request.base_url).rstrip('/'):
                raise HTTPException(403,'Cross-origin writes are disabled.')
        response=await call_next(request)
    except HTTPException as exc:
        response=JSONResponse({'detail':exc.detail},status_code=exc.status_code)
    except Exception:
        import logging
        logging.exception('Request failed')
        response=JSONResponse({'detail':'Unexpected service error. State may require reconciliation.'},status_code=500)
    finally:
        if identity_token is not None:auth.actor.reset(identity_token)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    if request.url.path in ('/docs', '/redoc'):
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data: https://fastapi.tiangolo.com; connect-src 'self'; frame-ancestors 'none'"
    return response

@app.get('/api/currencies')
def currency_catalog():
    from app.currencies import catalog
    return catalog()

@app.get('/api/health')
def health(): return {'status':'ok','payment_mode':MODE,'ai_mode':os.getenv('AI_MODE','demo'),'review_hours':120,'legacy_review_hours':72,'milestone_days':[14,21],'auth_enabled':db.secure(),'currency':'USD','currency_options':list(SUPPORTED),'vault_enabled':MODE=='demo' or os.getenv('PAYPAL_VAULT_ENABLED','false').lower()=='true'}

@app.get('/api/projects')
def projects():
    projects=[p for p in store.all_projects() if p.get('mode')==MODE and not p.get('_archived')]
    if db.secure():
        from sqlalchemy import select
        with db.engine().connect() as conn:
            allowed=set(conn.execute(select(db.members.c.project_id).where(db.members.c.user_id==auth.current()['id'])).scalars())
        projects=[p for p in projects if p['id'] in allowed]
    return [agile.public(p) for p in projects]

@app.get('/api/projects/{pid}')
def read_project(pid: str): return agile.public(project(pid))

@app.post('/api/contracts/draft')
async def draft(data: ChatInput): return await ai.extract(data.chat_history)

@app.post('/api/projects', status_code=201)
async def create_project(data: NewProject):
    p = {'id':uid(), **data.model_dump(), 'status':'draft', 'accepted':[], 'revisions_used':0, 'changes':[], 'payments':[], 'deliveries':[], 'events':[], 'review_deadline':None, 'created_at':timestamp(), 'mode':MODE}
    event(p, 'Contract draft created. Both parties must accept these exact terms.')
    store.save(p)
    return p

@app.post('/api/projects/{pid}/accept')
async def accept(pid: str, data: Accept):
    async with lock:
        p = project(pid)
        ensure(p, 'draft')
        if data.role not in p['accepted']:
            p['accepted'].append(data.role)
            event(p, data.role.title()+' accepted the baseline contract (operator-recorded demo consent)')
            store.save(p)
        return p

@app.post('/api/projects/{pid}/checkout')
async def checkout(pid: str):
    async with lock:
        p = project(pid)
        ensure(p, 'draft')
        if len(p['accepted']) != 2: raise HTTPException(409, 'Both parties must accept first.')
        previous = next((x for x in p['payments'] if x['change_id'] is None), None)
        return await complete_order(p, previous) if previous else await new_payment(p, p['contract']['price_cents'])

@app.post('/api/payments/{payment_id}/authorize')
async def authorize(payment_id: str):
    async with lock:
        p, payment = payment_for(payment_id)
        if p.get('engine') == 'agile_v2': raise HTTPException(409, 'Use the milestone authorization API.')
        if payment['status'] in ('authorized','captured'): return p
        if payment['status'] != 'awaiting_approval': raise HTTPException(409, 'Create checkout first.')
        ensure(p, 'draft', 'active', 'in_review', 'revision_requested')
        authorization_id = 'DEMO-AUTH-'+payment['id'] if MODE == 'demo' else await paypal.authorize(payment)
        payment.update(status='authorized', authorization_id=authorization_id, authorized_at=timestamp())
        if payment['change_id']:
            change = next(c for c in p['changes'] if c['id'] == payment['change_id'])
            change['status'] = 'funded'
            p['review_deadline'] = None
            p['status'] = 'active'
        else: p['status'] = 'active'
        event(p, 'Payment authorized. '+('Simulated funds only.' if MODE == 'demo' else 'Capture remains subject to PayPal availability.'))
        store.save(p)
        return p

@app.post('/api/projects/{pid}/changes')
async def change(pid: str, data: ChangeInput):
    async with lock:
        p = project(pid)
        ensure(p, 'active', 'in_review', 'revision_requested')
        if p['review_deadline'] and datetime.fromisoformat(p['review_deadline']) <= now():
            raise HTTPException(409, 'Review deadline passed; contact support.')
        contract = dict(p['contract'])
        contract['paid_additions'] = [c['request'] for c in p['changes'] if c['status']=='funded']
        decision = await ai.classify(contract, data.request)
        if decision.confidence < .75: decision.verdict = 'needs_review'
        if decision.verdict == 'in_scope' and p['revisions_used'] >= p['contract']['allowed_revisions']:
            decision.verdict = 'out_of_scope'
            decision.reasoning = 'The agreed revision allowance has been used. This additional revision requires a new approved quote.'
            decision.estimated_hours = 1
        cents = max(2000, decision.estimated_hours*p['contract']['hourly_rate_cents']) if decision.verdict == 'out_of_scope' else 0
        c = {'id':uid(), 'request':data.request, **decision.model_dump(), 'amount_cents':cents, 'status':'pending'}
        p['changes'].append(c)
        if decision.verdict == 'in_scope':
            p['revisions_used'] += 1
            c['status'] = 'accepted'
            p['status'] = 'revision_requested'
            p['review_deadline'] = None
        elif decision.verdict == 'needs_review':
            p['status'] = 'disputed'
            p['review_deadline'] = None
        event(p, 'Revision firewall: '+decision.verdict+'. '+decision.reasoning)
        store.save(p)
        if decision.verdict == 'out_of_scope': await new_payment(p, cents, c['id'])
        return project(pid)

@app.post('/api/projects/{pid}/changes/{cid}/checkout')
async def retry_change_checkout(pid: str, cid: str):
    async with lock:
        p = project(pid)
        ensure(p, 'active', 'in_review', 'revision_requested')
        c = next((c for c in p['changes'] if c['id']==cid and c['verdict']=='out_of_scope'), None)
        if not c: raise HTTPException(404, 'Change order not found.')
        pay = next((x for x in p['payments'] if x['change_id']==cid), None)
        return await complete_order(p, pay) if pay else await new_payment(p, c['amount_cents'], cid)

@app.post('/api/projects/{pid}/deliver')
async def deliver(pid: str, data: Delivery):
    async with lock:
        p = project(pid)
        ensure(p, 'active','revision_requested')
        p['deliveries'].append({**data.model_dump(mode='json'), 'at':timestamp()})
        p['status'] = 'in_review'
        p['review_deadline'] = (now()+timedelta(hours=72)).isoformat()
        event(p, 'Deliverable submitted. 72-hour UTC review clock started.')
        store.save(p)
        return p

@app.post('/api/projects/{pid}/approve')
async def approve(pid: str):
    async with lock: return await release(project(pid))

@app.post('/api/projects/{pid}/dispute')
async def dispute(pid: str, data: ChangeInput):
    async with lock:
        p = project(pid)
        ensure(p, 'active','in_review','revision_requested')
        p['status'] = 'disputed'
        p['review_deadline'] = None
        event(p, 'Dispute opened; capture paused. Reason: '+data.request)
        store.save(p)
        return p

@app.post('/api/projects/{pid}/resolve')
async def resolve(pid: str, data: ChangeInput):
    async with lock:
        p = project(pid)
        ensure(p, 'disputed')
        p['status'] = 'active'
        for c in p['changes']:
            if c['verdict']=='needs_review': c['status']='resolved'
        event(p, 'Operator recorded mutual resolution: '+data.request+'. New delivery required before release.')
        store.save(p)
        return p

@app.post('/api/projects/{pid}/demo-expire')
async def expire(pid: str):
    if MODE != 'demo': raise HTTPException(403, 'Time travel is available only in demo mode.')
    async with lock:
        p = project(pid)
        ensure(p, 'in_review')
        p['review_deadline'] = (now()-timedelta(seconds=1)).isoformat()
        event(p, 'Demo clock advanced by 72 hours')
        store.save(p)
    await run_due()
    return project(pid)

@app.post('/api/webhooks/paypal')
async def webhook(request: Request):
    if not os.getenv('PAYPAL_WEBHOOK_ID'):
        raise HTTPException(503, 'Sandbox webhook verification is not configured.')
    body = await request.json()
    headers = request.headers
    verification = await paypal.call('POST','/v1/notifications/verify-webhook-signature', {
        'auth_algo':headers.get('paypal-auth-algo'), 'cert_url':headers.get('paypal-cert-url'),
        'transmission_id':headers.get('paypal-transmission-id'), 'transmission_sig':headers.get('paypal-transmission-sig'),
        'transmission_time':headers.get('paypal-transmission-time'), 'webhook_id':os.getenv('PAYPAL_WEBHOOK_ID'), 'webhook_event':body})
    if verification.get('verification_status') != 'SUCCESS': raise HTTPException(400, 'Invalid PayPal signature.')
    if not body.get('id'): raise HTTPException(400, 'Missing webhook event ID.')
    # Verified inbox is durable and deduplicated. Reconciliation is intentionally operator-assisted.
    if db.enabled():
        from sqlalchemy import insert
        with db.engine().begin() as conn:
            if not db.one(conn,db.webhooks,db.webhooks.c.id==body['id']):
                conn.execute(insert(db.webhooks).values(id=body['id'],payload=body,status='received',created_at=db.now()))
                resource=body.get('resource',{})
                ids={resource.get('id'),*resource.get('supplementary_data',{}).get('related_ids',{}).values()}
                ids.update(x.get('seller_transaction_id') for x in resource.get('disputed_transactions',[]))
                # Fixed-role portal payments live in relational payment rows.
                for payment in db.rows(conn,db.milestone_payments):
                    known={payment['payload'].get(k) for k in ('order_id','authorization_id','capture_id')}-{None}
                    if known & (ids-{None}):
                        from sqlalchemy import update
                        if body.get('event_type') in ('CUSTOMER.DISPUTE.CREATED','PAYMENT.CAPTURE.REVERSED','PAYMENT.CAPTURE.REFUNDED','PAYMENT.AUTHORIZATION.VOIDED'):
                            payload={**payment['payload'],'attention':'PayPal reported '+body['event_type']+'. Payment paused.','provider_event_id':body['id']}
                            conn.execute(update(db.milestone_payments).where(db.milestone_payments.c.id==payment['id']).values(status='attention',payload=payload))
                            db.log(conn,'provider_payment_paused',None,payment['project_id'],event_id=body['id'],event_type=body['event_type'])
                        job_id='scan-'+payment['project_id'];job=db.one(conn,db.jobs,db.jobs.c.id==job_id)
                        if not job:conn.execute(insert(db.jobs).values(id=job_id,project_id=payment['project_id'],kind='step1_scan',payload={},status='queued',attempts=0,run_at=db.now()))
                        elif job['status'] in ('done','failed'):conn.execute(update(db.jobs).where(db.jobs.c.id==job_id).values(status='queued',run_at=db.now()))
                for row in db.rows(conn,db.projects,db.projects.c.archived==False):
                    p=db.unpack(row)
                    known={value for pay in p.get('payments',[]) for key,value in pay.items() if key in ('order_id','authorization_id','capture_id')}
                    known.update(c['id'] for pay in p.get('payments',[]) for c in pay.get('captures',[]))
                    if known & ids:
                        workspace.schedule(conn,p['id'],'reconcile',{'event_id':body['id']})
                        if body.get('event_type') in ('CUSTOMER.DISPUTE.CREATED','PAYMENT.CAPTURE.REVERSED'):
                            p['external_payment_issue']='Provider reported a dispute or reversal.'
                            db.save_project(p,conn)
        return {'received':True,'reconciliation':'queued'}
    with store.connection() as connection:
        connection.execute('INSERT OR IGNORE INTO webhook_events VALUES (?, ?)', (body['id'],json.dumps(body)))
    return {'received':True, 'reconciliation':'operator_required'}

app.mount('/static', StaticFiles(directory=ROOT/'static'), name='static')
import sys
agile.mount(app, sys.modules[__name__])
auth.mount(app)
judge_demo.mount(app)
workspace.mount(app)
from app import negotiation
from app import assurance
assurance.mount(app)
from app import payouts
payouts.mount(app)
from app import workflows
from app import funded_payments
funded_payments.mount(app)
workflows.mount(app)
negotiation.mount(app)
from app import project_assistant
project_assistant.mount(app)
@app.get('/')
def index(): return FileResponse(ROOT/'static'/('index.html' if db.secure() else 'operator.html'))

@app.get('/legacy',include_in_schema=False)
def legacy_index():return FileResponse(ROOT/'static/platform.html')
