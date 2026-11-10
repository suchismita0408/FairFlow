"use strict";
const projectAssistantChats=new Map();
const flowIcons={
  attach:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m8 13 6-6a3 3 0 0 1 4 4l-8 8a5 5 0 0 1-7-7l9-9a6 6 0 0 1 8 8l-9 9"/></svg>',
  send:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m5 12 7-7 7 7M12 5v15"/></svg>'
};
// Escape first; only a small formatting vocabulary is allowed in model replies.
function flowAnswerHtml(content){
  const inline=text=>esc(text).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>');
  const blocks=[];let list=[];
  const flush=()=>{if(list.length){blocks.push('<ul class="flow-findings">'+list.join('')+'</ul>');list=[];}};
  for(const raw of String(content||'').split(/\r?\n/)){
    const line=raw.trim();
    if(!line){flush();continue;}
    const bullet=line.match(/^(?:[-*?]|\d+[.)])\s+(.+)/);
    if(bullet){
      const match=bullet[1].match(/^(Met|Missing|Not verified|Needs review|Next step)\s*:\s*(.*)/i);
      const tone=match?({'met':'met','missing':'missing','not verified':'pending','needs review':'pending','next step':'next'})[match[1].toLowerCase()]:'';
      list.push('<li>'+ (match?'<span class="flow-verdict flow-verdict-'+tone+'">'+esc(match[1])+'</span> '+inline(match[2]):inline(bullet[1]))+'</li>');continue;
    }
    flush();
    if(/^#{1,4}\s/.test(line))blocks.push('<h4>'+inline(line.replace(/^#{1,4}\s+/,''))+'</h4>');
    else blocks.push('<p>'+inline(line)+'</p>');
  }
  flush();
  // Preserve all findings, with long replies tucked behind an explicit disclosure.
  if(String(content).length>1500 && blocks.length>4){
    let cut=3;if(blocks[cut-1].startsWith('<h4>'))cut--;
    return '<div class="flow-answer">'+blocks.slice(0,cut).join('')+'<details class="flow-more"><summary>Read full answer</summary>'+blocks.slice(cut).join('')+'</details></div>';
  }
  return '<div class="flow-answer">'+blocks.join('')+'</div>';
}
function projectAssistantTab(container,projectId,role){
  const instance=Symbol();container.assistantInstance=instance;
  const current=()=>container.isConnected&&container.assistantInstance===instance;
  const $=id=>container.querySelector('#'+id);
  const key=S.user.id+':'+projectId;
  const session=projectAssistantChats.get(key)||{turns:[],busy:false,loading:false,question:'',files:[],links:[]};
  projectAssistantChats.set(key,session);
  container.innerHTML=`<div class="flow-chat"><div class="flow-toolbar" aria-label="Chat actions"><button type="button" id="assistant-clear" title="Clear conversation and attachments">Clear</button><button type="button" id="assistant-review" title="Review submitted work">Review work</button><button type="button" id="assistant-dispute" title="Review dispute evidence">Disputes</button><button type="button" id="assistant-attach" class="flow-icon" aria-label="Attach files or website links" aria-expanded="false" aria-controls="assistant-attachment-menu">${flowIcons.attach}</button></div><div id="assistant-attachment-menu" class="flow-attachment-menu" hidden><button type="button" id="assistant-upload">Upload PDF, image or text</button><input id="assistant-file-input" type="file" hidden multiple accept=".pdf,.png,.jpg,.jpeg,.webp,.txt,.md,.csv,.json,.html,.css,.js,.py,.xml"><form id="assistant-link-form"><label for="assistant-link">Website or document link</label><div class="flow-link-row"><input id="assistant-link" type="url" placeholder="https://example.com" maxlength="2000" required><button type="submit" id="assistant-add-link">Add</button></div></form><small>Up to 3 files, 4 MB each (5 MB total), and 3 public links. PDFs up to 30 pages.</small></div><div id="assistant-messages" class="flow-messages" role="log" aria-live="polite" aria-relevant="additions text"></div><div id="assistant-attachments" class="flow-attachments" aria-label="Attached files and links"></div><form id="assistant-form" class="flow-composer"><label class="flow-sr-only" for="assistant-question">Message Flow</label><textarea id="assistant-question" rows="1" maxlength="3000" placeholder="Ask Flow anything about this project..."></textarea><button type="submit" id="assistant-send" class="flow-send" aria-label="Send message" title="Send message">${flowIcons.send}</button></form><p class="flow-footnote">AI can make mistakes. Files are sent to Gemini for review.</p></div>`;
  function sourceLabel(id){
    const labels={'signed-agreement':'Signed agreement','current-accepted-terms':'Accepted terms','proposed-agreement':'Proposed agreement','your-private-draft':'Your draft','project-status':'Project status'};
    return labels[id]|| (id.startsWith('upload-')?'Attachment '+id.split('-').pop():id.startsWith('website-')?'Website '+id.split('-').pop():id.startsWith('payment-')?'Payment record':id.startsWith('case-')?'Dispute evidence':id.startsWith('request-')?'Change request':'Submitted evidence');
  }
  function paint(){
    if(!current())return;
    const log=$('assistant-messages');const atBottom=log.scrollHeight-log.scrollTop-log.clientHeight<60;
    log.innerHTML=session.turns.map(t=>`<article class="flow-message flow-${t.role}${t.error?' flow-error':''}"><span class="flow-speaker">${t.role==='user'?'You':'Flow'}</span><div class="flow-bubble">${t.role==='assistant'?flowAnswerHtml(t.content):`<p>${esc(t.content)}</p>`}${t.attached?.length?`<div class="flow-message-files">${t.attached.map(name=>`<span>${esc(name)}</span>`).join('')}</div>`:''}${t.inspections?.length?`<details><summary>What Flow inspected</summary>${t.inspections.map(i=>`<p><strong>${esc(i.name||i.url||i.id)}</strong><br>${esc(i.inspection)}${i.pages?' ? '+(Array.isArray(i.pages)?i.pages.length:i.pages)+' pages':''}${i.error?'<br>'+esc(i.error):''}${i.limitations?'<br>'+esc(i.limitations):''}</p>${(i.screenshots||[]).map(shot=>`<figure><img src="${esc(shot.data_url)}" alt="${esc(shot.label)}" style="max-width:100%;height:auto" loading="lazy"><figcaption>${esc(shot.label)}</figcaption></figure>`).join('')}`).join('')}</details>`:''}${t.sources?.length?`<details><summary>Sources</summary><ul>${t.sources.map(id=>`<li>${esc(sourceLabel(id))}</li>`).join('')}</ul></details>`:''}${t.limitations?.length?`<details><summary>Review limits</summary><p>${t.limitations.map(esc).join('\n')}</p></details>`:''}</div></article>`).join('')+(session.busy?'<div class="flow-thinking" role="status"><span></span><span></span><span></span> Flow is reviewing...</div>':'');
    if(!session.turns.length&&!session.busy)log.innerHTML='<div class="flow-welcome"><div class="flow-avatar">F</div><h3>Hi, I\'m Flow.</h3><p>Ask about your agreement, review work, or attach a document, image or website.</p></div>';
    for(const id of ['assistant-send','assistant-review','assistant-dispute','assistant-clear','assistant-upload','assistant-add-link'])$(id).disabled=session.busy||session.loading;
    $('assistant-attachments').innerHTML=[...session.files.map((f,i)=>`<span class="flow-chip">${esc(f.name)}<button type="button" data-remove-file="${i}" aria-label="Remove ${esc(f.name)}" ${session.busy?'disabled':''}>&times;</button></span>`),...session.links.map((link,i)=>`<span class="flow-chip">${esc(new URL(link).hostname)}<button type="button" data-remove-link="${i}" aria-label="Remove link ${i+1}" ${session.busy?'disabled':''}>&times;</button></span>`)].join('');
    container.querySelectorAll('[data-remove-file]').forEach(b=>b.onclick=()=>{session.files.splice(Number(b.dataset.removeFile),1);paint();});
    container.querySelectorAll('[data-remove-link]').forEach(b=>b.onclick=()=>{session.links.splice(Number(b.dataset.removeLink),1);paint();});
    if(atBottom||session.busy)log.scrollTop=log.scrollHeight;
  }
  session.render=paint;
  function error(message){session.turns.push({role:'assistant',content:message,error:true});session.render();}
  async function ask(question,review_mode='question'){
    if(session.busy||session.loading)return;
    question=question.trim()||'Please inspect the attached content and explain how it compares with the project requirements.';
    const history=session.turns.filter(t=>!t.error).slice(-12).map(t=>({role:t.role,content:t.content.slice(0,12000)}));
    const attachments=session.files.map(({name,content_base64})=>({name,content_base64}));
    const links=[...session.links];
    session.turns.push({role:'user',content:question,attached:[...attachments.map(f=>f.name),...links]});
    session.question='';$('assistant-question').value='';$('assistant-question').style.height='';
    $('assistant-attachment-menu').hidden=true;$('assistant-attach').setAttribute('aria-expanded','false');
    session.busy=true;paint();
    try{
      const reply=await api(`/api/${role}/projects/${projectId}/assistant`,{question,history,attachments,links,review_mode});
      session.turns.push({role:'assistant',content:reply.answer,...reply});
    }catch(e){session.turns.push({role:'assistant',content:e.message||'I could not complete that review. Please try again.',error:true});}
    finally{session.busy=false;session.render();}
  }
  $('assistant-form').onsubmit=e=>{e.preventDefault();if($('assistant-question').value.trim()||session.files.length||session.links.length)ask($('assistant-question').value);};
  $('assistant-question').value=session.question;
  $('assistant-question').oninput=e=>{session.question=e.target.value;e.target.style.height='auto';e.target.style.height=Math.min(e.target.scrollHeight,110)+'px';};
  $('assistant-question').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('assistant-form').requestSubmit();}};
  $('assistant-review').onclick=()=>ask('Review submitted work against the signed requirements. Show evidence for each finding and distinguish verified content from claims and untested functionality.','work');
  $('assistant-dispute').onclick=()=>ask('Review dispute evidence from both parties, identify evidence gaps and suggest a non-binding resolution for both to consider.','dispute');
  $('assistant-clear').onclick=()=>{session.turns=[];session.files=[];session.links=[];session.question='';$('assistant-question').value='';paint();};
  $('assistant-attach').onclick=()=>{const menu=$('assistant-attachment-menu');menu.hidden=!menu.hidden;$('assistant-attach').setAttribute('aria-expanded',String(!menu.hidden));};
  $('assistant-upload').onclick=()=>$('assistant-file-input').click();
  $('assistant-file-input').onchange=async e=>{
    const files=[...e.target.files];e.target.value='';
    if(session.busy||session.loading)return;
    if(files.length+session.files.length>3){error('Please attach at most 3 files.');return;}
    if(files.some(f=>!f.size||f.size>4*1024*1024)||files.reduce((n,f)=>n+f.size,0)+session.files.reduce((n,f)=>n+f.size,0)>5*1024*1024){error('Use files up to 4 MB each, with a combined size of 5 MB.');return;}
    session.loading=true;paint();
    try{
      const added=await Promise.all(files.map(file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({name:file.name,size:file.size,content_base64:String(reader.result).split(',')[1]});reader.onerror=()=>reject(Error('Could not read the selected file.'));reader.readAsDataURL(file);})));
      session.files.push(...added);
      if(current()){$('assistant-attachment-menu').hidden=true;$('assistant-attach').setAttribute('aria-expanded','false');}
    }catch(e){error(e.message);}finally{session.loading=false;session.render();}
  };
  $('assistant-link-form').onsubmit=e=>{
    e.preventDefault();if(session.busy)return;
    const value=$('assistant-link').value.trim();
    try{const link=new URL(value);if(!['https:','http:'].includes(link.protocol)||link.username||link.password)throw Error();}catch{error('Please enter a public HTTP or HTTPS link.');return;}
    if(session.links.length>=3){error('Please attach at most 3 links.');return;}
    if(!session.links.includes(value))session.links.push(value);
    $('assistant-link').value='';$('assistant-attachment-menu').hidden=true;$('assistant-attach').setAttribute('aria-expanded','false');paint();
  };
  paint();
}

