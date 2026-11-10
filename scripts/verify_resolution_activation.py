"""Read-only financial/signature snapshots around additive migration/activation."""
import hashlib
import json
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
load_dotenv(ROOT/'.env')
from app import db

REPORT=ROOT/'reports'/'resolution-activation-check.json'

def snapshot():
    result={}
    with db.engine().connect() as conn:
        for table in (db.agreements,db.signatures,db.milestone_payments,db.milestone_payouts,db.ledger):
            rows=sorted(db.rows(conn,table),key=lambda row:row['id'])
            encoded=json.dumps(rows,sort_keys=True,separators=(',',':'),default=str).encode()
            result[table.name]={'count':len(rows),'sha256':hashlib.sha256(encoded).hexdigest()}
    return result

if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='before':
        report={'before':snapshot(),'started_at':db.now()}
        REPORT.write_text(json.dumps(report,indent=2))
        print('Pre-activation agreement/signature/payment/payout/ledger snapshot saved.')
    elif mode=='after':
        report=json.loads(REPORT.read_text());report['after']=snapshot()
        report['original_records_unchanged']=report['before']==report['after'];report['completed_at']=db.now()
        REPORT.write_text(json.dumps(report,indent=2))
        print('Original financial/signature records unchanged:',report['original_records_unchanged'])
        if not report['original_records_unchanged']:raise SystemExit(1)
    else:raise SystemExit('Use before or after.')
