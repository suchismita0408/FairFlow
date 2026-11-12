from typing import Literal
"""Email/password identities, server-side sessions, CSRF, and project permissions."""
import os, secrets, hashlib
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field, model_validator, field_validator, HttpUrl
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from sqlalchemy.exc import IntegrityError
from argon2 import PasswordHasher
from argon2.low_level import Type
from argon2.exceptions import VerificationError, InvalidHashError
from sqlalchemy import select, insert, update, delete, case
from . import db

actor=ContextVar('fairflow_actor',default=None)
ph=PasswordHasher(time_cost=2,memory_cost=19456,parallelism=1,type=Type.ID)
DUMMY_HASH=ph.hash(secrets.token_urlsafe(32))
COOKIE='fairflow_session'
def digest(token):return hashlib.sha256(token.encode()).hexdigest()
def until(minutes):return (datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat()
def session_secure():return os.getenv('APP_BASE_URL','http://127.0.0.1:8000').startswith('https://')
def mailbox_verified(user):
    if not user.get('verified'):return False
    if os.getenv('EMAIL_MODE','local')!='smtp':return True
    with db.engine().connect() as conn:
        return bool(db.one(conn,db.audit,(db.audit.c.actor_id==user['id'])&(db.audit.c.kind=='email_verified_smtp')))

def view(user):
    result={k:user.get(k) for k in ('id','email','name','verified','role','handle')}
    result['verified']=mailbox_verified(user)
    result['support_reviewer']=user['email'].casefold() in {x.strip().casefold() for x in os.getenv('FAIRFLOW_SUPPORT_EMAILS','').split(',') if x.strip()}
    return result

def portal_role(expected=None):
    user=verify_required()
    if user.get('role') not in ('client','freelancer'):raise HTTPException(403,'This legacy account has no portal role. Register a separate portal identity.')
    if expected and user['role']!=expected:raise HTTPException(403,{'message':'This portal belongs to the other role.','redirect':f"/{user['role']}/dashboard"})
    return user
def current():
    user=actor.get()
    if not user:raise HTTPException(401,'Sign in to continue.')
    return user
def verify_required():
    user=current()
    if not user['verified']:raise HTTPException(403,'Verify your email first.')
    return user

def role(pid,roles=None):
    if not db.secure():return None
    user=verify_required()
    with db.engine().connect() as conn:member=db.one(conn,db.members,(db.members.c.project_id==pid)&(db.members.c.user_id==user['id']))
    if not member:raise HTTPException(404,'Project not found.')
    if roles and member['role'] not in roles:raise HTTPException(403,'This action is not permitted for your project role.')
    return member['role']

def mail(conn,user_id,email,subject,body):
    conn.execute(insert(db.mail).values(id=db.uid(),user_id=user_id,email=email,subject=subject,body=db.encrypt(body),status='preview' if os.getenv('EMAIL_MODE','local')=='local' else 'queued',created_at=db.now()))

def token_message(conn,user,kind):
    raw=secrets.token_urlsafe(32)
    token_kind=kind+'_smtp' if os.getenv('EMAIL_MODE','local')=='smtp' else kind
    conn.execute(insert(db.tokens).values(id=digest(raw),user_id=user['id'],kind=token_kind,expires_at=until(30 if kind=='reset' else 1440),used=False))
    base=os.getenv('APP_BASE_URL','http://127.0.0.1:8000').rstrip('/')
    mail(conn,user['id'],user['email'],'FairFlow '+('password reset' if kind=='reset' else 'email verification'),base+'/#'+kind+'='+raw)
    db.log(conn,'password_reset_requested' if kind=='reset' else 'verification_requested',user['id'])

def create_session(conn,user,response,request=None):
    raw=secrets.token_urlsafe(48);csrf=secrets.token_urlsafe(32)
    conn.execute(insert(db.sessions).values(id=digest(raw),user_id=user['id'],csrf=csrf,expires_at=until(720),created_at=db.now(),last_active_at=db.now(),ip=request.client.host if request and request.client else '',user_agent=request.headers.get('user-agent','')[:500] if request else ''))
    response.set_cookie(COOKIE,raw,max_age=43200,httponly=True,secure=session_secure() or bool(request and request.url.scheme=='https'),samesite='strict',path='/')
    return {'user':view(user),'csrf_token':csrf}

def throttle(request,email):
    key=digest((request.client.host if request.client else '')+'|'+email.lower())
    with db.engine().begin() as conn:
        count=bump_limit(conn,key)
    if count>10:raise HTTPException(429,'Too many attempts. Try again after the rate-limit window.')

def bump_limit(conn,key):
    # Upsert avoids a race when simultaneous attempts create a new limit bucket.
    if conn.dialect.name=='postgresql':from sqlalchemy.dialects.postgresql import insert as upsert
    else:from sqlalchemy.dialects.sqlite import insert as upsert
    expired=db.limits.c.reset_at<db.now()
    statement=upsert(db.limits).values(id=key,count=1,reset_at=until(15)).on_conflict_do_update(index_elements=['id'],set_={'count':case((expired,1),else_=db.limits.c.count+1),'reset_at':case((expired,until(15)),else_=db.limits.c.reset_at)}).returning(db.limits.c.count)
    return conn.execute(statement).scalar_one()

def login_lock(email, failed=False, clear=False):
    key=digest('account|'+email)
    denied=False
    with db.engine().begin() as conn:
        row=db.one(conn,db.limits,db.limits.c.id==key)
        if clear:
            conn.execute(delete(db.limits).where(db.limits.c.id==key));return
        if row and row['reset_at']>db.now() and row['count']>=5:denied=True
        elif failed:
            bump_limit(conn,key)
    if denied:raise HTTPException(429,'Account locked for 15 minutes after failed login attempts.')

PUBLIC_PATHS = {'/api/health', '/api/currencies', '/api/auth/register', '/api/auth/login', '/api/auth/demo-login', '/api/auth/forgot-password', '/api/auth/reset-password', '/api/webhooks/paypal'}
PUBLIC_PATHS.update('/api/'+role+'/'+action for role in ('client','freelancer') for action in ('signup','login'))
PUBLIC_PATHS.add('/api/auth/judge-demo')

async def authenticate(request):
    if not db.secure():return None
    raw=request.cookies.get(COOKIE)
    if not raw:return None
    with db.engine().begin() as conn:
        s=db.one(conn,db.sessions,db.sessions.c.id==digest(raw))
        if not s:return None
        if s['expires_at']<db.now() or (s.get('last_active_at') and datetime.fromisoformat(s['last_active_at'])<datetime.now(timezone.utc)-timedelta(minutes=30)):
            conn.execute(delete(db.sessions).where(db.sessions.c.id==s['id']))
            db.log(conn,'session_expired',s['user_id']);return None
        user=db.one(conn,db.users,db.users.c.id==s['user_id'])
        conn.execute(update(db.sessions).where(db.sessions.c.id==s['id']).values(last_active_at=db.now()))
    if not user:return None
    user['verified']=mailbox_verified(user)
    if request.url.path not in PUBLIC_PATHS:
        if request.method not in ('GET','HEAD','OPTIONS') and not secrets.compare_digest(request.headers.get('x-csrf-token',''),s['csrf']):
            raise HTTPException(403,'Invalid CSRF token. Reload and retry.')
    return {**user,'csrf_token':s['csrf']}

class Register(BaseModel):
    email:EmailStr
    name:str=Field(min_length=1,max_length=100)
    password:str=Field(min_length=12,max_length=256)
    @field_validator('name')
    @classmethod
    def nonblank_name(cls,value):
        if not value.strip():raise ValueError('Enter your full name.')
        return value.strip()
class Login(BaseModel):
    email:EmailStr
    password:str=Field(min_length=1,max_length=256)
class Email(BaseModel):email:EmailStr
class Token(BaseModel):token:str=Field(min_length=20,max_length=256)
class Reset(Token):password:str=Field(min_length=12,max_length=256)
class DemoLogin(BaseModel):role:Literal['client','freelancer']='client' 

class PortalSignup(Register):
    account_type:Literal['individual','company']='individual'
    company_name:str=Field(default='',max_length=200)
    country:str=Field(min_length=2,max_length=100)
    time_zone:str=Field(min_length=1,max_length=80)
    professional_title:str=Field(default='',max_length=150)
    primary_category:str=Field(default='',max_length=100)
    skills:list[str]=Field(default_factory=list,max_length=50)
    experience_band:Literal['0-1','1-3','3-5','5-10','10+']='0-1'
    portfolio_link:HttpUrl|None=None

    @field_validator('country')
    @classmethod
    def valid_country(cls,value):
        import pycountry
        try:return pycountry.countries.lookup(value).alpha_2
        except LookupError:raise ValueError('Choose a valid country.')

    @field_validator('time_zone')
    @classmethod
    def valid_zone(cls,value):
        try:ZoneInfo(value)
        except (ZoneInfoNotFoundError,ValueError):raise ValueError('Choose an IANA time zone.')
        return value

def mount(app):
    router=APIRouter(prefix='/api/auth',tags=['Accounts'])
    @router.post('/register',status_code=201)
    def register(data:Register,request:Request,response:Response):
        if os.getenv('ENABLE_LEGACY_ACCOUNTS','false')!='true':raise HTTPException(410,'Use /api/client/signup or /api/freelancer/signup. Every new account has one fixed role.')
        email=str(data.email).lower();throttle(request,email)
        with db.engine().begin() as conn:
            if db.one(conn,db.users,db.users.c.email==email):raise HTTPException(409,'An account already exists for this email.')
            user={'id':db.uid(),'email':email,'name':data.name,'password_hash':ph.hash(data.password),'verified':False,'created_at':db.now()}
            conn.execute(insert(db.users).values(**user))
            token_message(conn,user,'verify')
            db.log(conn,'account_registered',user['id'])
            return create_session(conn,user,response)
    @router.post('/demo-login')
    def demo_login(data: DemoLogin, response: Response):
        if os.getenv('ENABLE_LEGACY_DEMO_LOGIN','false')!='true':raise HTTPException(404,'Demo login is disabled. Use authenticated signup.')
        email = f"demo-{data.role}@fairflow.com"
        name = "Demo Client" if data.role == 'client' else "Demo Freelancer"
        with db.engine().begin() as conn:
            user = db.one(conn, db.users, db.users.c.email == email)
            if not user:
                user = {'id': db.uid(), 'email': email, 'name': name, 'password_hash': ph.hash('Password-123456!'), 'verified': True, 'created_at': db.now()}
                conn.execute(insert(db.users).values(**user))
                db.log(conn, 'account_registered', user['id'])
            elif not user['verified']:
                conn.execute(update(db.users).where(db.users.c.id == user['id']).values(verified=True))
                user = dict(user)
                user['verified'] = True
            return create_session(conn, user, response)

    @router.post('/login')
    def login(data:Login,request:Request,response:Response):
        return password_login(data,request,response)

    @router.get('/sessions')
    def active_sessions(request:Request):
        user=current()
        with db.engine().connect() as conn:
            return [{k:r.get(k) for k in ('id','created_at','last_active_at','ip','user_agent')}|{'current':r['id']==digest(request.cookies.get(COOKIE,''))} for r in db.rows(conn,db.sessions,db.sessions.c.user_id==user['id']) if r['expires_at']>db.now() and (not r['last_active_at'] or datetime.fromisoformat(r['last_active_at'])>datetime.now(timezone.utc)-timedelta(minutes=30))]

    @router.post('/logout-everywhere')
    def logout_everywhere(response:Response):
        user=current()
        with db.engine().begin() as conn:
            conn.execute(delete(db.sessions).where(db.sessions.c.user_id==user['id']))
            db.log(conn,'logout_everywhere',user['id'])
        response.delete_cookie(COOKIE,path='/');return {'ok':True}
    @router.get('/me')
    def me():
        user=current();return {'user':view(user),'csrf_token':user['csrf_token'],'email_mode':os.getenv('EMAIL_MODE','local')}
    @router.post('/logout')
    def logout(request:Request,response:Response):
        current()
        with db.engine().begin() as conn:
            conn.execute(delete(db.sessions).where(db.sessions.c.id==digest(request.cookies.get(COOKIE,''))))
            db.log(conn,'logout',current()['id'])
        response.delete_cookie(COOKIE,path='/');return {'ok':True}
    @router.post('/verify')
    def verify(data:Token):
        user=current()
        with db.engine().begin() as conn:
            t=conn.execute(select(db.tokens).where(db.tokens.c.id==digest(data.token)).with_for_update()).mappings().first()
            token_kind='verify_smtp' if os.getenv('EMAIL_MODE','local')=='smtp' else 'verify'
            if not t or t['kind']!=token_kind or t['used'] or t['expires_at']<db.now() or t['user_id']!=user['id']:raise HTTPException(400,'Invalid or expired verification link.')
            conn.execute(update(db.tokens).where(db.tokens.c.id==t['id']).values(used=True))
            conn.execute(update(db.users).where(db.users.c.id==user['id']).values(verified=True))
            db.log(conn,'email_verified',user['id'])
            if token_kind=='verify_smtp':db.log(conn,'email_verified_smtp',user['id'])
            return {'verified':True}
    @router.post('/resend-verification')
    def resend(request:Request):
        user=current();throttle(request,user['email'])
        with db.engine().begin() as conn:token_message(conn,user,'verify')
        return {'ok':True}
    @router.post('/forgot-password')
    def forgot(data:Email,request:Request):
        email=str(data.email).lower();throttle(request,email)
        with db.engine().begin() as conn:
            user=db.one(conn,db.users,db.users.c.email==email)
            if user:token_message(conn,user,'reset')
        return {'message':'If an account exists, a reset message has been queued.'}
    @router.post('/reset-password')
    def reset(data:Reset):
        with db.engine().begin() as conn:
            t=conn.execute(select(db.tokens).where(db.tokens.c.id==digest(data.token)).with_for_update()).mappings().first()
            token_kind='reset_smtp' if os.getenv('EMAIL_MODE','local')=='smtp' else 'reset'
            if not t or t['kind']!=token_kind or t['used'] or t['expires_at']<db.now():raise HTTPException(400,'Invalid or expired reset link.')
            conn.execute(update(db.tokens).where(db.tokens.c.user_id==t['user_id'],db.tokens.c.kind.in_(('reset','reset_smtp'))).values(used=True))
            conn.execute(update(db.users).where(db.users.c.id==t['user_id']).values(password_hash=ph.hash(data.password)))
            conn.execute(delete(db.sessions).where(db.sessions.c.user_id==t['user_id']))
            db.log(conn,'password_changed',t['user_id'])
        return {'message':'Password updated. Sign in again.'}
    @router.get('/mail')
    def preview():
        user=current()
        if os.getenv('EMAIL_MODE','local')!='local':raise HTTPException(404,'Local email preview is disabled.')
        with db.engine().connect() as conn:
            messages=db.rows(conn,db.mail,(db.mail.c.user_id==user['id'])|(db.mail.c.email==user['email']))
            return [{**m,'body':db.decrypt(m['body'])} for m in messages]
    app.include_router(router)

    for account_role in ('client','freelancer'):
        def signup_endpoint(expected):
            def signup(data:PortalSignup,request:Request,response:Response):
                email=str(data.email).lower();throttle(request,email)
                if expected=='client' and data.account_type=='company' and not data.company_name.strip():raise HTTPException(422,'Company name is required.')
                if expected=='freelancer' and not all((data.professional_title.strip(),data.primary_category.strip(),data.skills)):raise HTTPException(422,'Professional title, category and skills are required.')
                with db.engine().begin() as conn:
                    if db.one(conn,db.users,db.users.c.email==email):raise HTTPException(409,'One email can have only one account and role.')
                    user=dict(id=db.uid(),email=email,name=data.name,password_hash=ph.hash(data.password),verified=False,created_at=db.now(),role=expected,handle=db.uid()[:12])
                    try:conn.execute(insert(db.users).values(**user))
                    except IntegrityError:raise HTTPException(409,'An account already exists for this email.')
                    fields=('account_type','company_name','country','time_zone') if expected=='client' else ('professional_title','primary_category','skills','experience_band','country','time_zone','portfolio_link')
                    profile={k:v for k,v in data.model_dump(mode='json').items() if k in fields}
                    conn.execute(insert(db.client_profiles if expected=='client' else db.freelancer_profiles).values(id=db.uid(),user_id=user['id'],payload=profile))
                    token_message(conn,user,'verify');db.log(conn,'account_registered',user['id'],role=expected)
                    return create_session(conn,user,response,request)
            return signup
        def login_endpoint(expected):
            def login(data:Login,request:Request,response:Response):return password_login(data,request,response,expected)
            return login
        app.add_api_route('/api/'+account_role+'/signup',signup_endpoint(account_role),methods=['POST'],status_code=201)
        app.add_api_route('/api/'+account_role+'/login',login_endpoint(account_role),methods=['POST'])

def password_login(data,request,response,expected=None):
    email=str(data.email).lower();throttle(request,email);login_lock(email)
    with db.engine().connect() as conn:user=db.one(conn,db.users,db.users.c.email==email)
    try:valid=ph.verify(user['password_hash'] if user else DUMMY_HASH,data.password)
    except (VerificationError,InvalidHashError):valid=False
    if not user or not valid:
        with db.engine().begin() as conn:db.log(conn,'failed_login',user['id'] if user else None,email_digest=digest(email),ip=request.client.host if request.client else '')
        login_lock(email,failed=True)
        raise HTTPException(401,'Invalid email or password.')
    if expected and user.get('role')!=expected:
        with db.engine().begin() as conn:db.log(conn,'role_login_denied',user['id'],requested_role=expected)
        raise HTTPException(403,{'message':'Use your own portal to sign in.','redirect':f"/{user.get('role') or 'client'}/login"})
    login_lock(email,clear=True)
    with db.engine().begin() as conn:
        old=request.cookies.get(COOKIE)
        if old:conn.execute(delete(db.sessions).where(db.sessions.c.id==digest(old)))
        if ph.check_needs_rehash(user['password_hash']):conn.execute(update(db.users).where(db.users.c.id==user['id']).values(password_hash=ph.hash(data.password)))
        db.log(conn,'login',user['id'])
        return create_session(conn,user,response,request)

def validate_role(pid,claimed):
    actual=role(pid)
    if db.secure() and actual!=claimed:raise HTTPException(403,'You may only accept for your own project role.')

async def guard(request):
    user=await authenticate(request)
    path=request.url.path
    if path.startswith('/api/') and path not in PUBLIC_PATHS and not user:raise HTTPException(401,'Sign in to continue.')
    if path.startswith('/api/') and request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):raise HTTPException(403,'Cross-origin writes are disabled.')
    if path.startswith('/api/') and not path.startswith('/api/auth/') and path not in PUBLIC_PATHS and user and not user['verified']:
        raise HTTPException(403,'Verify your email first.')
    return user

