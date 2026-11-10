"""Deterministic, bilateral schedule advancement; never moves money."""
import copy
from datetime import date,timedelta
from html import escape
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from . import db
from .agreement_schema import at
from .agreement_validation import digest


def fingerprint(terms):
    return digest({'timeline':terms.get('timeline',{}),'dependencies':at(terms,'scope.dependencies',[])})


def put(terms,path,value):
    parts=path.split('.');target=terms
    for part in parts[:-1]:target=target[int(part)] if isinstance(target,list) else target[part]
    if isinstance(target,list):target[int(parts[-1])]=value
    else:target[parts[-1]]=value


def plan(conn,p,v,mid,*,start=None,now=None,request_id=None):
    from . import assurance as a,agreement_amendments as am,workflows as w,funded_payments as funded
    from .payment_policy import reserve_days
    now=now or w.clock()
    zone=ZoneInfo(at(v['payload'],'timeline.time_zone','UTC'))
    today=now.astimezone(zone).date()
    beginning=date.fromisoformat(start) if start else today
    def require(ok,message):
        if not ok:raise HTTPException(409,message)
    require(p['status']=='AGREED' and not p.get('amendment'),'Finish the current agreement proposal before starting early.')
    require(not any(r['status'] in w.OPEN and r['id']!=request_id for r in p.get('workflow_requests',[])), 'Finish other schedule proposals before starting early.')
    require(not p.get('withdrawal') and not w.closed(p,mid),'Remaining work is closed.')
    require(not mid.startswith('CO-'),'Additional work uses its own agreed delivery schedule.')
    terms=v['payload'];milestones=at(terms,'timeline.milestones',[])
    index=next((i for i,m in enumerate(milestones) if m['id']==mid),None)
    require(index is not None,'Milestone not found.')
    require(beginning>=today,'The proposed early-start date has passed. Create a fresh schedule.')
    planned=date.fromisoformat(milestones[index].get('start_date') or (milestones[index-1]['due_date'] if index else terms['timeline']['start_date']))
    require(beginning<planned,'This milestone can already start on its current schedule.')
    done=set(am.completed_ids(conn,p))
    require(all(m['id'] in done or w.closed(p,m['id']) for m in milestones[:index] if not m['id'].startswith('CO-')),
            'Finish and verify the previous milestone payments before starting the next milestone early.')
    delta=(planned-beginning).days
    updates=[];rows=[];changed=copy.deepcopy(terms)
    def change(path,after,label):
        before=at(terms,path)
        if before==after:return
        updates.append({'path':path,'before':before,'after':after,'label':label})
        put(changed,path,after)
    for i,m in enumerate(milestones[index:],index):
        if m['id'].startswith('CO-') and date.fromisoformat(m['due_date'])<planned:continue
        require(not a.payment(conn,p['id'],m['id']),'A later milestone already has funding activity. Its dates are locked.')
        require(not w.closed(p,m['id']),'A later milestone is closed and cannot be rescheduled.')
        require(not any(c['milestone_id']==m['id'] and c['status']!='closed' for c in p.get('support_cases',[])), 'Resolve open work claims before moving this schedule.')
        due=date.fromisoformat(m['due_date'])-timedelta(days=delta)
        require(due>=beginning,'The early schedule would place delivery before its start.')
        old_start=m.get('start_date') or (milestones[i-1]['due_date'] if i else terms['timeline']['start_date'])
        new_start=str(date.fromisoformat(old_start)-timedelta(days=delta)) if i>index else str(beginning)
        if not m['id'].startswith('CO-'):
            change(f'timeline.milestones.{i}.start_date',new_start,m['title']+' - start')
        change(f'timeline.milestones.{i}.due_date',str(due),m['title']+' - delivery')
        nonworking=due.weekday() not in at(terms,'timeline.working_days',[]) or str(due) in at(terms,'timeline.blackout_dates',[])
        if nonworking:change(f'timeline.milestones.{i}.bumped_date',True,m['title']+' - agreed non-working delivery')
        reserve=reserve_days(terms,m)
        require(reserve is not None,'Review and revision periods are incomplete.')
        rows.append({'id':m['id'],'title':m['title'],'old_start':old_start,'start_date':new_start,
                     'old_due':m['due_date'],'due_date':str(due),'review_days':m['review_days'],
                     'completion_date':str(due+timedelta(days=reserve)),'nonworking':nonworking})
    if index==0 or beginning<date.fromisoformat(terms['timeline']['start_date']):change('timeline.start_date',str(beginning),'Project start')
    deadline=str(date.fromisoformat(terms['timeline']['deadline'])-timedelta(days=delta))
    change('timeline.deadline',deadline,'Final project deadline')
    for i,dependency in enumerate(at(terms,'scope.dependencies',[])):
        if dependency.get('due_date') and date.fromisoformat(dependency['due_date'])>=planned:
            change(f'scope.dependencies.{i}.due_date',str(date.fromisoformat(dependency['due_date'])-timedelta(days=delta)),
                   'Client dependency: '+dependency.get('description','Assets/access'))
    require(funded.window(changed,milestones[index]|{'due_date':rows[0]['due_date']},now.date())['available'],
            'The agreed work duration and review reserve exceed the funding window. Split this milestone before starting early.')
    return {'start_date':str(beginning),'shift_days':delta,'schedule_hash':fingerprint(terms),'updates':updates,'milestones':rows,
            'old_deadline':terms['timeline']['deadline'],'deadline':deadline,
            'note':'Move remaining work earlier by the same number of calendar days. Prices, scope and review durations stay unchanged. Listed non-working delivery dates are explicitly agreed. Funded/completed work and fixed holidays stay unchanged.'}


def apply(terms,request):
    for update in request['schedule']['updates']:put(terms,update['path'],update['after'])


def rendering(p,version):
    from .workflows import effective_terms
    from .agreement_renderer import render
    effective=effective_terms(p,version)
    result=render(effective['payload'],version['id'],version['number'])
    records=[r for r in p.get('workflow_requests',[]) if r['kind'] in ('early_start','extension','dependency','change') and r['status']=='applied']
    if records:
        result['original_sha256']=version['digest']
        result['schedule_addenda']=copy.deepcopy(records)
        provenance=[f"Original signed version SHA-256: {version['digest']}. Current schedule incorporates approved addenda; original signatures remain attached to the original version."]
        for r in records:
            provenance.append('Schedule addendum '+r['id']+' SHA-256 '+r['digest']+'. '+
                '; '.join(v['role']+': '+v['typed_name']+' at '+v['at'] for v in r['votes']))
        for i,text in enumerate(provenance,1):
            clause={'id':'S.'+str(i),'title':'Approved schedule','field_path':None,'text':text}
            result['clauses'].append(clause)
            result['html']=result['html'].replace('</article>','<section><h3>Approved schedule</h3><p>'+escape(text)+'</p></section></article>')
    return result
