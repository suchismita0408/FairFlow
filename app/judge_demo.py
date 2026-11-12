"""Private, browser-scoped judging workspaces. Never enables real payments."""
import copy
import os
import secrets
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import insert, update
from . import auth, db

COOKIE = 'fairflow_judge_demo'


def configure():
    # The zero-configuration Vercel demo deliberately uses disposable storage.
    if os.getenv('VERCEL') and os.getenv('FAIRFLOW_MODE', 'demo') in ('demo', 'sandbox') and not db.enabled():
        from cryptography.fernet import Fernet
        root = Path('/tmp/fairflow-judge-demo')
        root.mkdir(exist_ok=True)
        key = root / 'encryption.key'
        try:
            with key.open('x') as out:
                out.write(Fernet.generate_key().decode())
        except FileExistsError:
            pass
        os.environ['DATABASE_URL'] = 'sqlite:///' + str(root / 'demo.db')
        os.environ['TOKEN_ENCRYPTION_KEY'] = key.read_text().strip()
        os.environ['AUTH_ENABLED'] = 'true'
        os.environ['EMAIL_MODE'] = 'local'
        os.environ['RUN_EMBEDDED_WORKER'] = 'false'
        db.metadata.create_all(db.engine())


def enabled():
    return (os.getenv('FAIRFLOW_MODE', 'demo') in ('demo', 'sandbox') and db.secure()
            and db.enabled() and os.getenv('EMAIL_MODE', 'local') == 'local'
            and os.getenv('ENABLE_JUDGE_DEMO', 'true') == 'true')


class DemoInput(BaseModel):
    role: Literal['client', 'freelancer'] = 'client'
    action: Literal['start', 'switch', 'update'] = 'start'
    client_name: str = Field(default='Demo Client', min_length=1, max_length=100)
    freelancer_name: str = Field(default='Demo Freelancer', min_length=1, max_length=100)
    buyer_email: EmailStr | None = None
    receiver_email: EmailStr | None = None
    payment_mode: Literal['simulated', 'sandbox'] = 'simulated'


def require_sandbox():
    """Prevent walkthrough identities from accidentally invoking PayPal."""
    user = auth.actor.get()
    if not user or not db.enabled():
        return
    with db.engine().connect() as conn:
        row = db.one(conn, db.judge_demos, (db.judge_demos.c.client_id == user['id']) |
                     (db.judge_demos.c.freelancer_id == user['id']))
        if row and row['payload'].get('payment_mode') == 'simulated':
            raise HTTPException(409, 'This is a walkthrough with no PayPal transactions. Open the PayPal Sandbox demo to test checkout.')


def visit(conn, request):
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    row = db.one(conn, db.judge_demos, db.judge_demos.c.id == auth.digest(token))
    return row if row and row['expires_at'] > db.now() else None


def mount(app):
    @app.get('/api/auth/judge-demo')
    def status(request: Request):
        if not enabled():
            return {'enabled': False, 'active': False}
        with db.engine().connect() as conn:
            row = visit(conn, request)
            from . import platform_paypal
            configured = platform_paypal.configured() or bool(os.getenv('PAYPAL_CLIENT_ID') and os.getenv('PAYPAL_CLIENT_SECRET'))
            return {'enabled': True, 'active': bool(row), 'sandbox_configured': configured,
                    'settings': row['payload'] if row else {},
                    'project_id': row['project_id'] if row else None}

    @app.post('/api/auth/judge-demo')
    def enter(data: DemoInput, request: Request, response: Response):
        if not enabled():
            raise HTTPException(404, 'Quick demo is available only in an authenticated simulated-payment environment.')
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            raise HTTPException(403, 'Cross-origin demo requests are disabled.')
        with db.engine().begin() as conn:
            row = visit(conn, request)
            if row and data.action == 'start' and row['payload'].get('payment_mode') != data.payment_mode:
                row = None
            if not row:
                if data.action != 'start':
                    raise HTTPException(401, 'Start your own quick demo first.')
                key = auth.digest('judge-demo|' + (request.client.host if request.client else ''))
                if auth.bump_limit(conn, key) > 20:
                    raise HTTPException(429, 'Too many new demos. Try again later.')
                row, token = seed(conn, data)
                response.set_cookie(COOKIE, token, max_age=43200, httponly=True,
                                    secure=request.url.scheme == 'https', samesite='strict', path='/')
            elif data.action == 'update':
                if data.payment_mode != row['payload'].get('payment_mode'):
                    raise HTTPException(409, 'Start a separate demo to change payment mode.')
                settings = data.model_dump(mode='json', exclude={'role', 'action'})
                row['payload'] = settings
                conn.execute(update(db.judge_demos).where(db.judge_demos.c.id == row['id']).values(payload=settings))
                for role in ('client', 'freelancer'):
                    conn.execute(update(db.users).where(db.users.c.id == row[role + '_id']).values(name=settings[role + '_name']))
                save_recipient(conn, row['freelancer_id'], settings)
            user = db.one(conn, db.users, db.users.c.id == row[data.role + '_id'])
            result = auth.create_session(conn, user, response, request)
            result['redirect'] = f"/{data.role}/projects/{row['project_id']}"
            return result


