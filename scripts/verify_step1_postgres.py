"""Run Step 1 auth/state/concurrency/Playwright tests in a disposable PostgreSQL DB.

The configured application database is only used to derive local connection
settings. No test ever drops its schema. Requires local CREATE DATABASE rights.
"""
import os,subprocess,sys,uuid
from dotenv import load_dotenv
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url

def run():
    load_dotenv()
    source=make_url(os.environ['DATABASE_URL'])
    if not source.drivername.startswith('postgresql'):raise RuntimeError('This verification requires PostgreSQL.')
    name='_qa_step1_'+uuid.uuid4().hex[:12]
    admin=create_engine(source.set(database='postgres'),isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:conn.execute(text('CREATE DATABASE '+name))
    try:
        target=source.set(database=name).render_as_string(hide_password=False)
        result=subprocess.run([sys.executable,'-m','pytest','tests/test_step1_auth_validation.py','tests/test_guided_agreement.py','tests/test_chat_terms.py','tests/test_chat_monitor.py','tests/test_chat_terms_browser.py','tests/test_step1_negotiation.py','tests/test_step1_assurance.py','tests/test_step1_payouts.py','tests/test_step1_ledger.py','tests/test_payment_signing_recovery.py','tests/test_step1_automation.py','tests/test_project_lifecycle.py','tests/test_paid_amendments.py','tests/test_early_start.py','tests/test_early_start_browser.py','tests/test_workflows.py','tests/test_funded_payments.py','tests/test_mail_delivery.py','tests/test_step1_browser.py','tests/test_workflows_browser.py','tests/test_funded_browser.py','-q'],env={**os.environ,'FAIRFLOW_TEST_DATABASE_URL':target})
        return result.returncode
    finally:
        assert name.startswith('_qa_step1_') and name!=source.database
        with admin.connect() as conn:conn.execute(text('DROP DATABASE '+name+' WITH (FORCE)'))
        admin.dispose()

if __name__=='__main__':sys.exit(run())
