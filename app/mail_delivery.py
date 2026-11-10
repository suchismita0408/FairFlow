"""Encrypted outbox to SMTP. Local preview is never silently sent externally."""
import os, smtplib, ssl
from email.message import EmailMessage
from sqlalchemy import insert, update
from . import db


def enqueue():
    if os.getenv('EMAIL_MODE','local')!='smtp':return
    with db.engine().begin() as conn:
        for row in db.rows(conn,db.mail,db.mail.c.status=='queued'):
            key='mail-'+row['id']
            if not db.one(conn,db.jobs,db.jobs.c.id==key):
                conn.execute(insert(db.jobs).values(id=key,project_id=None,kind='mail',payload={'mail_id':row['id']},status='queued',attempts=0,run_at=db.now()))


def deliver(mail_id):
    if os.getenv('EMAIL_MODE','local')!='smtp':return
    host=os.getenv('SMTP_HOST');sender=os.getenv('SMTP_FROM')
    if not host or not sender:raise RuntimeError('SMTP_HOST and SMTP_FROM are required.')
    with db.engine().begin() as conn:
        row=db.one(conn,db.mail,db.mail.c.id==mail_id)
        if not row or row['status']!='queued':return
        changed=conn.execute(update(db.mail).where(db.mail.c.id==mail_id,db.mail.c.status=='queued').values(status='sending'))
        if changed.rowcount!=1:return
    try:
        message=EmailMessage();message['From']=sender;message['To']=row['email']
        message['Subject']=row['subject'];message['Message-ID']='<'+mail_id+'@fairflow.local>'
        message.set_content(db.decrypt(row['body']))
        port=int(os.getenv('SMTP_PORT','587'));context=ssl.create_default_context()
        client=smtplib.SMTP_SSL(host,port,timeout=20,context=context) if port==465 else smtplib.SMTP(host,port,timeout=20)
        with client:
            if port!=465:client.ehlo();client.starttls(context=context);client.ehlo()
            if os.getenv('SMTP_USERNAME'):client.login(os.environ['SMTP_USERNAME'],os.environ.get('SMTP_PASSWORD',''))
            client.send_message(message)
    except Exception:
        # A dropped response after SMTP DATA may mean it was sent. Never loop
        # delivery or log message content, reset tokens, credentials or raw errors.
        with db.engine().begin() as conn:
            conn.execute(update(db.mail).where(db.mail.c.id==mail_id).values(status='attention'))
            db.log(conn,'email_delivery_attention',None,mail_id=mail_id)
        raise RuntimeError('SMTP delivery failed or is uncertain; inspect provider delivery logs.') from None
    with db.engine().begin() as conn:
        conn.execute(update(db.mail).where(db.mail.c.id==mail_id).values(status='sent'))
        db.log(conn,'email_sent',None,mail_id=mail_id)
