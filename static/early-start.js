'use strict';
function earlyScheduleTable(schedule){
  return `<div class="table-wrap"><table><thead><tr><th>Schedule</th><th>Current</th><th>Proposed</th></tr></thead><tbody>${schedule.updates.map(change=>`<tr><th>${esc(change.label)}</th><td>${change.before===null?'Not specified':typeof change.before==='boolean'?(change.before?'Yes':'No'):esc(uiDate(change.before))}</td><td>${typeof change.after==='boolean'?(change.after?'Yes':'No'):esc(uiDate(change.after))}</td></tr>`).join('')}</tbody></table></div><p class="help">${esc(schedule.note)}</p>`;
}
async function proposeEarlyStart(mid){
  const base=`projects/${S.room.project.id}/workflow`;
  const schedule=await api(url(base+'/early-start/'+encodeURIComponent(mid)));
  modal('Start milestone early',`<p>Move the remaining schedule <strong>${schedule.shift_days} days earlier</strong>. Dates update after both participants approve.</p>${earlyScheduleTable(schedule)}<form id="early-start-form">${input('early-start-name','Type your account name to approve','','text',true,S.user.name)}<button class="primary">Propose & approve schedule</button></form>`,()=>{
    $('early-start-form').onsubmit=e=>{e.preventDefault();run(async()=>{
      const result=await api(url(base+'/requests'),{revision:S.room.revision,kind:'early_start',milestone_id:mid,reason:'Start this milestone early and move the remaining schedule forward automatically.'});
      // Approve only the exact server-generated schedule the participant saw.
      if(JSON.stringify(result.request.schedule)!==JSON.stringify(schedule))throw Error('The schedule changed. Review the newly created proposal in Resolution before accepting.');
      await api(url(base+`/requests/${result.request.id}/vote`),{revision:result.revision,digest:result.request.digest,decision:'accept',typed_name:$('early-start-name').value});
      $('dialog').close();await refreshWorkflow();toast('Early-start schedule sent for the other participant to approve.');
    });};
  });
}
function earlyStartControls(container){
  const requests=S.workflow?.requests||[];
  container.querySelectorAll('[data-milestone-card]').forEach(card=>{
    const mid=card.dataset.milestoneCard;
    const pending=requests.find(r=>r.kind==='early_start'&&r.milestone_id===mid&&['proposed','accepted_by_one'].includes(r.status));
    const target=card.querySelector('.payment-main')||card;
    if(pending){
      const voted=pending.votes.some(v=>v.user_id===S.user.id);
      target.insertAdjacentHTML('beforeend',`<div class="actions"><span class="help">Early-start schedule: ${voted?'waiting for the other participant':'your review needed'}</span>${!voted?`<button data-early-review="${esc(pending.id)}">Review early-start dates</button>`:''}</div>`);
    }else if(S.assurance?.early_start?.[mid]?.available){
      target.insertAdjacentHTML('beforeend',`<button data-start-early="${esc(mid)}">Start milestone early</button>`);
    }else if(!S.assurance?.payments.some(p=>p.milestone_id===mid)&&S.assurance?.early_start?.[mid]?.reason?.includes('previous milestone payments')){
      target.insertAdjacentHTML('beforeend','<div class="actions"><button disabled>Start milestone early</button><span class="help">Available after previous milestone payouts are verified.</span></div>');
    }
  });
  container.querySelectorAll('[data-start-early]').forEach(button=>button.onclick=()=>run(()=>proposeEarlyStart(button.dataset.startEarly)));
  container.querySelectorAll('[data-early-review]').forEach(button=>button.onclick=()=>workflowVote(requests.find(r=>r.id===button.dataset.earlyReview),'accept'));
}
