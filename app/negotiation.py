"""Step 1 human-only agreement lifecycle. No payment or AI decision imports.

Drafts are private to the editor. Sent snapshots, signatures and chat are evidence.
Every project mutation uses a database row lock and a conditional revision write.
"""
import copy, json, os, asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, available_timezones
from pathlib import Path
import pycountry
from fastapi import APIRouter, Depends, HTTPException, Request, Response, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from sqlalchemy import select, insert, update
from . import db, auth, guided_agreement as guided
from .agreement_schema import CATALOG, SECTIONS, AgreementDraft, clean_draft, leaves, at, field_spec
from .agreement_validation import validate, digest
from .agreement_renderer import render, pdf, assert_coverage
from .payment_policy import summary as payment_summary,automatic_release_agreed
from . import agreement_amendments as amendments

Portal=Literal['client','freelancer']
ENGINE='agreement_step1'
TERMINAL={'DECLINED','WITHDRAWN','EXPIRED','CANCELLED_BEFORE_AGREEMENT','FREELANCER_WITHDRAWN'}
DECLINE_REASONS=['capacity','budget','timeline','scope','skills','other']
ROOT=Path(__file__).resolve().parent.parent

@contextmanager
def transaction():
    with db.engine().begin() as conn:
        # SQLite has no row locks. Acquire its local write reservation before
        # reading a revision, so duplicate signatures become clean conflicts.
        if conn.dialect.name=='sqlite':conn.exec_driver_sql('BEGIN IMMEDIATE')
        yield conn

class Input(BaseModel):model_config=ConfigDict(extra='forbid')
class NewProject(Input):agreement:AgreementDraft=Field(default_factory=AgreementDraft)
class ValidateProject(NewProject):project_id:str|None=None
class Revision(Input):revision:int=Field(ge=1)
class Save(Revision):
    agreement:AgreementDraft
    review_conversation_hash:str|None=None
    review_agreement_hash:str|None=None
class Action(Revision):
    action:Literal['invite','open','send','counter','approve','sign','reject','resolve-field','decline','withdraw','cancel','resend','nudge','amend','reject-amendment','delete','restore','freelancer-withdraw','acknowledge-withdrawal','acknowledge','edit-invitation','confirm-chat-price']
    recipient:str=Field(default='',max_length=254)
    reason:str=Field(default='',max_length=3000)
    reason_code:str=Field(default='',max_length=50)
    version_hash:str=Field(default='',max_length=64)
    typed_name:str=Field(default='',max_length=200)
    payment_rules_confirmed:bool=False
    field_path:str=Field(default='',max_length=300)
    decisions:list[dict[str,str]]=Field(default_factory=list,max_length=300)
    flag_id:str=Field(default='',max_length=64)
class Chat(Input):
    body:str=Field(min_length=1,max_length=5000)
    field_path:str|None=Field(default=None,max_length=300)

class ChatTermRecord(Revision):
    field_path:str=Field(max_length=300)
    expected:object=None
    typed_name:str=Field(max_length=200)

class ChatTermWithdraw(Revision):
    reason:str=Field(min_length=10,max_length=3000)
    typed_name:str=Field(max_length=200)

class ProfessionalProfile(Input):
    professional_title:str=Field(min_length=1,max_length=150)
    primary_category:str=Field(min_length=1,max_length=100)
    skills:list[str]=Field(min_length=1,max_length=50)
    experience_band:Literal['0-1','1-3','3-5','5-10','10+']
    country:str
    time_zone:str
    portfolio_link:str|None=None

def other(role):return 'freelancer' if role=='client' else 'client'
def require(condition,message,status=409):
    if not condition:raise HTTPException(status,message)
def portal_user(portal:Portal):return auth.portal_role(portal)
def project(conn,pid,user,lock=False):
    member=db.one(conn,db.members,(db.members.c.project_id==pid)&(db.members.c.user_id==user['id']))
    if not member or member['role']!=user['role']:raise HTTPException(404,'Project not found.')
    query=select(db.projects).where(db.projects.c.id==pid)
    if lock:query=query.with_for_update()
    row=conn.execute(query).mappings().first()
    if not row or row['archived'] or row['payload'].get('engine')!=ENGINE:raise HTTPException(404,'Project not found.')
    if user['role']=='freelancer' and row['payload']['status']=='DRAFT':raise HTTPException(404,'Project not found.')
    payload=copy.deepcopy(row['payload'])
    from .funded_payments import inherit_seller
    inherit_seller(conn,payload)
    return payload,row['version']
def write(conn,p,revision):
    p['updated_at']=db.now()
    result=conn.execute(update(db.projects).where(db.projects.c.id==p['id'],db.projects.c.version==revision).values(payload=p,version=revision+1))
    require(result.rowcount==1,'Project changed concurrently. Reload to review the latest revision.')
def sent(conn,p):return db.one(conn,db.agreements,db.agreements.c.id==p.get('version_id'))
def signed(conn,version_id):return db.rows(conn,db.signatures,db.signatures.c.agreement_id==version_id) if version_id else []
def approvals(conn,version_id):return db.rows(conn,db.acceptances,db.acceptances.c.agreement_id==version_id) if version_id else []
def chat_price_conflicts(conn,p,v):
    if not v or not p.get('thread_id'):return []
    from .agreement_assistant import price_chat_issues
    messages=sorted((m for m in db.rows(conn,db.chat_messages,db.chat_messages.c.thread_id==p['thread_id']) if m['kind']=='text'),key=lambda m:(m['created_at'],m['id']))
    conversation_hash=digest([(m['id'],m['body'],m['field_path']) for m in messages])
    confirmed=p.get('chat_price_confirmations',{})
    return [dict(**issue,confirmation_key=digest([v['digest'],conversation_hash,issue['id']]),confirmed_by=confirmed.get(digest([v['digest'],conversation_hash,issue['id']]),{})) for issue in price_chat_issues(v['payload'],messages)]
def current_flags(p,payload,temporal=False,conn=None):
    try:zone=ZoneInfo(at(payload,'timeline.time_zone','UTC'))
    except (ValueError,KeyError):zone=ZoneInfo('UTC')
    clock=datetime.now(zone).date()
    done=amendments.protected_ids(conn,p) if conn is not None and p.get('active_agreement_id') else []
    return validate(payload,today=clock if temporal else None,completed_milestones=done,remaining_start_date=clock if done else None)
def eligible(p,payload,*,warnings=True,temporal=False,conn=None,defer_owner=None):
    if conn is not None and p.get('amendment'):amendments.enforce(conn,p,payload)
    flags=current_flags(p,payload,temporal,conn)
    blocking=[f for f in flags if f['severity']=='BLOCKER' or warnings and f['severity']=='WARNING' and f['status']!='acknowledged']
    if defer_owner:blocking=[f for f in blocking if guided.owner(f['field_path'])!=defer_owner]
    if blocking:raise HTTPException(422,dict(message='Resolve agreement flags before proceeding.',flags=flags))
    require(not p.get('disputes'),'Resolve or justify every disputed field before proceeding.')
    return flags
def system(conn,p,kind,body,actor=None,**data):
    db.log(conn,kind,actor,p['id'],message=body,**data)
    if p.get('thread_id'):
        conn.execute(insert(db.chat_messages).values(id=db.uid(),thread_id=p['thread_id'],user_id=None,body=body,kind='system',created_at=db.now()))
def notify(conn,p,kind,body,recipient_role=None):
    for member in db.rows(conn,db.members,db.members.c.project_id==p['id']):
        if recipient_role and recipient_role!=member['role']:continue
        user=db.one(conn,db.users,db.users.c.id==member['user_id'])
        conn.execute(insert(db.notifications).values(id=db.uid(),project_id=p['id'],user_id=user['id'],kind=kind,body=body,read=False,created_at=db.now()))
        auth.mail(conn,user['id'],user['email'],'FairFlow: '+kind.replace('_',' '),body)
