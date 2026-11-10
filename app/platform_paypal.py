"""Sandbox-only Multiparty adapter, isolated from legacy merchant credentials."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import httpx
from dotenv import dotenv_values
from fastapi import HTTPException

BASE = 'https://api-m.sandbox.paypal.com'
DELAY_SCOPE = 'https://uri.paypal.com/services/payments/delay-funds-disbursement'


def config():
    path = Path(os.getenv('PAYPAL_PLATFORM_CONFIG', str(Path(__file__).resolve().parents[1] / '.env.paypal-platform')))
    return dotenv_values(path) if path.is_file() else {}


def configured():
    c = config()
    return bool(c.get('PAYPAL_PLATFORM_CLIENT_ID') and c.get('PAYPAL_PLATFORM_CLIENT_SECRET'))


def fingerprint():
    return hashlib.sha256(config().get('PAYPAL_PLATFORM_CLIENT_ID', '').encode()).hexdigest()


async def call(method, path, body=None, key=None, seller=None):
    if not path.startswith(('/v1/customer/partners/', '/v2/customer/partner-referrals', '/v2/checkout/orders', '/v2/payments/captures/', '/v2/payments/refunds/', '/v1/payments/referenced-payouts-items')):
        raise HTTPException(409, 'Unsupported platform operation.')
    c = config()
    if not configured():
        raise HTTPException(503, 'Configure the separate Sandbox platform app first.')
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            auth = await client.post(BASE + '/v1/oauth2/token', auth=(c['PAYPAL_PLATFORM_CLIENT_ID'], c['PAYPAL_PLATFORM_CLIENT_SECRET']), data={'grant_type': 'client_credentials'})
            if auth.status_code != 200:
                raise HTTPException(502, 'Platform Sandbox authentication failed.')
            headers = {'Authorization': 'Bearer ' + auth.json()['access_token'], 'Content-Type': 'application/json'}
            if c.get('PAYPAL_PLATFORM_BN_CODE'):
                headers['PayPal-Partner-Attribution-Id'] = c['PAYPAL_PLATFORM_BN_CODE']
            if key:
                headers['PayPal-Request-Id'] = key
            if seller:
                encode = lambda value: base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode().rstrip('=')
                headers['PayPal-Auth-Assertion'] = encode({'alg': 'none'}) + '.' + encode({'iss': c['PAYPAL_PLATFORM_CLIENT_ID'], 'payer_id': seller}) + '.'
            result = await client.request(method, BASE + path, json=body, headers=headers)
        if result.status_code >= 400:
            problem = result.json()
            codes = [d.get('issue', '') for d in problem.get('details', [])]
            codes += [problem.get('name', '')]
            codes = [v for v in codes if isinstance(v, str) and re.fullmatch(r'[A-Z0-9_]{1,80}', v)]
            raise HTTPException(502, 'PayPal platform request failed; reconcile before retrying. ' + ', '.join(codes[:3]))
        return result.json() if result.content else {}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise HTTPException(502, 'Platform response is uncertain. Check provider status; do not create another charge.')
