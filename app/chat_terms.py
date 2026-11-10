"""Accepted chat changes require matching formal terms; chat never authorizes money."""
import copy,re
from datetime import date,timedelta
from decimal import Decimal
from babel.numbers import get_currency_precision
from fastapi import HTTPException
from . import db
from .agreement_schema import at
from .agreement_validation import digest

ACCEPT=re.compile(r'^(?:yes|yeah|yep|ok|okay|agreed|agree|accepted|accept|done|deal|sure|fine|sounds good|works for me|i agree|i accept|we agree)\b',re.I)
REJECT=re.compile(r'^(?:no|nope|reject|decline|not agreed|do not agree|don.t agree)\b',re.I)
INTENT=re.compile(r'\b(?:change|instead|increase|reduce|extend|extension|postpone|add|remove|replace|include|exclude|make|charge|cost|price|fee|deadline|due date|review period|revision rounds|ownership|license|licence|confidential|NDA)\b',re.I)


def messages(conn,p):
    return sorted((m for m in db.rows(conn,db.chat_messages,db.chat_messages.c.thread_id==p.get('thread_id')) if m['kind']=='text'),key=lambda m:(m['created_at'],m['id']))


def baseline(versions,message,fallback):
    older=[v for v in versions if v['created_at']<=message['created_at']]
    return max(older,key=lambda v:(v['created_at'],v['number']))['payload'] if older else fallback


def candidate(message,terms,*,skip_price=False):
    from .agreement_assistant import MONEY,SYMBOLS
    body=message['body'];pinned=message.get('field_path');ms=at(terms,'timeline.milestones',[])
    mid=re.search(r'\b(?:milestone\s*|m)(\d+)\b',body,re.I)
    index=int(mid.group(1))-1 if mid else None
    if pinned and re.match(r'timeline.milestones.\d+\.',pinned):index=int(pinned.split('.')[2])
    valid=index is not None and 0<=index<len(ms)
    milestone_id=ms[index]['id'] if valid else ''
    money=list(MONEY.finditer(body))
    plain=re.search(r'\b(?:price|charge|cost|fee|amount)(?:\s+(?:is|to|of|will|should|be|it|at|now))*\s*[:=]?\s*(\d[\d,]*(?:\.\d{1,2})?)',body,re.I)
    path=None;value=None;currency=None
    if (money or plain) and not skip_price:
        path=f'timeline.milestones.{index}.amount_minor' if valid else 'payment.total_minor'
        if money:
            match=money[-1] if re.search(r'\bfrom\b.+\bto\b',body,re.I) else money[0]
            currency=(SYMBOLS.get(match.group(1),match.group(1)) if match.group(1) else match.group(4)).upper();raw=match.group(2) or match.group(3)
        else:currency=at(terms,'payment.currency');raw=plain.group(1)
        value=int(Decimal(raw.replace(',',''))*10**get_currency_precision(currency))
    elif re.search(r'\b(?:deadline|due|deliver|delivery|extend|extension|postpone|start date)\b',body,re.I) or pinned and pinned.endswith(('.due_date','.deadline','.start_date')):
        path=pinned if pinned and pinned.endswith(('.due_date','.deadline','.start_date')) else f'timeline.milestones.{index}.due_date' if valid else 'timeline.start_date' if re.search(r'\bstart date\b',body,re.I) else 'timeline.deadline'
        exact=re.search(r'\b\d{4}-\d{2}-\d{2}\b',body)
        relative=re.search(r'\b(?:by|another|extra|more|for)\s+(\d+)\s+days?\b',body,re.I)
        if exact:
            try:value=str(date.fromisoformat(exact.group()))
            except ValueError:return None
        elif relative and at(terms,path):value=str(date.fromisoformat(at(terms,path))+timedelta(days=int(relative.group(1))))
    elif pinned and INTENT.search(body):path=pinned
    elif INTENT.search(body):
        if re.search(r'\b(?:review|revision)\b',body,re.I):path=f'timeline.milestones.{index}.review_days' if valid and re.search(r'\breview\b',body,re.I) else 'revisions'
        elif re.search(r'\b(?:ownership|license|licence|copyright|portfolio)\b',body,re.I):path='ip'
        elif re.search(r'\b(?:confidential|nda)\b',body,re.I):path='confidentiality'
        else:path='scope'
    if not path:return None
    if value is None and path.endswith('.review_days'):
        number=re.search(r'\b([1-5])\s+days?\b',body,re.I)
        if number:value=int(number.group(1))
    addition_amount=None
    if path.endswith(('amount_minor','total_minor')) and re.search(r'\b(?:extra|additional|added|new scope|for this|new tab|new feature)\b',body,re.I):
        addition_amount=value;path='timeline.milestones';value=None;milestone_id=''
    return {'field_path':path,'expected':value,'currency':currency,'addition_amount':addition_amount,'baseline':copy.deepcopy(at(terms,path)),
            'milestone_id':milestone_id,'message_id':message['id'],'body':body,'author':message['user_id'],'created_at':message['created_at']}


