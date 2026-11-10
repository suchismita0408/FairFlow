"""Multi-version negotiation probe using the same API as the portals."""
import copy
import json
import os
import pytest

from test_step1_negotiation import start, call, endpoint, sign_both
from test_step1_auth_validation import portals

pytestmark = pytest.mark.skipif(os.getenv('FAIRFLOW_RUN_HACKATHON_PROBE') != '1', reason='Opt-in live Gemini evaluation')


def test_three_versions_with_vague_terms_and_conflicts(portals):
    client, freelancer, room = start(portals)
    pid = room['project']['id']
    metrics = {}

    def capture(label, value):
        metrics[label] = {
            'blockers': [f['field_path'] + ': ' + f['message'] for f in value['flags'] if f['severity'] == 'BLOCKER' and f['status'] == 'open'],
            'warnings': [f['field_path'] + ': ' + f['message'] for f in value['flags'] if f['severity'] == 'WARNING' and f['status'] == 'open'],
            'versions': len(value['versions']),
        }

    capture('v1', room)
    base = copy.deepcopy(room['agreement'])
    response = freelancer.post(endpoint(freelancer, 'freelancer', pid) + '/chat', json={'body': 'Could milestone 1 cost INR 1500 instead? I have not accepted INR 1000.', 'field_path': 'timeline.milestones.0.amount_minor'})
    assert response.status_code == 200
    proposal = copy.deepcopy(base)
    proposal['scope']['deliverables'][0]['specification'] = 'Build a standard page, etc.'
    proposal['payment']['total_minor'] = 120000
    saved = freelancer.put(endpoint(freelancer, 'freelancer', pid) + '/draft', json={'revision': room['revision'], 'agreement': proposal})
    assert saved.status_code == 200, saved.text
    room = saved.json()
    capture('v2_private_bad', room)
    assert freelancer.post(endpoint(freelancer, 'freelancer', pid) + '/actions', json={'revision': room['revision'], 'action': 'send'}).status_code == 422
    assert len(client.get(endpoint(client, 'client', pid)).json()['versions']) == 1

    proposal['scope']['deliverables'][0]['specification'] = base['scope']['deliverables'][0]['specification']
    proposal['timeline']['milestones'][0]['amount_minor'] = 120000
    saved = freelancer.put(endpoint(freelancer, 'freelancer', pid) + '/draft', json={'revision': room['revision'], 'agreement': proposal})
    assert saved.status_code == 200, saved.text
    room = call(freelancer, 'freelancer', saved.json(), 'send')
    capture('v2_sent', room)

    room = call(client, 'client', room, 'counter')
    proposal = copy.deepcopy(room['agreement'])
    proposal['timeline']['deadline'] = base['timeline']['start_date']
    proposal['basics']['background'] = 'Please finish ASAP with reasonable quality.'
    saved = client.put(endpoint(client, 'client', pid) + '/draft', json={'revision': room['revision'], 'agreement': proposal})
    assert saved.status_code == 200, saved.text
    room = saved.json()
    capture('v3_private_bad', room)
    assert client.post(endpoint(client, 'client', pid) + '/actions', json={'revision': room['revision'], 'action': 'send'}).status_code == 422
    proposal['timeline']['deadline'] = base['timeline']['deadline']
    saved = client.put(endpoint(client, 'client', pid) + '/draft', json={'revision': room['revision'], 'agreement': proposal})
    assert saved.status_code == 200, saved.text
    room = call(client, 'client', saved.json(), 'send')
    capture('v3_sent', room)

    response = client.post(endpoint(client, 'client', pid) + '/chat', json={'body': 'Can we make the final deadline next Friday? The current date may be too early.', 'field_path': 'timeline.deadline'})
    assert response.status_code == 200
    review = client.post(endpoint(client, 'client', pid) + '/ai-review', json={'revision': room['revision']})
    assert review.status_code == 200, review.text
    result = review.json()
    metrics['review'] = {'available': result['available'], 'saved_versions': len(result['reviewed_versions']), 'messages': result['reviewed_message_count'], 'issues': [(i['field_path'], i['question']) for i in result['issues']], 'summary': result['summary']}
    assert result['reviewed_message_count'] >= 2
    assert len(result['reviewed_versions']) == 3
    print('HACKATHON_PROBE=' + json.dumps(metrics, ensure_ascii=True))


def test_unresolved_chat_price_requires_both_confirmations(portals):
    client, freelancer, room = start(portals)
    pid = room['project']['id']
    response = freelancer.post(endpoint(freelancer, 'freelancer', pid) + '/chat', json={'body': 'I now require INR 1500 for milestone 1; the agreement still says INR 1000.', 'field_path': 'timeline.milestones.0.amount_minor'})
    assert response.status_code == 200
    reviewed = freelancer.post(endpoint(freelancer, 'freelancer', pid) + '/ai-review', json={'revision': room['revision']})
    assert reviewed.status_code == 200, reviewed.text
    assert any(i['field_path'] == 'timeline.milestones.0.amount_minor' for i in reviewed.json()['issues'])
    room, sha = __import__('test_step1_negotiation').approve_both(client, freelancer, room)
    call(client, 'client', room, 'sign', 422, version_hash=sha, typed_name='Alice Baker')
    issue = room['chat_price_conflicts'][0]
    room = call(client, 'client', room, 'confirm-chat-price', flag_id=issue['id'], version_hash=sha, reason='We agreed the signed milestone amount is final.')
    call(client, 'client', room, 'sign', 422, version_hash=sha, typed_name='Alice Baker')
    room = call(freelancer, 'freelancer', room, 'confirm-chat-price', flag_id=issue['id'], version_hash=sha, reason='The larger chat amount was an unaccepted proposal.')
    room = call(client, 'client', room, 'sign', version_hash=sha, typed_name='Alice Baker')
    signed = call(freelancer, 'freelancer', room, 'sign', version_hash=sha, typed_name='Rahul Kumar')
    assert signed['project']['status'] == 'AGREED'
    print('HACKATHON_SIGNING_GATE=' + json.dumps({'chat_price_issue': True, 'signing_blocked_before_both_confirm': True, 'status_after_confirm': signed['project']['status']}))
