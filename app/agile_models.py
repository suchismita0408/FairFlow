from pydantic import BaseModel, Field, model_validator
from typing import Literal
from .models import Contract, ChatInput

class MilestoneSpec(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    scope: str = Field(min_length=10, max_length=8000)
    amount_cents: int = Field(gt=0, le=100000000)
    duration_days: int = Field(default=21, ge=14, le=21)

class AgileDraft(ChatInput):
    milestone_count: int = Field(default=3, ge=1, le=12)

class Plan(BaseModel):
    milestones: list[MilestoneSpec] = Field(min_length=1, max_length=12)

class AgileProject(BaseModel):
    contract: Contract
    client_name: str = Field(min_length=1, max_length=100)
    freelancer_name: str = Field(min_length=1, max_length=100)
    milestones: list[MilestoneSpec] = Field(min_length=1, max_length=12)

    @model_validator(mode='after')
    def balanced(self):
        if sum(m.amount_cents for m in self.milestones) != self.contract.price_cents:
            raise ValueError('Milestone amounts must exactly equal the baseline contract price.')
        return self

class Consent(BaseModel):
    role: Literal['client', 'freelancer']
    plan_hash: str = Field(min_length=64, max_length=64)

class VaultConsent(BaseModel):
    agreed: Literal[True]
    plan_hash: str = Field(min_length=64, max_length=64)

class Progress(BaseModel):
    percent: int = Field(ge=1, le=99)
    evidence_url: str = Field(min_length=10, max_length=2000)
    completed_work: str = Field(min_length=10, max_length=5000)
    remaining_work: str = Field(min_length=10, max_length=5000)

    @model_validator(mode='after')
    def url(self):
        from pydantic import TypeAdapter, HttpUrl
        self.evidence_url = str(TypeAdapter(HttpUrl).validate_python(self.evidence_url))
        return self

class ProgressConsent(BaseModel):
    progress_id: str
    agreed: Literal[True]

class DemoClock(BaseModel):
    days: int = Field(ge=1, le=35)

class DemoBilling(BaseModel):
    fail_next: bool

class Retry(BaseModel):
    reconciled: Literal[True]
