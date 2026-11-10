"""Open an interactive local demo in a separate Chrome/Edge browser profile."""
import json
import secrets
import socket
import subprocess
import time
import uuid
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright, expect

BASE = 'http://127.0.0.1:8000'
ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.showcase'


def post(client, path, body=None):
    response = client.post('/api/' + path, json=body or {})
    response.raise_for_status()
    return response.json()


def seed():
    health = httpx.get(BASE + '/api/health').json()
    if health['payment_mode'] != 'demo' or health['ai_mode'] != 'demo' or not health['auth_enabled']:
        raise RuntimeError('Showcase requires authenticated local demo payment and AI modes.')
    tag = uuid.uuid4().hex[:8]
    password = secrets.token_urlsafe(24)
    clients = {}
    emails = {}
    for role, name in [('client', 'Showcase Client'), ('freelancer', 'Showcase Freelancer')]:
        client = httpx.Client(base_url=BASE, timeout=30)
        email = f'showcase-{role}-{tag}@example.com'
        result = post(client, 'auth/register', {'email': email, 'password': password, 'name': name})
        client.headers['x-csrf-token'] = result['csrf_token']
        message = client.get('/api/auth/mail').json()[0]
        post(client, 'auth/verify', {'token': message['body'].split('#verify=')[1]})
        clients[role] = client
        emails[role] = email
    client, freelancer = clients['client'], clients['freelancer']
    draft = post(client, 'agile/draft', {
        'chat_history': 'Build a responsive coffee shop landing page with menu, about and contact information. Project budget is $900 with 2 revisions per milestone. Additional work is $50 per hour. No database, customer accounts or payment processing.',
        'milestone_count': 3,
    })
    draft['contract']['title'] = 'SHOWCASE - Coffee Studio Website'
    titles = ['Design and content', 'Responsive website', 'Testing and handoff']
    for milestone, title in zip(draft['milestones'], titles):
        milestone['title'] = title
    from app.agreement_checks import example_details
    body = {
        'details': example_details(),
        **draft, 'currency': 'USD', 'creator_role': 'client',
        'counterparty_email': emails['freelancer'],
        'client_name': 'Showcase Coffee Studio', 'freelancer_name': 'Showcase Web Designer',
        'clauses': [
            {'reference': 'DELIVERY', 'kind': 'deliverable', 'text': 'Deliver a responsive coffee shop website with menu, about and contact information.'},
            {'reference': 'ACCEPTANCE', 'kind': 'acceptance', 'text': 'Navigation and content work on mobile and desktop; final files include handoff documentation.'},
            {'reference': 'DEFECTS', 'kind': 'defect_policy', 'text': 'Correct broken agreed functionality without consuming presentation revisions.'},
            {'reference': 'REVISIONS', 'kind': 'revision_policy', 'text': 'Two consolidated rounds of font, color and spacing adjustments per milestone.'},
            {'reference': 'EXCLUSIONS', 'kind': 'exclusion', 'text': 'Database, customer login and payment processing require separately agreed scope and price.'},
        ],
        'work_items': [[
            {'title': title + ' implementation', 'acceptance': 'Matches the accepted scope and approved design.', 'amount_cents': 20000},
            {'title': title + ' review', 'acceptance': 'Agreed acceptance checks and review notes are supplied.', 'amount_cents': 10000},
        ] for title in titles],
    }
    room = post(client, 'platform/projects', body)
    pid = room['project']['id']
    invitation = next(m for m in freelancer.get('/api/auth/mail').json() if '#invite=' in m['body'])
    post(freelancer, 'platform/invitations/accept', {'token': invitation['body'].split('#invite=')[1]})
    agreement = room['agreements'][0]
    for participant in (client, freelancer):
        room = post(participant, f"platform/projects/{pid}/agreements/{agreement['id']}/accept", {'digest': agreement['digest']})
    root = f'agile/projects/{pid}'
    post(client, root + '/vault/setup', {'agreed': True, 'plan_hash': room['project']['plan_hash']})
    post(client, root + '/vault/confirm')
    first, second = room['project']['milestones'][:2]
    post(client, root + f"/milestones/{first['id']}/fund")
    post(freelancer, root + f"/milestones/{first['id']}/deliver", {'url': 'https://example.com/showcase-design', 'notes': 'SAMPLE ONLY: design and content delivery for the interactive demonstration.'})
    post(client, root + f"/milestones/{first['id']}/approve")
    post(freelancer, root + f"/milestones/{second['id']}/deliver", {'url': 'https://example.com/showcase-website', 'notes': 'SAMPLE ONLY: responsive build submitted. This demonstrates the five-day review window.'})
    platform = f'platform/projects/{pid}'
    post(client, platform + '/messages', {'body': 'Welcome to the FairFlow showcase. These accounts, evidence links and payments are sample data. Explore the six project tabs.'})
    post(freelancer, platform + '/messages', {'body': 'Design has been approved and its simulated payment captured. The responsive website is now in its five-day review window.'})
    room = post(client, platform + '/evidence', {'milestone_id': second['id'], 'title': 'Sample mobile navigation report', 'url': 'https://example.com/showcase-evidence', 'description': 'SAMPLE ONLY: mobile navigation does not open in this demonstration scenario; this is not an actual uploaded test report.'})
    room = post(client, platform + '/decisions', {'milestone_id': second['id'], 'request': 'The agreed mobile navigation is broken. Please correct this defect.', 'evidence_ids': [room['evidence'][0]['id']]})
    decision = room['decisions'][0]
    post(client, platform + '/actions', {'kind': 'decision', 'milestone_id': second['id'], 'decision_id': decision['id'], 'category': 'defect', 'reason': 'Sample proposal: correct agreed mobile navigation without charging extra or consuming a revision.'})
    for participant in clients.values():
        participant.close()
    return {'project_id': pid, 'emails': emails, 'password': password}