def authorize_path(request):
    path=request.url.path
    user=actor.get()
    if user and user.get('role') and path.startswith(('/api/platform/','/api/agile/','/api/projects','/api/payments/','/api/contracts/')):
        raise HTTPException(403,'Step 1 accounts use their role portal. Payment and AI decision workflows are unavailable.')
    if path.startswith(('/api/client/','/api/freelancer/','/api/support/')):return
    if not path.startswith('/api/') or path.startswith(('/api/auth/','/api/platform/')) or path in ('/api/health','/api/webhooks/paypal'):return
    if path in ('/api/projects','/api/agile/projects') and request.method=='POST':raise HTTPException(409,'Create projects through the authenticated project room.')
    if path.startswith('/api/payments/'):raise HTTPException(409,'Legacy payment actions are archived; use authenticated milestones.')
    parts=path.split('/')
    if 'projects' not in parts:return
    index=parts.index('projects')
    if len(parts)<=index+1:return
    pid=parts[index+1]
    actual=role(pid)
    if request.method=='GET':return
    if not path.startswith('/api/agile/'):raise HTTPException(409,'Legacy project mutations are disabled in authenticated mode.')
    action=parts[-1]
    if action in ('resolve','retry','changes','progress','partial-settle') or (action=='accept' and parts[-2]=='progress') or (action=='accept' and parts[-2]!='progress'):raise HTTPException(409,'Use versioned acceptance or the joint decision workflow.')
    if action=='accept' and parts[-2]=='progress' and actual!='client':raise HTTPException(403,'Only the client can approve progress.')
    client_actions=('fund','confirm-payment','approve','setup','confirm','revoke')
    if action in client_actions and actual!='client':raise HTTPException(403,'Only the project client can perform this action.')
    if action in ('deliver','progress') and actual!='freelancer':raise HTTPException(403,'Only the freelancer can submit work or progress.')
    if action=='partial-settle' and actual!='client':raise HTTPException(403,'Only the client can settle approved partial progress manually.')
