"""Explicit demo terms, never automatically accepted or signed."""
from datetime import date, timedelta
from .agreement_schema import CATALOG

def example_agreement(today=None):
    today=today or date.today()
    def fields(specs):
        result={}
        for key,spec in specs.items():
            if not spec['required']:continue
            if spec['type']=='repeat':result[key]=[fields(spec['children'])]
            elif spec['example']!='':result[key]=spec['example']
            elif spec['options']:result[key]=spec['options'][0]
            elif spec['type']=='boolean':result[key]=False
            elif spec['type'] in ('number','money'):result[key]=1
            elif spec['type']=='date':result[key]=str(today+timedelta(days=20))
            else:result[key]='Explicit agreed terms'
        return result
    a={section:fields(specs) for section,specs in CATALOG.items()}
    a['scope']['exclusions']=['Backend database and payment integration']
    a['scope']['deliverables'][0].update(id='D1',milestone_id='M1',type='deployed_site',quantity=1,revision_rounds=2)
    a['timeline'].update(start_date=str(today+timedelta(days=7)),deadline=str(today+timedelta(days=34)),working_days=list(range(7)))
    a['timeline']['milestones'][0].update(id='M1',deliverable_ids=['D1'],due_date=str(today+timedelta(days=20)),amount_minor=100000,currency='INR',review_days=3,auto_accept=True,auto_accept_days=3)
    a['scope']['dependencies'][0]['due_date']=str(today+timedelta(days=7))
    a['payment'].update(total_minor=100000,currency='INR',deposit_percent=0,kill_fee_percent=0)
    a['revisions']['written_approval']=True
    a['ip'].update(source_files=True,source_format='ZIP archive containing HTML and CSS')
    a['acknowledgments']={}
    return a
