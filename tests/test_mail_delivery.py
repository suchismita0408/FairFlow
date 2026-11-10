import asyncio
import pytest
from app import auth,db,mail_delivery,worker
from test_step1_auth_validation import portals


def test_smtp_outbox_is_encrypted_and_sent_once_with_tls(portals,monkeypatch):
    client=portals('client');uid=client.get('/api/auth/me').json()['user']['id']
    monkeypatch.setenv('EMAIL_MODE','smtp');monkeypatch.setenv('SMTP_HOST','smtp.example.com');monkeypatch.setenv('SMTP_FROM','verify@example.com')
    with db.engine().begin() as conn:
        auth.mail(conn,uid,'client@example.com','Test verification','private-token-link')
        row=db.rows(conn,db.mail,db.mail.c.status=='queued')[0]
        assert 'private-token-link' not in row['body']
    calls=[]
    class SMTP:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def ehlo(self):pass
        def starttls(self,context):calls.append('tls')
        def login(self,*args):pass
        def send_message(self,message):calls.append(message.get_content().strip())
    monkeypatch.setattr(mail_delivery.smtplib,'SMTP',SMTP)
    mail_delivery.enqueue();mail_delivery.enqueue()
    with db.engine().connect() as conn:jobs=db.rows(conn,db.jobs,db.jobs.c.kind=='mail')
    assert len(jobs)==1
    asyncio.run(worker.process(jobs[0]));asyncio.run(worker.process(jobs[0]))
    assert calls==['tls','private-token-link']
    with db.engine().connect() as conn:assert db.one(conn,db.mail,db.mail.c.id==row['id'])['status']=='sent'
    assert client.get('/api/auth/mail').status_code==404


def test_smtp_failure_never_marks_sent_or_blindly_retries(portals,monkeypatch):
    client=portals('client');uid=client.get('/api/auth/me').json()['user']['id']
    monkeypatch.setenv('EMAIL_MODE','smtp');monkeypatch.setenv('SMTP_HOST','smtp.example.com');monkeypatch.setenv('SMTP_FROM','verify@example.com')
    with db.engine().begin() as conn:
        auth.mail(conn,uid,'client@example.com','Test','private-token')
        row=db.rows(conn,db.mail,db.mail.c.status=='queued')[0]
    def failed(*a,**kw):raise OSError('sensitive-provider-response')
    monkeypatch.setattr(mail_delivery.smtplib,'SMTP',failed)
    with pytest.raises(RuntimeError,match='uncertain') as exc:mail_delivery.deliver(row['id'])
    assert 'sensitive-provider-response' not in str(exc.value)
    mail_delivery.deliver(row['id'])
    with db.engine().connect() as conn:assert db.one(conn,db.mail,db.mail.c.id==row['id'])['status']=='attention'


def test_local_previews_are_not_sent_when_smtp_enabled_later(portals,monkeypatch):
    portals('client')
    monkeypatch.setenv('EMAIL_MODE','smtp');mail_delivery.enqueue()
    with db.engine().connect() as conn:assert db.rows(conn,db.jobs,db.jobs.c.kind=='mail')==[]


def test_message_preparation_failure_does_not_leave_sending_record(portals,monkeypatch):
    client=portals('client');uid=client.get('/api/auth/me').json()['user']['id']
    monkeypatch.setenv('EMAIL_MODE','smtp');monkeypatch.setenv('SMTP_HOST','smtp.example.com');monkeypatch.setenv('SMTP_FROM','verify@example.com')
    with db.engine().begin() as conn:
        auth.mail(conn,uid,'client@example.com','Test','private-token')
        row=db.rows(conn,db.mail,db.mail.c.status=='queued')[0]
    def broken(value):raise ValueError('private-decryption-details')
    monkeypatch.setattr(db,'decrypt',broken)
    with pytest.raises(RuntimeError,match='uncertain') as exc:mail_delivery.deliver(row['id'])
    assert 'private-decryption-details' not in str(exc.value)
    with db.engine().connect() as conn:
        assert db.one(conn,db.mail,db.mail.c.id==row['id'])['status']=='attention'


def test_switch_to_smtp_requires_real_mailbox_verification(portals,monkeypatch):
    c=portals('client');assert c.get('/api/auth/me').json()['user']['verified']
    c.post('/api/auth/resend-verification',json={})
    local=c.get('/api/auth/mail').json()[-1]['body'].split('#verify=')[1]
    monkeypatch.setenv('EMAIL_MODE','smtp')
    assert not c.get('/api/auth/me').json()['user']['verified']
    assert c.get('/api/client/projects').status_code==403
    assert c.post('/api/auth/verify',json={'token':local}).status_code==400
    assert c.post('/api/auth/resend-verification',json={}).status_code==200
    # Read the encrypted test outbox as the SMTP transport would; there is no
    # user-facing preview in SMTP mode.
    with db.engine().connect() as conn:
        row=db.rows(conn,db.mail,db.mail.c.status=='queued')[-1]
        raw=db.decrypt(row['body']).split('#verify=')[1]
    assert c.post('/api/auth/verify',json={'token':raw}).status_code==200
    assert c.get('/api/auth/me').json()['user']['verified']
    assert c.get('/api/client/projects').status_code==200