def void(conn,p,user):
    vid=p.get('version_id')
    if vid and vid!=p.get('active_agreement_id') and vid not in p.get('voided_versions',[]):
        p.setdefault('voided_versions',[]).append(vid)
        system(conn,p,'consent_voided','Editing voided approvals and signatures on the previous proposed version.',user['id'],version_id=vid)
def snapshot(conn,p,payload,user):
    old=sent(conn,p);number=conn.execute(select(db.agreements.c.number).where(db.agreements.c.project_id==p['id']).order_by(db.agreements.c.number.desc()).limit(1)).scalar() or 0
    version=dict(id=db.uid(),project_id=p['id'],number=number+1,digest=digest(payload),payload=copy.deepcopy(payload),status='sent',created_by=user['id'],created_at=db.now(),parent_id=old['id'] if old else p.get('active_agreement_id'))
    conn.execute(insert(db.agreements).values(**version))
    before=dict(leaves(old['payload'])) if old else {};after=dict(leaves(payload))
    for path in sorted(before.keys()|after.keys()):
        if before.get(path)!=after.get(path):conn.execute(insert(db.field_changes).values(id=db.uid(),agreement_id=version['id'],field_path=path,before=before.get(path),after=after.get(path),actor_id=user['id'],created_at=db.now()))
    for flag in validate(payload):conn.execute(insert(db.validation_flags).values(id=db.uid(),agreement_id=version['id'],flag_id=flag['id'],payload=flag))
    if old and old['id']!=p.get('active_agreement_id'):void(conn,p,user)
    p.update(version_id=version['id'],draft=None,draft_owner=None,disputes={})
    system(conn,p,'version_sent',f"Version {number+1} sent for review.",user['id'],version_hash=version['digest'])
    return version

def detail(conn,p,revision,user):
    amendment_blocked=amendments.blocked_reason(conn,p)
    done=amendments.protected_ids(conn,p)
    versions=db.rows(conn,db.agreements,db.agreements.c.project_id==p['id'])
    for v in versions:
        v['approvals']=approvals(conn,v['id']);v['signatures']=signed(conn,v['id'])
        v['voided']=v['id'] in p.get('voided_versions',[])
    v=next((v for v in versions if v['id']==p.get('version_id')),None)
    owns=p.get('draft_owner')==user['role']
    payload=p.get('draft') if owns and p.get('draft') is not None else v['payload'] if v else {}
    read=db.one(conn,db.chat_reads,(db.chat_reads.c.thread_id==p.get('thread_id'))&(db.chat_reads.c.user_id==user['id']))
    messages=db.rows(conn,db.chat_messages,db.chat_messages.c.thread_id==p.get('thread_id')) if p.get('thread_id') else []
    from .early_start import rendering as effective_render
    active_version=next((x for x in versions if x['id']==p.get('active_agreement_id')),None)
    effective_export=effective_render(p,active_version) if active_version else None
    if p['status']=='AGREED' and effective_export:payload=effective_export['agreement']
    from .chat_terms import changes as chat_changes
    project_data={k:v for k,v in p.items() if k not in ('draft',)}
    if p.get('platform_seller'):
        from .funded_payments import seller_view
        project_data['platform_seller']=seller_view(p,user)
    return dict(chat_term_changes=chat_changes(conn,p,payload),has_unshared_changes=bool(v and p.get('draft') is not None and digest(p['draft'])!=v['digest']),project=project_data,revision=revision,agreement=payload,versions=versions,amendment_blocked_reason=amendment_blocked,amendment_locks=amendments.locks(conn,p),payment_rules=payment_summary(v['payload'] if v else payload,done,datetime.now(timezone.utc).date() if done else None),chat_price_conflicts=chat_price_conflicts(conn,p,v),flags=guided.brief_flags(payload,datetime.now(timezone.utc).date()) if p.get('guided') and p['status']=='DRAFT' else current_flags(p,payload,conn=conn),
        can_edit=owns and p['turn']==user['role'] and p['status'] not in TERMINAL|{'AGREED'},
        changes=db.rows(conn,db.field_changes,db.field_changes.c.agreement_id==p.get('version_id')) if v else [],
        messages=sorted(messages,key=lambda m:(m['created_at'],m['id'])),unread_count=sum(m['user_id']!=user['id'] and (not read or m['created_at']>read['read_at']) for m in messages),
        activity=db.rows(conn,db.audit,db.audit.c.project_id==p['id']),
        rendered=render(payload,v['id'] if v else p['id'],v['number'] if v else 0) if not any(f['severity']=='BLOCKER' for f in validate(payload)) else None,
        active_rendered=effective_export,
        members=[{'role':m['role'],'name':db.one(conn,db.users,db.users.c.id==m['user_id'])['name']} for m in db.rows(conn,db.members,db.members.c.project_id==p['id'])])

def invite(conn,p,data,user):
    require(user['role']=='client','Only clients send invitations.',403)
    require(p['status'] in ('DRAFT','EXPIRED'),'Invitation is not available in this state.')
    payload=p.get('draft') or sent(conn,p)['payload']
    if p.get('guided'):
        flags=guided.brief_flags(payload,datetime.now(timezone.utc).date())
        if flags:raise HTTPException(422,dict(message='Complete the short client brief first.',flags=flags))
    else:eligible(p,payload,warnings=False,temporal=True,conn=conn)
    require(bool(data.recipient.strip()),'Enter a known freelancer email or platform handle.',422)
    recipient=data.recipient.strip().lower()
    target=db.one(conn,db.users,(db.users.c.email==recipient)|(db.users.c.handle==recipient.lstrip('@')))
    require(not target or target.get('role')=='freelancer','The recipient must have a freelancer account.',422)
    if not target:
        try:recipient=str(EmailStr._validate(recipient)).lower()
        except ValueError:raise HTTPException(422,'Use a valid email or an existing freelancer handle.')
    else:recipient=target['email']
    # Reinvitations cannot silently replace an established counterparty.
    existing=db.rows(conn,db.members,(db.members.c.project_id==p['id'])&(db.members.c.role=='freelancer'))
    if existing:require(target and existing[0]['user_id']==target['id'],'Re-invite the original freelancer or create a new project.')
    if target and not existing:conn.execute(insert(db.members).values(id=db.uid(),project_id=p['id'],user_id=target['id'],role='freelancer'))
    if not p.get('thread_id'):
        p['thread_id']=db.uid();conn.execute(insert(db.threads).values(id=p['thread_id'],project_id=p['id']))
    version=snapshot(conn,p,payload,user)
    expiration=(datetime.now(timezone.utc)+timedelta(days=int(os.getenv('INVITATION_EXPIRY_DAYS','7')))).isoformat()
    invitation=dict(id=db.uid(),project_id=p['id'],email=recipient,role='freelancer',expires_at=expiration)
    conn.execute(insert(db.invitations).values(**invitation))
    p.update(status='INVITED',turn='freelancer',invitation_id=invitation['id'],invitation_expires_at=expiration,recipient_email=recipient,first_response=False,reminders=[])
    auth.mail(conn,target['id'] if target else None,recipient,'FairFlow invitation',f"You have been invited to review an agreement. Sign in at /freelancer/login and open your inbox. Invitation {invitation['id']} expires {expiration}.")
    system(conn,p,'invited','Client invited a specific freelancer.',user['id'],expires_at=expiration)
    notify(conn,p,'invite','An agreement is ready for review.','freelancer')