let assistantWidgetKey='', assistantWidgetGeneration=0, assistantWidgetUser=null;
function closeAssistant(){
  document.getElementById('assistant-panel').hidden=true;
  const launcher=document.getElementById('assistant-launcher');
  launcher.setAttribute('aria-expanded','false');launcher.focus();
}
function openAssistant(){
  syncAssistantWidget(true);
  document.getElementById('assistant-panel').hidden=false;
  document.getElementById('assistant-launcher').setAttribute('aria-expanded','true');
  document.getElementById('assistant-close').focus();
}
function syncAssistantWidget(force=false){
  if(!document.getElementById('assistant-launcher')){
    document.body.insertAdjacentHTML('beforeend',`<button id="assistant-launcher" aria-label="Ask Flow, your project assistant" aria-controls="assistant-panel" aria-expanded="false" title="Ask Flow"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5a9.5 9.5 0 0 1 19 0Z"/><path d="M7 10h10M7 14h6"/></svg><span class="assistant-launcher-label">Ask Flow</span></button><section id="assistant-panel" role="dialog" aria-modal="false" aria-labelledby="assistant-panel-title" hidden><button id="assistant-resize" aria-label="Resize chat window" title="Drag to resize. Use arrow keys when focused."><span aria-hidden="true">&#10530;</span></button><header class="assistant-panel-header"><div><strong id="assistant-panel-title">Flow &middot; Project assistant</strong><small id="assistant-project-name"></small></div><button id="assistant-close" aria-label="Close assistant">&#215;</button></header><div id="assistant-panel-body"></div></section>`);
    document.getElementById('assistant-launcher').onclick=openAssistant;
    document.getElementById('assistant-close').onclick=closeAssistant;
    setupAssistantResize();
    document.getElementById('assistant-panel').addEventListener('keydown',e=>{if(e.key==='Escape'){e.stopPropagation();closeAssistant();}});
  }
  const uid=S.user?.id||'';
  if(assistantWidgetUser!==uid){projectAssistantChats.clear();assistantWidgetKey='';assistantWidgetUser=uid;}
  const pid=S.room?.project.id||'';
  const key=uid+':'+pid+':'+S.role;
  if(key===assistantWidgetKey&&!force)return;
  // Opening the same project should preserve the existing composer and scroll position.
  if(key===assistantWidgetKey&&document.getElementById('assistant-form'))return;
  assistantWidgetKey=key;
  const generation=++assistantWidgetGeneration;
  const body=document.getElementById('assistant-panel-body');
  const title=document.getElementById('assistant-project-name');
  body.assistantInstance=null;body.replaceChildren();
  if(!uid){title.textContent='Welcome';body.innerHTML='<p>Sign in to ask about your projects, agreements, and submitted work.</p>';return;}
  if(pid){
    title.textContent=at(S.room.agreement,'basics.title')||'Current project';
    projectAssistantTab(body,pid,S.role);return;
  }
  title.textContent='Choose a project';
  body.innerHTML='<p>Select a project to ask about its agreement, deadlines, payments, or work.</p><p role="status">Loading your projects...</p>';
  const role=S.role;
  api(`/api/${role}/projects`).then(projects=>{
    if(generation!==assistantWidgetGeneration)return;
    body.innerHTML='<label for="assistant-project-picker">Project</label><select id="assistant-project-picker"><option value="">Choose a project</option>'+projects.map(p=>`<option value="${esc(p.id)}">${esc(p.title||p.id.slice(0,8))}</option>`).join('')+'</select><div id="assistant-selected-chat"></div>';
    if(!projects.length){body.innerHTML='<p>Your assistant will be available when you create or join a project.</p>';return;}
    document.getElementById('assistant-project-picker').onchange=e=>{
      const chat=document.getElementById('assistant-selected-chat');
      chat.assistantInstance=null;chat.replaceChildren();if(e.target.value)projectAssistantTab(chat,e.target.value,role);
    };
  }).catch(()=>{if(generation===assistantWidgetGeneration)body.innerHTML='<p>Could not load projects. Close and reopen the assistant to retry.</p>';});
}