def changes(conn,p,terms):
    history=messages(conn,p);versions=db.rows(conn,db.agreements,db.agreements.c.project_id==p['id']);offers=[];accepted=[]
    for i,message in enumerate(history):
        body=message['body'].strip()
        if ACCEPT.search(body) and not re.search(r"\b(?:not|don.t|cannot|can.t|but)\b|\?",body,re.I):
            offer=next((offer for offer in reversed(offers) if offer['author']!=message['user_id'] and not offer.get('rejected') and not offer.get('accepted')),None)
            if offer:
                for related in offers:
                    if related['message_id']==offer['message_id'] and not related.get('accepted'):
                        related['accepted']=True
                        accepted.append({**related,'accepted_at':message['created_at'],'acceptance_id':message['id']})
        if REJECT.search(body):
            for offer in offers[-1:]:
                if offer['author']!=message['user_id']:offer['rejected']=True
        offer=None if REJECT.search(body) else candidate(message,baseline(versions,message,terms))
        if offer:
            offers.append({**offer,'position':i})
            if (offer['field_path'].endswith(('amount_minor','total_minor')) or offer.get('addition_amount')) and re.search(r'\b(?:deadline|due|delivery date|extend|postpone|start date|review)\b',body,re.I):
                extra=candidate(message,baseline(versions,message,terms),skip_price=True)
                if extra and extra['field_path']!=offer['field_path']:offers.append({**extra,'position':i})
            if offer['field_path']!='scope' and re.search(r'\b(?:add|include|remove|replace)\b.*\b(?:tab|page|feature|app|scope|deliverable|css|design)\b',body,re.I):
                offers.append({**offer,'field_path':'scope','expected':None,'currency':None,'addition_amount':None,'milestone_id':'','baseline':copy.deepcopy(at(baseline(versions,message,terms),'scope')),'position':i})
    accepted.extend(copy.deepcopy([c for c in p.get('recorded_chat_changes',[]) if c['status']=='accepted']))
    latest={}
    for change in sorted(accepted,key=lambda item:item['accepted_at']):latest[change['field_path']]=change
    result=[]
    for change in latest.values():
        change['id']=digest([change['message_id'],change['acceptance_id'],change['field_path'],change['expected']])[:32]
        path=change['field_path']
        if change['milestone_id'] and path.startswith('timeline.milestones.'):
            current_index=next((i for i,m in enumerate(at(terms,'timeline.milestones',[])) if m['id']==change['milestone_id']),None)
            path='.'.join(['timeline','milestones',str(current_index),*path.split('.')[3:]]) if current_index is not None else '__missing_milestone'
        value=at(terms,path)
        reflected=(value==change['expected'] and (not change.get('currency') or change['currency']==at(terms,'payment.currency'))) if change['expected'] is not None else value!=change['baseline']
        if change.get('addition_amount'):
            old_ids={m['id'] for m in change['baseline']}
            reflected=any(m['id'] not in old_ids and m.get('amount_minor')==change['addition_amount'] and m.get('currency')==change['currency'] for m in at(terms,'timeline.milestones',[]))
        withdrawn=p.get('withdrawn_chat_changes',{}).get(change['id'],{})
        change['reflected']=reflected;change['withdrawal_votes']=list(withdrawn)
        change['resolved']=reflected or set(withdrawn)=={'client','freelancer'}
        result.append(change)
    return result


def require_current(conn,p,terms,mid=None):
    from .chat_monitor import blockers
    pending=blockers(p,terms)
    if pending:raise HTTPException(409,{'message':'Resolve the chat negotiation and review the proposed agreement changes before proceeding.','chat_review':pending})
    unresolved=[c for c in changes(conn,p,terms) if not c['resolved'] and (not mid or not c['milestone_id'] or c['milestone_id']==mid)]
    if unresolved:raise HTTPException(409,{'message':'Accepted chat changes are missing from the agreement. Update the affected terms and obtain formal approval before proceeding.','chat_term_changes':unresolved})
