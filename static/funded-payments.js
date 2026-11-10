'use strict';
function fundedPaymentControls(container){
  earlyStartControls(container);
  const platform=S.assurance?.platform;if(!platform?.configured)return;
  const seller=platform.seller||{},base=`projects/${S.room.project.id}/assurance/platform`;
  container.insertAdjacentHTML('afterbegin',`<section class="card workflow-panel"><div class="split-title"><strong>Upfront funding</strong><span>${seller.status==='verified'?'Receiving account connected':!platform.enabled?'Available for future milestones':'Receiving account not connected'}</span></div>${seller.status==='verified'?`<p class="help">${esc(seller.email)}</p>`:''}<div class="actions">${S.role==='freelancer'&&seller.status!=='verified'?'<button id="connect-platform-seller">Connect PayPal</button>':''}${seller.action_url&&seller.status!=='verified'&&S.role==='freelancer'?`<a class="button" href="${esc(seller.action_url)}" target="_blank" rel="noopener noreferrer">Continue in PayPal</a><button id="verify-platform-seller">Check connection</button>`:''}</div></section>`);
  if(platform.buyer_email)container.insertAdjacentHTML('afterbegin',`<p class="notice"><strong>Client PayPal account:</strong> ${esc(platform.buyer_email)}<br><strong>Freelancer receiving account:</strong> ${esc(seller.email||'Connect receiving account')}<br>Use the client account when signing into PayPal checkout. The platform business account runs the integration.</p>`);
  if($('connect-platform-seller'))$('connect-platform-seller').onclick=()=>modal('Connect your seller account',`<form id="platform-seller-form">${input('platform-seller-email','PayPal receiving email',seller.email||'','email')}<button class="primary">Connect</button></form>`,()=>{$('platform-seller-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api(url(base+'/connect'),{email:$('platform-seller-email').value});$('dialog').close();await refreshAssurance();});};});
  if($('verify-platform-seller'))$('verify-platform-seller').onclick=()=>run(async()=>{await api(url(base+'/verify'),{});await refreshAssurance();toast('Freelancer receiving account verified.');});
  container.querySelectorAll('[data-milestone-card]').forEach(card=>{
    const payment=S.assurance.payments.find(p=>p.milestone_id===card.dataset.milestoneCard),funded=payment?.funding_model==='paypal_funded_v1',newFunded=!payment&&platform.enabled;
    if(!funded&&!newFunded)return;
    const start=card.querySelector('[data-pay-start]');
    if(start){start.textContent='Fund milestone in PayPal';start.disabled=!platform.terms_agreed||seller.status!=='verified'||!!S.room.project.withdrawal||!!S.room.project.work_closed||!!S.room.project.amendment||S.room.project.closed_milestones?.includes(card.dataset.milestoneCard)||!S.assurance.funding_windows?.[card.dataset.milestoneCard]?.available;}
    const collect=card.querySelector('[data-pay-authorize]');if(collect)collect.textContent='Confirm approval & collect funding';
    card.querySelectorAll('[data-pay-renew]').forEach(b=>b.remove());
    if(payment?.provider_disburse_by&&!['captured','refunded','disbursing'].includes(payment.status))(card.querySelector('.payment-main')||card).insertAdjacentHTML('beforeend',`<p class="help">PayPal automatic disbursement deadline: <strong>${esc(uiDate(payment.provider_disburse_by,true))}</strong>. Client approval or the review deadline can request an earlier release.</p>`);
    if(funded&&payment.status==='refunded'){
      const main=card.querySelector('.payment-main')||card;
      const badge=card.querySelector('.split-title .pill');if(badge)badge.textContent='Refund confirmed';
      main.querySelectorAll(':scope > p').forEach(node=>{if(node.textContent.startsWith('Payment release:'))node.remove();});
      main.insertAdjacentHTML('beforeend',`<section class="notice" aria-label="Client refund"><strong>Refund confirmed by PayPal</strong><p>Refunded amount: <strong>${money(payment.refunded_minor??payment.amount_minor,payment.currency)}</strong></p><p>The milestone payment was refunded to the client. Your original payment method may take additional time to show the credit.</p>${payment.refund_id?`<p class="help">PayPal refund reference: ${esc(payment.refund_id)}</p>`:''}${payment.refunded_at?`<p class="help">Refund verified: ${esc(uiDate(payment.refunded_at,true))}</p>`:''}</section>`);
      return;
    }
    if(funded&&payment.status==='settling'&&!payment.delivery_url&&S.room.project.withdrawal?.status==='accepted')
      (card.querySelector('.payment-main')||card).insertAdjacentHTML('beforeend','<section class="notice" aria-label="Client refund"><strong>Refund processing</strong><p>Withdrawal was accepted. This unsubmitted milestone is being settled; the refund is not confirmed until PayPal verification completes. See Resolution for operation details.</p></section>');
    if(funded){
      const paid=payment.status==='captured', released=paid||payment.status==='disbursing'||!!payment.disbursed_at;
      if(payment.capture_id)(card.querySelector('.payment-main')||card).insertAdjacentHTML('beforeend',`<section class="notice" aria-label="PayPal collection and release"><strong>Collection and release</strong><p>Client funds: collected${payment.collection_verified_at?' - Verified '+esc(uiDate(payment.collection_verified_at,true)):''}.</p><p>PayPal disbursement mode: <strong>${esc(payment.provider_disbursement_mode||'Check provider status')}</strong>.</p><p>FairFlow release: ${payment.release_requested_at?'Requested '+esc(uiDate(payment.release_requested_at,true)):payment.release_reason?'Approval recorded; check provider status':'No release request recorded'}.</p>${payment.disbursed_at?`<p>Seller release verified: ${esc(uiDate(payment.disbursed_at,true))}.</p>`:''}<small>PayPal controls fund availability. A completed collection is not proof that the seller can withdraw it.</small></section>`);
      if(payment.capture_id&&!paid)(card.querySelector('.payment-main')||card).insertAdjacentHTML('beforeend','<p class="help">The client payment has been collected. Seller release is a separate step. PayPal may show the collection as Completed before release; that label alone does not verify the available balance.</p>');
      const stages=[['Client payment',payment.capture_id?'Collected':payment.status==='awaiting_approval'?'Awaiting PayPal approval':'Not collected'],['Work submitted',payment.delivered_at?uiDate(payment.delivered_at,true):'Awaiting submission'],['Client review',released?'Finished':payment.objection?'Paused for resolution':payment.review_deadline?'Review by '+uiDate(payment.review_deadline,true):'Starts after submission'],['Freelancer payment',paid?'Released by PayPal':payment.status==='disbursing'?'Release processing':payment.status==='attention'?'Needs verification':'Awaiting release']];
      (card.querySelector('.payment-main')||card).insertAdjacentHTML('beforeend',`<section class="notice" aria-label="Payment progress"><strong>Payment progress</strong><ol>${stages.map(([name,state])=>`<li><strong>${esc(name)}:</strong> ${esc(state)}</li>`).join('')}</ol>${paid&&payment.seller_net_amount?`<p>Freelancer receives: <strong>${esc(payment.seller_net_amount.value)} ${esc(payment.seller_net_amount.currency_code)}</strong> after PayPal fees.</p>`:''}${payment.payout_transaction_id?`<p class="help">PayPal payout: ${esc(payment.payout_transaction_id)}</p>`:''}${payment.provider_checked_at?`<p class="help">Last verified: ${esc(uiDate(payment.provider_checked_at,true))}</p>`:''}</section>`);
      const badge=card.querySelector('.split-title .pill');if(badge)badge.textContent=paid?'Payment released':payment.status==='disbursing'?'Release processing':label(payment.status);
      if(paid)card.querySelectorAll('.payment-due, .payment-schedule').forEach(el=>el.remove());
    }
    if(payment?.status==='funded')(card.querySelector('.payment-main')||card).insertAdjacentHTML('afterbegin','<p class="notice"><strong>Milestone funded.</strong> Ready for work.</p>');
    if(payment?.status==='captured')(card.querySelector('.payment-main')||card).insertAdjacentHTML('beforeend','<p class="notice"><strong>Payment released.</strong> PayPal fees apply.</p>');
    if(payment?.status==='disbursing')(card.querySelector('.payment-main')||card).insertAdjacentHTML('beforeend','<p class="notice">Payment release requested. Waiting for PayPal confirmation.</p>');
    if(funded&&payment?.status==='attention'&&!payment.order_id)(card.querySelector('.payment-main')||card).insertAdjacentHTML('beforeend',`<button data-platform-recover="${esc(payment.milestone_id)}">Recover payment reference</button>`);
    if(funded&&payment?.capture_id){
      (card.querySelector('.payment-main')||card).insertAdjacentHTML('beforeend',`<button data-platform-check="${esc(payment.milestone_id)}">Check platform payment status</button>`);
    }
  });
  container.querySelectorAll('[data-platform-recover]').forEach(b=>b.onclick=()=>modal('Recover payment reference',`<form id="platform-recover-form">${input('platform-order-reference','PayPal order ID')}<button class="primary">Check original payment</button></form>`,()=>{$('platform-recover-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api(url(`projects/${S.room.project.id}/assurance/platform/${encodeURIComponent(b.dataset.platformRecover)}/recover-order`),{order_id:$('platform-order-reference').value.trim()});$('dialog').close();await refreshAssurance();});};}));
  container.querySelectorAll('[data-platform-check]').forEach(b=>b.onclick=()=>run(async()=>{
    b.disabled=true;b.textContent='Checking PayPal?';
    try{
      const result=await api(url(`projects/${S.room.project.id}/assurance/platform/${encodeURIComponent(b.dataset.platformCheck)}/reconcile`),{});
      await refreshAssurance();
      modal('PayPal payment status',`<p><strong>${esc(label(result.local?.status||'unknown'))}</strong></p><p>${esc(result.message||'PayPal status checked.')}</p><p class="help">Buyer capture status: ${esc(result.provider_status||'unavailable')}</p><p class="help">Capture reference: ${esc(result.provider_id||'unavailable')}</p>`);
    }finally{b.disabled=false;b.textContent='Check platform payment status';}
  }));
}

let paymentRefreshBusy=false;
setInterval(async()=>{
  if(paymentRefreshBusy||document.hidden||typeof S==='undefined'||S.tab!=='Payments'||!S.room?.project?.id||S.dirty||document.querySelector('dialog[open]'))return;
  paymentRefreshBusy=true;
  try{
    const next=await api(url(`projects/${S.room.project.id}/assurance`));
    if(JSON.stringify(next.payments)!==JSON.stringify(S.assurance?.payments)){
      const scroll=window.scrollY;S.assurance=next;renderProject();window.scrollTo(0,scroll);
    }
  }catch(error){/* Keep the last verified status and its timestamp during outages. */}
  finally{paymentRefreshBusy=false;}
},15000);

async function reconcilePlatformReturn(){
  const params=new URLSearchParams(location.search);
  if(S.role!=='client'||params.get('paypal_return')!=='1'||!S.assurance)return;
  const payment=S.assurance.payments.find(p=>p.funding_model==='paypal_funded_v1'&&p.order_id===params.get('token')&&['awaiting_approval','attention'].includes(p.status));
  if(!payment)return;
  try{
    await api(url(`projects/${S.room.project.id}/assurance/platform/${encodeURIComponent(payment.milestone_id)}/reconcile`),{});
    S.assurance=await api(url(`projects/${S.room.project.id}/assurance`));
    for(const key of ['paypal_return','token','PayerID'])params.delete(key);
    history.replaceState({},'',location.pathname+'?'+params.toString());
  }catch(error){toast(error.message,true);}
}
