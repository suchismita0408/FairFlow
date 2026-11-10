import asyncio

import pytest
from fastapi import HTTPException

from app import ai
from app.agreement_checks import monetary_terms


REJECTED_CHAT = (
    'Client: Build a responsive coffee-brand landing page, with hero, menu, about and contact details. '
    'No database or customer accounts. Freelancer: $900 for the project, with 2 revisions per milestone. '
    'Additional work is $50 per hour. Client: Not Agreed. I will pay 800 dollars. Freelancer: Okay 850$.'
)


@pytest.mark.parametrize('mode', ['demo', 'gemini'])
def test_screenshot_negotiation_requires_clarification(monkeypatch, mode):
    monkeypatch.setenv('AI_MODE', mode)
    async def unexpected(*args, **kwargs):
        if mode=='gemini':
            from app.semantic_review import ExtractionReview
            return ExtractionReview(status='needs_clarification',contract=None,questions=['Which final price applies?'],explanation='Unresolved prices: $800.00, $850.00 and $900.00.')
        pytest.fail('Demo checks must not invoke Gemini.')
    monkeypatch.setattr(ai, 'structured', unexpected)
    with pytest.raises(HTTPException) as error:
        asyncio.run(ai.extract(REJECTED_CHAT))
    assert error.value.status_code == 422
    assert all(amount in error.value.detail for amount in ('$800.00', '$850.00', '$900.00'))


@pytest.mark.parametrize('price', ['$850', '850$', '850 dollars', 'USD 850', '850 USD'])
def test_clear_current_terms_keep_project_price_separate_from_rate(monkeypatch, price):
    monkeypatch.setenv('AI_MODE', 'demo')
    contract = asyncio.run(ai.extract(f'Build a responsive landing page for {price}. Includes 2 revisions. Additional work is 50 dollars per hour.'))
    assert contract.price_cents == 85000
    assert contract.hourly_rate_cents == 5000


@pytest.mark.parametrize('rate', ['$50/hr', '50$/hour', 'USD 50 per hr', 'hourly rate: $50', 'hourly rate is 50 dollars'])
def test_hourly_rate_not_mistaken_for_second_budget(rate):
    assert monetary_terms(f'Project total $900. Additional work {rate}.') == ({90000}, {5000})


def test_single_price_with_explicit_rejection_is_not_a_draft(monkeypatch):
    monkeypatch.setenv('AI_MODE', 'demo')
    with pytest.raises(HTTPException) as error:
        asyncio.run(ai.extract('Freelancer: Build the page for $900, including 2 revisions. Client: I do not agree.'))
    assert 'rejection' in error.value.detail


@pytest.mark.parametrize('reply',[
    'Agreed not. i will pay less.', 'Agreed not.', 'I will pay less.',
    'We can only pay less.', 'I would pay more.', 'The price is too high.',
    'The quote is unacceptable.', 'I don’t agree.',
])
@pytest.mark.parametrize('mode',['demo','gemini'])
def test_non_numeric_price_objection_blocks_draft(monkeypatch,reply,mode):
    monkeypatch.setenv('AI_MODE',mode)
    async def unexpected(*args,**kwargs):
        if mode=='gemini':
            from app.semantic_review import ExtractionReview
            return ExtractionReview(status='needs_clarification',contract=None,questions=['What price is agreed?'],explanation='There is an unresolved rejection in the client reply.')
        pytest.fail('Demo checks must not invoke Gemini.')
    monkeypatch.setattr(ai,'structured',unexpected)
    with pytest.raises(HTTPException) as error:
        asyncio.run(ai.extract('Client: Build a landing page. Freelancer: $900 with 2 revisions. Extra work $50 per hour. Client: '+reply))
    assert error.value.status_code==422
    assert 'rejection' in error.value.detail


def test_agreement_to_exclude_a_feature_is_not_a_price_rejection(monkeypatch):
    monkeypatch.setenv('AI_MODE','demo')
    contract=asyncio.run(ai.extract('Build a page for $900 with 2 revisions. We agreed not to include a database.'))
    assert contract.price_cents==90000
