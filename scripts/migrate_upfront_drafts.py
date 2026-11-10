"""Apply the default flow to unsigned drafts; retain every signed version."""
import copy
import json
from pathlib import Path
import sys
from dotenv import load_dotenv
from sqlalchemy import select

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run():
    load_dotenv(ROOT/'.env')
    from app import db,negotiation as n,agreement_amendments as amendments
    from app.funded_payments import prepare_draft
    from app.agreement_schema import at
    changed=[]
    with n.transaction() as conn:
        for source in conn.execute(select(db.projects).with_for_update()).mappings():
            p=copy.deepcopy(source['payload'])
            if p.get('engine')!=n.ENGINE or p.get('deleted') or p.get('status') in n.TERMINAL or p.get('withdrawal'):continue
            draft=p.get('draft')
            if draft is None:
                if not p.get('active_agreement_id') or amendments.blocked_reason(conn,p):continue
                version=n.sent(conn,p)
                draft=copy.deepcopy(version['payload'])
                before=copy.deepcopy(draft)
                prepare_draft(conn,p,draft)
                if draft==before:continue
                # An unsigned replacement is staged for the existing turn holder.
                # Approval/signature rows remain immutable and are never fabricated.
                if version['id']!=p['active_agreement_id'] and version['id'] not in p.get('voided_versions',[]):
                    p.setdefault('voided_versions',[]).append(version['id'])
                p.update(status='AMENDMENT_PROPOSED',amendment=True,draft=draft,draft_owner=p.get('turn','client'),
                         amendment_financial_snapshot=amendments.financial_snapshot(conn,p))
            else:
                before=copy.deepcopy(draft)
                prepare_draft(conn,p,draft)
                if draft==before:continue
            n.system(conn,p,'default_payment_flow_updated','Upfront funding terms added to the unsigned proposal for future unfunded work. Existing payment records and signed versions retain their original terms.',None)
            n.write(conn,p,source['version'])
            changed.append({'project_id':p['id'],'title':at(draft,'basics.title','Untitled'),'status':p['status']})
    (ROOT/'reports/default-funding-migration.json').write_text(json.dumps({'updated_drafts':changed,'signed_versions_modified':False,'payments_modified':False},indent=2))
    print('Unsigned agreement drafts updated:',len(changed))
    print('No signed version, signature, capture or payout was modified.')


if __name__=='__main__':run()
