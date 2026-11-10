import pytest
from fastapi import HTTPException
from app import db, assurance, step1_ledger
from test_step1_auth_validation import portals
from test_step1_payouts import setup_payment


@pytest.mark.parametrize('currency,minor,hundredths', [('USD',1234,1234),('JPY',1234,123400)])
def test_balanced_capture_receipt_return_and_replay(portals,currency,minor,hundredths):
    c,f,pid,mid,payment=setup_payment(portals)
    payment.update(currency=currency,amount_minor=minor)
    payout={'id':'PAYOUT-1'}
    with db.engine().begin() as conn:
        step1_ledger.capture(conn,payment)
        step1_ledger.capture(conn,payment)
        step1_ledger.payout(conn,payment,payout,'pending')
        entries=db.rows(conn,db.ledger)
        assert len(entries)==2 and sum(e['amount_cents'] for e in entries)==0
        assert next(e for e in entries if e['account']=='project_liability')['amount_cents']==-hundredths
        step1_ledger.payout(conn,payment,payout,'paid')
        step1_ledger.payout(conn,payment,payout,'paid')
        entries=db.rows(conn,db.ledger)
        assert len(entries)==4
        assert sum(e['amount_cents'] for e in entries if e['account']=='project_liability')==0
        step1_ledger.payout(conn,payment,payout,'returned')
        step1_ledger.payout(conn,payment,payout,'reversed')
        entries=db.rows(conn,db.ledger)
        assert len(entries)==6 and sum(e['amount_cents'] for e in entries)==0
        assert sum(e['amount_cents'] for e in entries if e['account']=='project_liability')==-hundredths
        assert {e['currency'] for e in entries}=={currency}
        with pytest.raises(HTTPException):step1_ledger.payout(conn,payment,payout,'paid')


def test_capture_transition_atomic_and_ledger_private(portals):
    c,f,pid,mid,payment=setup_payment(portals)
    with pytest.raises(RuntimeError):
        with db.engine().begin() as conn:
            assurance.save(conn,payment,'captured')
            raise RuntimeError('Simulated failed commit')
    with db.engine().connect() as conn:assert db.rows(conn,db.ledger)==[]
    with db.engine().begin() as conn:assurance.save(conn,payment,'captured')
    root=f'/api/client/projects/{pid}/assurance/ledger'
    response=c.get(root)
    assert response.status_code==200 and len(response.json()['entries'])==2
    assert response.json()['unit']=='hundredths_of_currency'
    assert f.get(root.replace('/client/','/freelancer/')).status_code==200
    outsider=portals('client','outsider@example.com')
    assert outsider.get(root).status_code==404
    with db.engine().begin() as conn:
        with pytest.raises(HTTPException):step1_ledger.capture(conn,{**payment,'amount_minor':payment['amount_minor']+1})


def test_unclaimed_return_does_not_invent_receipt(portals):
    c,f,pid,mid,payment=setup_payment(portals)
    with db.engine().begin() as conn:
        step1_ledger.capture(conn,payment)
        step1_ledger.payout(conn,payment,{'id':'UNCLAIMED'},'returned')
        assert len(db.rows(conn,db.ledger))==2
