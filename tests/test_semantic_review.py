import asyncio
import copy

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app import ai, semantic_review


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setenv('AI_MODE','gemini')


def test_semantic_rejection_blocks_even_without_known_objection_phrase(gemini,monkeypatch):
    async def review(schema,instruction,data):
        assert schema is semantic_review.ExtractionReview
        return schema(status='needs_clarification',explanation='The client cannot afford the proposed figure, so the price is unresolved.',questions=['What total do both parties agree to?'],contract=None)
    monkeypatch.setattr(ai,'structured',review)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ai.extract('Freelancer: $900 with 2 revisions. Extra work $50 per hour. Client: That figure is beyond what I can stretch to.'))
    assert exc.value.status_code==422
    assert 'Gemini needs clarification' in exc.value.detail


def test_provider_outage_never_becomes_a_demo_draft(gemini,monkeypatch):
    async def outage(*args):raise HTTPException(502,'Provider unavailable')
    monkeypatch.setattr(ai,'structured',outage)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ai.extract('Build a page for $900 with 2 revisions. Extra work $50 per hour. Agreed.'))
    assert exc.value.status_code==502


def test_cross_field_semantic_conflict_blocks_final_document(gemini,monkeypatch):
    async def review(schema,instruction,data):
        assert schema is semantic_review.AgreementReview
        return schema(status='needs_clarification',issues=[{'fields':['details.ownership','details.third_party_materials'],'explanation':'The client is promised ownership but another term denies all use rights.','question':'Which ownership and licensing terms apply?'}])
    monkeypatch.setattr(ai,'structured',review)
    with pytest.raises(HTTPException) as exc:asyncio.run(semantic_review.require_ready({'details':{}}))
    assert exc.value.status_code==422


def test_model_cannot_claim_ready_while_reporting_conflicts():
    with pytest.raises(ValidationError):
        semantic_review.AgreementReview(status='ready',issues=[{'fields':['contract'],'explanation':'There is a conflict in the scope.','question':'Which scope applies?'}])


def test_unknown_ai_field_references_are_not_accepted(gemini,monkeypatch):
    async def review(schema,*args):
        return schema(status='needs_clarification',issues=[{'fields':['invented_field'],'explanation':'Unverifiable invented field in the model output.','question':'Please clarify this field.'}])
    monkeypatch.setattr(ai,'structured',review)
    with pytest.raises(HTTPException) as exc:asyncio.run(semantic_review.check({}))
    assert exc.value.status_code==502


def test_every_policy_edit_changes_semantic_fingerprint():
    original={'details':{'ownership':'Transfer after payment'},'contract':{'price_cents':100}}
    changed=copy.deepcopy(original);changed['details']['ownership']='No rights granted'
    assert semantic_review.fingerprint(original)!=semantic_review.fingerprint(changed)


def test_resolved_negotiation_is_reviewed_by_gemini(gemini,monkeypatch):
    async def review(schema,instruction,data):
        from app.models import Contract
        return schema(status='ready',explanation='Both participants explicitly agreed the final counteroffer.',questions=[],contract=Contract(title='Landing page',task_description='Build a static landing page.',price_cents=85000,allowed_revisions=2,hourly_rate_cents=5000,deliverables=['Landing page'],exclusions=['Database']))
    monkeypatch.setattr(ai,'structured',review)
    result=asyncio.run(ai.extract('Freelancer: $900 with 2 revisions, extra work $50 per hour. Client: I offer $850. Freelancer: Agreed to $850. Client: Confirmed final total $850.'))
    assert result.price_cents==85000
