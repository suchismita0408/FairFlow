"""One catalog drives draft types, the form, required rules and clause coverage.

Drafts may be incomplete. Unknown fields and invalid primitive types are rejected;
business completeness belongs to the deterministic validator, never an LLM.
"""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, create_model

SECTIONS = {
    'parties':('B','Parties','1'), 'scope':('C','Scope of work','2'),
    'basics': ('A','Project basics','3'), 'timeline':('D','Timeline','4'),
    'payment':('E','Budget and payment terms','5'), 'revisions':('F','Revisions and change orders','6'),
    'ip':('G','Intellectual property','7'), 'confidentiality':('H','Confidentiality and tooling','8'),
    'communication':('I','Communication','9'), 'termination':('J','Termination and disputes','10'),
    'attachments':('K','Attachments','11'),
}

def f(label, kind='text', required=False, owner='both', options=None, example='', helper='', **kw):
    return dict(label=label,type=kind,required=required,owner=owner,options=options or [],example=example,
                helper=helper or 'Use explicit, measurable terms that both parties can confirm.',**kw)

DELIVERABLE = {
    'id':f('Reference',required=True,example='D1'), 'name':f('Name',required=True,example='Responsive landing page'),
    'description':f('Description','long',True,example='Build the approved three-section landing page.'),
    'type':f('Type','select',True,options=['file','source_code','deployed_site','document','other']),
    'quantity':f('Quantity','number',True,example=1),
    'specification':f('Format or specification','long',True,example='HTML/CSS, layouts at 1440px and 390px'),
    'acceptance_criteria':f('Acceptance criteria','tags',True,example=['All links work with keyboard navigation.']),
    'milestone_id':f('Linked milestone',required=True,example='M1'),
    'revision_rounds':f('Included revision rounds','number',True,example=2),
}
MILESTONE = {
    'id':f('Reference',required=True,example='M1'), 'title':f('Title',required=True,example='Website delivery'),
    'deliverable_ids':f('Included deliverables','tags',True,example=['D1']),
    'start_date':f('Agreed milestone start','date'),
    'due_date':f('Due date','date',True), 'amount_minor':f('Amount','money',True,example=100000),
    'currency':f('Currency','currency',True,example='INR'), 'review_days':f('Client review period in days','number',True,example=3,helper='Choose 1-5 calendar days; review must fit inside the 29-day PayPal authorization window.'),
    'auto_accept':f('Auto-accept if no response','boolean',True),
    'auto_accept_days':f('Auto-accept days','number',example=3,helper='Required when auto-accept is enabled; must match the review period.'),
    'bumped_date':f('Explicitly agreed non-working due date','boolean'),
}
DEPENDENCY={'description':f('Assets, access or feedback',required=True,example='Client supplies logo and copy'), 'due_date':f('Due by','date',True)}
ATTACHMENT={'label':f('Label',required=True,example='Approved design'), 'url':f('URL','url',True,example='https://example.com/design'), 'binding':f('Binding attachment','boolean',True,helper='Informational links cannot override any agreement clause.')}
CATALOG = {
 'basics':{
    'title':f('Project title',required=True,owner='client',example='Bakery landing page',min_length=5,max_length=100),
    'category':f('Category','select',True,'client',['Design','Frontend','Backend','Mobile','Writing','Video','Marketing','Data','Other']),
    'subcategory':f('Subcategory',required=True,owner='client',example='Landing page'),
    'summary':f('Project summary','long',True,'client',example='Create a responsive bakery landing page with three approved sections and keyboard-accessible navigation.',min_length=50,max_length=1000),
    'background':f('Background and goals','long',owner='client'), 'skills':f('Required skills and tools','tags',True,example=['HTML','CSS']),
    'audience':f('Target audience / platform',owner='client',example='Local customers on desktop and mobile'),
    'reference_links':f('Reference links','urls',owner='client'),
 },
 'parties':{
    'client_name':f('Client legal or business name',required=True,owner='client',example='Alice Baker'),
    'client_account_type':f('Client account type','select',True,'client',['individual','company']),
    'freelancer_name':f('Freelancer legal name',required=True,owner='freelancer',example='Rahul Kumar'),
    'client_country':f('Client country','country',True,'client',example='IN'), 'client_time_zone':f('Client time zone','zone',True,'client',example='Asia/Kolkata'),
    'freelancer_country':f('Freelancer country','country',True,'freelancer',example='IN'), 'freelancer_time_zone':f('Freelancer time zone','zone',True,'freelancer',example='Asia/Kolkata'),
    'authorized_contact':f('Authorized company contact',owner='client',helper='Required for a company client.'), 'tax_id':f('Tax or business ID'),
 },
 'scope':{
    'deliverables':f('Deliverables','repeat',True,children=DELIVERABLE),
    'inclusions':f('Explicit inclusions','tags',True,example=['Build the agreed page sections.']),
    'exclusions':f('Explicit exclusions','tags',example=['Backend development']),
    'no_exclusions':f('Explicitly agree there are no exclusions','boolean'), 'assumptions':f('Assumptions','tags'),
    'dependencies':f('Client dependencies','repeat',True,children=DEPENDENCY),
    'out_of_scope_policy':f('Out-of-scope work policy','select',True,options=['quote_and_approve','new_project']),
 },
 'timeline':{
    'start_date':f('Start date','date',True), 'deadline':f('Final delivery deadline','date',True),
    'working_days':f('Working days (Mon=0, Sun=6)','days',True,example=[0,1,2,3,4]),
    'time_zone':f('Time zone for deadlines','zone',True,example='Asia/Kolkata'), 'blackout_dates':f('Holidays and blackout dates','dates'),
    'grace_days':f('Delay policy: grace days','number',True,example=0),
    'client_delay_extension':f('Client-caused delay extension','select',True,options=['day_for_day','written_agreement']),
    'milestones':f('Milestones','repeat',True,children=MILESTONE),
 },
 'payment':{
    'funding_model':f('Milestone funding model','select',options=['paypal_funded_v1']),
    'funding_rules':f('Upfront funding and delayed disbursement terms','long'),
    'pricing_model':f('Pricing model','select',True,options=['fixed_milestone'],helper='Fixed price per milestone. Hourly projects are coming soon.'),
    'currency':f('Currency','currency',True,example='INR'), 'total_minor':f('Total project price','money',True,example=100000),
    'deposit_percent':f('Deposit percent','number',example=0), 'deposit_explanation':f('Deposit exceeding milestone 1: explanation'),
    'expense_policy':f('Expense policy','select',True,options=['not_reimbursable','capped_preapproved']),
    'expense_cap_minor':f('Pre-approved expense cap','money'),
    'tax_responsibility':f('Tax responsibility','select',True,options=['each_own','other']), 'tax_details':f('Other tax responsibility','long'),
    'due_trigger':f('Payment due trigger','select',True,options=['milestone_approval','days_after_delivery']),
    'due_days':f('Payment due days after delivery','number'), 'late_terms':f('Late payment terms','long'),
    'kill_fee_percent':f('Kill fee percent','number',example=0),
 },
 'revisions':{
    'definition':f('Definition of a revision','long',True,example='A revision adjusts presentation within the accepted scope without adding features or changing direction.'),
    'turnaround_days':f('Revision turnaround in days','number',True,example=2),
    'not_revision':f('What is not a revision','long',True,example='New features, new pages and changes in direction require a change order.'),
    'change_rate_kind':f('Change order rate basis','select',True,options=['hourly','fixed_unit']),
    'change_rate_minor':f('Change order rate','money',True,example=50000), 'change_rate_unit':f('Fixed-price unit',helper='Required for a fixed unit rate.'),
    'written_approval':f('Written approval before change work','boolean',True,helper='Fixed to yes. No change work starts without written approval.'),
    'extra_round_minor':f('Rate for an extra revision round','money',True,example=10000),
    'defect_days':f('Free defect fixes: days after delivery','number',True,example=14),
 },
 'ip':{
    'transfer_timing':f('Ownership transfer timing','select',True,options=['full_payment','milestone_payment','delivery_exception']),
    'transfer_exception':f('Explicit ownership exception','long',helper='Delivery before deferred payment requires both parties to acknowledge the exception warning.'),
    'license_type':f('License type',required=True,example='Exclusive assignment of original work'),
    'portfolio_rights':f('Portfolio display rights','boolean',True), 'third_party_assets':f('Who licenses third-party assets',required=True,example='Client licenses supplied assets; freelancer licenses introduced assets.'),
    'source_files':f('Source files delivered','boolean',True), 'source_format':f('Source format',helper='Required if source files are delivered.'), 'credit':f('Credit and attribution'),
 },
 'confidentiality':{
    'nda':f('NDA applies','boolean',True), 'ai_policy':f('AI tools usage','select',True,options=['allowed','restricted','prohibited']),
    'ai_details':f('AI usage details','long',helper='Required for restricted AI usage.'), 'data_handling':f('Data handling notes','long'),
 },
 'communication':{
    'channel':f('Primary channel',required=True,example='FairFlow project chat'), 'update_frequency':f('Update frequency',required=True,example='Every Monday'),
    'meeting_cadence':f('Meeting cadence'), 'response_hours':f('Max response time in business hours','number',True,example=24),
 },
 'termination':{
    'delivery_rules':f('Delivery protections and default policy','long',False,helper='Optional exact delivery protections: overdue closure uses the signed grace and notice; late timing requires acceptance. Both signatures provide consent.'),
    'notice_days':f('Termination notice in days','number',True,example=7), 'work_in_progress':f('Payment for work in progress','long',True,example='Pay for completed and documented work up to the agreed milestone cap.'),
    'cancellation_rule':f('Cooling-off or cancellation rule','long'), 'jurisdiction':f('Governing country or ISO region',required=True,example='IN'),
    'dispute_steps':f('Dispute steps','tags',True,example=['negotiation','mediation','platform_support_review'],helper='Fixed order: negotiation, mediation, then platform support review. No dispute resolution is implemented in this step.'),
 },
 'attachments':{'links':f('Briefs and reference files','repeat',children=ATTACHMENT)},
}

