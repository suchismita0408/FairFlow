"""Gross Sandbox settlement postings, using the legacy ledger's hundredths.

Call inside the same transaction as the verified payment transition. These
postings are not a provider balance statement: fees, refunds and taxes require
separate evidence and are not inferred from the signed milestone amount.
"""
from babel.numbers import get_currency_precision
from fastapi import HTTPException
from sqlalchemy import insert
from . import db


def post(conn, payment, kind, reference, accounts):
    amount, remainder = divmod(payment['amount_minor'] * 100,
                               10 ** get_currency_precision(payment['currency']))
    if remainder or amount <= 0 or not reference:
        raise HTTPException(409, 'Settlement evidence cannot be represented in the ledger.')
    for account, sign in accounts:
        external = f'step1:{kind}:{reference}:{account}'
        values = dict(project_id=payment['project_id'], external_id=external,
                      account=account, amount_cents=sign * amount, currency=payment['currency'])
        existing = db.one(conn, db.ledger, db.ledger.c.external_id == external)
        if existing:
            if any(existing[key] != value for key, value in values.items()):
                raise HTTPException(409, 'Settlement reference conflicts with existing ledger evidence.')
        else:
            conn.execute(insert(db.ledger).values(id=db.uid(), created_at=db.now(), **values))


def capture(conn, payment):
    payment={**payment,'amount_minor':payment['amount_minor'] if payment['payload'].get('funding_model')=='paypal_funded_v1' else payment['payload'].get('settlement_amount_minor',payment['amount_minor'])}
    post(conn, payment, 'capture', payment['payload']['capture_id'],
         [('provider_receivable', 1), ('project_liability', -1)])


def payout(conn, payment, row, status):
    payment={**payment,'amount_minor':row.get('amount_minor',payment['payload'].get('settlement_amount_minor',payment['amount_minor']))}
    if status == 'paid':
        returned = f"step1:payout_return:{row['id']}:provider_receivable"
        if db.one(conn, db.ledger, db.ledger.c.external_id == returned):
            raise HTTPException(409, 'A returned payout needs investigation before receipt can be restored.')
        post(conn, payment, 'payout', row['id'],
             [('provider_receivable', -1), ('project_liability', 1)])
    elif status in ('returned', 'refunded', 'reversed'):
        # Reverse only a previously verified receipt; pending/unclaimed amounts
        # have never discharged the project liability in this gross ledger.
        key = f"step1:payout:{row['id']}:provider_receivable"
        if db.one(conn, db.ledger, db.ledger.c.external_id == key):
            post(conn, payment, 'payout_return', row['id'],
                 [('provider_receivable', 1), ('project_liability', -1)])