def seed(conn, data):
    from .agreement_examples import example_agreement
    from . import negotiation
    token = secrets.token_urlsafe(48)
    settings = data.model_dump(mode='json', exclude={'role', 'action'})
    users = {}
    for role in ('client', 'freelancer'):
        uid = db.uid()
        user = dict(id=uid, email=f'judge-{uid}@example.com', name=settings[role + '_name'],
                    password_hash=auth.DUMMY_HASH, verified=True, created_at=db.now(), role=role, handle='judge-' + uid)
        conn.execute(insert(db.users).values(**user))
        profile = dict(country='IN', time_zone='Asia/Kolkata')
        profile.update(dict(account_type='individual', company_name='') if role == 'client' else
                       dict(professional_title='Frontend developer', primary_category='Frontend',
                            skills=['HTML', 'CSS'], experience_band='1-3', portfolio_link=None))
        conn.execute(insert(db.client_profiles if role == 'client' else db.freelancer_profiles)
                     .values(id=db.uid(), user_id=uid, payload=profile))
        users[role] = user
    terms = example_agreement()
    terms['payment']['currency'] = 'USD'
    terms['payment']['total_minor'] = 10000
    for milestone in terms['timeline']['milestones']:
        milestone.update(currency='USD', amount_minor=10000)
    terms['basics']['title'] = 'Judge demo: Coffee shop landing page'
    terms['parties'].update(client_name=data.client_name, freelancer_name=data.freelancer_name)
    p = dict(id=db.uid(), engine=negotiation.ENGINE, status='IN_NEGOTIATION', turn='freelancer',
             draft_owner='freelancer', draft=terms, created_at=db.now(), updated_at=db.now(),
             voided_versions=[], disputes={}, first_response=True, reminders=[], judge_demo=True)
    p['judge_demo_payment_mode'] = data.payment_mode
    conn.execute(insert(db.projects).values(id=p['id'], payload=p, version=1, archived=False, created_at=p['created_at']))
    for role, user in users.items():
        conn.execute(insert(db.members).values(id=db.uid(), project_id=p['id'], user_id=user['id'], role=role))
    p['thread_id'] = db.uid()
    conn.execute(insert(db.threads).values(id=p['thread_id'], project_id=p['id']))
    if data.payment_mode == 'sandbox':
        from .funded_payments import prepare_draft
        prepare_draft(conn, p, terms)
    negotiation.snapshot(conn, p, terms, users['client'])
    p.update(draft=copy.deepcopy(terms), draft_owner='freelancer')
    conn.execute(update(db.projects).where(db.projects.c.id == p['id']).values(payload=p))
    row = dict(id=auth.digest(token), client_id=users['client']['id'], freelancer_id=users['freelancer']['id'],
               project_id=p['id'], payload=settings, expires_at=auth.until(720))
    conn.execute(insert(db.judge_demos).values(**row))
    save_recipient(conn, users['freelancer']['id'], settings)
    return row, token


def save_recipient(conn, user_id, settings):
    if settings.get('payment_mode') != 'sandbox':
        return
    email = settings.get('receiver_email')
    existing = db.one(conn, db.sandbox_recipients, db.sandbox_recipients.c.user_id == user_id)
    if email:
        if existing:
            conn.execute(update(db.sandbox_recipients).where(db.sandbox_recipients.c.user_id == user_id).values(email=email.lower(), updated_at=db.now()))
        else:
            conn.execute(insert(db.sandbox_recipients).values(id=db.uid(), user_id=user_id, email=email.lower(), updated_at=db.now()))
