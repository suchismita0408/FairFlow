'use strict';
function openWorkTab(tab='Work Tracking'){S.tab=tab;renderProject();}
function workEvidence(e){
  let body;try{body=JSON.parse(e.description);}catch{body={description:e.description};}
  return `<div class="read-field"><strong>${esc(e.title)}</strong><small> · ${esc(uiDate(e.created_at,true))}</small><p style="white-space:pre-wrap">${esc(body.description||'')}</p>${e.url?`<a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">Open submitted work</a>`:''}${(body.files||[]).map(file=>`<p><a href="${url(workflowBase()+'/files/'+encodeURIComponent(file.id))}" download>${esc(file.filename)}</a></p>`).join('')}</div>`;
}
function workTrackingLayout(container,a,ms,payments){
  const fragment=document.createDocumentFragment(),heading=document.createElement('section');
  heading.className='payment-heading';heading.innerHTML='<h2>Work Tracking</h2><p class="help">Submissions, review and work issues for each milestone.</p>';fragment.append(heading);
  ms.forEach(m=>{
    const old=[...container.querySelectorAll('[data-milestone-card]')].find(card=>card.dataset.milestoneCard===m.id);
    const payment=payments.find(p=>p.milestone_id===m.id),details=(a.scope?.deliverables||[]).filter(d=>d.milestone_id===m.id);
    const evidence=(S.workflow?.evidence||[]).filter(e=>e.milestone_id===m.id);
    const cases=(S.workflow?.cases||[]).filter(c=>c.milestone_id===m.id);
    const requests=(S.workflow?.requests||[]).filter(r=>r.milestone_id===m.id||r.schedule?.milestones.some(item=>item.id===m.id));
    const state=payment?.unexpected_disbursement?'Unexpected PayPal release':payment?.status==='captured'?'Review completed':payment?.delivery_url?label(payment.status):payment?.status==='funded'||payment?.status==='authorized'?'Ready for submission':'Awaiting funding';
    const card=document.createElement('section');card.className='card work-tracking-card';card.dataset.workMilestone=m.id;
    card.innerHTML=`<div class="split-title"><h2>${esc(m.title)}</h2>${pill(state)}</div><p class="help">Delivery due <strong>${esc(uiDate(m.due_date))}</strong>${payment?.delivered_at?' · Submitted '+esc(uiDate(payment.delivered_at,true)):''}${payment?.review_deadline?' · Review by '+esc(uiDate(payment.review_deadline,true)):''}</p><div data-work-current></div><div class="actions" data-work-actions></div><details class="advanced"><summary>Agreed work & revision allowance</summary>${details.map(d=>{const used=payment?.revision_counts?.[d.id]||0;return `<div class="read-field"><h3>${esc(d.name)} · ${esc(d.id)}</h3><strong>Specification</strong><p>${esc(d.specification||'')}</p><strong>Acceptance criteria</strong><ul>${(d.acceptance_criteria||[]).map(c=>`<li>${esc(c)}</li>`).join('')}</ul><small>Quantity ${esc(d.quantity)} · Revision rounds used ${used} · Remaining ${Math.max(0,d.revision_rounds-used)}</small></div>`;}).join('')}</details>${evidence.length?`<details class="advanced"><summary>Submission history & files · ${evidence.length}</summary>${evidence.slice().reverse().map(workEvidence).join('')}</details>`:''}${!evidence.length&&payment?.delivery_history?.length?`<details class="advanced"><summary>Previous submissions</summary>${payment.delivery_history.map(entry=>`<div class="read-field"><p>${esc(entry.note||entry.delivery_note||'')}</p>${entry.url?`<a href="${esc(entry.url)}" target="_blank" rel="noopener noreferrer">Submitted work</a>`:''}</div>`).join('')}</details>`:''}${cases.length?`<details class="advanced" open><summary>Disputes & work claims · ${cases.length}</summary>${cases.map(c=>`<div class="read-field"><strong>${esc(label(c.category))} · ${esc(label(c.status))}</strong><p style="white-space:pre-wrap">${esc(c.reason)}</p>${c.messages.map(message=>`<p><strong>${esc(message.role)}:</strong> ${esc(message.body)}</p>`).join('')}<button data-work-case="${esc(c.id)}">Respond / add evidence</button></div>`).join('')}</details>`:''}${requests.length?`<details class="advanced"><summary>Related proposals & date changes · ${requests.length}</summary>${requests.map(r=>`<div class="read-field"><strong>${esc(label(r.kind))} · ${esc(label(r.status))}</strong><p>${esc(r.reason)}</p>${['proposed','accepted_by_one'].includes(r.status)&&!r.votes.some(v=>v.user_id===S.user.id)?`<button data-work-review="${esc(r.id)}">Review proposal</button>`:''}</div>`).join('')}</details>`:''}<div class="actions"><button data-work-case-new="${esc(m.id)}">Open dispute / work claim</button>${!payment&&!S.room.project.withdrawal?'<button data-work-amend>Amend remaining milestones</button>':''}<button data-work-resolution>More resolution options</button></div>`;
    const current=card.querySelector('[data-work-current]'),actions=card.querySelector('[data-work-actions]');
    old?.querySelectorAll('.delivery-link,[aria-label="Client objection"]').forEach(node=>current.append(node));
    if(S.role==='client'&&payment?.status==='in_review'&&payment?.funding_model==='paypal_funded_v1'&&!payment.objection){
      const approve=document.createElement('button');approve.className='primary';approve.textContent='Approve work & release payment';
      approve.onclick=()=>modal('Approve submitted work',`<p>Approve this submission and request PayPal payment release now. If you prefer, payment will be requested automatically after the review period unless disputed.</p><button id="confirm-work-approval" class="primary">Approve & release payment</button>`,()=>{$('confirm-work-approval').onclick=()=>run(async()=>{const button=$('confirm-work-approval');if(button.disabled)return;button.disabled=true;button.textContent='Checking release...';try{const result=await api(url(`projects/${S.room.project.id}/assurance/${encodeURIComponent(m.id)}/approve-work`),{});$('dialog').close();await refreshAssurance();toast(result.message||'Approval recorded. Check payment status for PayPal confirmation.');}finally{button.disabled=false;button.textContent='Approve & release payment';}});});
      actions.prepend(approve);
    }
    if(!payment?.delivery_url)current.insertAdjacentHTML('beforeend','<p class="help">No submission yet.</p>');
    old?.querySelectorAll('[data-pay-deliver],[data-pay-object],[data-pay-resolve]').forEach(node=>actions.append(node));
    if((!payment||['authorized','funded','revision_requested'].includes(payment.status))&&!S.room.project.withdrawal&&!S.room.project.work_closed){
      const extension=document.createElement('button');extension.textContent='Propose deadline extension';
      extension.onclick=()=>{workflowProposal('extension');$('workflow-milestone').value=m.id;};actions.append(extension);
    }
    fragment.append(card);
  });
  container.replaceChildren(fragment);
  container.querySelectorAll('[data-work-amend]').forEach(b=>b.onclick=()=>openWorkTab('Amendments'));
  container.querySelectorAll('[data-work-resolution]').forEach(b=>b.onclick=()=>openWorkTab('Resolution'));
  container.querySelectorAll('[data-work-case-new]').forEach(b=>b.onclick=()=>{workflowCase();$('case-mid').value=b.dataset.workCaseNew;});
  container.querySelectorAll('[data-work-case]').forEach(b=>b.onclick=()=>workflowReply(S.workflow.cases.find(c=>c.id===b.dataset.workCase)));
  container.querySelectorAll('[data-work-review]').forEach(b=>b.onclick=()=>workflowVote(S.workflow.requests.find(r=>r.id===b.dataset.workReview),'accept'));
}
function paymentWorkLinks(container){
  container.querySelectorAll('[data-milestone-card]').forEach(card=>{
    const work=card.querySelectorAll('.delivery-link,[aria-label="Client objection"],[data-pay-deliver],[data-pay-object],[data-pay-resolve]');
    work.forEach(node=>node.remove());
    const button=document.createElement('button');button.textContent=S.role==='freelancer'?'Submit work & view feedback':'View work & review';button.onclick=()=>{const mid=card.dataset.milestoneCard;openWorkTab();[...document.querySelectorAll('[data-work-milestone]')].find(node=>node.dataset.workMilestone===mid)?.scrollIntoView({block:'center'});};
    const main=card.querySelector('.payment-main')||card;
    const row=document.createElement('div');row.className='actions milestone-action-row';
    const notes=[];
    [...main.children].forEach(node=>{
      if(node.matches('button,a.button'))row.append(node);
      else if(node.matches('.actions')){
        [...node.children].forEach(child=>{if(child.matches('button,a.button'))row.append(child);else if(child.matches('.help'))notes.push(child);});
        if(!node.children.length)node.remove();
      }
    });
    row.append(button);main.append(row);
    notes.forEach(note=>{note.classList.add('milestone-action-note');main.append(note);});
  });
}
