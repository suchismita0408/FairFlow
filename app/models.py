from pydantic import BaseModel, Field, HttpUrl
from typing import Literal

class Contract(BaseModel):
    title: str = Field(min_length=3, max_length=100)
    task_description: str = Field(min_length=10, max_length=8000)
    price_cents: int = Field(gt=0, le=100000000)
    allowed_revisions: int = Field(ge=0, le=100)
    deliverables: list[str] = Field(min_length=1, max_length=30)
    exclusions: list[str] = Field(max_length=30)
    hourly_rate_cents: int = Field(default=5000, gt=0, le=100000)

class ChatInput(BaseModel):
    chat_history: str = Field(min_length=20, max_length=30000)

class NewProject(BaseModel):
    contract: Contract
    client_name: str = Field(min_length=1, max_length=100)
    freelancer_name: str = Field(min_length=1, max_length=100)

class ChangeInput(BaseModel):
    request: str = Field(min_length=5, max_length=5000)

class Decision(BaseModel):
    verdict: Literal['in_scope', 'out_of_scope', 'needs_review']
    reasoning: str = Field(min_length=10, max_length=2000)
    estimated_hours: int = Field(ge=0, le=200)
    confidence: float = Field(ge=0, le=1)

class Delivery(BaseModel):
    url: HttpUrl
    notes: str = Field(default='', max_length=4000)

class Accept(BaseModel):
    role: Literal['client', 'freelancer']
