from copy import deepcopy
from datetime import date,timedelta
from app.agreement_examples import example_agreement
from app.agreement_validation import validate


def test_checkout_window_opens_only_when_capture_budget_fits():
    from app.payment_policy import checkout_window
    a=example_agreement();m=a['timeline']['milestones'][0]
    a['scope']['deliverables'][0]['revision_rounds']=0
    m['due_date']='2026-12-03'
    early=checkout_window(a,m,date(2026,11,6))
    assert early['earliest_checkout']=='2026-11-08'
    assert early['capture_by']=='2026-12-07' and early['reserve_days']==4
    assert early['available'] is False
    assert checkout_window(a,m,date(2026,11,8))['available'] is True
    assert checkout_window(a,m,date(2026,12,4))['available'] is False


def test_review_blocker_explains_arithmetic_and_exact_boundary():
    a=example_agreement(date(2030,1,1));m=a['timeline']['milestones'][0]
    a['scope']['deliverables'][0]['revision_rounds']=1
    m['due_date']='2030-01-22';a['timeline']['deadline']='2030-01-30'
    flag=next(f for f in validate(a) if 'do not fit' in f['message'])
    assert 'Only 8 calendar days' in flag['problem']
    assert '9 are required' in flag['problem']
    assert '3 initial review + 1 revision round(s) × (2 revision + 3 fresh review) + 1 capture buffer = 9' in flag['problem']
    assert '2030-01-31' in flag['solution'] and '2030-01-21' in flag['solution']
    assert 'timeline.deadline' in flag['related_fields']


def test_deliverable_blocker_identifies_duplicate_assignments():
    a=example_agreement();m=a['timeline']['milestones'][0]
    a['timeline']['milestones'].append({**deepcopy(m),'id':'M2','title':'Second delivery'})
    flag=next(f for f in validate(a) if 'exactly one' in f['message'])
    assert 'Milestone 1' in flag['problem'] and 'Milestone 2' in flag['problem']
    assert 'select D1' in flag['solution'] and 'deselect it in every other' in flag['solution']
    assert len(flag['related_fields'])==2


def test_feedback_has_consistent_structure_for_missing_fields_and_conflicts():
    a=example_agreement();a['payment']['total_minor']=99999
    a['basics']['title']='';a['timeline']['milestones'][0]['currency']='USD'
    flags=validate(a)
    assert all(f['problem'] and f['solution'] and isinstance(f['related_fields'],list) for f in flags)
    total=next(f for f in flags if 'Milestone total' in f['message'])
    assert 'INR 1,000.00' in total['problem'] and 'INR 999.99' in total['problem']
    assert 'INR 0.01' in total['problem']
    currency=next(f for f in flags if 'one currency' in f['message'])
    assert 'USD' in currency['problem'] and 'INR' in currency['problem']
    assert next(f for f in flags if f['field_path']=='basics.title')['problem']=='Project title is missing.'


def test_out_of_order_dates_prioritize_chronology_and_keep_rules_pure():
    a=example_agreement();original=deepcopy(a)
    m=a['timeline']['milestones'][0]
    a['timeline']['milestones'].append({**m,'id':'M2','due_date':a['timeline']['start_date']})
    before=deepcopy(a);flags=validate(a)
    assert any('Fix the date order first' in f['solution'] for f in flags)
    assert a==before
    assert not validate(original)
