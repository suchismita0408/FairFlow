"""Shared, deterministic authorization planning for signing and checkout."""
from datetime import date, timedelta
from .agreement_schema import at

AUTHORIZATION_DAYS = 29
CAPTURE_BUFFER_DAYS = 1
POLICY = ('Each milestone has its own 29-day PayPal authorization window. Delivery, '
          '1-5 calendar review days, every included revision and its fresh review, '
          'and a 1-day capture buffer must fit. The first 3 days are the honor period; '
          'authorization does not guarantee capture. After delivery, expiry of the signed '
          'review period without an objection automatically requests capture and a separate payout '
          'of the full milestone amount to the freelancer. Only verified payout item '
          'SUCCESS confirms receipt. The merchant covers payout fees. This is Sandbox money.')


def automatic_release_agreed(agreement,completed_milestones=()):
    milestones=at(agreement,'timeline.milestones',[]) or []
    return bool(milestones) and at(agreement,'payment.due_trigger')=='milestone_approval' and all(
        m.get('auto_accept') is True and m.get('auto_accept_days')==m.get('review_days')
        for m in milestones if m.get('id') not in completed_milestones)


def reserve_days(agreement, milestone, used=None):
    used = used or {}
    rounds = sum(max(0, d.get('revision_rounds', 0) - used.get(d.get('id'), 0))
                 for d in at(agreement, 'scope.deliverables', []) or []
                 if d.get('milestone_id') == milestone.get('id')
                 and type(d.get('revision_rounds')) is int)
    review = milestone.get('review_days')
    turn = at(agreement, 'revisions.turnaround_days')
    if type(review) is not int or type(turn) is not int or review < 0 or turn < 0:
        return None
    return review + rounds * (turn + review) + CAPTURE_BUFFER_DAYS


def checkout_window(agreement,milestone,today):
    reserve=reserve_days(agreement,milestone)
    try:
        due=date.fromisoformat(milestone['due_date'])
        if reserve is None or not 1<=reserve<=29:raise ValueError()
        capture_by=due+timedelta(days=reserve)
        earliest=capture_by-timedelta(days=29)
        return dict(earliest_checkout=str(earliest),delivery_date=str(due),reserve_days=reserve,capture_by=str(capture_by),available=earliest<=today<=due)
    except (ValueError,TypeError,KeyError,OverflowError):
        return dict(available=False)


def summary(agreement,completed_milestones=(),remaining_start_date=None):
    funded=at(agreement,'payment.funding_model')=='paypal_funded_v1'
    days=26 if funded else AUTHORIZATION_DAYS
    rows = []
    anchor = at(agreement, 'timeline.start_date')
    for m in at(agreement, 'timeline.milestones', []) or []:
        reserve = reserve_days(agreement, m)
        try:
            start = date.fromisoformat(m.get('start_date') or anchor)
            if m.get('id') not in completed_milestones and (not rows or rows[-1].get('completed')) and remaining_start_date:
                start=max(start,remaining_start_date)
            due = date.fromisoformat(m.get('due_date'))
            latest = start + timedelta(days=days)
            planned = due + timedelta(days=reserve)
            rows.append(dict(id=m.get('id'), title=m.get('title'), anchor=str(start),
                             due_date=str(due), reserve_days=reserve,
                             planned_capture_by=str(planned), authorization_end=str(latest),
                             days_required=(due-start).days+reserve,
                             completed=m.get('id') in completed_milestones,
                             fits=m.get('id') in completed_milestones or (due-start).days+reserve <= days))
        except (ValueError, TypeError, OverflowError):
            rows.append(dict(id=m.get('id'), title=m.get('title'), fits=False))
        anchor = m.get('due_date')
    if funded:
        from .funded_payments import POLICY as policy
    else:policy=POLICY
    return {'policy': policy, 'funding_model':'paypal_funded_v1' if funded else 'legacy_authorization', 'milestones': rows}
