"""Conservative checks for conflicting monetary terms, not proof of agreement.

Historical offers and complex negotiations need a clarified current-terms summary.
Actual acceptance is recorded independently by each authenticated participant.
"""
import re, os
from decimal import Decimal

NUMBER = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?'
MONEY = re.compile(
    rf'(?<![\w.,])(?:\$\s*|USD\s+)({NUMBER})(?!\d|,\d|\.\d)'
    rf'|(?<![\w.,])({NUMBER})\s*(?:\$|\b(?:USD|dollars?)\b)', re.I,
)
HOURLY_AFTER = re.compile(r'\s*(?:/\s*(?:hours?|hrs?)|per\s+(?:hour|hr)|an?\s+hour|hourly)\b', re.I)
HOURLY_BEFORE = re.compile(r'(?:hourly(?:\s+rate)?|rate\s+per\s+(?:hour|hr))\s*(?:(?:is|of|at)\s+|[:=]\s*)?$', re.I)
REJECTION = re.compile(
    r"\b(?:not\s+(?:agreed|agreeing|accepted|acceptable)|(?:do\s+not|don['’]t|cannot|can['’]t)\s+agree|"
    r"(?:agreed|agree)\s+not(?=\s*(?:[.!?,;:]|$))|"
    r"(?:i|we)\s+(?:(?:will|would|can|want\s+to)\s+)?(?:only\s+)?pay\s+(?:less|more)|"
    r"(?:price|quote|budget|rate)\s+(?:is|seems)\s+(?:too\s+(?:high|much)|unacceptable)|"
    r"(?:price|budget|rate)\s+(?:is\s+)?(?:TBD|undecided|unconfirmed)|still\s+negotiating)\b", re.I,
)


def monetary_terms(text):
    prices, rates = set(), set()
    for match in MONEY.finditer(text):
        amount = int(Decimal((match[1] or match[2]).replace(',', '')) * 100)
        hourly = HOURLY_AFTER.match(text[match.end():]) or HOURLY_BEFORE.search(text[max(0, match.start()-60):match.start()])
        (rates if hourly else prices).add(amount)
    return prices, rates


def conversation_issues(text):
    prices, rates = monetary_terms(text)
    revisions = {int(n) for n in re.findall(r'\b(\d+)\s+revisions?\b', text, re.I)}
    issues = []
    if len(prices) > 1:
        values = ', '.join(f'${amount / 100:,.2f}' for amount in sorted(prices))
        issues.append(f'Conflicting project prices ({values}). Clarify one final project total; a counteroffer is not an accepted price.')
    if len(rates) > 1:
        issues.append('Conflicting hourly rates. Clarify one rate for additional work.')
    if len(revisions) > 1:
        issues.append('Conflicting revision limits. Clarify one revision allowance.')
    if REJECTION.search(text):
        issues.append('The conversation contains a rejection or unresolved terms. Provide a clarified current-terms summary before proposing an agreement.')
    return issues


def agreement_issues(payload):
    texts = [payload.get('source_conversation', ''), payload.get('contract', {}).get('task_description', '')]
    # Gemini reviews negotiation history semantically; demo rules require a clean summary.
    issues=list(dict.fromkeys(issue for text in texts for issue in conversation_issues(text))) if os.getenv('AI_MODE','demo')!='gemini' else []
    issues.extend(cross_field_issues(payload))
    return list(dict.fromkeys(issues))


POLICY_FIELDS = {
    'client_legal_name': 'Client contracting name',
    'freelancer_legal_name': 'Freelancer contracting name',
    'dependencies': 'Client inputs and dependency handling',
    'handoff': 'Files, formats, access and handoff',
    'ownership': 'Ownership and license terms',
    'third_party_materials': 'Pre-existing and third-party materials',
    'confidentiality': 'Confidentiality and permitted use of information',
    'cancellation': 'Cancellation, notice and payment for completed work',
    'expenses': 'Expenses, taxes and provider fees',
    'governing_law': 'Governing law and jurisdiction',
    'liability': 'Responsibilities, warranty and liability terms',
}


def example_details():
    """Explicit synthetic terms for the opt-in demo example, never real-user defaults."""
    return {
        'client_legal_name':'Sample Client Ltd', 'freelancer_legal_name':'Sample Freelancer',
        'dependencies':'Client supplies approved copy and images. Missing inputs require a jointly agreed schedule adjustment.',
        'handoff':'Supply editable source files, final exported assets and setup instructions through the project delivery link.',
        'ownership':'Rights in original paid work transfer after the applicable payment completes; unpaid work remains with its author.',
        'third_party_materials':'Disclose pre-existing tools and third-party assets with their applicable licenses before use.',
        'confidentiality':'Use private project information only to perform the work; portfolio publication requires written permission.',
        'cancellation':'Give written notice in the project room. Agree payment for delivered priced items and any refund jointly before closing.',
        'expenses':'Baseline includes agreed expenses and taxes. Provider fees and any conversion quote require disclosure; no FairFlow FX markup.',
        'governing_law':'Synthetic example only: England and Wales law, with courts in London. Replace for an actual agreement.',
        'liability':'Each participant is responsible for supplied materials and agreed obligations. Discuss limits and mandatory rights before real use.',
        'review_days':5, 'schedule_basis':'calendar_days', 'dispute_process':'joint_agreement', 'revision_basis':'per_milestone',
    }
