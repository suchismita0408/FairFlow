"""Pure deterministic validation. The caller supplies today's date for send/sign.

No DB, network, mutable global state or AI result can change a validation outcome.
Flag IDs bind the rule, path and offending data; acknowledgments cannot survive
an edit to that data. BLOCKER flags have no dismissal mechanism.
"""
import hashlib, json, re, unicodedata
from datetime import date, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import pycountry
from decimal import Decimal
from babel.numbers import get_currency_precision
from .agreement_schema import CATALOG, SECTIONS, at, leaves, field_spec
from .payment_policy import reserve_days
from .agreement_feedback import feedback

def display_amount(value,currency):
    places=get_currency_precision(currency or 'USD')
    return f'{currency or ""} {Decimal(value)/(10**places):,.{places}f}'.strip()

VAGUE=re.compile(r'\b(?:etc\.?|and so on|as needed|ASAP|reasonable|unlimited|similar to|standard|minor changes|good quality|approximately|TBD)\b',re.I)
AI_TOOLS=re.compile(r'\b(?:ChatGPT|Gemini|Midjourney|DALL[ -]?E|Copilot|Stable Diffusion|OpenAI|AI[- ]generated)\b',re.I)
STOP={'the','a','an','and','or','of','to','for','with','in','on','no','not','is','be','any','work','development','deliver','build'}

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()
def normalized(text):return re.sub(r'[^\w]+',' ',unicodedata.normalize('NFKC',text).casefold()).strip()
def keywords(text):return set(normalized(text).split())-STOP
def empty(value):return value is None or value=='' or value==[] or isinstance(value,str) and not value.strip()
def iso_date(value):
    try:return date.fromisoformat(value)
    except (ValueError,TypeError):return None
def safe_url(value):
    try:
        p=urlparse(value)
        port=p.port
        return p.scheme in ('http','https') and bool(p.hostname) and not p.username and not p.password and not re.search(r'[\s\\<>"\x00-\x1f]',value)
    except (ValueError,TypeError):return False
def jurisdiction(value):
    try:pycountry.countries.lookup(value);return True
    except (LookupError,AttributeError):
        try:pycountry.subdivisions.lookup(value);return True
        except (LookupError,AttributeError):return False

