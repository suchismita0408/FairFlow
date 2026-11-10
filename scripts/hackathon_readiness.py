"""Read-only local readiness report. Never prints credential values or user data."""
import json,os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import text
from app import db


def report():
    load_dotenv()
    result={'sandbox_only':True,'email_mode':os.getenv('EMAIL_MODE','local'),
            'paypal_credentials_present':all(os.getenv(k) for k in ('PAYPAL_CLIENT_ID','PAYPAL_CLIENT_SECRET')),
            'webhook_id_present':bool(os.getenv('PAYPAL_WEBHOOK_ID')),
            'gemini_configured':os.getenv('AI_MODE')=='gemini' and bool(os.getenv('GEMINI_API_KEY')),
            'smtp_configured':os.getenv('EMAIL_MODE')=='smtp' and all(os.getenv(k) for k in ('SMTP_HOST','SMTP_FROM')),
            'project_has_own_git_directory':Path('.git').exists(),
            'open_source_license_present':any(Path(name).exists() for name in ('LICENSE','LICENSE.md','LICENSE.txt'))}
    with db.engine().connect() as conn:
        result['database']=conn.dialect.name
        result['migration']=conn.execute(text('SELECT version_num FROM alembic_version')).scalar()
        result['step1_scan_jobs']=len(db.rows(conn,db.jobs,db.jobs.c.kind=='step1_scan'))
        result['automatic_release_enrolled_in_review']=sum(row['status']=='in_review' and bool(row['payload'].get('auto_release')) for row in db.rows(conn,db.milestone_payments))
    return result


if __name__=='__main__':print(json.dumps(report(),indent=2))
