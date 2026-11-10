"""Provider reconciliation confirms observations; joint actions authorize new money movements."""
from fastapi import HTTPException
from sqlalchemy import insert
from . import paypal,store,agile,db

async def reconcile(p):
    if p['mode']=='demo':
        with db.engine().begin() as conn:db.log(conn,'demo_reconciliation',project_id=p['id'],message='No provider funds exist in demo mode.')
        return
    issues=[]
    for pay in p['payments']:
        if not pay.get('order_id'):continue
        order=await paypal.call('GET','/v2/checkout/orders/'+pay['order_id'])
        observed=[x for unit in order.get('purchase_units',[]) for x in unit.get('payments',{}).get('captures',[])]
        for op in pay.get('operations',[]):
            if op['status']=='done' or 'capture' not in op['kind']:continue
            matches=[x for x in observed if x.get('invoice_id')==op['key']]
            if len(matches)==1:
                cap=await paypal.call('GET','/v2/payments/captures/'+matches[0]['id'])
                if cap.get('status')=='COMPLETED' and cap.get('amount')==paypal.currency_amount(pay,op['amount_cents']):
                    op.update(status='done',provider_id=cap['id'])
                    if not any(x['id']==cap['id'] for x in pay['captures']):pay['captures'].append({'id':cap['id'],'amount_cents':op['amount_cents'],'at':db.now(),'operation_key':op['key']})
                    pay['status']='captured' if op['final_capture'] else 'partially_captured'
        if pay.get('authorization_id') and pay['status'] in ('authorized','partially_captured'):
            auth=await paypal.call('GET','/v2/payments/authorizations/'+pay['authorization_id'])
            if auth.get('status') in ('VOIDED','DENIED'):
                pay['status']='voided' if auth['status']=='VOIDED' else 'failed'
                m=agile.milestone(p,pay['milestone_id'])
                m.update(status='billing_failed',review_deadline=None,last_error='Provider no longer confirms an active authorization.')
                issues.append('Authorization no longer active')
        for cap in pay.get('captures',[]):
            current=await paypal.call('GET','/v2/payments/captures/'+cap['id'])
            if current.get('status') in ('DECLINED','FAILED','REFUNDED','PARTIALLY_REFUNDED'):
                if not any(r.get('capture_id')==cap['id'] and r.get('status')=='done' for r in pay.get('refund_operations',[])):
                    issues.append('External capture status requires review: '+current['status'])
    if issues:
        p['external_payment_issue']='; '.join(issues)
        for m in p['milestones']:
            if m['status'] in ('active','in_review','revision_requested'):m.update(status='disputed',dispute_open=True,review_deadline=None)
    agile.log(p,'Provider reconciliation completed'+(': '+p['external_payment_issue'] if issues else '.'))
    agile.sync(p)
    with db.engine().begin() as conn:db.log(conn,'provider_reconciliation',project_id=p['id'],issues=issues)

async def retry_agreed(p,m):
    if p['mode']!='demo':await reconcile(p)
    if m.get('settlement')=='full':
        m['status']='in_review';await agile.finish(p,m)
    elif m.get('settlement')=='partial':
        m['status']='settling_partial';await agile.settle_partial(p,m)
    elif p['emergency_paused']:
        await agile.void(p,m,'emergency_void');m.update(status='paused_emergency',review_deadline=None);agile.sync(p)
    else:await agile.fund(p,m,True)

async def refund(p,body,key):
    pay=next((x for x in p['payments'] if any(c['id']==body['capture_id'] for c in x.get('captures',[]))),None)
    if not pay:raise HTTPException(409,'Capture does not belong to this project.')
    cap=next(c for c in pay['captures'] if c['id']==body['capture_id'])
    existing=next((r for r in pay.get('refund_operations',[]) if r['key']==key),None)
    if existing and existing['status']=='done':return
    reserved=sum(r['amount_cents'] for r in pay.get('refund_operations',[]) if r['key']!=key and r['capture_id']==cap['id'])
    if body['amount_cents']>cap['amount_cents']-reserved:raise HTTPException(409,'Refund exceeds the unrefunded captured amount.')
    if not existing:
        existing={'key':key,'capture_id':cap['id'],'amount_cents':body['amount_cents'],'status':'pending'}
        pay.setdefault('refund_operations',[]).append(existing);store.save(p)
    if p['mode']=='demo':result={'id':'DEMO-REFUND-'+key,'status':'COMPLETED'}
    else:result=await paypal.call('POST','/v2/payments/captures/'+cap['id']+'/refund',{'amount':paypal.currency_amount(pay,body['amount_cents'])},key)
    if result.get('status')!='COMPLETED':raise HTTPException(502,'Refund is pending or unverified. No completion has been recorded.')
    existing.update(status='done',provider_id=result['id'])
    # Project state and balanced refund ledger commit together.
    with db.engine().begin() as conn:
        db.save_project(p,conn)
        for account,sign in [('provider_receivable',-1),('project_liability',1)]:
            external='refund:'+result['id']+':'+account
            if not db.one(conn,db.ledger,db.ledger.c.external_id==external):conn.execute(insert(db.ledger).values(id=db.uid(),project_id=p['id'],external_id=external,account=account,amount_cents=sign*body['amount_cents'],currency=p.get('currency','USD'),created_at=db.now()))
        db.log(conn,'refund_completed',project_id=p['id'],refund_id=result['id'],amount_cents=body['amount_cents'])
