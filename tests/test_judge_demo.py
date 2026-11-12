from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest
import main
from app import db


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'judge.db'))
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    monkeypatch.setenv('FAIRFLOW_MODE', 'demo')
    monkeypatch.setenv('EMAIL_MODE', 'local')
    monkeypatch.setenv('ENABLE_JUDGE_DEMO', 'true')
    monkeypatch.setenv('TOKEN_ENCRYPTION_KEY', Fernet.generate_key().decode())
    db.metadata.create_all(db.engine())
    with TestClient(main.app) as client:
        yield client


def test_demo_has_both_roles_unsigned_terms_and_private_project(demo):
    result = demo.post('/api/auth/judge-demo', json={})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['user']['role'] == 'client' and data['user']['verified']
    pid = data['redirect'].split('/')[-1]
    room = demo.get('/api/client/projects/' + pid)
    assert room.status_code == 200, room.text
    room = room.json()
    assert room['agreement']['basics']['title'].startswith('Judge demo:')
    assert len(room['members']) == 2
    assert all(not v['signatures'] for v in room['versions'])
    switched = demo.post('/api/auth/judge-demo', json={'action': 'switch', 'role': 'freelancer'})
    assert switched.status_code == 200, switched.text
    assert switched.json()['user']['role'] == 'freelancer'
    other = demo.get('/api/freelancer/projects/' + pid)
    assert other.status_code == 200, other.text
    assert other.json()['can_edit']
    proposal = other.json()
    demo.headers['x-csrf-token'] = switched.json()['csrf_token']
    sent = demo.post('/api/freelancer/projects/' + pid + '/actions', json={
        'revision': proposal['revision'], 'action': 'send'})
    assert sent.status_code == 200, sent.text
    back = demo.post('/api/auth/judge-demo', json={'action': 'switch', 'role': 'client'})
    demo.headers['x-csrf-token'] = back.json()['csrf_token']
    reviewed = demo.get('/api/client/projects/' + pid).json()
    assert reviewed['project']['turn'] == 'client'
    # Role switching never permits access to a different browser's workspace.
    with TestClient(main.app) as stranger:
        assert stranger.post('/api/auth/judge-demo', json={'action': 'switch'}).status_code == 401
        fresh = stranger.post('/api/auth/judge-demo', json={})
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()['redirect'] != data['redirect']
        assert stranger.get('/api/client/projects/' + pid).status_code == 404


def test_demo_accounts_editable_and_cross_origin_rejected(demo):
    started = demo.post('/api/auth/judge-demo', json={'client_name': 'Judge Buyer', 'freelancer_name': 'Judge Seller',
        'buyer_email': 'buyer@example.com', 'receiver_email': 'seller@example.com'})
    assert started.status_code == 200, started.text
    changed = demo.post('/api/auth/judge-demo', json={'action': 'update', 'role': 'freelancer',
        'client_name': 'New Buyer', 'freelancer_name': 'New Seller', 'receiver_email': 'new@example.com'})
    assert changed.status_code == 200, changed.text
    assert changed.json()['user']['name'] == 'New Seller'
    settings = demo.get('/api/auth/judge-demo').json()['settings']
    assert settings['receiver_email'] == 'new@example.com'
    assert demo.post('/api/auth/judge-demo', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403


def test_demo_can_be_disabled(demo, monkeypatch):
    monkeypatch.setenv('ENABLE_JUDGE_DEMO', 'false')
    assert not demo.get('/api/auth/judge-demo').json()['enabled']
    assert demo.post('/api/auth/judge-demo', json={}).status_code == 404


def test_sandbox_receiver_is_used_and_modes_are_separate(demo):
    from app.judge_demo import require_sandbox
    from app import auth
    walk = demo.post('/api/auth/judge-demo', json={}).json()
    marker = auth.actor.set(walk['user'])
    try:
        with pytest.raises(Exception, match='walkthrough'):
            require_sandbox()
    finally:
        auth.actor.reset(marker)
    result = demo.post('/api/auth/judge-demo', json={'payment_mode': 'sandbox', 'receiver_email': 'judge-seller@example.com'})
    assert result.status_code == 200, result.text
    assert result.json()['redirect'] != walk['redirect']
    pid = result.json()['redirect'].split('/')[-1]
    with db.engine().connect() as conn:
        member = db.one(conn, db.members, (db.members.c.project_id == pid) & (db.members.c.role == 'freelancer'))
        recipient = db.one(conn, db.sandbox_recipients, db.sandbox_recipients.c.user_id == member['user_id'])
        assert recipient['email'] == 'judge-seller@example.com'
    marker = auth.actor.set(result.json()['user'])
    try:
        require_sandbox()
    finally:
        auth.actor.reset(marker)