function setupAssistantResize(){
  const panel=document.getElementById('assistant-panel');
  const handle=document.getElementById('assistant-resize');
  let drag=null;
  function resize(width,height){
    const style=getComputedStyle(panel);
    const maxWidth=Math.max(120,window.innerWidth-parseFloat(style.right)-12);
    const maxHeight=Math.max(120,window.innerHeight-parseFloat(style.bottom)-12);
    panel.style.width=Math.min(maxWidth,Math.max(Math.min(300,maxWidth),width))+'px';
    panel.style.height=Math.min(maxHeight,Math.max(Math.min(280,maxHeight),height))+'px';
  }
  handle.onpointerdown=e=>{
    if(e.button!==0)return;
    e.preventDefault();
    const rect=panel.getBoundingClientRect();
    drag={x:e.clientX,y:e.clientY,width:rect.width,height:rect.height};
    handle.setPointerCapture(e.pointerId);
  };
  handle.onpointermove=e=>{if(drag)resize(drag.width+drag.x-e.clientX,drag.height+drag.y-e.clientY);};
  handle.onpointerup=handle.onpointercancel=handle.onlostpointercapture=()=>{drag=null;};
  handle.onkeydown=e=>{
    if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;
    e.preventDefault();const rect=panel.getBoundingClientRect();const step=e.shiftKey?40:16;
    resize(rect.width+(e.key==='ArrowLeft'?step:e.key==='ArrowRight'?-step:0),rect.height+(e.key==='ArrowUp'?step:e.key==='ArrowDown'?-step:0));
  };
  window.addEventListener('resize',()=>{if(panel.style.width)resize(parseFloat(panel.style.width),parseFloat(panel.style.height));});
}
