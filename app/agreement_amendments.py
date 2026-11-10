"""Payment-aware amendment boundaries. Paid terms and provider records are immutable."""
import copy
from fastapi import HTTPException
from . import db
from .agreement_schema import at


def funded(conn,p):
    return db.rows(conn,db.milestone_payments,db.milestone_payments.c.project_id==p['id'])


def completed_ids(conn,p):
    result=[]
    for payment in funded(conn,p):
        payout=db.one(conn,db.milestone_payouts,db.milestone_payouts.c.payment_id==payment['id'])
        if payment['status']=='captured' and (payment['payload'].get('funding_model')=='paypal_funded_v1' or payout and payout['status']=='paid'):result.append(payment['milestone_id'])
    return result


def protected_ids(conn,p):
    return [row['milestone_id'] for row in funded(conn,p)]


def financial_snapshot(conn,p):
    result=[]
    for row in funded(conn,p):
        payout=db.one(conn,db.milestone_payouts,db.milestone_payouts.c.payment_id==row['id'])
        result.append((row['id'],row['status'],row['amount_minor'],row['currency'],row['payload'].get('capture_id'),
                       payout['status'] if payout else None))
    return sorted([list(item) for item in result])


def blocked_reason(conn,p):
    if p.get('withdrawal'):return 'Remaining work is stopped following freelancer withdrawal. Discuss existing deliveries and payments in project chat.'
    protected=set(protected_ids(conn,p))
    if protected and p.get('active_agreement_id'):
        version=db.one(conn,db.agreements,db.agreements.c.id==p['active_agreement_id'])
        remaining=[m['id'] for m in at(version['payload'],'timeline.milestones',[]) if m['id'] not in protected]
        if not remaining:return 'All existing milestones have funding records and are locked: '+', '.join(sorted(protected))+'. Use Change Requests for additional work or resolve the existing funding first.'
    return None


def locks(conn,p):
    ids=protected_ids(conn,p)
    if not ids or not p.get('active_agreement_id'):return None
    version=db.one(conn,db.agreements,db.agreements.c.id==p['active_agreement_id'])
    terms=version['payload']
    return {'milestone_ids':ids,'milestone_indices':[i for i,m in enumerate(at(terms,'timeline.milestones',[]) or []) if m.get('id') in ids],
            'deliverable_indices':[i for i,d in enumerate(at(terms,'scope.deliverables',[]) or []) if d.get('milestone_id') in ids],
            'editable_fields':['timeline.milestones','scope.deliverables','payment.total_minor','timeline.deadline','payment.funding_model','payment.funding_rules']}


def enforce(conn,p,proposed):
    """Rechecked at save, send, approval and signature under the project lock."""
    if not p.get('active_agreement_id'):return
    rows=funded(conn,p)
    additions=[r for r in p.get('workflow_requests',[]) if r['kind']=='change' and r['status']=='applied']
    if not rows and not additions:return
    reason=blocked_reason(conn,p)
    if reason:raise HTTPException(409,reason)
    if p.get('amendment') and p.get('amendment_financial_snapshot') is not None and p['amendment_financial_snapshot']!=financial_snapshot(conn,p):
        raise HTTPException(409,'Payment state changed during amendment review. Reject this proposal and review a fresh amendment; existing funding remains intact.')
    from .workflows import effective_terms
    original=effective_terms(p,db.one(conn,db.agreements,db.agreements.c.id==p['active_agreement_id']))['payload']
    frozen=locks(conn,p) or {'milestone_indices':[],'deliverable_indices':[],
        'editable_fields':['timeline.milestones','scope.deliverables','payment.total_minor','timeline.deadline','payment.funding_model','payment.funding_rules']}
    for collection,indices in [('timeline.milestones',frozen['milestone_indices']),('scope.deliverables',frozen['deliverable_indices'])]:
        old=at(original,collection,[]) or [];new=at(proposed,collection,[]) or []
        for index in indices:
            if index>=len(new) or new[index]!=old[index]:
                raise HTTPException(422,{'message':'Funded milestones and their deliverables are locked. Edit only remaining unfunded work.','field_path':f'{collection}.{index}'})
    # Project identity, currency and shared legal terms cannot rewrite paid work.
    from .funded_payments import MODEL,POLICY
    if at(proposed,'payment.funding_model')!=at(original,'payment.funding_model') or at(proposed,'payment.funding_rules')!=at(original,'payment.funding_rules'):
        if at(proposed,'payment.funding_model')!=MODEL or at(proposed,'payment.funding_rules')!=POLICY:
            raise HTTPException(422,'Future funding terms must use the configured upfront funding policy.')
    before=copy.deepcopy(original);after=copy.deepcopy(proposed)
    for path in [*frozen['editable_fields'],'acknowledgments']:
        parts=path.split('.')
        for payload in (before,after):
            target=payload
            for key in parts[:-1]:target=target.setdefault(key,{})
            target.pop(parts[-1],None)
    if before!=after:
        raise HTTPException(422,'After funding, only unfunded milestones, their deliverables, total price and final deadline can change. Project identity, currency and shared legal terms remain locked.')
