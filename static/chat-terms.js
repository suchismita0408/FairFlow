'use strict';
function aiOwnerPending(){return (S.room.project.ai_field_reviews||[]).filter(r=>!r.approved_by&&JSON.stringify(at(S.room.agreement,r.field_path))===JSON.stringify(r.expected));}
function chatTermsBlocked(mid=null){if(aiOwnerPending().length)return true;if(chatMonitorBlocked())return true;return (S.room.chat_term_changes||[]).some(c=>!c.resolved&&(!mid||!c.milestone_id||c.milestone_id===mid));}
function chatBlockReason(mid=null){
  if(aiOwnerPending().length)return 'AI changes await field approval: '+aiOwnerPending().map(r=>(specFor(r.field_path)?.label||r.field_path)+' ('+(r.owner==='both'?'either party':r.owner)+')').join('; ');
  const monitor=S.room.project.chat_monitor;
  if(monitor&&monitor.status!=='reviewed')return monitor.status==='pending'?'AI is checking the latest chat. Please wait.':'Chat review could not finish or is out of date. It will retry automatically.';
  const findings=chatMonitorFindings();
  if(findings.length)return findings.map(f=>f.explanation).join(' ');
  return (S.room.chat_term_changes||[]).filter(c=>!c.resolved&&(!mid||!c.milestone_id||c.milestone_id===mid)).map(c=>'The agreed chat change to '+(specFor(c.field_path)?.label||c.field_path)+' must be included in the approved agreement.').join(' ');
}
function guardChatTermButtons(){
  document.querySelectorAll('.chat-block-reason').forEach(el=>el.remove());

  document.querySelectorAll('[data-action="approve"],[data-action="sign"],[data-pay-start],[data-pay-authorize],[data-pay-deliver]').forEach(button=>{
    const mid=button.dataset.payStart||button.dataset.payAuthorize||button.dataset.payDeliver||null;
    if(chatTermsBlocked(mid)){button.disabled=true;button.title=chatBlockReason(mid);button.setAttribute('aria-describedby','chat-block-reason');}
  });
  if(chatTermsBlocked()&&document.querySelector('[data-action="approve"],[data-action="sign"]')){
    const turn=document.querySelector('#action-bar .turn');
    if(turn){
      const reason=document.createElement('div');reason.id='chat-block-reason';reason.className='chat-block-reason';reason.setAttribute('role','status');
      reason.innerHTML='<strong>Approval blocked:</strong> '+esc(chatBlockReason())+' <button type="button" class="quiet">View chat issue</button>';
      turn.appendChild(reason);
      reason.querySelector('button').onclick=()=>{const target=document.querySelector('[aria-label="Automatic chat review"],[aria-label="Chat changes awaiting agreement update"]');target?.scrollIntoView({behavior:'smooth',block:'center'});};
    }
  }
}
function chatTermBanner(){
  const changes=(S.room.chat_term_changes||[]).filter(c=>!c.resolved);
  const proposed=(S.room.project.recorded_chat_changes||[]).filter(c=>c.status==='proposed');
  if(!changes.length&&!proposed.length)return chatMonitorBanner();
  return chatMonitorBanner()+`<section class="notice" aria-label="Chat changes awaiting agreement update"><strong>Chat changes need formal agreement</strong><p class="help">Accepted changes must appear in the approved terms before new funding or affected submissions. Existing submitted-work payments keep their signed rules.</p>${changes.map(c=>`<div class="read-field"><strong>${esc(specFor(c.field_path)?.label||label(c.field_path))}</strong><p style="white-space:pre-wrap">${esc(c.body)}</p><div class="actions"><button data-chat-update>Update agreement</button>${c.field_path.endsWith('.due_date')&&workspace()?`<button data-chat-extension="${esc(c.milestone_id)}">Propose deadline extension</button>`:''}${!c.withdrawal_votes.includes(S.role)?`<button data-chat-withdraw="${esc(c.id)}">Withdraw this change</button>`:'<span class="help">Waiting for the other participant to confirm withdrawal.</span>'}</div></div>`).join('')}${proposed.map(c=>`<div class="read-field"><strong>${esc(specFor(c.field_path)?.label||c.field_path)}</strong><p>Proposed: ${esc(c.field_path.endsWith('minor')?money(c.expected,c.currency):Array.isArray(c.expected)?c.expected.join('; '):String(c.expected))}</p>${!c.votes.some(v=>v.role===S.role)?`<button data-chat-record-confirm="${esc(c.record_id)}">Review & confirm chat change</button>`:'<span class="help">Waiting for the other participant to confirm this chat change.</span>'}</div>`).join('')}</section>`;
}
function bindChatTermBanner(){
  bindChatMonitor();
  document.querySelectorAll('[data-chat-update]').forEach(b=>b.onclick=()=>{S.tab=workspace()&&!S.room.project.amendment?'Amendments':'Agreement';renderProject();});
  document.querySelectorAll('[data-chat-extension]').forEach(b=>b.onclick=()=>{workflowProposal('extension');$('workflow-milestone').value=b.dataset.chatExtension;});
  document.querySelectorAll('[data-chat-withdraw]').forEach(b=>b.onclick=()=>modal('Withdraw the chat change',`<p>Confirm that this change is no longer being adopted and the existing agreement remains in force. Both participants must confirm; one confirmation does not unblock the deal.</p><form id="chat-change-withdraw-form">${input('chat-withdraw-reason','Why are you withdrawing this change?','','text',true)}${input('chat-withdraw-name','Type your account name','','text',true,S.user.name)}<button class="primary">Confirm withdrawal</button></form>`,()=>{$('chat-change-withdraw-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api(url(`projects/${S.room.project.id}/chat-term-changes/${b.dataset.chatWithdraw}/withdraw`),{revision:S.room.revision,reason:$('chat-withdraw-reason').value,typed_name:$('chat-withdraw-name').value});$('dialog').close();await refreshWorkflow();});};}));
  document.querySelectorAll('[data-chat-record-confirm]').forEach(b=>b.onclick=()=>recordChatTerm(null,(S.room.project.recorded_chat_changes||[]).find(c=>c.record_id===b.dataset.chatRecordConfirm)));
}
function recordChatTerm(messageId,record=null){
  const fields=[],terms=S.room.agreement;
  function walk(value,path=''){
    if(Array.isArray(value)&&value.some(v=>typeof v==='object'))value.forEach((v,i)=>walk(v,path+'.'+i));
    else if(value&&typeof value==='object'&&!Array.isArray(value))Object.entries(value).forEach(([key,v])=>walk(v,path?path+'.'+key:key));
    else if(specFor(path))fields.push({value:path,label:(specFor(path).label||path)+' · '+path});
  }
  walk(terms);
  const message=S.room.messages.find(m=>m.id===(record?.message_id||messageId));
  modal('Record an agreed term change',`<p>${esc(message?.body||record?.body||'')}</p><p class="help">Choose the exact field and proposed value. Both must confirm this record, then update the agreement or addendum. Recording it does not edit or sign the agreement.</p><form id="chat-term-record-form">${select('chat-term-field','Agreement field',fields,record?.field_path||'payment.total_minor')}<label for="chat-term-value">Proposed value</label><textarea id="chat-term-value" required></textarea><p class="help" id="chat-term-format"></p>${input('chat-term-name','Type your account name','','text',true,S.user.name)}<button class="primary">Confirm exact chat change</button></form>`,()=>{
    const field=$('chat-term-field'),value=$('chat-term-value');
    if(record)field.value=record.field_path;
    function show(){const old=at(terms,field.value),proposed=record?.expected??old;value.value=field.value.endsWith('minor')?workflowMoneyInput(proposed,terms.payment.currency):Array.isArray(proposed)?proposed.join('\n'):String(proposed??'');$('chat-term-format').textContent=field.value.endsWith('minor')?'Amount in '+terms.payment.currency:Array.isArray(old)?'One item per line.':typeof old==='boolean'?'Enter true or false.':specFor(field.value)?.type==='date'?'Use YYYY-MM-DD.':'';}
    field.onchange=show;show();if(record){field.disabled=true;value.readOnly=true;}
    $('chat-term-record-form').onsubmit=e=>{e.preventDefault();run(async()=>{
      const old=at(terms,field.value),raw=value.value.trim();
      const proposed=record?record.expected:field.value.endsWith('minor')?workflowAmount(raw,terms.payment.currency):Array.isArray(old)?raw.split('\n').map(s=>s.trim()).filter(Boolean):typeof old==='number'?Number(raw):typeof old==='boolean'?raw==='true':raw;
      await api(url(`projects/${S.room.project.id}/chat/${record?.message_id||messageId}/term-change`),{revision:S.room.revision,field_path:field.value,expected:proposed,typed_name:$('chat-term-name').value});$('dialog').close();await refreshWorkflow();
    });};
  });
}

function chatMonitorFindings(){return (S.room.project.chat_monitor?.findings||[]).filter(f=>f.state!=='agreed'||JSON.stringify(at(S.room.agreement,f.field_path))!==JSON.stringify(f.expected));}
function chatMonitorBlocked(){const m=S.room.project.chat_monitor;return !!m&&(m.status!=='reviewed'||chatMonitorFindings().length>0);}
function chatMonitorBanner(){return aiOwnerBanner()+chatMonitorBody();}
function chatMonitorBody(){
  const m=S.room.project.chat_monitor;if(!m)return '';
  const findings=chatMonitorFindings();if(m.status==='reviewed'&&!findings.length){const flags=(S.room.flags||[]).filter(f=>f.severity==='BLOCKER');return m.draft_applied&&flags.length?`<section class="notice danger" aria-label="AI update validation issues"><strong>AI updated the draft ? corrections needed</strong><p>The agreed changes are saved. Correct these validation issues manually before sending:</p>${flags.map(f=>`<div class="read-field"><strong>${esc(specFor(f.field_path)?.label||f.field_path)}</strong><p>${esc(f.problem||f.message)}</p><p>${esc(f.solution||f.suggested_fix||'')}</p><button type="button" data-ai-blocker-field="${esc(f.field_path)}">Go to this field</button></div>`).join('')}</section>`:'';}
  const applicationError=m.application_error?`<p class="inline-error">${esc(typeof m.application_error==='string'?m.application_error:m.application_error.message||'Review the proposed change and payment locks.')}</p>`:'';
  const agreed=findings.length&&findings.every(f=>f.state==='agreed');
  return `<section class="notice" aria-label="Automatic chat review"><strong>${m.status==='pending'?'Checking chat terms?':agreed?'Agreement update drafted':'Chat terms need resolution'}</strong>${findings.map(f=>`<div class="read-field"><strong>${esc(specFor(f.field_path)?.label||f.field_path)}</strong><p>${esc(f.explanation)}</p>${f.state==='agreed'?`<p>Proposed: ${esc(f.field_path.endsWith('minor')?money(f.expected,S.room.agreement.payment.currency):typeof f.expected==='object'?JSON.stringify(f.expected):String(f.expected))}</p>`:`<span class="help">${esc(f.state==='disputed'?'Disagreement ? discuss and agree in chat.':'Waiting for a clear response from the other participant.')}</span>`}</div>`).join('')}${applicationError}${['unavailable','stale'].includes(m.status)?'<p>Automatic review could not finish or is out of date. Review will retry automatically.</p>':''}<p class="help">New commitments wait for resolved terms. Existing submitted-work payment rules remain active.</p><div class="actions">${agreed?'<button id="chat-monitor-propose">Review drafted agreement update</button>':''}</div></section>`;
}
function bindChatMonitor(){
 document.querySelectorAll('[data-ai-owner-approve]').forEach(b=>b.onclick=()=>run(async()=>{S.room=await api(url(`projects/${S.room.project.id}/ai-field-reviews/${b.dataset.aiOwnerApprove}/approve`),{revision:S.room.revision});renderProject();}));
  document.querySelectorAll('[data-ai-blocker-field]').forEach(b=>b.onclick=()=>goToField(b.dataset.aiBlockerField));
  if($('chat-monitor-recheck'))$('chat-monitor-recheck').onclick=()=>run(async()=>{await api(url(`projects/${S.room.project.id}/chat-monitor/recheck`),{revision:S.room.revision});await refreshWorkflow();});
  if($('chat-monitor-propose'))$('chat-monitor-propose').onclick=()=>modal('Review drafted agreement update',`<p>These changes will be placed in the corresponding agreement fields and sent through the normal approval and signature process. The signed agreement stays active until both sign.</p>${chatMonitorFindings().map(f=>`<div class="read-field"><strong>${esc(specFor(f.field_path)?.label||f.field_path)}</strong><p>Current: ${esc(f.field_path.endsWith('minor')?money(at(S.room.agreement,f.field_path),S.room.agreement.payment.currency):JSON.stringify(at(S.room.agreement,f.field_path)))}</p><p>Proposed: ${esc(f.field_path.endsWith('minor')?money(f.expected,S.room.agreement.payment.currency):JSON.stringify(f.expected))}</p></div>`).join('')}<button id="chat-monitor-send" class="primary">Propose this agreement update</button>`,()=>{$('chat-monitor-send').onclick=()=>run(async()=>{S.room=await api(url(`projects/${S.room.project.id}/chat-monitor/propose`),{revision:S.room.revision});const savedDraft=S.room.chat_draft_saved;S.dirty=false;$('dialog').close();S.tab=S.room.project.status==='AGREED'?'Resolution':'Agreement';await refreshWorkflow();if(savedDraft)toast('AI changes saved in your draft. Correct the highlighted date or amount issues, then send the counter.');});});
}

function aiOwnerBanner(){
 const records=(S.room.project.ai_field_reviews||[]).filter(r=>JSON.stringify(at(S.room.agreement,r.field_path))===JSON.stringify(r.expected));
 if(!records.length)return '';
 return `<section class="notice" aria-label="AI field approvals"><strong>AI-proposed changes: field approval</strong><p>Editing turn: ${esc(S.room.project.turn)}. Each field owner approves their changes below; either party may approve shared fields. Then both approve the full agreement.</p>${records.map(r=>`<div class="read-field"><strong>${esc(specFor(r.field_path)?.label||r.field_path)}</strong><p>Proposed: ${esc(agreementValue(r.expected,r.field_path))}</p>${r.approved_by?`<p>Approved by ${esc(r.approved_by.name)} (${esc(r.approved_by.role)})</p>`:`<p>Waiting for ${esc(r.owner==='both'?'either party':r.owner)} to review this change.</p>${['both',S.role].includes(r.owner)?`<button data-ai-owner-approve="${esc(r.id)}">Approve this field change</button>`:''}`}</div>`).join('')}<p>Full agreement approved by: ${esc((currentVersion()?.approvals||[]).map(a=>a.role).join(', ')||'neither party yet')}.</p></section>`;
}
