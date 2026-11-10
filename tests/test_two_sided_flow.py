import asyncio
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import main
from app import db, store

@pytest.fixture
def platform_env(tmp_path, monkeypatch, isolate_legacy_configuration):
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'two_sided.db'))
    monkeypatch.setenv('AUTH_ENABLED','true')
    monkeypatch.setenv('EMAIL_MODE','local')
    monkeypatch.setenv('TOKEN_ENCRYPTION_KEY',Fernet.generate_key().decode())
    monkeypatch.setenv('AI_MODE','demo')
    monkeypatch.setattr(main,'MODE','demo')
    db.metadata.create_all(db.engine())
    clients=[]
    def account(email, name=None):
        c=TestClient(main.app);clients.append(c)
        r=c.post('/api/auth/register',json={'email':email,'name':name or email.split('@')[0],'password':'Password-123456!'})
        assert r.status_code==201,r.text
        c.headers['x-csrf-token']=r.json()['csrf_token']
        message=c.get('/api/auth/mail').json()[0]
        assert c.post('/api/auth/verify',json={'token':message['body'].split('#verify=')[1]}).status_code==200
        return c
    yield account
    for c in clients:c.close()

def test_two_sided_collaborative_flow(platform_env):
    client = platform_env('bakery_owner@example.com', 'Bakery Owner')
    freelancer = platform_env('web_developer@example.com', 'Web Developer')

    # 1. Client creates the initial shared project room
    from app.agreement_checks import example_details
    data = {
        'creator_role': 'client',
        'counterparty_email': 'web_developer@example.com',
        'client_name': 'Bakery Owner',
        'freelancer_name': 'Web Developer',
        'contract': {
            'title': 'Bakery Landing Page',
            'task_description': 'I need a landing page that captures emails for my new bakery.',
            'price_cents': 45000,
            'allowed_revisions': 2,
            'hourly_rate_cents': 5000,
            'deliverables': ['Bakery landing page with email capture'],
            'exclusions': ['No backend database setup.', 'No logo design.']
        },
        'milestones': [{
            'title': 'Frontend Design & Build',
            'scope': 'I need a landing page that captures emails for my new bakery.',
            'duration_days': 16,
            'amount_cents': 45000
        }],
        'clauses': [
            {'reference': 'D1', 'kind': 'deliverable', 'text': 'Deliver the 3-section bakery landing page.'},
            {'reference': 'A1', 'kind': 'acceptance', 'text': 'Mobile and tablet responsive email capture form works.'},
            {'reference': 'B1', 'kind': 'defect_policy', 'text': 'Defects fixed without consuming revisions.'},
            {'reference': 'R1', 'kind': 'revision_policy', 'text': '2 revision rounds included.'},
            {'reference': 'E1', 'kind': 'exclusion', 'text': 'No logo design. No backend database setup.'}
        ],
        'work_items': [[
            {'title': '3-Section Landing Page', 'acceptance': 'Home, About, Contact sections', 'amount_cents': 25000},
            {'title': 'Mailchimp Integration', 'acceptance': 'Connect email form', 'amount_cents': 20000}
        ]],
        'details': example_details(),
        'currency': 'USD'
    }
    r = client.post('/api/platform/projects', json=data)
    assert r.status_code == 201, r.text
    pid = r.json()['project']['id']

    # 2. Freelancer accepts invitation
    invite = next(m for m in freelancer.get('/api/auth/mail').json() if '#invite=' in m['body'])
    freelancer.post('/api/platform/invitations/accept', json={'token': invite['body'].split('#invite=')[1]})

    # 3. Client's Contribution: The Big Picture & Assets/Inspiration
    vision_payload = {
        'goal': 'I need a landing page that captures emails for my new bakery.',
        'assets_inspiration': 'Figma: https://figma.com/file/bakery, Colors: warm gold and cream, Ref: tartinebakery.com'
    }
    # Freelancer cannot edit client vision!
    res_f_vision = freelancer.post(f'/api/platform/projects/{pid}/client-vision', json=vision_payload)
    assert res_f_vision.status_code == 403

    res_c_vision = client.post(f'/api/platform/projects/{pid}/client-vision', json=vision_payload)
    assert res_c_vision.status_code == 200, res_c_vision.text
    cv = res_c_vision.json()['project']['smart_proposal']['client_vision']
    assert cv['goal'] == vision_payload['goal']
    assert cv['assets_inspiration'] == vision_payload['assets_inspiration']

    # 4. Two-Sided Communication (Messaging)
    client.post(f'/api/platform/projects/{pid}/messages', json={'body': 'Hi! Excited to work with you on the bakery site.'})
    freelancer.post(f'/api/platform/projects/{pid}/messages', json={'body': 'Great! I will build 3 sections and connect Mailchimp for $450 in 16 days.'})

    room_data = client.get(f'/api/platform/projects/{pid}').json()
    assert len(room_data['messages']) >= 3  # Vision update note + 2 chat messages
    # Check role tagging on messages
    roles = {m['body']: m.get('role') for m in room_data['messages']}
    assert any('Excited to work' in body and role == 'client' for body, role in roles.items())
    assert any('connect Mailchimp' in body and role == 'freelancer' for body, role in roles.items())

    # 5. AI Friction-Killer: "Draft Agreement with AI"
    # Client cannot call smart-draft!
    assert client.post(f'/api/platform/projects/{pid}/smart-draft', json={}).status_code == 403

    draft_res = freelancer.post(f'/api/platform/projects/{pid}/smart-draft', json={'client_goal': vision_payload['goal'], 'assets_inspiration': vision_payload['assets_inspiration']})
    assert draft_res.status_code == 200, draft_res.text
    proposal = draft_res.json()['project']['smart_proposal']

    assert 'Bakery' in proposal['section1']['project_title']
    assert len(proposal['section2']['deliverables']) >= 1
    assert proposal['section3']['revisions_included'] == 2
    assert len(proposal['section3']['out_of_scope']) >= 1
    assert proposal['section4']['price_cents'] == 45000
    assert proposal['section4']['duration_days'] <= 21
    assert 'safely within the 21-day platform limit' in proposal['section4']['delivery_date']

    # 6. Contradiction Protection & Validation
    contradiction_proposal = dict(proposal)
    contradiction_proposal['section3'] = dict(proposal['section3'])
    contradiction_proposal['section3']['out_of_scope'] = [proposal['section2']['deliverables'][0]]
    bad_res = freelancer.post(f'/api/platform/projects/{pid}/proposal/submit', json=contradiction_proposal)
    assert bad_res.status_code == 422
    assert 'Contradiction detected' in bad_res.text

    # Strict > 21 days limit rejection
    long_proposal = dict(proposal)
    long_proposal['section4'] = dict(proposal['section4'])
    long_proposal['section4']['duration_days'] = 25
    bad_res2 = freelancer.post(f'/api/platform/projects/{pid}/proposal/submit', json=long_proposal)
    assert bad_res2.status_code == 422

    # 7. Freelancer Submits Valid Proposal: "Send to Client"
    # Client cannot submit proposal
    assert client.post(f'/api/platform/projects/{pid}/proposal/submit', json=proposal).status_code == 403

    submit_res = freelancer.post(f'/api/platform/projects/{pid}/proposal/submit', json=proposal)
    assert submit_res.status_code == 200, submit_res.text
    assert submit_res.json()['project']['smart_proposal']['status'] == 'sent_to_client'

    # 8. Client Authorizes PayPal Hold: "Authorize PayPal Hold"
    # Freelancer cannot authorize hold
    assert freelancer.post(f'/api/platform/projects/{pid}/proposal/authorize-hold').status_code == 403

    hold_res = client.post(f'/api/platform/projects/{pid}/proposal/authorize-hold')
    assert hold_res.status_code == 200, hold_res.text
    updated_project = hold_res.json()['project']
    assert updated_project['smart_proposal']['status'] == 'hold_authorized'
    assert updated_project['milestones'][0]['status'] == 'active'
    assert updated_project['milestones'][0]['work_allowed'] is True
    assert updated_project['milestones'][0]['funding_signal'] == 'green'

    # Chat received hold confirmation
    latest_messages = [m['body'] for m in hold_res.json()['messages']]
    assert any('authorized PayPal Hold for $450.00' in msg for msg in latest_messages)