PLACEHOLDER = re.compile(r'^(?:tbd|todo|n/?a|none|test|unknown|later|to be decided|to be confirmed|\?+|[-.]+)$', re.I)


def cross_field_issues(payload):
    issues=[]
    contract=payload.get('contract') or {}
    details=payload.get('details') or {}
    for key,label in POLICY_FIELDS.items():
        value=details.get(key)
        if not isinstance(value,str) or len(value.strip()) < (2 if key.endswith('_legal_name') else 10) or PLACEHOLDER.fullmatch(value.strip()):
            issues.append(f'details.{key}: {label} must be explicitly completed; placeholders are not terms.')
    for key,expected in [('review_days',5),('schedule_basis','calendar_days'),('dispute_process','joint_agreement'),('revision_basis','per_milestone')]:
        if details.get(key)!=expected:issues.append(f'details.{key}: Must match the platform policy ({expected}).')
    def inspect(value,path):
        if isinstance(value,str) and (not value.strip() or PLACEHOLDER.fullmatch(value.strip())):
            issues.append(f'{path}: Blank or placeholder text is not allowed; explain any non-applicable term.')
        elif isinstance(value,dict):
            for k,v in value.items():
                if k!='source_conversation':inspect(v,f'{path}.{k}' if path else k)
        elif isinstance(value,list):
            for i,v in enumerate(value):inspect(v,f'{path}[{i+1}]')
    inspect(payload,'')
    for key in ('deliverables','exclusions'):
        values=contract.get(key,[])
        if not values:issues.append(f'contract.{key}: Provide explicit {key}; explain if there are no exclusions.')
        normalized=[v.strip().casefold() for v in values if isinstance(v,str)]
        if len(normalized)!=len(set(normalized)):issues.append(f'contract.{key}: Duplicate entries must be consolidated.')
    included={str(v).strip().casefold() for v in contract.get('deliverables',[])}
    for excluded in contract.get('exclusions',[]):
        phrase=str(excluded).strip().casefold()
        if phrase in included:issues.append(f'contract.exclusions: "{excluded}" is also an included deliverable.')
    if details.get('client_legal_name','').strip().casefold()==details.get('freelancer_legal_name','').strip().casefold():
        issues.append('details: Client and freelancer must identify distinct contracting parties.')
    for text in [payload.get('source_conversation',''),contract.get('task_description','')]:
        prices,rates=monetary_terms(text)
        if payload.get('currency','USD')=='USD':
            if len(prices)==1 and contract.get('price_cents') not in prices:issues.append('contract.price_cents: Total differs from the price in the source/scope. Clarify the source before proceeding.')
            if len(rates)==1 and contract.get('hourly_rate_cents') not in rates:issues.append('contract.hourly_rate_cents: Rate differs from the source/scope.')
        revisions={int(n) for n in re.findall(r'\b(\d+)\s+revisions?\b',text,re.I)}
        if len(revisions)==1 and contract.get('allowed_revisions') not in revisions:issues.append('contract.allowed_revisions: Revision allowance differs from the source/scope.')
    for clause in payload.get('clauses',[]):
        text=clause.get('text','')
        if clause.get('kind')=='defect_policy':
            for sentence in re.split(r'[.!;\n]',text):
                if re.search(r'\b(?:defects?|bugs?|fixes|corrections)\b.{0,60}\b(?:consume|use up|count towards)\b.{0,25}\brevisions?\b',sentence,re.I) and not re.search(r'\b(?:not|never|without|no)\b',sentence,re.I):
                    issues.append('clauses.defect_policy: Defect corrections cannot consume presentation revisions.')
        if clause.get('kind')=='revision_policy':
            counts={int(n) for n in re.findall(r'\b(\d+)\s+(?:presentation\s+)?revisions?\b',text,re.I)}
            if counts and counts!={contract.get('allowed_revisions')}:issues.append('clauses.revision_policy: Revision count conflicts with the baseline.')
        if re.search(r'\bunlimited\s+revisions?\b',text,re.I):issues.append('clauses.revision_policy: Unlimited revisions conflict with the platform numeric allowance.')
        for days in re.findall(r'\b(\d+)[ -]days?\s+review\b',text,re.I):
            if int(days)!=5:issues.append('clauses: Review window conflicts with the fixed five-day review.')
        for hours in re.findall(r'\b(\d+)[ -]hours?\s+review\b',text,re.I):
            if int(hours)!=120:issues.append('clauses: Review window conflicts with the fixed five-day review.')
    milestones=payload.get('milestones',[])
    titles=[str(m.get('title','')).strip().casefold() for m in milestones]
    if len(titles)!=len(set(titles)):issues.append('milestones: Use distinct milestone titles so acceptances cannot be confused.')
    if milestones and sum(m.get('amount_cents',0) for m in milestones)!=contract.get('price_cents'):issues.append('milestones: Amounts must sum exactly to the agreement total.')
    work=payload.get('work_items',[])
    if len(work)!=len(milestones) or not work:issues.append('work_items: Supply priced acceptance items for every milestone.')
    for i,(m,items) in enumerate(zip(milestones,work)):
        if not items or sum(x.get('amount_cents',0) for x in items)!=m.get('amount_cents'):issues.append(f'work_items[{i+1}]: Priced items must sum to this milestone amount.')
    return issues