def apply(conn,p,data,user,request):
    action=data.action;state=p['status'];role=user['role'];v=sent(conn,p)
    if action in ('delete','restore'):
        require(role=='client','Only the client can remove or restore projects.',403)
        if action=='restore':
            require(p.get('hidden_by_client'),'This project is already in your list.')
            p.pop('hidden_by_client',None)
            system(conn,p,'project_restored','The client restored this project to their project list.',user['id'])
        elif state=='DRAFT':
            p['deleted']=True
            system(conn,p,'delete','The client deleted their unsent draft.',user['id'])
        else:
            require(not p.get('hidden_by_client'),'This project is already removed from your list.')
            p['hidden_by_client']=True
            body='The client removed this project from their project list. The agreement, chat and existing payment rules remain unchanged.'
            system(conn,p,'project_removed',body,user['id']);notify(conn,p,'project_removed',body,'freelancer')
        return
    if action=='invite':
        require(not p.get('withdrawal'),'A withdrawn project cannot be re-invited.')
        return invite(conn,p,data,user)
    require(state not in TERMINAL,'This project is read-only in its terminal state.')
    if action=='freelancer-withdraw':
        require(role=='freelancer','Only the freelancer can withdraw from work.',403)
        require(len(data.reason.strip())>=10,'Explain why you cannot continue in at least 10 characters.',422)
        require(not p.get('withdrawal'),'Withdrawal has already been recorded.')
        p['withdrawal']={'status':'requested' if p.get('active_agreement_id') else 'accepted','reason':data.reason.strip(),'requested_at':db.now(),'user_id':user['id']}
        if not p.get('active_agreement_id'):
            void(conn,p,user);p.update(status='FREELANCER_WITHDRAWN',draft=None,draft_owner=None)
        elif p.get('amendment'):
            void(conn,p,user);p.update(status='AGREED',version_id=p['active_agreement_id'],amendment=False,draft=None,draft_owner=None)
        body='The freelancer withdrew from remaining work: '+data.reason.strip()+'. New milestone funding is stopped. Previously submitted work retains its agreed review and payment rules. Discuss any unfinished funded work in this chat.'
        system(conn,p,'freelancer_withdrawal',body,user['id']);notify(conn,p,'freelancer_withdrawal',body,'client')
        return
    if action=='acknowledge-withdrawal':
        require(role=='client','Only the client can acknowledge freelancer withdrawal.',403)
        require(p.get('withdrawal',{}).get('status')=='requested','There is no pending withdrawal.')
        p['withdrawal'].update(status='accepted',acknowledged_at=db.now(),acknowledged_by=user['id'])
        body='The client acknowledged withdrawal from remaining work. Previously submitted work keeps its agreed review and payment rules; no payment or refund was created by this acknowledgment.'
        system(conn,p,'withdrawal_acknowledged',body,user['id']);notify(conn,p,'withdrawal_acknowledged',body,'freelancer')
        return
    require(not p.get('withdrawal'),'Remaining work is closed following freelancer withdrawal. Use chat to discuss existing deliveries and payments.')
    if action=='open':
        require(role=='freelancer' and state=='INVITED','Only the invited freelancer can open the brief.',403)
        require(p['invitation_expires_at']>db.now(),'This invitation has expired.')
        p.update(status='IN_NEGOTIATION',first_response=True,turn=role,draft=copy.deepcopy(v['payload']),draft_owner=role)
        if p.get('guided'):
            profile=db.one(conn,db.freelancer_profiles,db.freelancer_profiles.c.user_id==user['id'])
            guided.complete_profile(p['draft'],user,profile['payload'] if profile else {})
        conn.execute(update(db.invitations).where(db.invitations.c.id==p['invitation_id']).values(accepted_by=user['id']))
    elif action=='edit-invitation':
        require(role=='client' and state=='INVITED' and not p.get('first_response'),'Client edits stop when the freelancer opens the invitation.',403)
        p.update(turn='client',draft_owner='client',draft=copy.deepcopy(v['payload']))
    elif action in ('counter','amend'):
        if action=='amend':
            require(state=='AGREED','Amendments start from an active signed agreement.')
            reason=amendments.blocked_reason(conn,p)
            require(not reason,reason)
            p.update(status='AMENDMENT_PROPOSED',amendment=True,turn=role,draft=copy.deepcopy(v['payload']),draft_owner=role,amendment_financial_snapshot=amendments.financial_snapshot(conn,p))
            from .workflows import effective_terms,OPEN
            require(not any(r['status'] in OPEN for r in p.get('workflow_requests',[])),'Finish or withdraw open workflow proposals before amending.')
            operational=effective_terms(p,v)['payload']
            paid=set(amendments.protected_ids(conn,p))
            for m in at(p['draft'],'timeline.milestones',[]):
                effective=next(x for x in at(operational,'timeline.milestones',[]) if x['id']==m['id'])
                for key in ('start_date','due_date','bumped_date'):
                    if key in effective:m[key]=effective[key]
            p['draft']['timeline']['deadline']=at(operational,'timeline.deadline')
            p['draft']['timeline']['start_date']=at(operational,'timeline.start_date')
            if at(operational,'scope.dependencies') is not None:p['draft']['scope']['dependencies']=copy.deepcopy(at(operational,'scope.dependencies'))
            from .funded_payments import prepare_draft
            prepare_draft(conn,p,p['draft'])
        else:
            require(state in ('IN_NEGOTIATION','READY_TO_SIGN','PARTIALLY_SIGNED','AMENDMENT_PROPOSED'),'A counter is not allowed now.')
            require(p['turn']==role or state in ('READY_TO_SIGN','PARTIALLY_SIGNED'),'The other party holds the turn.')
            if state=='PARTIALLY_SIGNED':void(conn,p,user)
            p.update(status='AMENDMENT_PROPOSED' if p.get('amendment') else 'IN_NEGOTIATION',turn=role,draft=copy.deepcopy(v['payload']),draft_owner=role)
            from .funded_payments import prepare_draft
            prepare_draft(conn,p,p['draft'])
    elif action=='send':
        require(state in ('INVITED','IN_NEGOTIATION','AMENDMENT_PROPOSED') and p['turn']==role and p.get('draft_owner')==role,'Only the turn holder can send a counter.')
        if state=='INVITED' and p.get('guided'):
            flags=guided.brief_flags(p['draft'],datetime.now(timezone.utc).date())
            if flags:raise HTTPException(422,dict(message='Complete the client brief.',flags=flags))
        else:eligible(p,p['draft'],warnings=False,temporal=True,conn=conn,defer_owner=other(role))
        snapshot(conn,p,p['draft'],user)
        p.update(turn=other(role),status='INVITED' if state=='INVITED' else 'AMENDMENT_PROPOSED' if p.get('amendment') else 'IN_NEGOTIATION')
        notify(conn,p,'turn_change','A new version is ready for your response.',other(role))
    elif action in ('approve','sign'):
        require(v is not None and state in ('IN_NEGOTIATION','READY_TO_SIGN','PARTIALLY_SIGNED','AMENDMENT_PROPOSED'),'This version is not available for approval/signing.')
        require(v['id'] not in p.get('voided_versions',[]),'This version was voided by an edit. Send the revised draft first.')
        require(not (p.get('draft') is not None and digest(p['draft'])!=v['digest']),'Send or discard your private edits before approving or signing.')
        require(data.version_hash==v['digest'],'Review the exact current hash before approving or signing.')
        require(digest(v['payload'])==v['digest'],'Stored agreement hash does not match its content.')
        from .chat_terms import require_current
        from .chat_monitor import pending_owner_reviews
        require(not pending_owner_reviews(p,v['payload']),'AI-proposed field changes need approval from their field owners before full agreement approval.')
        require_current(conn,p,v['payload'])
        eligible(p,v['payload'],temporal=True,conn=conn)
        if action=='sign':
            require(data.payment_rules_confirmed is True,'Review and confirm the 29-day payment rules before signing.',422)
            require(automatic_release_agreed(v['payload'],amendments.protected_ids(conn,p)),'Every unfunded milestone must include automatic release after its agreed review period. Update the proposal and obtain both approvals before signing.',422)
            unresolved=[issue for issue in chat_price_conflicts(conn,p,v) if not all(issue['confirmed_by'].get(r,{}).get('reason') for r in ('client','freelancer'))]
            require(not unresolved,{'message':'Both parties must confirm every chat price difference for this version before signing.','chat_price_conflicts':unresolved},422)
        if action=='approve':
            require(not any(x['user_id']==user['id'] for x in approvals(conn,v['id'])),'You already approved this version.')
            conn.execute(insert(db.acceptances).values(id=db.uid(),agreement_id=v['id'],user_id=user['id'],role=role,digest=v['digest'],created_at=db.now()))
            if len(approvals(conn,v['id']))==2:p['status']='READY_TO_SIGN'
            notify(conn,p,'approval','A participant approved the current version.',other(role))
        else:
            require(state in ('READY_TO_SIGN','PARTIALLY_SIGNED'),'Both parties must approve before signing.')
            require(len(approvals(conn,v['id']))==2,'Both accounts must approve this exact version.')
            require(data.typed_name.strip().casefold()==user['name'].strip().casefold(),'Type your full account name to confirm signing.',422)
            require(not any(s['user_id']==user['id'] for s in signed(conn,v['id'])),'You already signed this hash.')
            conn.execute(insert(db.signatures).values(id=db.uid(),agreement_id=v['id'],user_id=user['id'],version_hash=v['digest'],typed_name=data.typed_name.strip(),ip=request.client.host if request.client else '',user_agent=request.headers.get('user-agent','')[:500],created_at=db.now()))
            db.log(conn,'payment_rules_confirmed',user['id'],p['id'],version_hash=v['digest'],rules=payment_summary(v['payload']))
            p['status']='AGREED' if len(signed(conn,v['id']))==2 else 'PARTIALLY_SIGNED'
            if p['status']=='AGREED':
                p.update(active_agreement_id=v['id'],amendment=False,draft=None,draft_owner=None)
                from .workflow_policy import POLICY_TEXT,POLICY_VERSION
                if at(v['payload'],'termination.delivery_rules')==POLICY_TEXT:
                    p['workflow_policy']={'version':POLICY_VERSION,'text':POLICY_TEXT,'agreement_id':v['id'],'accepted_at':db.now()}
            notify(conn,p,'signature','The agreement has a new signature.',other(role))
    elif action=='confirm-chat-price':
        require(v and state in ('IN_NEGOTIATION','READY_TO_SIGN','PARTIALLY_SIGNED','AMENDMENT_PROPOSED'),'Price clarification is available on a sent version.')
        require(data.version_hash==v['digest'],'Review the exact current hash before confirming.')
        require(bool(data.reason.strip()),'Explain why the agreement amount is the final amount.',422)
        issue=next((item for item in chat_price_conflicts(conn,p,v) if item['id']==data.flag_id),None)
        require(issue is not None,'This chat price question changed. Review it again.',409)
        confirmations=p.setdefault('chat_price_confirmations',{}).setdefault(issue['confirmation_key'],{})
        require(role not in confirmations,'You already confirmed this chat price question.')
        confirmations[role]={'reason':data.reason.strip(),'at':db.now(),'user_id':user['id']}
    elif action=='acknowledge':
        require(v and state in ('IN_NEGOTIATION','AMENDMENT_PROPOSED'),'Acknowledge warnings on a sent negotiation version.')
        require(bool(data.reason.strip()),'An acknowledgment reason is mandatory.',422)
        flag=next((f for f in validate(v['payload']) if f['id']==data.flag_id and f['severity']=='WARNING'),None)
        require(flag is not None,'Only existing warnings can be acknowledged; blockers require data changes.',422)
        payload=copy.deepcopy(v['payload']);acks=payload.setdefault('acknowledgments',{}).setdefault(flag['id'],{})
        require(role not in acks,'You already acknowledged this warning.')
        acks[role]=data.reason.strip()
        snapshot(conn,p,payload,user)
        # Each acknowledgment is itself immutable, included in the signed hash.
        p['status']='AMENDMENT_PROPOSED' if p.get('amendment') else 'IN_NEGOTIATION'
    elif action=='reject':
        require(v and p['turn']==role and state in ('IN_NEGOTIATION','AMENDMENT_PROPOSED'),'Only the receiving party can reject fields.')
        require(bool(data.decisions),'Provide field-level decisions.',422)
        require(len({d.get('field_path') for d in data.decisions})==len(data.decisions),'Decide each field only once per response.',422)
        disputes=p.setdefault('disputes',{})
        for decision in data.decisions:
            path=decision.get('field_path','');choice=decision.get('decision');reason=decision.get('reason','').strip()
            require(field_spec(path) is not None and at(v['payload'],path) is not None,'Choose an existing agreement field.',422)
            require(choice in ('accept','reject'),'Choose accept or reject.',422)
            if choice=='reject':
                require(bool(reason),'Every rejected field requires a reason.',422)
                disputes[path]=dict(reason=reason,rejected_by=user['id'],version_hash=v['digest'])
            system(conn,p,'field_decision','Field '+path+': '+choice,user['id'],reason=reason)
        p.update(turn=other(role),draft_owner=other(role),draft=copy.deepcopy(v['payload']))
    elif action=='resolve-field':
        require(p['turn']==role and data.field_path in p.get('disputes',{}),'Only the editor can address a disputed field.')
        require(bool(data.reason.strip()),'Explain the change or justification.',422)
        del p['disputes'][data.field_path]
        system(conn,p,'field_dispute_addressed','Editor addressed '+data.field_path,user['id'],reason=data.reason)
    elif action=='decline':
        require(role=='freelancer' and state in ('INVITED','IN_NEGOTIATION'),'Only the freelancer may decline before signing.',403)
        require(data.reason_code in DECLINE_REASONS,'Choose a fixed decline reason.',422)
        p.update(status='DECLINED',decline_reason=data.reason_code,decline_text=data.reason)
        notify(conn,p,'decline',data.reason_code+': '+data.reason,'client')
    elif action=='withdraw':
        require(role=='client' and state=='INVITED' and not p.get('first_response'),'Only an unanswered invitation can be withdrawn.',403)
        p['status']='WITHDRAWN';notify(conn,p,'withdrawal','The invitation was withdrawn.')
    elif action=='cancel':
        require(state in ('IN_NEGOTIATION','READY_TO_SIGN','PARTIALLY_SIGNED') and not p.get('active_agreement_id'),'Cancellation is available before agreement.')
        require(bool(data.reason.strip()),'Cancellation requires a reason.',422)
        void(conn,p,user);p.update(status='CANCELLED_BEFORE_AGREEMENT',cancellation_reason=data.reason)
        notify(conn,p,'cancellation',data.reason)
    elif action=='reject-amendment':
        require(p.get('amendment') and p.get('active_agreement_id') and role==p['turn'],'Only the receiving party may reject a proposed amendment.')
        require(bool(data.reason.strip()),'Amendment rejection requires a reason.',422)
        void(conn,p,user);p.update(status='AGREED',version_id=p['active_agreement_id'],amendment=False,draft=None,draft_owner=None,disputes={})
    elif action in ('resend','nudge'):
        require(state=='INVITED' and role=='client' or action=='nudge' and p['turn']!=role and state in ('IN_NEGOTIATION','AMENDMENT_PROPOSED'),'Reminder is not available now.')
        last=p.get('last_nudge_at')
        require(not last or datetime.fromisoformat(last)<datetime.now(timezone.utc)-timedelta(hours=1),'Wait one hour before sending another reminder.')
        p['last_nudge_at']=db.now()
        auth.mail(conn,None,p['recipient_email'],'FairFlow invitation reminder','Your invitation is waiting in the freelancer inbox.') if action=='resend' else notify(conn,p,'nudge','Your counterpart is waiting for a response.',other(role))
    else:raise HTTPException(422,'Unsupported action.')
    system(conn,p,action,'Agreement action: '+action.replace('-',' '),user['id'],reason=data.reason,version_hash=v['digest'] if v else None)