def validate(agreement_json, *, today=None,completed_milestones=(),remaining_start_date=None):
    a=agreement_json;flags=[]
    funded=at(a,'payment.funding_model')=='paypal_funded_v1'
    funding_days=26 if funded else 29
    def add(rule,path,message,fix,severity='BLOCKER',value=None):
        fid=digest([rule,path,value if value is not None else at(a,path)])[:32]
        ack=a.get('acknowledgments',{}).get(fid,{})
        resolved=severity=='WARNING' and all(isinstance(ack.get(r),str) and ack[r].strip() for r in ('client','freelancer'))
        flags.append(dict(id=fid,severity=severity,section=SECTIONS.get(path.split('.')[0],('A',))[0],field_path=path,message=message,suggested_fix=fix,status='acknowledged' if resolved else 'open',why='Both parties must be able to rely on one precise, testable agreement.',**feedback(a,rule,path,message,fix)))
    def required(path):
        if empty(at(a,path)):add('required',path,'This field is mandatory.','Enter a specific value for this field.')
    def visit(fields,obj,prefix):
        for key,spec in fields.items():
            path=prefix+'.'+key;value=obj.get(key) if isinstance(obj,dict) else None
            if spec['required']:required(path)
            if empty(value):continue
            kind=spec['type']
            if kind=='repeat':
                for i,row in enumerate(value):visit(spec['children'],row,path+'.'+str(i))
            elif kind in ('number','money') and (type(value) is not int or value<0 or value>10**12):add('integer',path,'Use a non-negative integer.','Enter a whole number in the stated unit.')
            elif kind=='boolean' and type(value) is not bool:add('boolean',path,'Choose yes or no.','Select an explicit yes/no value.')
            elif kind=='select' and value not in spec['options']:add('option',path,'Choose one of the supported options.','Select an option from the list.')
            elif kind=='date' and not iso_date(value):add('date',path,'Invalid calendar date.','Enter a date as YYYY-MM-DD.')
            elif kind=='dates' and any(not iso_date(v) for v in value):add('date_list',path,'Invalid blackout date.','Use valid YYYY-MM-DD dates.')
            elif kind=='days' and (len(set(value))!=len(value) or any(type(v) is not int or v not in range(7) for v in value)):add('working_days',path,'Invalid or duplicate working days.','Select Monday through Sunday once each.')
            elif kind=='url' and not safe_url(value):add('url',path,'Only absolute HTTP(S) links without credentials are allowed.','Enter a valid https:// URL; links are never fetched.')
            elif kind=='urls' and any(not safe_url(v) for v in value):add('urls',path,'One or more links are invalid.','Use absolute HTTP(S) links without embedded credentials.')
            elif kind in ('country','currency'):
                try:(pycountry.countries if kind=='country' else pycountry.currencies).lookup(value)
                except LookupError:add(kind,path,'Invalid ISO country or currency.','Use an ISO country code or uppercase ISO-4217 currency code.')
                if kind=='currency' and (len(value)!=3 or value!=value.upper()):add('currency_code',path,'Use an uppercase ISO-4217 code.','For example, INR, USD or JPY.')
            elif kind=='zone':
                try:ZoneInfo(value)
                except (ValueError,ZoneInfoNotFoundError):add('zone',path,'Invalid IANA time zone.','For example, Asia/Kolkata or Europe/London.')
            if isinstance(value,str):
                if len(value.strip())<spec.get('min_length',0) or len(value)>spec.get('max_length',20000):add('length',path,'Text length is outside the permitted range.',f"Use {spec.get('min_length',1)}â€“{spec.get('max_length',20000)} characters.")
            if isinstance(value,list) and kind in ('tags','urls','dates') and any(not str(v).strip() for v in value):add('empty_item',path,'Empty list entries are not allowed.','Remove blank entries or write explicit terms.')
    for section,fields in CATALOG.items():visit(fields,a.get(section,{}),section)
    conditionals=[('parties.client_account_type','company','parties.authorized_contact'),('payment.expense_policy','capped_preapproved','payment.expense_cap_minor'),('payment.tax_responsibility','other','payment.tax_details'),('payment.due_trigger','days_after_delivery','payment.due_days'),('revisions.change_rate_kind','fixed_unit','revisions.change_rate_unit'),('ip.source_files',True,'ip.source_format'),('confidentiality.ai_policy','restricted','confidentiality.ai_details')]
    for source,value,target in conditionals:
        if at(a,source)==value:required(target)
    if not at(a,'scope.no_exclusions',False):required('scope.exclusions')
    elif at(a,'scope.exclusions'):add('exclusions_choice','scope.exclusions','Exclusions conflict with the no-exclusions checkbox.','Choose explicit exclusions or no exclusions, not both.')
    if funded:
        from .funded_payments import POLICY
        if at(a,'payment.funding_rules')!=POLICY:add('funding_rules','payment.funding_rules','The exact upfront funding terms must be reviewed by both parties.','Restore the platform funding terms before signing.')
    ms=at(a,'timeline.milestones',[]) or [];ds=at(a,'scope.deliverables',[]) or []
    currency=at(a,'payment.currency');total=at(a,'payment.total_minor');start=iso_date(at(a,'timeline.start_date'));end=iso_date(at(a,'timeline.deadline'))
    ints=all(type(m.get('amount_minor')) is int for m in ms)
    if ms and ints and type(total) is int and sum(m['amount_minor'] for m in ms)!=total:
        amount=sum(m['amount_minor'] for m in ms)
        add('sum','payment.total_minor',f'Milestone total is {display_amount(amount, currency)}; project total is {display_amount(total, currency)}. Difference: {display_amount(abs(total-amount), currency)}. The milestone total is {"higher" if amount>total else "lower"} than the project total.','Adjust milestone amounts or the total to match exactly.',value=[amount,total,currency])
    for i,m in enumerate(ms):
        path=f'timeline.milestones.{i}'
        if m.get('currency')!=currency:add('one_currency',path+'.currency','All amounts must use one currency.','Use the project currency on every milestone.',value=[currency,m.get('currency')])
        if type(m.get('amount_minor')) is int and m['amount_minor']<=0:add('positive_amount',path+'.amount_minor','Milestone amounts must be positive.','Enter at least one minor unit.')
        if m.get('id') not in completed_milestones and (type(m.get('review_days')) is not int or not 1<=m['review_days']<=5):
            add('paypal_review',path+'.review_days','Client review must be 1-5 calendar days within the 29-day authorization window.','Choose 1, 2, 3, 4 or 5 review days.')
        if m.get('auto_accept'):
            if at(a,'payment.due_trigger')!='milestone_approval':add('auto_payment_trigger','payment.due_trigger','Automatic acceptance conflicts with deferred payment terms.','Use milestone approval as the payment trigger, or disable automatic acceptance.')
            required(path+'.auto_accept_days')
            if m.get('auto_accept_days')!=m.get('review_days'):add('auto_accept',path+'.auto_accept_days','Auto-accept days must equal the agreed review period.','Use the same number of days as client review.')
    if start and end and end<=start:add('date_order','timeline.deadline','The final deadline must be after the start.','Choose a later final delivery date.')
    if today and start and start<today and not completed_milestones:add('past_start','timeline.start_date','Start date must be today or later when sending or signing.','Agree a current start date before proceeding.',value=[str(start),str(today)])
    previous=None
    for i,m in enumerate(ms):
        path=f'timeline.milestones.{i}.due_date';due=iso_date(m.get('due_date'))
        if m.get('id') in completed_milestones:continue
        if not due:continue
        if start and due<start or end and due>end:add('milestone_range',path,'Milestone date falls outside the project period.','Choose a date within the start and final deadline.')
        if previous and due<previous:add('chronology',path,'Milestones must be in chronological order.','Move this due date after the previous milestone.')
        previous=due
        if today and completed_milestones and due<today:add('past_remaining_milestone',path,'An unfunded milestone cannot have a delivery date in the past.','Choose a current or future delivery date.')
        if not m.get('bumped_date') and (due.weekday() not in (at(a,'timeline.working_days',[]) or []) or str(due) in (at(a,'timeline.blackout_dates',[]) or [])):add('nonworking',path,'Due date is a non-working day or blackout date.','Choose a working date or explicitly agree a bumped date.')
        next_due=iso_date(ms[i+1].get('due_date')) if i+1<len(ms) else end
        reserve=reserve_days(a,m)
        if next_due and reserve is not None and reserve>(next_due-due).days:add('review_window',path,'All included revisions, fresh review periods and the capture buffer do not fit before the next deadline.','Move delivery earlier, reduce agreed revision rounds, or extend the next deadline within the 29-day window.',value=[str(due),str(next_due),reserve])
    # Each sequential milestone gets its own authorization, not a project-wide hold.
    for i,m in enumerate(ms):
        if m.get('id') in completed_milestones:continue
        anchor=iso_date(m.get('start_date')) or (start if i==0 else iso_date(ms[i-1].get('due_date')))
        if remaining_start_date and (i==0 or ms[i-1].get('id') in completed_milestones) and anchor:anchor=max(anchor,remaining_start_date)
        due=iso_date(m.get('due_date'));reserve=reserve_days(a,m)
        if anchor and due and reserve is not None:
            if (due-anchor).days+reserve>funding_days:
                add('paypal_window',f'timeline.milestones.{i}.due_date',f'Delivery, every included revision, fresh review periods and buffer exceed the {funding_days}-day payment planning window.','Shorten or split this milestone, or reduce agreed revision rounds. Plan authorization at project start (first milestone) or the previous milestone delivery date.',value=[str(anchor),str(due),reserve])
    if start and end and ms:
        anchor=iso_date(ms[-1].get('start_date')) or (start if len(ms)==1 else iso_date(ms[-2].get('due_date')))
        if remaining_start_date and (len(ms)==1 or ms[-2].get('id') in completed_milestones) and anchor:anchor=max(anchor,remaining_start_date)
        if anchor and (end-anchor).days>funding_days:
            add('paypal_final_window','timeline.deadline','Final deadline exceeds the last milestone 29-day window.','Bring the deadline forward or split the work into shorter milestones; the revision reserve already includes the capture buffer.')
    dids=[d.get('id') for d in ds];mids=[m.get('id') for m in ms]
    if len(set(dids))!=len(dids):add('deliverable_ids','scope.deliverables','Deliverable references must be unique.','Give each deliverable its own reference.')
    if len(set(mids))!=len(mids):add('milestone_ids','timeline.milestones','Milestone references must be unique.','Give each milestone its own reference.')
    for i,d in enumerate(ds):
        linked=[m for m in ms if d.get('id') in (m.get('deliverable_ids') or [])]
        if len(linked)!=1 or d.get('milestone_id') not in mids or linked and linked[0].get('id')!=d.get('milestone_id'):add('deliverable_link',f'scope.deliverables.{i}.milestone_id','Each deliverable must belong to exactly one matching milestone.','Link this deliverable once and update the milestone inclusion list.')
        if type(d.get('quantity')) is int and d['quantity']<1:add('quantity',f'scope.deliverables.{i}.quantity','Quantity must be at least one.','Enter a positive quantity.')
        for exclusion in at(a,'scope.exclusions',[]) or []:
            dk=keywords(' '.join(str(d.get(k,'')) for k in ('name','description','specification')));ek=keywords(exclusion)
            if ek and (ek<=dk or len(ek&dk)/max(1,min(len(ek),len(dk)))>=0.7):add('exclusion_overlap',f'scope.deliverables.{i}.description',f'Deliverable overlaps exclusion: {exclusion}','Remove the overlap or narrow the exclusion.',value=[d,exclusion])
        if d.get('type')=='source_code' and at(a,'ip.source_files') is False:add('source_files',f'scope.deliverables.{i}.type','Source code is promised while source files are excluded.','Agree to deliver source files or change the deliverable type.')
    for i,m in enumerate(ms):
        ids=m.get('deliverable_ids') or []
        if not ids or any(x not in dids for x in ids) or len(set(ids))!=len(ids):add('milestone_link',f'timeline.milestones.{i}.deliverable_ids','Milestone has missing, unknown or duplicate deliverables.','Include at least one known deliverable once.')
    for key in ('deposit_percent','kill_fee_percent'):
        value=at(a,'payment.'+key)
        if value is not None and (type(value) is not int or not 0<=value<=100):add('percent','payment.'+key,'Percent must be between 0 and 100.','Choose a whole percentage within 0â€“100.')
    dep=at(a,'payment.deposit_percent',0)
    if type(dep) is int and type(total) is int and ms and type(ms[0].get('amount_minor')) is int and total*dep>ms[0]['amount_minor']*100 and empty(at(a,'payment.deposit_explanation')):add('deposit','payment.deposit_explanation','Deposit exceeds the first milestone amount.','Explain and explicitly agree the larger deposit.')
    if at(a,'revisions.written_approval') is not True:add('written_approval','revisions.written_approval','Written approval before change work is mandatory.','Set written approval to yes.')
    if at(a,'scope.out_of_scope_policy')=='quote_and_approve':required('revisions.change_rate_minor')
    if at(a,'ip.transfer_timing')=='delivery_exception' and at(a,'payment.due_trigger')=='days_after_delivery':
        if empty(at(a,'ip.transfer_exception')):add('ip_payment','ip.transfer_exception','Ownership on delivery precedes deferred payment.','Write an explicit exception or transfer ownership on payment.')
        else:add('ip_exception','ip.transfer_exception','Ownership transfers before deferred payment under an explicit exception.','Both parties must acknowledge this exception with a reason.','WARNING',value=[at(a,'ip'),at(a,'payment.due_days')])
    if at(a,'confidentiality.ai_policy')=='prohibited':
        for path,value in leaves({'scope':a.get('scope',{}),'basics':a.get('basics',{})}):
            if isinstance(value,str) and AI_TOOLS.search(value):add('ai_conflict',path,'An AI tool is required while AI usage is prohibited.','Remove the AI requirement or revise the AI policy.')
    from .workflow_policy import POLICY_TEXT
    if at(a,'termination.delivery_rules') not in (None,'',POLICY_TEXT):add('delivery_policy','termination.delivery_rules','The delivery protection policy must match the supported rules.','Use the supplied delivery protections or leave this optional policy empty.')
    law=at(a,'termination.jurisdiction')
    if law and not jurisdiction(law):add('jurisdiction','termination.jurisdiction','Unrecognized country or region.','Choose an ISO country name/code or subdivision code, for example IN or US-CA.')
    if at(a,'termination.dispute_steps')!=['negotiation','mediation','platform_support_review']:add('dispute_order','termination.dispute_steps','Dispute steps must follow the fixed order.','Use negotiation, mediation, then platform_support_review.')
    for path,value in leaves(a):
        if path.startswith('acknowledgments.') or not isinstance(value,str):continue
        spec=field_spec(path)
        if spec and spec['type'] in ('url','urls','date','dates','zone','country','currency'):continue
        if VAGUE.search(value):
            severity='BLOCKER' if '.acceptance_criteria.' in path or path.endswith('.specification') or path.endswith('.revision_rounds') or (path.startswith('revisions.') or re.search(r'\brevisions?\b',value,re.I)) and re.search(r'\b(?:unlimited|as needed)\b',value,re.I) else 'WARNING'
            add('ambiguity',path,'Vague wording: '+', '.join(m.group(0) for m in VAGUE.finditer(value)), 'Replace vague wording with measurable limits or dates.',severity)
        if not path.endswith('.revision_rounds'):
            numbers=re.findall(r'\b(\d+)\s+(?:included\s+)?(?:revision\s+rounds?|revisions?)\b',value,re.I)
            rounds={d.get('revision_rounds') for d in ds}
            if any(int(n) not in rounds for n in numbers):add('revision_conflict',path,'Revision count conflicts with structured deliverable terms.','Keep revision counts only in the per-deliverable revision fields.')
    return list({f['id']:f for f in flags}.values())

