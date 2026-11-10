"""Create a separate Sandbox seller referral; never initiates a payment."""
import base64
import json
from pathlib import Path
import urllib.error
import urllib.request
import uuid

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://api-m.sandbox.paypal.com'


def main():
    config = dotenv_values(ROOT / '.env.paypal-platform')
    keys = ['PAYPAL_PLATFORM_CLIENT_ID', 'PAYPAL_PLATFORM_CLIENT_SECRET', 'PAYPAL_PLATFORM_SELLER_EMAIL']
    if any(not config.get(k, '').strip() for k in keys):
        raise SystemExit('Fill in the platform credentials and seller email first.')
    folder = ROOT / '.paypal-platform-test'
    folder.mkdir(exist_ok=True)
    state_file = folder / 'onboarding.json'
    state = json.loads(state_file.read_text()) if state_file.exists() else {'tracking_id': 'fairflow-' + uuid.uuid4().hex}
    if state.get('action_url'):
        print('Existing seller onboarding link: ' + state['action_url'])
        return
    # Persist the correlation key before the external request.
    state_file.write_text(json.dumps(state, indent=2))

    def post(path, body, headers):
        request = urllib.request.Request(BASE + path, data=body, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            result = json.load(error)
            print(json.dumps({'status': error.code, 'error': result.get('name', result.get('error')), 'details': result.get('details'), 'debug_id': result.get('debug_id')}))
            raise SystemExit('PayPal rejected the Sandbox request; credentials were not printed.')

    credentials = (config[keys[0]].strip() + ':' + config[keys[1]].strip()).encode()
    token = post('/v1/oauth2/token', b'grant_type=client_credentials', {
        'Authorization': 'Basic ' + base64.b64encode(credentials).decode(),
        'Content-Type': 'application/x-www-form-urlencoded',
    })
    headers = {'Authorization': 'Bearer ' + token['access_token'], 'Content-Type': 'application/json', 'PayPal-Request-Id': state['tracking_id']}
    if config.get('PAYPAL_PLATFORM_BN_CODE'):
        headers['PayPal-Partner-Attribution-Id'] = config['PAYPAL_PLATFORM_BN_CODE'].strip()
    # Seller permissions are granted by the seller through PayPal's action URL.
    body = {
        'tracking_id': state['tracking_id'],
        'email': config[keys[2]].strip(),
        'operations': [{'operation': 'API_INTEGRATION', 'api_integration_preference': {
            'rest_api_integration': {'integration_method': 'PAYPAL', 'integration_type': 'THIRD_PARTY',
                                     'third_party_details': {'features': ['PAYMENT', 'REFUND', 'DELAY_FUNDS_DISBURSEMENT']}}
        }}],
        'products': ['EXPRESS_CHECKOUT'],
    }
    result = post('/v2/customer/partner-referrals', json.dumps(body).encode(), headers)
    state['links'] = result.get('links', [])
    state['action_url'] = next((link['href'] for link in state['links'] if link['rel'] == 'action_url'), None)
    state_file.write_text(json.dumps(state, indent=2))
    print('Sandbox seller referral created. No payment initiated.')
    print('ONBOARDING_URL: ' + str(state['action_url']))


if __name__ == '__main__':
    main()