def sweep_invitations():
    """Worker extension: deterministic reminders/expiry, with durable deduplication."""
    with transaction() as conn:
        for row in conn.execute(select(db.projects).where(db.projects.c.archived==False).with_for_update(skip_locked=True)).mappings():
            p=copy.deepcopy(row['payload'])
            if p.get('engine')!=ENGINE or p['status']!='INVITED':continue
            remaining=(datetime.fromisoformat(p['invitation_expires_at'])-datetime.now(timezone.utc)).total_seconds();changed=False
            if remaining<=0:
                p['status']='EXPIRED';system(conn,p,'expiry','The unanswered invitation expired.');notify(conn,p,'expiry','The unanswered invitation expired.');changed=True
            else:
                for hours in (48,24):
                    if remaining<=hours*3600 and str(hours) not in p.get('reminders',[]):
                        p.setdefault('reminders',[]).append(str(hours));changed=True
                        auth.mail(conn,None,p['recipient_email'],'FairFlow invitation reminder',f'Your invitation expires within {hours} hours.')
                        system(conn,p,'invitation_reminder',f'Invitation reminder: {hours} hours before expiry.')
            if changed:write(conn,p,row['version'])

def mount(app):
    router=APIRouter(prefix='/api/{portal}',tags=['Step 1 portals'],dependencies=[Depends(portal_user)])
    @router.get('/agreement-schema')
    def schema(portal:Portal):
        return dict(essentials=guided.ESSENTIALS,sections=SECTIONS,fields=CATALOG,decline_reasons=DECLINE_REASONS,countries=[{'code':c.alpha_2,'name':c.name} for c in pycountry.countries],currencies=sorted(c.alpha_3 for c in pycountry.currencies),time_zones=sorted(available_timezones()))
    @router.get('/profile')
    def profile(portal:Portal,user=Depends(portal_user)):
        with db.engine().connect() as conn:
            row=db.one(conn,db.client_profiles if portal=='client' else db.freelancer_profiles,(db.client_profiles if portal=='client' else db.freelancer_profiles).c.user_id==user['id'])
            return dict(user=auth.view(user),profile=row['payload'] if row else {})
    @router.put('/profile')
    def edit_profile(portal:Portal,data:ProfessionalProfile,user=Depends(portal_user)):
        require(portal=='freelancer','Only freelancer professional profiles can be edited here.',403)
        require(bool(data.professional_title.strip() and data.primary_category.strip() and data.skills),'Professional title, category and skills are mandatory.',422)
        from .agreement_validation import safe_url
        require(not data.portfolio_link or safe_url(data.portfolio_link),'Use a valid HTTP(S) portfolio URL.',422)
        try:pycountry.countries.lookup(data.country);ZoneInfo(data.time_zone)
        except (LookupError,ValueError,KeyError):raise HTTPException(422,'Choose a valid country and IANA time zone.')
        fields=('professional_title','primary_category','skills','experience_band','country','time_zone','portfolio_link')
        with transaction() as conn:
            conn.execute(update(db.freelancer_profiles).where(db.freelancer_profiles.c.user_id==user['id']).values(payload={k:v for k,v in data.model_dump(mode='json').items() if k in fields}))
            db.log(conn,'profile_updated',user['id'])
        return {'ok':True}
    @router.post('/validate')
    def validation(portal:Portal,data:ValidateProject,user=Depends(portal_user)):
        payload=data.agreement.model_dump(exclude_none=True)
        if not data.project_id:return {'flags':validate(payload)}
        with db.engine().connect() as conn:
            p,_=project(conn,data.project_id,user)
            if p.get('amendment'):amendments.enforce(conn,p,payload)
            return {'flags':current_flags(p,payload,conn=conn)}
    @router.get('/example-terms')
    def example_terms(portal:Portal):
        from .agreement_examples import example_agreement
        return {'agreement':example_agreement()}
    @router.get('/projects')
    def projects(portal:Portal,include_deleted:bool=False,user=Depends(portal_user)):
        result=[]
        with db.engine().connect() as conn:
            for m in db.rows(conn,db.members,db.members.c.user_id==user['id']):
                row=db.one(conn,db.projects,db.projects.c.id==m['project_id']);p=row['payload']
                if row['archived'] or p.get('engine')!=ENGINE or p.get('deleted'):continue
                if portal=='client' and p.get('hidden_by_client') and not include_deleted:continue
                v=sent(conn,p);payload=p.get('draft') if p.get('draft_owner')==portal else None;payload=payload or (v['payload'] if v else {})
                counterpart=next((x for x in db.rows(conn,db.members,db.members.c.project_id==p['id']) if x['role']!=portal),None)
                from .payouts import project_completed
                result.append(dict(hidden_by_client=bool(p.get('hidden_by_client')) if portal=='client' else False,withdrawal=p.get('withdrawal'),completed=project_completed(conn,p),id=p['id'],title=at(payload,'basics.title','Untitled draft'),status=p['status'],turn=p['turn'],has_active_agreement=bool(p.get('active_agreement_id')),updated_at=p.get('updated_at',p['created_at']),counterpart=db.one(conn,db.users,db.users.c.id==counterpart['user_id'])['name'] if counterpart else p.get('recipient_email','Not invited')))
        return result
    @router.post('/projects',status_code=201)
    def create(portal:Portal,data:NewProject,user=Depends(portal_user)):
        require(portal=='client','Only clients create projects.',403)
        payload=data.agreement.model_dump(exclude_none=True);payload['acknowledgments']={}
        p=dict(id=db.uid(),engine=ENGINE,status='DRAFT',turn='client',draft_owner='client',draft=payload,created_at=db.now(),updated_at=db.now(),voided_versions=[],disputes={})
        with transaction() as conn:
            guided.enforce_owners({},payload,portal)
            profile=db.one(conn,db.client_profiles,db.client_profiles.c.user_id==user['id'])
            p['guided']=True;p['draft']=guided.defaults(user,profile['payload'] if profile else {})
            for section,fields in payload.items():
                if isinstance(fields,dict):p['draft'].setdefault(section,{}).update(fields)
            guided.derived_fields(p['draft'])
            from .funded_payments import prepare_draft
            prepare_draft(conn,p,p['draft'])
            conn.execute(insert(db.projects).values(id=p['id'],payload=p,version=1,archived=False,created_at=p['created_at']))
            conn.execute(insert(db.members).values(id=db.uid(),project_id=p['id'],user_id=user['id'],role='client'))
            db.log(conn,'project_created',user['id'],p['id'])
            return detail(conn,p,1,user)
    @router.get('/inbox')
    def inbox(portal:Portal,user=Depends(portal_user)):
        require(portal=='freelancer','The invitation inbox belongs to freelancers.',403)
        with db.engine().connect() as conn:
            return [dict(id=i['id'],project_id=i['project_id'],expires_at=i['expires_at']) for i in db.rows(conn,db.invitations,(db.invitations.c.email==user['email'])&(db.invitations.c.accepted_by==None)) if i['expires_at']>db.now() and (db.one(conn,db.projects,db.projects.c.id==i['project_id'])['payload'].get('status')=='INVITED')]
    @router.post('/invitations/{iid}/open')
    def open_invitation(portal:Portal,iid:str,request:Request,user=Depends(portal_user)):
        require(portal=='freelancer','Only freelancers receive briefs.',403)
        with transaction() as conn:
            i=db.one(conn,db.invitations,(db.invitations.c.id==iid)&(db.invitations.c.email==user['email']))
            require(i is not None,'Invitation not found.',404)
            row=conn.execute(select(db.projects).where(db.projects.c.id==i['project_id']).with_for_update()).mappings().first()
            require(i['expires_at']>db.now() and row['payload'].get('status')=='INVITED' and row['payload'].get('invitation_id')==iid,'Invitation expired or closed.')
            if not db.one(conn,db.members,(db.members.c.project_id==i['project_id'])&(db.members.c.user_id==user['id'])):conn.execute(insert(db.members).values(id=db.uid(),project_id=i['project_id'],user_id=user['id'],role='freelancer'))
            p,rev=project(conn,i['project_id'],user,True);apply(conn,p,Action(revision=rev,action='open'),user,request);write(conn,p,rev)
            return detail(conn,p,rev+1,user)
    @router.get('/projects/{pid}')
    def read(portal:Portal,pid:str,user=Depends(portal_user)):
        with db.engine().connect() as conn:
            p,rev=project(conn,pid,user);return detail(conn,p,rev,user)
    @router.put('/projects/{pid}/draft')
    def save(portal:Portal,pid:str,data:Save,user=Depends(portal_user)):
        with transaction() as conn:
            p,rev=project(conn,pid,user,True);require(rev==data.revision,'Draft changed concurrently. Reload before retrying.')
            require(not p.get('withdrawal'),'New agreement edits are stopped following freelancer withdrawal.')
            require(p['turn']==portal and p.get('draft_owner')==portal and p['status'] in ('DRAFT','INVITED','IN_NEGOTIATION','AMENDMENT_PROPOSED'),'Only the turn holder can edit this draft.')
            if data.review_conversation_hash or data.review_agreement_hash:
                messages=sorted(db.rows(conn,db.chat_messages,db.chat_messages.c.thread_id==p.get('thread_id')),key=lambda m:(m['created_at'],m['id']))
                require(digest(messages)==data.review_conversation_hash and digest(p.get('draft') or {})==data.review_agreement_hash,'AI review is stale. Review the updated conversation and agreement again.')
            payload=data.agreement.model_dump(exclude_none=True)
            from .funded_payments import prepare_draft
            prepare_draft(conn,p,payload)
            if p.get('amendment'):amendments.enforce(conn,p,payload)
            # A client cannot forge the other party's warning acknowledgment.
            old=p.get('draft') or {};guided.enforce_owners(old,payload,portal)
            if p.get('guided'):guided.derived_fields(payload)
            if p.get('amendment'):amendments.enforce(conn,p,payload)
            payload['acknowledgments']=copy.deepcopy(old.get('acknowledgments',{}))
            ids={f['id'] for f in validate(payload) if f['severity']=='WARNING'}
            payload['acknowledgments']={k:v for k,v in payload['acknowledgments'].items() if k in ids}
            if payload!=old:
                if signed(conn,p.get('version_id')):void(conn,p,user)
                p['draft']=payload;system(conn,p,'draft_saved','Editor saved private agreement changes.',user['id'])
            write(conn,p,rev);return detail(conn,p,rev+1,user)
    @router.post('/projects/{pid}/handoff-editing')
    def handoff_editing(portal:Portal,pid:str,data:Revision,user=Depends(portal_user)):
        with transaction() as conn:
            p,rev=project(conn,pid,user,True);require(rev==data.revision,'Project changed. Refresh before transferring editing.')
            require(p['turn']==portal and p.get('draft_owner')==portal and p.get('draft') is not None,'Only the current draft editor can transfer editing.')
            require(p['status'] in ('IN_NEGOTIATION','AMENDMENT_PROPOSED'),'Editing transfer is unavailable now.')
            require(db.one(conn,db.members,(db.members.c.project_id==pid)&(db.members.c.role==other(portal))) is not None,'The other participant has not joined.')
            p.update(turn=other(portal),draft_owner=other(portal))
            system(conn,p,'editing_transferred','Editing passed to the other participant. The draft remains unapproved.',user['id'])
            notify(conn,p,'turn_change','Your turn to edit the agreement.',other(portal));write(conn,p,rev)
            return detail(conn,p,rev+1,user)

    @router.post('/projects/{pid}/actions')
    def actions(portal:Portal,pid:str,data:Action,request:Request,user=Depends(portal_user)):
        with transaction() as conn:
            p,rev=project(conn,pid,user,True);require(rev==data.revision,'Project changed concurrently. Reload before retrying.')
            apply(conn,p,data,user,request);write(conn,p,rev)
            if p.get('deleted'):conn.execute(update(db.projects).where(db.projects.c.id==pid).values(archived=True))
            return detail(conn,p,rev+1,user)
    @router.post('/projects/{pid}/ai-review')
    async def ai_review(portal:Portal,pid:str,data:Revision,request:Request,user=Depends(portal_user)):
        from .agreement_assistant import review
        auth.throttle(request,'ai-review:'+user['id'])
        with db.engine().connect() as conn:
            p,rev=project(conn,pid,user)
            require(rev==data.revision,'Project changed. Refresh before reviewing.')
            v=sent(conn,p)
            payload=p['draft'] if p.get('draft_owner')==portal and p.get('draft') is not None else v['payload'] if v else {}
            versions=sorted(db.rows(conn,db.agreements,db.agreements.c.project_id==pid),key=lambda row:row['number'])
            active=next((row for row in versions if row['id']==p.get('active_agreement_id')),None)
            review_input={'current_agreement':payload,'reviewed_versions':[{'id':row['id'],'number':row['number'],'hash':row['digest'],'payload':row['payload'],'created_at':row['created_at']} for row in versions], 'active_agreement':active['payload'] if active else None,'active_version_hash':active['digest'] if active else None}
            messages=sorted(db.rows(conn,db.chat_messages,db.chat_messages.c.thread_id==p.get('thread_id')),key=lambda m:(m['created_at'],m['id']))
            fingerprint=digest(messages)
        with db.engine().connect() as conn:
            members={m['user_id']:m['role'] for m in db.rows(conn,db.members,db.members.c.project_id==pid)}
        evidence=[{**m,'speaker_role':members.get(m['user_id'],'system')} for m in messages]
        result=await review(review_input,evidence)
        with transaction() as conn:
            fresh,new_revision=project(conn,pid,user,True)
            latest=sorted(db.rows(conn,db.chat_messages,db.chat_messages.c.thread_id==fresh.get('thread_id')),key=lambda m:(m['created_at'],m['id']))
            require(new_revision==rev and digest(latest)==fingerprint,'Terms or conversation changed during AI review. Run it again.')
            db.log(conn,'advisory_agreement_review',user['id'],pid,agreement_hash=digest(payload),conversation_hash=fingerprint,available=result['available'],issue_count=len(result['issues']),suggestion_count=len(result['suggestions']))
        return dict(**result,revision=rev,agreement_hash=digest(payload),conversation_hash=fingerprint)

    @router.post('/projects/{pid}/chat')
    def chat(portal:Portal,pid:str,data:Chat,background_tasks:BackgroundTasks,user=Depends(portal_user)):
        with transaction() as conn:
            p,rev=project(conn,pid,user,True)
            require(p.get('thread_id') and (p['status'] not in TERMINAL or p['status']=='FREELANCER_WITHDRAWN'),'Chat is read-only or unavailable.')
            require(bool(data.body.strip()),'Message cannot be blank.',422)
            if data.field_path:require(field_spec(data.field_path) is not None,'Choose a known agreement field.',422)
            msg=dict(id=db.uid(),thread_id=p['thread_id'],user_id=user['id'],body=data.body.strip(),field_path=data.field_path,kind='text',created_at=db.now())
            conn.execute(insert(db.chat_messages).values(**msg));db.log(conn,'chat_message',user['id'],pid,message_id=msg['id'])
            # One unread chat notice per recipient/project avoids flooding the inbox.
            for member in db.rows(conn,db.members,db.members.c.project_id==pid):
                if member['user_id']==user['id']:continue
                condition=(db.notifications.c.project_id==pid)&(db.notifications.c.user_id==member['user_id'])&(db.notifications.c.kind=='chat_message')&(db.notifications.c.read==False)
                unread=db.one(conn,db.notifications,condition)
                body=user['name']+' sent a message: '+msg['body'][:180]
                if unread:conn.execute(update(db.notifications).where(db.notifications.c.id==unread['id']).values(body=body,created_at=msg['created_at']))
                else:conn.execute(insert(db.notifications).values(id=db.uid(),project_id=pid,user_id=member['user_id'],kind='chat_message',body=body,read=False,created_at=msg['created_at']))
            from .chat_monitor import queue
            if queue(conn,p,background_tasks):write(conn,p,rev)
            return msg

    @router.post('/projects/{pid}/ai-field-reviews/{review_id}/approve')
    def approve_ai_field(portal:Portal,pid:str,review_id:str,data:Revision,user=Depends(portal_user)):
        from .chat_monitor import terms
        with transaction() as conn:
            p,rev=project(conn,pid,user,True);require(rev==data.revision,'Project changed. Review the latest proposal first.')
            record=next((r for r in p.get('ai_field_reviews',[]) if r['id']==review_id),None)
            require(record is not None,'AI change not found.',404)
            require(record['owner'] in ('both',portal),'Only the field owner can approve this AI change.',403)
            require(not record.get('approved_by'),'This change is already approved.')
            require(at(terms(conn,p),record['field_path'])==record['expected'],'The field changed. Review the latest proposal first.')
            record['approved_by']={'user_id':user['id'],'name':user['name'],'role':portal,'at':db.now()}
            system(conn,p,'ai_field_approved','AI-proposed change approved by '+user['name']+': '+record['field_path'],user['id'])
            write(conn,p,rev);return detail(conn,p,rev+1,user)

    @router.post('/projects/{pid}/chat-monitor/recheck')
    def recheck_chat(portal:Portal,pid:str,data:Revision,background_tasks:BackgroundTasks,user=Depends(portal_user)):
        from .chat_monitor import queue
        with transaction() as conn:
            p,rev=project(conn,pid,user,True);require(rev==data.revision,'Project changed. Refresh first.')
            require(queue(conn,p,background_tasks),'AI monitoring is unavailable. Configure Gemini before proceeding.',503)
            write(conn,p,rev)
            return {'status':'pending'}

    @router.post('/projects/{pid}/chat-monitor/propose')
    def propose_chat_amendment(portal:Portal,pid:str,data:Revision,request:Request,user=Depends(portal_user)):
        from .chat_monitor import proposed_terms,terms
        with transaction() as conn:
            p,rev=project(conn,pid,user,True);require(rev==data.revision,'Project changed. Refresh the proposed changes first.')
            try:payload=proposed_terms(p,terms(conn,p))
            except ValueError as exc:raise HTTPException(422,str(exc))
            findings=p['chat_monitor']['findings']
            if p['status']=='AGREED' and len(findings)==1 and findings[0]['field_path'].startswith('timeline.milestones.') and findings[0]['field_path'].endswith('.due_date'):
                from . import workflows as w,assurance as a
                from .payment_policy import reserve_days
                raw=a.signed_terms(conn,p);effective=w.effective_terms(p,raw)
                index=int(findings[0]['field_path'].split('.')[2]);mid=at(effective['payload'],'timeline.milestones')[index]['id']
                require(len(w.requests(p))<300,'Request history is full.')
                require(not any(x['kind']=='extension' and x['milestone_id']==mid and x['status'] in w.OPEN|{'executing','needs_attention'} for x in w.requests(p)),'A deadline proposal is already open.')
                r=w.Proposal(revision=rev,kind='extension',milestone_id=mid,due_date=findings[0]['expected'],reason='Chat-agreed deadline update: '+findings[0]['explanation'][:2800]).model_dump(exclude={'revision'})
                r.update(id=db.uid(),status='proposed',created_by=user['id'],created_at=db.now(),agreement_id=raw['id'],agreement_digest=raw['digest'],financial_snapshot=w.snapshot(conn,p,mid),votes=[])
                w.request_valid(conn,p,effective,r)
                r['completion_date']=str(datetime.fromisoformat(r['due_date']).date()+timedelta(days=reserve_days(effective['payload'],w.milestone(effective,mid))))
                r['digest']=w.proposal_digest(r);w.requests(p).append(r)
                w.event(conn,p,'workflow_proposed','Chat-agreed deadline addendum proposed for both participants.',user,request=copy.deepcopy(r))
                write(conn,p,rev);return detail(conn,p,rev+1,user)
            # Reuse normal protected-milestone, ownership, payment-window and signing rules.
            if p['status']=='AGREED':apply(conn,p,Action(revision=rev,action='amend'),user,request)
            elif not (p.get('draft_owner')==portal and p['turn']==portal):
                apply(conn,p,Action(revision=rev,action='counter'),user,request)
            require(p.get('draft_owner')==portal and p['turn']==portal,'The current editor must propose these changes.')
            # This endpoint accepts no caller-supplied terms: payload contains only
            # the reviewed, bilateral chat patches. Cross-owner proposals are
            # permitted here; ordinary draft editing retains ownership checks.
            # The counterpart still approves/signs the exact sent version.
            from .funded_payments import prepare_draft
            prepare_draft(conn,p,payload)
            if p.get('amendment'):amendments.enforce(conn,p,payload)
            payload['acknowledgments']=copy.deepcopy((p.get('draft') or {}).get('acknowledgments',{}))
            p['draft']=payload
            from .chat_monitor import register_owner_reviews
            register_owner_reviews(p)
            # Preserve the generated edits when consequential date/amount checks
            # need correction; never silently roll the AI draft back with send.
            remaining=[f for f in current_flags(p,payload,True,conn) if f['severity']=='BLOCKER' and guided.owner(f['field_path'])!=other(portal)]
            if remaining:
                if signed(conn,p.get('version_id')):void(conn,p,user)
                system(conn,p,'chat_draft_prepared','Chat-agreed changes applied to the draft; validation issues must be corrected before sharing.',user['id'])
                write(conn,p,rev)
                return dict(detail(conn,p,rev+1,user),chat_draft_saved=True)
            apply(conn,p,Action(revision=rev,action='send'),user,request)
            write(conn,p,rev)
            return detail(conn,p,rev+1,user)
    @router.post('/projects/{pid}/chat/{message_id}/term-change')
    def record_chat_change(portal:Portal,pid:str,message_id:str,data:ChatTermRecord,user=Depends(portal_user)):
        from . import chat_terms
        from .workflows import effective_terms
        with transaction() as conn:
            p,rev=project(conn,pid,user,True);require(rev==data.revision,'Project changed. Refresh first.')
            require(data.typed_name.strip().casefold()==user['name'].strip().casefold(),'Type your account name to confirm.',422)
            require(field_spec(data.field_path) is not None,'Choose an agreement field.',422)
            require(len(json.dumps(data.expected))<=20000,'The proposed field value is too large.',422)
            message=next((m for m in chat_terms.messages(conn,p) if m['id']==message_id),None)
            require(message is not None,'Chat message not found.',404)
            terms=effective_terms(p,sent(conn,p))['payload'] if p.get('version_id') else p.get('draft',{})
            value=at(terms,data.field_path)
            require(type(data.expected)==type(value) or value is None and isinstance(data.expected,(str,int,bool,list)),'Proposed value must have the same type as this agreement field.',422)
            ident=digest([message_id,data.field_path,data.expected])[:32]
            records=p.setdefault('recorded_chat_changes',[])
            record=next((r for r in records if r['record_id']==ident),None)
            if not record:
                require(len(records)<300,'Chat change history is full.')
                record={'record_id':ident,'message_id':message_id,'acceptance_id':'record-'+ident,'field_path':data.field_path,'expected':data.expected,
                        'baseline':copy.deepcopy(value),'currency':at(terms,'payment.currency') if data.field_path.endswith(('amount_minor','total_minor')) else None,
                        'milestone_id':at(terms,'.'.join(data.field_path.split('.')[:3])+'.id','') if data.field_path.startswith('timeline.milestones.') else '',
                        'body':message['body'],'author':message['user_id'],'created_at':db.now(),'accepted_at':db.now(),'status':'proposed','votes':[]}
                records.append(record)
            require(record['status']=='proposed' and not any(v['role']==portal for v in record['votes']),'You already confirmed this exact chat change.')
            record['votes'].append({'role':portal,'user_id':user['id'],'typed_name':data.typed_name.strip(),'at':db.now()})
            if len(record['votes'])==2:record.update(status='accepted',accepted_at=db.now())
            notify(conn,p,'chat_change','A chat change needs to be reflected in the agreement.',other(portal))
            write(conn,p,rev);return {'record':record}

    @router.post('/projects/{pid}/chat-term-changes/{change_id}/withdraw')
    def withdraw_chat_change(portal:Portal,pid:str,change_id:str,data:ChatTermWithdraw,user=Depends(portal_user)):
        from . import chat_terms
        from .workflows import effective_terms
        with transaction() as conn:
            p,rev=project(conn,pid,user,True);require(rev==data.revision,'Project changed. Refresh first.')
            require(data.typed_name.strip().casefold()==user['name'].strip().casefold(),'Type your account name to confirm.',422)
            terms=effective_terms(p,sent(conn,p))['payload'] if p.get('version_id') else p.get('draft',{})
            change=next((c for c in chat_terms.changes(conn,p,terms) if c['id']==change_id),None)
            require(change is not None and not change['resolved'],'This chat change is already resolved or was superseded.')
            votes=p.setdefault('withdrawn_chat_changes',{}).setdefault(change_id,{})
            require(portal not in votes,'You already confirmed withdrawal.')
            votes[portal]={'reason':data.reason,'typed_name':data.typed_name,'user_id':user['id'],'at':db.now()}
            notify(conn,p,'chat_change_withdrawal','A participant proposes withdrawing a chat change; both must confirm.',other(portal))
            write(conn,p,rev);return {'withdrawal_votes':list(votes)}

    @router.post('/projects/{pid}/chat/read')
    def read_chat(portal:Portal,pid:str,user=Depends(portal_user)):
        with transaction() as conn:
            p,_=project(conn,pid,user,True);require(bool(p.get('thread_id')),'Chat is not open.')
            row=db.one(conn,db.chat_reads,(db.chat_reads.c.thread_id==p['thread_id'])&(db.chat_reads.c.user_id==user['id']))
            if row:conn.execute(update(db.chat_reads).where(db.chat_reads.c.id==row['id']).values(read_at=db.now()))
            else:conn.execute(insert(db.chat_reads).values(id=db.uid(),thread_id=p['thread_id'],user_id=user['id'],read_at=db.now()))
            conn.execute(update(db.notifications).where(db.notifications.c.project_id==pid,db.notifications.c.user_id==user['id'],db.notifications.c.kind=='chat_message').values(read=True))
            return {'ok':True}
    @router.get('/projects/{pid}/events')
    def events(portal:Portal,pid:str,request:Request,user=Depends(portal_user)):
        with db.engine().connect() as conn:project(conn,pid,user)
        async def stream():
            for _ in range(6):
                if await request.is_disconnected():return
                with db.engine().connect() as conn:
                    p,rev=project(conn,pid,user)
                    messages=db.rows(conn,db.chat_messages,db.chat_messages.c.thread_id==p.get('thread_id'))
                yield 'event: update\ndata: '+json.dumps({'revision':rev,'messages':len(messages)})+'\n\n'
                await asyncio.sleep(5)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache'})
    @router.get('/notifications')
    def notices(portal:Portal,user=Depends(portal_user)):
        with db.engine().connect() as conn:return db.rows(conn,db.notifications,db.notifications.c.user_id==user['id'])
    @router.post('/notifications/{notice_id}/read')
    def read_notice(portal:Portal,notice_id:str,user=Depends(portal_user)):
        with db.engine().begin() as conn:
            row=db.one(conn,db.notifications,(db.notifications.c.id==notice_id)&(db.notifications.c.user_id==user['id']))
            require(row is not None,'Notification not found.',404)
            conn.execute(update(db.notifications).where(db.notifications.c.id==notice_id).values(read=True))
        return {'ok':True}
    @router.get('/projects/{pid}/export/{format}')
    def export(portal:Portal,pid:str,format:Literal['html','json','pdf'],response:Response,version_id:str|None=None,user=Depends(portal_user)):
        with db.engine().connect() as conn:
            p,_=project(conn,pid,user);vid=version_id or p.get('active_agreement_id') or p.get('version_id')
            v=db.one(conn,db.agreements,(db.agreements.c.id==vid)&(db.agreements.c.project_id==pid))
            require(v is not None,'Agreement version not found.',404)
            require(digest(v['payload'])==v['digest'],'Agreement hash mismatch.')
            from .early_start import rendering as effective_render
            result=effective_render(p,v) if not version_id and v['id']==p.get('active_agreement_id') else render(v['payload'],v['id'],v['number'])
            assert_coverage(result)
            if format=='html':return HTMLResponse(result['html'])
            if format=='pdf':return Response(pdf(result),media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="agreement-v{v["number"]}.pdf"'})
            response.headers['Content-Disposition']=f'attachment; filename="agreement-v{v["number"]}.json"'
            return dict(agreement_id=v['id'],version=v['number'],sha256=result['sha256'],original_sha256=v['digest'],agreement=result['agreement'],clause_map=result['clause_map'],schedule_addenda=result.get('schedule_addenda',[]))
    @router.get('/projects/{pid}/contract-summary')
    def contract_summary(portal:Portal,pid:str,user=Depends(portal_user)):
        with db.engine().connect() as conn:
            p,_=project(conn,pid,user);require(bool(p.get('active_agreement_id')),'Both signatures are required before a contract summary is available.')
            v=db.one(conn,db.agreements,db.agreements.c.id==p['active_agreement_id'])
            from .early_start import rendering as effective_render
            result=effective_render(p,v)
            return dict(agreement_id=v['id'],version_hash=v['digest'],effective_sha256=result['sha256'],terms=result['agreement'],clause_map=result['clause_map'],schedule_addenda=result.get('schedule_addenda',[]))
    app.include_router(router)

    @app.get('/{portal}/{page:path}',include_in_schema=False)
    def page(portal:Portal,page:str,request:Request):
        user=auth.actor.get()
        if user and user.get('role') and user['role']!=portal:
            target=f"/{user['role']}/dashboard"
            return HTMLResponse(f'<!doctype html><title>403 Forbidden</title><h1>403 — Use your own portal</h1><p><a href="{target}">Continue to your dashboard</a></p><meta http-equiv="refresh" content="0;url={target}">',status_code=403,headers={'Location':target})
        return FileResponse(ROOT/'static/portals.html')
