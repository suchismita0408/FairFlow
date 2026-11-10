"""PayPal currency precision and explicit Sandbox capability gating.

Legacy *_cents fields remain hundredths of the agreement currency, including
zero-decimal currencies, whose values must therefore be multiples of 100.
They are not ISO minor-unit fields. No exchange rate is invented here.
"""
import os
from fastapi import HTTPException

SUPPORTED=tuple('AUD BRL CAD CNY CZK DKK EUR HKD HUF ILS JPY MYR MXN TWD NZD NOK PHP PLN GBP RUB SGD SEK CHF THB USD'.split())
ZERO_DECIMAL={'HUF','JPY','TWD'}

def validate(code,amount):
    if code not in SUPPORTED:raise ValueError('Currency is not supported by this PayPal adapter.')
    if code in ZERO_DECIMAL and amount%100:raise ValueError(code+' requires whole-unit amounts; fractional amounts are not supported by PayPal.')
    return amount

def value(amount,code='USD'):
    validate(code,amount)
    return str(amount//100) if code in ZERO_DECIMAL else f'{amount//100}.{amount%100:02d}'

def amount(payment,total=None):
    code=payment.get('currency','USD')
    return {'currency_code':code,'value':value(payment['amount_cents'] if total is None else total,code)}

def funding_available(project):
    code=project.get('currency','USD')
    if project['mode']=='demo':return
    enabled=os.getenv('PAYPAL_ENABLED_CURRENCIES','USD').split(',')
    if code not in {x.strip().upper() for x in enabled}:
        raise HTTPException(409,'This currency has not been enabled for the Sandbox merchant. Confirm PayPal receiving preferences and country eligibility first.')

def catalog():
    return {'default':'USD','currencies':[{'code':code,'decimals':0 if code in ZERO_DECIMAL else 2} for code in SUPPORTED],
            'fx_markup_bps':0,'conversion_source':'PayPal checkout quote',
            'conversion_notice':'PayPal supplies applicable conversion rates and fees during eligible checkout. FairFlow adds no FX markup. No guaranteed rate is available before a provider quote.',
            'country_policy':'Worldwide audience; payment availability remains subject to PayPal country, account and currency eligibility.'}
