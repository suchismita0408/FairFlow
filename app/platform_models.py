from pydantic import BaseModel,Field,EmailStr,model_validator,HttpUrl,ConfigDict
from typing import Literal
from .agile_models import AgileProject
class Clause(BaseModel):
    reference:str=Field(pattern=r'^[A-Z][A-Z0-9_-]{0,29}$')
    kind:Literal['deliverable','acceptance','exclusion','defect_policy','revision_policy','dependency']
    text:str=Field(min_length=5,max_length=4000)
class Item(BaseModel):
    title:str=Field(min_length=3,max_length=200)
    acceptance:str=Field(min_length=10,max_length=4000)
    amount_cents:int=Field(gt=0,le=100000000)
class AgreementDetails(BaseModel):
    model_config=ConfigDict(str_strip_whitespace=True,extra='forbid')
    client_legal_name:str=Field(min_length=2,max_length=200)
    freelancer_legal_name:str=Field(min_length=2,max_length=200)
    dependencies:str=Field(min_length=10,max_length=4000)
    handoff:str=Field(min_length=10,max_length=4000)
    ownership:str=Field(min_length=10,max_length=4000)
    third_party_materials:str=Field(min_length=10,max_length=4000)
    confidentiality:str=Field(min_length=10,max_length=4000)
    cancellation:str=Field(min_length=10,max_length=4000)
    expenses:str=Field(min_length=10,max_length=4000)
    governing_law:str=Field(min_length=10,max_length=4000)
    liability:str=Field(min_length=10,max_length=4000)
    review_days:Literal[5]
    schedule_basis:Literal['calendar_days']
    dispute_process:Literal['joint_agreement']
    revision_basis:Literal['per_milestone']

class CreateWorkspace(AgileProject):
    model_config=ConfigDict(extra='forbid')
    details:AgreementDetails
    currency:str="USD"
    source_conversation:str=Field(default='',max_length=30000)
    creator_role:Literal['client','freelancer']
    counterparty_email:EmailStr
    clauses:list[Clause]=Field(min_length=4,max_length=50)
    work_items:list[list[Item]]=Field(min_length=1,max_length=12)
    @model_validator(mode='after')
    def clauses_and_items(self):
        from .agreement_checks import agreement_issues
        issues=agreement_issues(self.model_dump())
        if issues:raise ValueError(' '.join(issues))
        from .currencies import validate
        for amount in [self.contract.price_cents,self.contract.hourly_rate_cents]+[m.amount_cents for m in self.milestones]+[x.amount_cents for items in self.work_items for x in items]:validate(self.currency,amount)
        refs=[c.reference for c in self.clauses]
        if len(set(refs))!=len(refs):raise ValueError('Clause references must be unique.')
        kinds={c.kind for c in self.clauses}
        if not {'acceptance','defect_policy','revision_policy','deliverable'}.issubset(kinds):raise ValueError('Include deliverable, acceptance, defect and revision clauses.')
        if self.work_items:
            if len(self.work_items)!=len(self.milestones):raise ValueError('Provide work items for every milestone.')
            for m,items in zip(self.milestones,self.work_items):
                if not items or sum(x.amount_cents for x in items)!=m.amount_cents:raise ValueError('Priced work items must sum to each milestone.')
        return self
class AcceptVersion(BaseModel):digest:str=Field(min_length=64,max_length=64)
class EvidenceInput(BaseModel):
    milestone_id:str
    title:str=Field(min_length=3,max_length=200)
    url:HttpUrl
    description:str=Field(min_length=10,max_length=5000)
class MessageInput(BaseModel):body:str=Field(min_length=1,max_length=5000)
class DecisionInput(BaseModel):
    milestone_id:str
    request:str=Field(min_length=5,max_length=5000)
    evidence_ids:list[str]=Field(default_factory=list,max_length=20)
    dispute_current_work:bool=False
class Recommendation(BaseModel):
    category:Literal['defect','included_revision','additional_scope','client_dependency','ambiguous','disputed_evidence']
    clause_ids:list[str]=Field(max_length=30)
    evidence_ids:list[str]=Field(max_length=20)
    explanation:str=Field(min_length=10,max_length=3000)
    missing_information:list[str]=Field(max_length=10)
    recommended_action:str=Field(min_length=5,max_length=1000)
    estimated_hours:int=Field(ge=0,le=200)
class JointProposal(BaseModel):
    kind:Literal['decision','dispute_resolution','reconciliation','refund','partial_items']
    milestone_id:str
    reason:str=Field(min_length=10,max_length=4000)
    decision_id:str|None=None
    category:Literal['defect','included_revision','additional_scope','client_dependency','ambiguous','disputed_evidence']|None=None
    amount_cents:int=Field(default=0,ge=0,le=100000000)
    capture_id:str|None=None
    item_ids:list[str]=Field(default_factory=list,max_length=30)
    evidence_id:str|None=None
class Vote(BaseModel):digest:str=Field(min_length=64,max_length=64)

class ClientVisionInput(BaseModel):
    goal:str=Field(default='',max_length=5000)
    assets_inspiration:str=Field(default='',max_length=5000)

class SmartProposalSection1(BaseModel):
    project_title:str=Field(min_length=2,max_length=150)
    milestone_phase:str=Field(min_length=2,max_length=150)
    goal_summary:str=Field(min_length=5,max_length=5000)

class SmartProposalSection2(BaseModel):
    deliverables:list[str]=Field(min_length=1,max_length=30)

class SmartProposalSection3(BaseModel):
    revisions_included:int=Field(default=2,ge=0,le=50)
    revision_rules:str=Field(default='Tweaking colors, changing images, or adjusting text sizes. New pages or new buttons are extra.',min_length=5,max_length=2000)
    out_of_scope:list[str]=Field(min_length=1,max_length=30)

class SmartProposalSection4(BaseModel):
    price_cents:int=Field(gt=0,le=100000000)
    duration_days:int=Field(default=16,ge=1,le=21)
    delivery_date:str=Field(min_length=4,max_length=100)

class SmartProposalData(BaseModel):
    section1:SmartProposalSection1
    section2:SmartProposalSection2
    section3:SmartProposalSection3
    section4:SmartProposalSection4
    client_vision:ClientVisionInput|None=None
    status:Literal['draft','sent_to_client','hold_authorized','completed']='draft'

    @model_validator(mode='after')
    def validate_rules(self):
        deliv={d.strip().casefold() for d in self.section2.deliverables if d.strip()}
        for item in self.section3.out_of_scope:
            if item.strip().casefold() in deliv:
                raise ValueError(f'Contradiction detected: "{item}" cannot be both a deliverable and out of scope.')
        if self.section4.duration_days > 21:
            raise ValueError('Platform strictly prevents milestone delivery past 21 days.')
        return self

class SmartDraftInput(BaseModel):
    chat_history:str=Field(default='',max_length=50000)
    client_goal:str=Field(default='',max_length=5000)
    assets_inspiration:str=Field(default='',max_length=5000)
    currency:str="USD"


class TwoSidedQuickstartInput(BaseModel):
    title:str=Field(min_length=2,max_length=200)
    counterparty_email:str=Field(min_length=3,max_length=200)
    client_goal:str=Field(min_length=3,max_length=5000)
    assets_inspiration:str=Field(default='',max_length=5000)
    currency:str='USD'
