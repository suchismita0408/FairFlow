// Shared splash screen: external script works with the app's security policy.
(() => {
  const dismiss = () => {
    const overlay = document.getElementById('loading-overlay');
    if (!overlay) return;
    window.setTimeout(() => {
      overlay.classList.add('hidden');
      window.setTimeout(() => overlay.remove(), 550);
    }, window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 1600);
  };
  // Start from DOM readiness so slow images or network requests cannot trap the user.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', dismiss, {once: true});
  else dismiss();
})();

// The original operator console runs only on its own page.
(() => {
if (!document.getElementById('create-form')) return;
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n/100);
let projects=[], selected=null, config={}, draft=null;
const sample='Client: I need a responsive static landing page for my coffee brand, with a hero, menu, about section and contact details. No database, backend or online payments.\nFreelancer: I can deliver the HTML and CSS for $800, including 2 revisions. Additional work is quoted separately.\nClient: Agreed. Let us start.';
function toast(message){$('toast').textContent=message;$('toast').style.display='block';clearTimeout(window.toastTimer);window.toastTimer=setTimeout(()=>$('toast').style.display='none',5000)}
async function api(path,body){const r=await fetch('/api'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Admin-Token':sessionStorage.getItem('fairflow-token')||''},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data}
async function busy(button,fn){if(button)button.disabled=true;try{await fn()}catch(e){toast(e.message)}finally{if(button)button.disabled=false}}
function openModal(){$('create-modal').showModal()}
function badge(state){return `<span class="badge ${esc(state)}">${esc(state.replaceAll('_',' '))}</span>`}
async function refresh(){projects=await api('/projects');render()}
function render(){
 const pays=projects.flatMap(p=>p.payments),changes=projects.flatMap(p=>p.changes);
 $('authorized').textContent=money(pays.filter(p=>p.status==='authorized').reduce((a,p)=>a+p.amount_cents-(p.captures||[]).reduce((n,c)=>n+c.amount_cents,0),0));
 $('captured').textContent=money(pays.reduce((a,p)=>a+(p.captures?p.captures.reduce((n,c)=>n+c.amount_cents,0):(p.status==='captured'?p.amount_cents:0)),0));
 $('opportunities').textContent=money(changes.filter(c=>c.verdict==='out_of_scope').reduce((a,c)=>a+c.amount_cents,0));
 $('active-count').textContent=projects.filter(p=>!['draft','completed'].includes(p.status)).length;
 $('list-count').textContent=projects.length;
 $('project-list').innerHTML=projects.length?projects.map(p=>`<button class="project-card ${p.id===selected?'selected':''}" data-select="${p.id}"><div class="card-top"><div><h3>${esc(p.contract.title)}</h3><small>${esc(p.client_name)} <span>·</span> ${esc(p.freelancer_name)}</small></div>${badge(p.status)}</div><div class="card-bottom"><span><b>${money(p.contract.price_cents)}</b> &nbsp; baseline</span><span>${p.revisions_used}/${p.contract.allowed_revisions} revisions &nbsp; ↗</span></div></button>`).join(''):`<div class="empty"><div class="shield">◇</div><h2>Your next project starts here.</h2><p>Bring the conversation. We’ll help turn it into a clear agreement and a protected workflow.</p><button class="primary" id="empty-create">Create your first agreement →</button></div>`;
 document.querySelectorAll('[data-select]').forEach(b=>b.onclick=()=>{selected=b.dataset.select;render()});
 if($('empty-create'))$('empty-create').onclick=openModal;
 const p=projects.find(p=>p.id===selected);$('detail').innerHTML=p?(p.engine==='agile_v2'?agileDetail(p):detail(p)):'';
 if(p){if(p.engine==='agile_v2')bindAgile(p);else bindDetail(p);}
}
function payButtons(pay){if(pay.status==='creating')return '<p>Checkout creation was interrupted. Use the checkout action to retry with the same payment ID.</p>';if(pay.status!=='awaiting_approval')return badge(pay.status);return `<div class="actions">${pay.checkout_url?`<a class="secondary" href="${esc(pay.checkout_url)}" target="_blank" rel="noopener noreferrer">Open PayPal checkout ↗</a>`:''}<button class="primary" data-pay="${pay.id}">${config.payment_mode==='demo'?'Simulate buyer approval':'Confirm PayPal approval'}</button></div>`}
function detail(p){const c=p.contract;return `<article class="detail-panel"><div class="card-top"><h2>${esc(c.title)}</h2>${badge(p.status)}</div><h3>Baseline agreement</h3><p class="scope">${esc(c.task_description)}</p><div class="two-col"><div><h3>Included deliverables</h3><ul>${c.deliverables.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div><div><h3>Outside the agreement</h3><ul>${c.exclusions.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div></div><p>${money(c.price_cents)} · ${c.allowed_revisions} revisions · ${money(c.hourly_rate_cents)}/hour for approved additions</p>
 ${p.status==='draft'?`<h3>Record acceptance</h3><p>Prototype operator controls. Review the agreement with both parties before recording consent.</p><div class="actions">${['freelancer','client'].map(role=>`<button class="secondary" data-accept="${role}" ${p.accepted.includes(role)?'disabled':''}>${p.accepted.includes(role)?'✓ ':''}${role} accepts</button>`).join('')}<button class="primary" id="checkout" ${p.accepted.length!==2?'disabled':''}>Create checkout</button></div>`:''}
 ${p.payments.filter(x=>!x.change_id).map(x=>`<h3>Baseline payment · ${money(x.amount_cents)}</h3>${payButtons(x)}`).join('')}
 ${p.review_deadline?`<div class="timer">REVIEW WINDOW<strong id="countdown" data-deadline="${esc(p.review_deadline)}"></strong><small>Deadline: ${esc(new Date(p.review_deadline).toLocaleString())}</small></div>`:''}
 ${['active','in_review','revision_requested'].includes(p.status)?`<h3>✦ Revision firewall</h3><p>Changes are checked against the stored agreement. Estimates are proposals, not market-price guarantees.</p><textarea id="change-request" rows="3" placeholder="Could you also add a database and customer login?"></textarea><div class="actions"><button class="primary" id="evaluate">Evaluate request →</button><button class="secondary" id="dispute">Open dispute / pause</button></div>`:''}
 ${['active','revision_requested'].includes(p.status)?`<h3>Submit your deliverable</h3><label>Delivery URL<input id="delivery-url" type="url" placeholder="https://example.com/deliverable"></label><label>Delivery notes<textarea id="delivery-notes" rows="2" placeholder="What is ready for review?"></textarea></label><button class="primary" id="deliver">Submit & start 72-hour review</button>`:''}
 ${['in_review','payment_attention'].includes(p.status)?`<div class="actions"><button class="primary" id="approve">${p.status==='payment_attention'?'Retry capture after reconciliation':'Client approves & captures payment'}</button>${config.payment_mode==='demo'&&p.status==='in_review'?'<button class="secondary" id="expire">Demo: advance 72 hours →</button>':''}</div>`:''}
 ${p.status==='disputed'?'<h3>Release is paused</h3><p>Record the resolution agreed by both parties. A new delivery will start a fresh review window.</p><textarea id="resolution" placeholder="Agreed resolution and evidence reference"></textarea><button class="secondary" id="resolve">Record mutual resolution</button>':''}
 ${p.status==='completed'?'<div class="notice">✓ Payment captured. '+(config.payment_mode==='demo'?'This was a simulation; no money moved.':'Captured to the configured Sandbox merchant; no marketplace payout is implemented.')+'</div>':''}
 ${p.deliveries.length?`<h3>Deliveries</h3>${p.deliveries.map(d=>`<p><a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">${esc(d.url)} ↗</a><br>${esc(d.notes)}</p>`).join('')}`:''}
 ${p.changes.length?`<h3>Change requests & quotes</h3>${p.changes.map(ch=>{const pay=p.payments.find(x=>x.change_id===ch.id);return `<div class="change"><div class="card-top">${badge(ch.verdict)}<b>${money(ch.amount_cents)}</b></div><p><strong>${esc(ch.request)}</strong><br>${esc(ch.reasoning)}</p>${ch.amount_cents?`<p>Estimate: ${ch.estimated_hours} hours × ${money(c.hourly_rate_cents)}/hour; $20 minimum. ${esc(ch.status)}.</p>`:''}${pay?payButtons(pay):''}${ch.verdict==='out_of_scope'&&(!pay||pay.status==='creating')?`<button class="secondary" data-retry="${ch.id}">Retry change checkout</button>`:''}</div>`}).join('')}`:''}
 <h3>Project activity</h3><div class="audit">${[...p.events].reverse().map(e=>`<div>${esc(e.message)}<small>${esc(new Date(e.at).toLocaleString())}</small></div>`).join('')}</div></article>`}
function bindDetail(p){
 const action=(id,path,getBody=()=>({}))=>{if($(id))$(id).onclick=()=>busy($(id),async()=>{await api(`/projects/${p.id}/${path}`,getBody());await refresh();toast('Project updated.')})};
 document.querySelectorAll('[data-accept]').forEach(b=>b.onclick=()=>busy(b,async()=>{await api(`/projects/${p.id}/accept`,{role:b.dataset.accept});await refresh()}));
 document.querySelectorAll('[data-pay]').forEach(b=>b.onclick=()=>busy(b,async()=>{await api(`/payments/${b.dataset.pay}/authorize`,{});await refresh();toast('Payment authorization recorded.')}));
 document.querySelectorAll('[data-retry]').forEach(b=>b.onclick=()=>busy(b,async()=>{await api(`/projects/${p.id}/changes/${b.dataset.retry}/checkout`,{});await refresh()}));
 action('checkout','checkout');action('evaluate','changes',()=>({request:$('change-request').value}));action('dispute','dispute',()=>({request:$('change-request').value}));action('deliver','deliver',()=>({url:$('delivery-url').value,notes:$('delivery-notes').value}));action('approve','approve');action('expire','demo-expire');action('resolve','resolve',()=>({request:$('resolution').value}));tick();
}
function tick(){const el=$('countdown');if(!el)return;let seconds=Math.max(0,Math.floor((new Date(el.dataset.deadline)-Date.now())/1000));el.textContent=`${String(Math.floor(seconds/3600)).padStart(2,'0')}h ${String(Math.floor(seconds%3600/60)).padStart(2,'0')}m ${String(seconds%60).padStart(2,'0')}s`;}
setInterval(()=>{tick();tickAgile();},1000);
['new-project','nav-new'].forEach(id=>$(id).onclick=openModal);
$('nav-projects').onclick=()=>window.scrollTo({top:0,behavior:'smooth'});
$('close-modal').onclick=()=>$('create-modal').close();
$('refresh').onclick=()=>busy($('refresh'),refresh);
$('sample').onclick=()=>$('chat').value=sample;
$('extract').onclick=()=>busy($('extract'),async()=>{const result=await api('/agile/draft',{chat_history:$('chat').value,milestone_count:Number($('milestone-count').value)});draft=result.contract;renderPlanEditor(result.milestones);$('title').value=draft.title;$('scope').value=draft.task_description;$('price').value=draft.price_cents/100;$('revisions').value=draft.allowed_revisions;$('rate').value=draft.hourly_rate_cents/100;$('deliverables').value=draft.deliverables.join('\n');$('exclusions').value=draft.exclusions.join('\n');$('contract-fields').hidden=false;toast('Draft extracted. Review and edit the terms before creating.');});
$('create-form').onsubmit=e=>{e.preventDefault();busy(e.submitter,async()=>{if(!draft)throw Error('Extract a draft first.');const contract={title:$('title').value,task_description:$('scope').value,price_cents:Math.round(Number($('price').value)*100),allowed_revisions:Number($('revisions').value),hourly_rate_cents:Math.round(Number($('rate').value)*100),deliverables:$('deliverables').value.split('\n').filter(Boolean),exclusions:$('exclusions').value.split('\n').filter(Boolean)};const milestones=readPlanEditor();if(milestones.reduce((sum,m)=>sum+m.amount_cents,0)!==contract.price_cents)throw Error('Milestone prices must add up exactly to the baseline price.');const p=await api('/agile/projects',{contract,milestones,client_name:$('client').value,freelancer_name:$('freelancer').value});selected=p.id;$('create-modal').close();$('contract-fields').hidden=true;draft=null;await refresh();toast('Agreement created. Record acceptance to begin checkout.');});};
$('settings').onclick=()=>{const token=prompt('Sandbox operator token (stored only in this browser tab session):');if(token!==null){sessionStorage.setItem('fairflow-token',token);busy(null,refresh)}};
(async()=>{try{config=await api('/health');$('connection').textContent=config.payment_mode==='demo'?'Demo workspace':'PayPal Sandbox';$('mode-notice').textContent=config.payment_mode==='demo'?'DEMO MODE · All funds are simulated. Explore the full project lifecycle without moving money.':'SANDBOX MODE · Test payments only. This operator console requires a private access token.';$('ai-label').textContent=config.ai_mode==='gemini'?'Gemini API connected by configuration':'Offline rules · Gemini not enabled';$('pay-label').textContent=config.payment_mode==='demo'?'Simulated authorization & capture':'Sandbox authorization & capture';await refresh();const params=new URLSearchParams(location.search),payment=params.get('payment')||params.get('agile_payment');if(payment){toast('PayPal returned you here. Select the project and confirm approval to verify authorization.');const p=projects.find(p=>p.payments.some(x=>x.id===payment));if(p){selected=p.id;render()}history.replaceState({},'',location.pathname)}if(params.has('vault_project')){selected=params.get('vault_project');render();toast('Verify PayPal save approval to activate the saved payment method.');history.replaceState({},'',location.pathname)}if(params.has('cancelled'))toast('Checkout cancelled; payment remains unfunded.');}catch(e){toast(e.message)}})();



})();