def open_browser(data):
    candidates = [Path('C:/Program Files/Google/Chrome/Application/chrome.exe'), Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')]
    executable = next((p for p in candidates if p.is_file()), None)
    if executable is None:
        raise RuntimeError('Chrome or Edge is required for the visible showcase.')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    profile = LOCAL / ('browser-' + uuid.uuid4().hex[:8])
    subprocess.Popen([str(executable), f'--user-data-dir={profile}', f'--remote-debugging-port={port}', '--remote-debugging-address=127.0.0.1', '--no-first-run', '--no-default-browser-check', '--new-window', BASE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for attempt in range(40):
        try:
            httpx.get(f'http://127.0.0.1:{port}/json/version', timeout=1).raise_for_status()
            break
        except httpx.HTTPError:
            time.sleep(0.25)
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
        context = browser.contexts[0]
        page = next((p for p in context.pages if p.url.startswith(BASE)), None) or context.new_page()
        page.goto(BASE)
        page.locator('#email').fill(data['emails']['client'])
        page.locator('#password').fill(data['password'])
        page.locator('#auth-form button[type=submit]').click()
        expect(page.locator('[data-project]')).to_be_visible()
        page.locator(f"[data-project='{data['project_id']}']").click()
        page.locator('[data-tab=Milestones]').click()
        expect(page.locator('#room-body')).to_contain_text('Design and content')
        for tab in ('AI review', 'Payments'):
            extra = context.new_page()
            extra.goto(BASE)
            extra.locator(f"[data-project='{data['project_id']}']").click()
            extra.locator(f'[data-tab="{tab}"]').click()
        page.bring_to_front()
        page.screenshot(path=str(LOCAL / 'showcase.png'), full_page=True)
        print('Opened visible showcase with Milestones, AI review and Payments tabs. Browser remains open.')


if __name__ == '__main__':
    LOCAL.mkdir(exist_ok=True)
    data = seed()
    (LOCAL / 'accounts.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    open_browser(data)
