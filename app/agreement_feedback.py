"""Plain-language explanations of validation results; never changes the rules."""
from datetime import date, timedelta
from decimal import Decimal
from babel.numbers import get_currency_precision
from .agreement_schema import at, field_spec
from .payment_policy import reserve_days


def feedback(a, rule, path, message, fix):
    spec=field_spec(path) or {}
    name=spec.get('label',path.split('.')[-1].replace('_',' '))
    value=at(a,path)
    milestones=at(a,'timeline.milestones',[]) or []
    deliverables=at(a,'scope.deliverables',[]) or []
    parts=path.split('.')
    index=int(parts[2]) if len(parts)>2 and parts[2].isdigit() else None
    def milestone(i):
        m=milestones[i]
        return f"Milestone {i+1} ({m.get('title') or m.get('id') or 'untitled'})"
    def show(v):
        if v is None or v=='' or v==[]:return 'not set'
        if type(v) is bool:return 'Yes' if v else 'No'
        if isinstance(v,list):return ', '.join(show(x) for x in v)[:300]
        if isinstance(v,dict):return 'an incomplete entry' if not v else str(v)[:200]
        return str(v)[:300]
    def money(v):
        code=at(a,'payment.currency','USD')
        try:return f'{code} {Decimal(v)/(10**get_currency_precision(code)):,.{get_currency_precision(code)}f}'
        except Exception:return show(v)+' minor units'
    def shifted(v,days):
        try:return str(date.fromisoformat(v)+timedelta(days=days))
        except (ValueError,TypeError,OverflowError):return 'a date within the valid range'
    problem=f'{name}: {message} Current value: {money(value) if spec.get("type")=="money" and type(value) is int else show(value)}.'
    solution=fix
    related=[]
    if rule=='required':
        problem=f'{name} is missing.'
        solution=f'Fill in {name}. '+({'repeat':'Add at least one entry and complete its required fields.','boolean':'Choose Yes or No.','tags':'Enter at least one specific item, one per line.','money':'Enter the agreed amount in the project currency.','date':'Choose a calendar date.'}.get(spec.get('type'),'Enter a specific value rather than leaving it blank.'))
        conditions={'parties.authorized_contact':('parties.client_account_type','Company'), 'payment.expense_cap_minor':('payment.expense_policy','Capped preapproved expenses'), 'payment.tax_details':('payment.tax_responsibility','Other'), 'payment.due_days':('payment.due_trigger','Days after delivery'), 'revisions.change_rate_unit':('revisions.change_rate_kind','Fixed unit'), 'ip.source_format':('ip.source_files','Yes'), 'confidentiality.ai_details':('confidentiality.ai_policy','Restricted')}
        if path in conditions:
            source,choice=conditions[path];label=(field_spec(source) or {}).get('label',source)
            problem+=f' It is required because {label} is set to {choice}.'
            related=[source]
        if path=='scope.exclusions':
            solution='List the work excluded from the price, or explicitly set No exclusions to Yes if both parties agree nothing is excluded.'
            related=['scope.no_exclusions']
        if path.endswith('.auto_accept_days'):
            review=milestones[index].get('review_days')
            problem=f'Automatic release is enabled on {milestone(index)}, but the number of automatic approval days is missing.'
            solution=f'Enter {review} days to match the client review period, or turn automatic release off.'
            related=[f'timeline.milestones.{index}.auto_accept',f'timeline.milestones.{index}.review_days']
    elif rule=='length':
        problem=f'{name} has {len(str(value).strip())} characters; it needs {spec.get("min_length",0)}–{spec.get("max_length",20000)}.'
        solution=f'Edit {name} to stay within that character range.'
    elif rule=='option':solution='Choose one of: '+', '.join(str(v).replace('_',' ') for v in spec.get('options',[]))+'.'
    elif rule=='integer' and spec.get('type')=='money':solution='Enter a non-negative amount in the displayed currency; do not type currency symbols or text.'
    elif rule=='sum':
        total=sum(m['amount_minor'] for m in milestones)
        problem=f'Milestone prices add up to {money(total)}, but Total project price is {money(value)}. Difference: {money(abs(total-value))}.'
        solution='Adjust the milestone prices or Total project price until both totals match. No currency conversion is applied.'
        related=['timeline.milestones']
    elif rule=='one_currency':
        problem=f'{milestone(index)} uses {show(value)}, but Project currency is {show(at(a,"payment.currency"))}.'
        solution=f'Set this milestone currency to {show(at(a,"payment.currency"))}, or agree a different project currency and update every milestone. Review prices explicitly; changing a currency does not convert them.'
        related=['payment.currency']
    elif rule=='paypal_review':
        problem=f'{milestone(index)} has {show(value)} client review days. FairFlow requires 3, 4 or 5 calendar days.'
        solution='Set Client review days to 3, 4 or 5, then check the delivery and revision window again.'
    elif rule=='auto_accept':
        review=milestones[index].get('review_days')
        problem=f'Automatic approval is set to {show(value)} days, but client review is {review} days.'
        solution=f'Set automatic approval days to {review}, or turn automatic release off.'
        related=[f'timeline.milestones.{index}.review_days',f'timeline.milestones.{index}.auto_accept']
    elif rule in ('review_window','paypal_window'):
        m=milestones[index];reserve=reserve_days(a,m);review=m.get('review_days');turn=at(a,'revisions.turnaround_days')
        rounds=sum(d.get('revision_rounds',0) for d in deliverables if d.get('milestone_id')==m.get('id') and type(d.get('revision_rounds')) is int)
        breakdown=f'{review} initial review + {rounds} revision round(s) × ({turn} revision + {review} fresh review) + 1 capture buffer = {reserve} calendar days after delivery.'
        if rule=='review_window':
            boundary=milestones[index+1].get('due_date') if index+1<len(milestones) else at(a,'timeline.deadline')
            target=milestone(index+1) if index+1<len(milestones) else 'Final completion deadline'
            try:gap=(date.fromisoformat(boundary)-date.fromisoformat(value)).days
            except (ValueError,TypeError):gap='unknown'
            problem=f'{milestone(index)} is due {value}; {target} is {boundary}. Only {gap} calendar days are available afterward, but {reserve} are required. '+breakdown
            solution=f'Move {target} to {shifted(value,reserve)} or later, or move this delivery to {shifted(boundary,-reserve)} or earlier. Alternatively, agree fewer revision rounds. Recheck all other date and 29-day limits.'
            if isinstance(gap,int) and gap<0:
                problem=f'{target} is dated {boundary}, before {milestone(index)} on {value}. The schedule is out of order. '+breakdown
                solution=f'Fix the date order first: {target} must be {shifted(value,reserve)} or later with the current allowances. Remove any milestone added accidentally, then recheck all 29-day limits.'
            related=[f'timeline.milestones.{index+1}.due_date' if index+1<len(milestones) else 'timeline.deadline','scope.deliverables','revisions.turnaround_days']
        else:
            anchor=at(a,'timeline.start_date') if index==0 else milestones[index-1].get('due_date')
            problem=f'{milestone(index)} cannot fit delivery on {value} plus {reserve} reserved days into the 29-day window planned from {anchor}. '+breakdown
            solution=f'The latest delivery under this planning anchor is {shifted(anchor,29-reserve)}. Shorten or split the milestone, or agree fewer revisions. Check working days and the other milestones before choosing a date.'
            related=['scope.deliverables','revisions.turnaround_days']
    elif rule=='chronology':
        problem=f'{milestone(index)} is due {value}, before {milestone(index-1)} on {milestones[index-1].get("due_date")}.'
        solution='Move this delivery after the preceding milestone and leave its full review/revision buffer, or remove this milestone if it was added accidentally.'
        related=[f'timeline.milestones.{index-1}.due_date']
    elif rule=='milestone_range':solution=f'Choose a delivery between project start {show(at(a,"timeline.start_date"))} and final completion {show(at(a,"timeline.deadline"))}, leaving review/revision time afterward.'
    elif rule=='date_order':solution=f'Choose a final completion deadline later than the start date {show(at(a,"timeline.start_date"))}.'
    elif rule=='paypal_final_window':
        anchor=at(a,'timeline.start_date') if len(milestones)==1 else milestones[-2].get('due_date')
        solution=f'The current last-milestone planning anchor is {anchor}; final completion must be no later than {shifted(anchor,29)}. Shorten or split the work if needed.'
    elif rule=='deliverable_link':
        d=deliverables[index];included=[milestone(i) for i,m in enumerate(milestones) if d.get('id') in (m.get('deliverable_ids') or [])]
        chosen=next((i for i,m in enumerate(milestones) if m.get('id')==value),None)
        problem=f'Deliverable {d.get("id")} ({d.get("name") or "untitled"}) is linked to {value}, but is included in: {", ".join(included) or "no milestones"}.'
        solution=f'Keep this deliverable selected in exactly one milestone. '+(f'If {milestone(chosen)} is intended, select {d.get("id")} in that milestone’s Included deliverables and deselect it in every other milestone.' if chosen is not None else 'Choose an existing Linked milestone, then select this deliverable in that milestone’s Included deliverables.')
        related=[f'timeline.milestones.{i}.deliverable_ids' for i in range(len(milestones))]
    elif rule=='milestone_link':
        unknown=[x for x in value or [] if x not in [d.get('id') for d in deliverables]]
        problem=f'{milestone(index)} includes {show(value)}.'+(f' Unknown references: {show(unknown)}.' if unknown else ' Its selection is empty or contains duplicates.')
        solution='Select each intended existing deliverable once. Set each selected deliverable’s Linked milestone to this milestone and remove it from other milestones.'
        related=['scope.deliverables']
    elif rule in ('deliverable_ids','milestone_ids'):
        rows=deliverables if rule=='deliverable_ids' else milestones
        ids=[r.get('id') for r in rows];duplicates=sorted({str(x) for x in ids if ids.count(x)>1})
        problem='Duplicate references: '+', '.join(duplicates)+'. Each row needs its own reference.'
        solution='Give the duplicate rows unique references, then update Linked milestone and Included deliverables to match.'
    elif rule=='revision_conflict':solution='The revision count written here differs from the deliverable settings. Edit the wording or the deliverable’s Included revision rounds so they agree.'
    elif rule=='nonworking':solution=f'{value} is outside selected working days or is a blackout date. Choose a working date, or explicitly enable the agreed non-working-date exception for this milestone.'
    elif rule=='empty_item':solution=f'Remove blank lines/entries from {name}, or replace each blank with a specific item.'
    elif rule=='auto_payment_trigger':
        solution='Set When payment is released to milestone approval, or turn automatic release off on the milestones that use it.'
        related=['timeline.milestones']
    elif rule=='exclusions_choice':
        solution='Either keep the excluded-work list and set No exclusions to No, or remove the excluded-work list and explicitly choose No exclusions.'
        related=['scope.no_exclusions']
    elif rule=='source_files':
        solution='Set Source files to Yes and specify the source-file format, or change this deliverable’s type so it does not promise source code.'
        related=['ip.source_files','ip.source_format']
    elif rule in ('ip_payment','ip_exception'):
        related=['ip.transfer_timing','payment.due_trigger','ip.transfer_exception']
    elif rule=='ai_conflict':
        solution='Remove the named AI tool from this requirement, or mutually change the AI policy from Prohibited to an agreed permitted/restricted policy.'
        related=['confidentiality.ai_policy']
    elif rule=='deposit':
        related=['payment.deposit_percent','timeline.milestones.0.amount_minor']
    elif rule=='ambiguity':
        solution='Replace the highlighted vague wording with a measurable count, date, format or acceptance test. For example, describe which screens and devices must pass instead of promising “good quality”.'
    return dict(problem=problem,solution=solution,related_fields=related)
