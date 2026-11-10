"""Short role-owned intake; complete terms remain the signing source of truth."""
import copy
from fastapi import HTTPException
from .agreement_schema import at, leaves, field_spec
from .agreement_validation import validate

ESSENTIALS={
 'basics':['title','category','summary'],
 'parties':[],
 'scope':['deliverables','inclusions','exclusions','dependencies'],
 'timeline':['start_date','deadline','milestones'],
 'payment':['currency','total_minor'],
 'revisions':['change_rate_minor','extra_round_minor'],
 'ip':[], 'confidentiality':['nda','ai_policy'],
 'communication':[], 'termination':['jurisdiction'], 'attachments':[],
}

def owner(path):
    if path.startswith('scope.deliverables') or path.startswith('revisions.'):return 'freelancer'
    if path.startswith('scope.dependencies') or path.startswith('scope.inclusions'):return 'client'
    spec=field_spec(path)
    return spec.get('owner','both') if spec else 'both'

def enforce_owners(before,after,role):
    old=dict(leaves(before));new=dict(leaves(after))
    for path in old.keys()|new.keys():
        if old.get(path)!=new.get(path) and owner(path) not in ('both',role):
            raise HTTPException(403,f'{path} belongs to the {owner(path)}. Discuss the field to request a change.')

def defaults(user,profile):
    from .agreement_examples import example_agreement
    sample=example_agreement()
    result={k:copy.deepcopy(sample[k]) for k in ('revisions','ip','confidentiality','communication','termination')}
    for k in ('change_rate_minor','extra_round_minor'):result['revisions'].pop(k,None)
    result['ip']['source_files']=False;result['ip'].pop('source_format',None)
    result['termination']['jurisdiction']=profile.get('country','')
    from .workflow_policy import POLICY_TEXT
    result['termination']['delivery_rules']=POLICY_TEXT
    result.update(basics={},scope={'out_of_scope_policy':'quote_and_approve'},
        timeline={'working_days':[0,1,2,3,4],'time_zone':profile.get('time_zone','UTC'),'grace_days':0,'client_delay_extension':'day_for_day'},
        payment={'pricing_model':'fixed_milestone','expense_policy':'not_reimbursable','tax_responsibility':'each_own','due_trigger':'milestone_approval','deposit_percent':0,'kill_fee_percent':0},
        parties={'client_name':profile.get('company_name') or user['name'],'client_account_type':profile.get('account_type','individual'), 'client_country':profile.get('country',''),'client_time_zone':profile.get('time_zone','UTC')},attachments={},acknowledgments={})
    if profile.get('account_type')=='company':result['parties']['authorized_contact']=user['name']
    from . import platform_paypal
    if platform_paypal.configured():
        from .funded_payments import MODEL,POLICY
        result['payment'].update(funding_model=MODEL,funding_rules=POLICY)
    return result

def brief_flags(payload,today):
    required=['basics.title','basics.category','basics.summary','payment.currency','payment.total_minor','timeline.start_date','timeline.deadline','scope.inclusions','scope.dependencies']
    flags=validate(payload,today=today)
    # The client must validate supplied optional fields too, before handing off.
    # Freelancer specifications may legitimately be absent from a short brief.
    return [f for f in flags if f['severity']=='BLOCKER' and (
        f['field_path'] in required or f['field_path'].startswith('scope.dependencies.') or
        owner(f['field_path'])=='client' or
        (f['field_path'].startswith('timeline.') and at(payload,f['field_path']) is not None)
    ) and 'Milestone total' not in f['message']]

def complete_profile(payload,user,profile):
    payload.setdefault('parties',{}).update(freelancer_name=user['name'],freelancer_country=profile.get('country',''),freelancer_time_zone=profile.get('time_zone','UTC'))
    payload.setdefault('basics',{}).setdefault('skills',profile.get('skills',[]))

def derived_fields(payload):
    basics=payload.setdefault('basics',{})
    if basics.get('category'):basics.setdefault('subcategory',basics['category'])
    # References and links are identifiers, not extra questions for the user.
    ds=at(payload,'scope.deliverables',[]) or []
    ms=at(payload,'timeline.milestones',[]) or []
    for i,d in enumerate(ds):
        d.setdefault('id',f'D{i+1}');d.setdefault('quantity',1);d.setdefault('revision_rounds',2)
        if len(ms)==1:d.setdefault('milestone_id',ms[0].get('id','M1'))
    for i,m in enumerate(ms):
        m.setdefault('id',f'M{i+1}');m.setdefault('review_days',3);m.setdefault('auto_accept',True)
        if m.get('auto_accept'):m['auto_accept_days']=m.get('review_days',3)
        m.setdefault('currency',at(payload,'payment.currency'))
        if len(ms)==1:m.setdefault('deliverable_ids',[d['id'] for d in ds])