class StrictDraft(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)

def build_model(name,fields):
    definitions={}
    for key,spec in fields.items():
        kind=spec['type']
        if kind=='repeat':typ=list[build_model(name+key.title(),spec['children'])]
        elif kind in ('tags','urls','dates'):typ=list[str]
        elif kind=='days':typ=list[int]
        elif kind in ('number','money'):typ=int
        elif kind=='boolean':typ=bool
        else:typ=str
        definitions[key]=(typ|None,None)
    return create_model(name,__base__=StrictDraft,**definitions)

AgreementDraft=create_model('AgreementDraft',__base__=StrictDraft,
    **{section:(build_model(section.title(),fields)|None,None) for section,fields in CATALOG.items()},
    acknowledgments=(dict[str,dict[str,str]],{}))

def clean_draft(value):
    return AgreementDraft.model_validate(value).model_dump(exclude_none=True)

def leaves(value,path=''):
    if isinstance(value,dict):
        for key,item in value.items():yield from leaves(item,f'{path}.{key}' if path else key)
    elif isinstance(value,list):
        for i,item in enumerate(value):yield from leaves(item,f'{path}.{i}')
    else:yield path,value

def at(value,path,default=None):
    try:
        for part in path.split('.'):value=value[int(part)] if isinstance(value,list) else value[part]
        return value
    except (KeyError,IndexError,TypeError,ValueError):return default

def field_spec(path):
    parts=path.split('.')
    if len(parts)<2 or parts[0] not in CATALOG:return None
    spec=CATALOG[parts[0]].get(parts[1])
    if len(parts)>2 and spec:
        if spec['type']=='repeat' and len(parts)>3:spec=spec['children'].get(parts[3])
    return spec
