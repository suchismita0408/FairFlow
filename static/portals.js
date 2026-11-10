'use strict';
const $=id=>document.getElementById(id), esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const S={role:location.pathname.split('/')[1]==='freelancer'?'freelancer':'client',user:null,csrf:'',schema:null,room:null,tab:'Overview',section:'basics',dirty:false,saving:false,lastActivity:Date.now(),events:null,poll:null};
const terminal=['DECLINED','WITHDRAWN','EXPIRED','CANCELLED_BEFORE_AGREEMENT'];
const names={basics:'Project basics',parties:'Parties',scope:'Scope of work',timeline:'Timeline',payment:'Budget and payment terms',revisions:'Revisions and change orders',ip:'Intellectual property',confidentiality:'Confidentiality and tooling',communication:'Communication',termination:'Termination and disputes',attachments:'Attachments'};
const label=v=>String(v||'').replaceAll('_',' ').replaceAll('-',' '),pill=v=>`<span class="pill ${v==='AGREED'?'green':''}">${esc(label(v))}</span>`;
const url=path=>`/api/${S.role}/${path}`;
const at=(obj,path)=>path.split('.').reduce((o,k)=>o?.[k],obj);
function put(obj,path,value){const parts=path.split('.');let o=obj;for(const k of parts.slice(0,-1))o=o[k]??(o[k]={});o[parts.at(-1)]=value;}
function toast(message,isError=false){
  const target=$('toast');
  ($('dialog').open?$('dialog'):document.body).append(target);
  target.classList.toggle('toast-error',isError);
  target.setAttribute('role',isError?'alert':'status');
  target.textContent=message;
  clearTimeout(S.toast);
  S.toast=setTimeout(()=>target.textContent='',5000);
}
function fail(error){toast(error.message||String(error),true);}
async function api(path,body,method){
  const r=await fetch(path,{method:method||(body===undefined?'GET':'POST'),credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':S.csrf},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await r.json();
  if(!r.ok){
    if(r.status===401&&S.user){persist();sessionStorage.setItem('fairflow-return',location.pathname);S.user=null;stopUpdates();go(`/${S.role}/login`);}
    if(r.status===403&&data.detail?.redirect){location.assign(data.detail.redirect);}
    if(data.detail?.flags&&S.room){S.room.flags=data.detail.flags;updateFlags();}
    const flagReasons=(data.detail?.flags||[]).filter(f=>f.severity==='BLOCKER').map(f=>(f.problem||f.message)+(f.solution||f.suggested_fix?' '+(f.solution||f.suggested_fix):''));
    throw Error(typeof data.detail==='string'?data.detail:[data.detail?.message,...flagReasons].filter(Boolean).join('\n\n')||JSON.stringify(data.detail));
  }return data;
}
async function run(fn){$('error').textContent='';try{await fn();}catch(e){fail(e);}}
function go(path){history.pushState({},'',path);route();}
document.addEventListener('click',async e=>{S.lastActivity=Date.now();const a=e.target.closest('a[data-route]');if(a){e.preventDefault();if(a.dataset.notice&&S.user){try{await api(url(`notifications/${encodeURIComponent(a.dataset.notice)}/read`),{});}catch{} }go(a.getAttribute('href'));}});
document.addEventListener('input',()=>S.lastActivity=Date.now());
window.addEventListener('popstate',()=>route());
window.addEventListener('offline',()=>{$('connection').textContent='You are offline. Your draft is saved on this device; reconnect to send or sign.';persist();});
window.addEventListener('online',()=>{$('connection').textContent='';if(S.dirty)run(saveDraft);});
$('theme').onclick=()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';localStorage.setItem('fairflow-theme',document.documentElement.dataset.theme);};
document.documentElement.dataset.theme=localStorage.getItem('fairflow-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
$('dialog-close').onclick=()=>$('dialog').close();
function modal(title,html,fn){$('dialog-content').innerHTML=`<h2 id="dialog-title">${esc(title)}</h2>${html}`;$('dialog').showModal();if(fn)fn();}
function frame(){
  $('portal-label').textContent=S.role==='client'?'CLIENT PORTAL':'FREELANCER PORTAL';
  $('breadcrumb').textContent=`${S.role==='client'?'Client':'Freelancer'} / ${S.room?'Project room':'Workspace'}`;
  $('navigation').innerHTML=S.user?`<a data-route href="/${S.role}/dashboard">My projects</a>${S.role==='freelancer'?'<a data-route href="/freelancer/inbox">Invitation inbox</a>':''}<a data-route href="/${S.role}/notifications">Notifications <span id="notification-count" class="notification-count" hidden></span></a><a data-route href="/${S.role}/profile">Profile & sessions</a><a data-route href="/${S.role}/mail">${S.emailMode==='local'?'Local email preview':'Verify email'}</a>${S.user.support_reviewer?`<a data-route href="/${S.role}/support">Support queue</a>`:''}<a href="#" id="logout">Sign out</a>`:`<a data-route href="/client/login">Client portal</a><a data-route href="/freelancer/login">Freelancer portal</a>`;
  paintNotificationCount();
  $('identity').textContent=S.user?`${S.user.name} · ${S.user.email}`:'One account. One fixed role.';
  if($('logout'))$('logout').onclick=e=>{e.preventDefault();run(async()=>{persist();await api('/api/auth/logout',{});S.user=null;S.notices=[];go(`/${S.role}/login`);});};
}
function input(id,title,value='',type='text',required=true,help=''){return `<div class="field"><label for="${id}">${esc(title)}${required?' *':''}</label><input id="${id}" type="${type}" value="${esc(value)}" ${required?'required':''} ${type==='password'?'minlength="12" autocomplete="new-password"':''}><div class="help" id="${id}-help">${esc(help)}</div></div>`;}
function select(id,title,options,value='',required=true){return `<div class="field"><label for="${id}">${esc(title)}${required?' *':''}</label><select id="${id}" ${required?'required':''}>${options.map(o=>`<option value="${esc(o.value??o)}" ${(o.value??o)===value?'selected':''}>${esc(o.label??label(o))}</option>`).join('')}</select></div>`;}
function authForm(mode='login'){
  const signup=mode==='signup';frame();
  $('content').innerHTML=`<div class="auth-layout"><div class="eyebrow">CLEAR SCOPE. SHARED CONFIDENCE.</div><h1>${signup?'Start with clear terms.':'Welcome back.'}</h1><p class="muted">${S.role==='client'?'Create a brief, agree the details, and work with confidence.':'Review your brief, negotiate the details, and sign on your terms.'}</p><div class="role-tabs"><a data-route class="${S.role==='client'?'active':''}" href="/client/${mode}">Client</a><a data-route class="${S.role==='freelancer'?'active':''}" href="/freelancer/${mode}">Freelancer</a></div><div class="card"><h2>${signup?'Create your '+S.role+' account':'Sign in to your '+S.role+' portal'}</h2><form id="auth-form">${signup?input('name','Full name'):''}${input('email','Email','','email')}${input('password','Password','','password',true,'Use at least 12 characters.')}${signup?`${S.role==='client'?select('account-type','Account type',['individual','company'])+input('company-name','Company name','','text',false,'Required for company accounts.'):input('professional-title','Professional title')+select('primary-category','Primary category',['Design','Frontend','Backend','Mobile','Writing','Video','Marketing','Data','Other'])+input('skills','Skills (comma separated)')+select('experience-band','Years of experience',['0-1','1-3','3-5','5-10','10+'])+input('portfolio-link','Portfolio link','','url',false)}<div class="form-grid">${input('country','Country ISO code','IN','text',true,'For example IN, US, GB.')}${input('time-zone','IANA time zone',Intl.DateTimeFormat().resolvedOptions().timeZone)}</div>`:''}<button class="primary full" type="submit">${signup?'Create '+S.role+' account':'Sign in'}</button></form><p><a data-route href="/${S.role}/${signup?'login':'signup'}">${signup?'Already have an account? Sign in':'Create an account'}</a></p><button id="forgot" class="quiet">Forgot password?</button></div></div>`;
  $('auth-form').onsubmit=e=>{e.preventDefault();run(async()=>{
    const body={email:$('email').value,password:$('password').value};
    if(signup){Object.assign(body,{name:$('name').value,country:$('country').value,time_zone:$('time-zone').value});if(S.role==='client')Object.assign(body,{account_type:$('account-type').value,company_name:$('company-name').value});else Object.assign(body,{professional_title:$('professional-title').value,primary_category:$('primary-category').value,skills:$('skills').value.split(',').map(x=>x.trim()).filter(Boolean),experience_band:$('experience-band').value,portfolio_link:$('portfolio-link').value||null});}
    const r=await api(`/api/${S.role}/${signup?'signup':'login'}`,body);S.user=r.user;S.csrf=r.csrf_token;S.emailMode=(await api('/api/auth/me')).email_mode;
    const back=sessionStorage.getItem('fairflow-return');sessionStorage.removeItem('fairflow-return');go(signup?`/${S.role}/mail`:back||`/${S.role}/dashboard`);
  });};
  $('forgot').onclick=()=>modal('Reset your password',`<form id="reset-form">${input('reset-email','Account email',$('email').value,'email')}<button class="primary">Send reset link</button></form>`,()=>{$('reset-form').onsubmit=e=>{e.preventDefault();run(async()=>{const r=await api('/api/auth/forgot-password',{email:$('reset-email').value});$('dialog').close();toast(r.message);});};});
}
async function mail(){
  if(S.emailMode!=='local'){$('content').innerHTML='<div class="card"><h1>Check your inbox</h1><p>Open your verification link to continue.</p><button id="resend">Resend verification</button></div>';$('resend').onclick=()=>run(async()=>{await api('/api/auth/resend-verification',{});toast('Verification message queued.');});return;}
  const messages=await api('/api/auth/mail');$('content').innerHTML=`<div class="page-top"><div><div class="eyebrow">DEVELOPMENT EMAIL OUTBOX</div><h1>Local email preview</h1><p>Demo verification only: messages are stored locally. This does not prove ownership of a real email address. Configure SMTP for inbox verification.</p></div></div>${messages.length?messages.slice().reverse().map((m,i)=>`<div class="card"><h2>${esc(m.subject)}</h2><p class="muted">${esc(m.created_at)}</p><p>${esc(m.body)}</p>${m.body.includes('#verify=')?`<button class="primary" data-verify="${esc(m.body.split('#verify=')[1])}">Verify email</button>`:''}${m.body.includes('#reset=')?`<button data-reset="${esc(m.body.split('#reset=')[1])}">Set a new password</button>`:''}</div>`).join(''):'<div class="card empty">No messages yet.</div>'}`;
  document.querySelectorAll('[data-verify]').forEach(b=>b.onclick=()=>run(async()=>{await api('/api/auth/verify',{token:b.dataset.verify});S.user.verified=true;go(`/${S.role}/dashboard`);}));
  document.querySelectorAll('[data-reset]').forEach(b=>b.onclick=()=>resetForm(b.dataset.reset));
}
function resetForm(token){modal('Choose a new password',`<form id="new-password-form">${input('new-password','New password','','password')}<button class="primary">Update password</button></form>`,()=>{$('new-password-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api('/api/auth/reset-password',{token,password:$('new-password').value});$('dialog').close();S.user=null;S.notices=[];go(`/${S.role}/login`);toast('Password changed. Sign in again.');});};});}
async function dashboard(){
  const deleted=new URLSearchParams(location.search).get('deleted')==='1'&&S.role==='client';
  const projects=(await api(url('projects'+(deleted?'?include_deleted=true':'')))).filter(p=>!deleted||p.hidden_by_client);
  const cards=S.role==='client'?[['Drafts',p=>p.status==='DRAFT'],['Awaiting freelancer',p=>p.status==='INVITED'],['Needs my response',p=>p.turn===S.role&&['IN_NEGOTIATION','AMENDMENT_PROPOSED','READY_TO_SIGN','PARTIALLY_SIGNED'].includes(p.status)],['Active',p=>p.has_active_agreement&&!p.completed],['Completed',p=>p.completed]]:[['New invitations',p=>p.status==='INVITED'],['Needs my response',p=>p.turn===S.role&&p.status==='IN_NEGOTIATION'],['In negotiation',p=>['IN_NEGOTIATION','AMENDMENT_PROPOSED','READY_TO_SIGN','PARTIALLY_SIGNED'].includes(p.status)],['Active',p=>p.has_active_agreement&&!p.completed],['Completed',p=>p.completed]];
  $('content').innerHTML=`<div class="page-top"><div><div class="eyebrow">YOUR ${S.role.toUpperCase()} WORKSPACE</div><h1>Your work, on fair terms.</h1></div>${S.role==='client'?'<button class="primary" id="new-project">＋ New project</button>':`<a data-route href="/freelancer/inbox">Open invitation inbox →</a>`}</div><div class="kpis">${cards.map(([name,test])=>`<div class="kpi"><span>${name}</span><strong>${projects.filter(test).length}</strong></div>`).join('')}</div><div class="card"><div class="split-title"><h2>${deleted?'Archived projects':'Projects'} <span class="muted">${projects.length}</span></h2>${S.role==='client'?`<a data-route href="/client/dashboard${deleted?'':'?deleted=1'}">${deleted?'Back to projects':'Archived projects'}</a>`:''}</div>${projects.length?`<div class="table-wrap"><table><thead><tr><th>Project</th><th>Counterpart</th><th>Status</th><th>Next action</th><th>Last activity</th>${S.role==='client'?'<th>Actions</th>':''}</tr></thead><tbody>${projects.map(p=>`<tr><td><a data-route href="/${S.role}/projects/${p.id}">${esc(p.title)}</a></td><td>${esc(p.counterpart)}</td><td>${pill(p.completed?'COMPLETED':p.status)}</td><td>${p.status==='AGREED'?'Open workspace':p.status==='DRAFT'?'Finish your brief':p.turn===S.role?'Review and respond':'Waiting for counterpart'}</td><td>${new Date(p.updated_at).toLocaleDateString()}</td>${S.role==='client'?`<td><button class="quiet" data-project-delete="${p.id}" data-restore="${!!p.hidden_by_client}">${p.hidden_by_client?'Restore':p.status==='DRAFT'?'Delete draft':'Archive'}</button></td>`:''}</tr>`).join('')}</tbody></table></div>`:'<div class="empty"><h2>Your next collaboration starts here.</h2><p>'+ (S.role==='client'?'Create a project and invite a freelancer you know.':'Invitations from your clients will appear in your inbox.')+'</p></div>'}</div>`;
  if($('new-project'))$('new-project').onclick=()=>go('/client/new');
  document.querySelectorAll('[data-project-delete]').forEach(b=>b.onclick=()=>run(async()=>{const r=await api(url('projects/'+b.dataset.projectDelete));if(b.dataset.restore==='true'){await api(url(`projects/${r.project.id}/actions`),{revision:r.revision,action:'restore'});await dashboard();}else await removeProject(r.project.id,r.revision,r.project.status==='DRAFT');}));
}
async function newProject(){
  const r=await api(url('projects'),{agreement:{}});go(`/client/projects/${r.project.id}`);
}
async function inbox(){
  const items=await api(url('inbox'));
  $('content').innerHTML=`<div class="page-top"><div><div class="eyebrow">FREELANCER INBOX</div><h1>New invitations</h1><p>Opening a brief starts negotiation. Review every term before approving.</p></div></div>${items.length?items.map(i=>`<div class="card"><h2>Project invitation</h2><p>Expires ${new Date(i.expires_at).toLocaleString()}</p><button class="primary" data-open-invite="${i.id}">Open project brief</button></div>`).join(''):'<div class="card empty"><h2>You’re all caught up.</h2><p>Invite a client to send a brief to your account email or platform handle.</p></div>'}`;
  document.querySelectorAll('[data-open-invite]').forEach(b=>b.onclick=()=>run(async()=>{const r=await api(url(`invitations/${b.dataset.openInvite}/open`),{});S.tab='Overview';go(`/freelancer/projects/${r.project.id}`);}));
}
function paintNotificationCount(){
  const badge=$('notification-count');if(!badge)return;
  const count=(S.notices||[]).filter(n=>!n.read).length;
  badge.textContent=String(count);badge.hidden=!count;
}
async function refreshNotifications(){
  if(!S.user?.verified||S.notificationBusy||Date.now()-S.lastActivity>29*60000)return;
  S.notificationBusy=true;
  try{
    const items=await api(url('notifications'));
    const before=S.notices;
    S.notices=items;paintNotificationCount();
    if(before){const seen=new Map(before.map(n=>[n.id,n.created_at]));const fresh=items.find(n=>!n.read&&seen.get(n.id)!==n.created_at);if(fresh)toast(fresh.kind==='chat_message'?'New chat message. Open Notifications to read it.':fresh.body);}
  }catch{}finally{S.notificationBusy=false;}
}
setInterval(refreshNotifications,8000);
window.addEventListener('focus',()=>refreshNotifications());
async function notifications(){
  const items=await api(url('notifications'));S.notices=items;paintNotificationCount();
  $('content').innerHTML=`<div class="page-top"><h1>Notifications</h1></div>${items.length?items.slice().sort((a,b)=>b.created_at.localeCompare(a.created_at)).map(i=>`<div class="card notification-card ${i.read?'':'notification-unread'}"><div class="split-title"><h2>${i.kind==='chat_message'?'New chat message':esc(label(i.kind))}</h2>${i.read?'<span class="pill">Read</span>':'<span class="pill">Unread</span>'}</div><p>${esc(i.body)}</p><small>${esc(uiDate(i.created_at,true))}</small><p><a data-route data-notice="${esc(i.id)}" href="/${S.role}/projects/${i.project_id}?tab=Chat">${i.kind==='chat_message'?'Open conversation':'Open project chat'}</a> ${i.read?'':`<button class="quiet" data-mark-notice="${esc(i.id)}">Mark as read</button>`}</p></div>`).join(''):'<div class="card empty">No notifications yet.</div>'}`;
  document.querySelectorAll('[data-mark-notice]').forEach(button=>button.onclick=()=>run(async()=>{
    button.disabled=true;
    try{
      await api(url(`notifications/${encodeURIComponent(button.dataset.markNotice)}/read`),{});
      const notice=(S.notices||[]).find(n=>n.id===button.dataset.markNotice);if(notice)notice.read=true;
      paintNotificationCount();
      const card=button.closest('.notification-card');card.classList.remove('notification-unread');card.querySelector('.pill').textContent='Read';button.remove();
    }finally{button.disabled=false;}
  }));
}
async function removeProject(pid,revision,isDraft){
  modal(isDraft?'Delete draft':'Archive project',`<p>${isDraft?'Delete this unsent draft?':'Remove this project from your list? You can restore it from Archived projects. The freelancer keeps access; the agreement, chat and existing payment rules stay in effect.'}</p><button class="danger" id="delete-project-confirm">${isDraft?'Delete draft':'Archive project'}</button>`,()=>{
    $('delete-project-confirm').onclick=()=>run(async()=>{const button=$('delete-project-confirm');button.disabled=true;try{await api(url(`projects/${pid}/actions`),{revision,action:'delete'});$('dialog').close();S.dirty=false;go(`/${S.role}/dashboard`);}finally{button.disabled=false;}});
  });
}

async function profile(){
  const [data,sessions]=await Promise.all([api(url('profile')),api('/api/auth/sessions')]);
  $('content').innerHTML=`<div class="page-top"><div><h1>Your profile</h1><p>${esc(S.user.email)} · ${S.role} account · Role is fixed at signup</p></div></div><div class="workspace-grid"><div class="card"><h2>${esc(S.user.name)}</h2><p>Platform handle: <strong>@${esc(S.user.handle)}</strong></p>${Object.entries(data.profile).map(([k,v])=>`<div class="read-field"><strong>${esc(label(k))}</strong>${esc(Array.isArray(v)?v.join(', '):v)}</div>`).join('')}${S.role==='freelancer'?'<button id="edit-profile" class="spaced">Edit professional details</button>':''}</div><div class="card"><h2>Active sessions</h2>${sessions.map(s=>`<div class="read-field"><strong>${s.current?'This session':'Other session'}</strong>${esc(s.ip)} · ${esc(s.user_agent)}<br><small>Last active: ${esc(s.last_active_at)}</small></div>`).join('')}<button class="danger spaced" id="logout-everywhere">Log out everywhere</button></div></div>`;
  $('logout-everywhere').onclick=()=>run(async()=>{await api('/api/auth/logout-everywhere',{});S.user=null;S.notices=[];go(`/${S.role}/login`);});
  if(S.role==='freelancer'){
    const recipient=await api(url('payout-recipient'));
    $('content').insertAdjacentHTML('beforeend',`<div class="card"><h2>PayPal Sandbox payout recipient</h2><p>${recipient.email?esc(recipient.email):'No recipient configured.'}</p><button id="profile-payout-recipient">Set Sandbox recipient</button></div>`);
    $('profile-payout-recipient').onclick=()=>recipientDialog(recipient.email,profile);
  }
  if($('edit-profile'))$('edit-profile').onclick=()=>{const p=data.profile;modal('Professional details',`<form id="profile-form">${input('p-title','Professional title',p.professional_title)}${input('p-category','Primary category',p.primary_category)}${input('p-skills','Skills (comma separated)',p.skills.join(', '))}${select('p-band','Experience band',['0-1','1-3','3-5','5-10','10+'],p.experience_band)}${input('p-country','Country',p.country)}${input('p-zone','Time zone',p.time_zone)}${input('p-portfolio','Portfolio URL',p.portfolio_link||'','url',false)}<button class="primary">Save profile</button></form>`,()=>{$('profile-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api(url('profile'),{professional_title:$('p-title').value,primary_category:$('p-category').value,skills:$('p-skills').value.split(',').map(x=>x.trim()).filter(Boolean),experience_band:$('p-band').value,country:$('p-country').value,time_zone:$('p-zone').value,portfolio_link:$('p-portfolio').value||null},'PUT');$('dialog').close();await profile();});};});};
}
function draftKey(){return S.user&&S.room?`fairflow-draft:${S.user.id}:${S.room.project.id}`:null;}
function persist(){const key=draftKey();if(key&&S.dirty)localStorage.setItem(key,JSON.stringify({agreement:S.room.agreement,revision:S.room.revision}));}
async function saveDraft(){
  if(!S.room?.can_edit||!S.dirty||S.saving)return;
  const invalid=document.querySelector('[data-field]:invalid');if(invalid){invalid.reportValidity();throw Error('Correct the highlighted field before saving.');}
  S.saving=true;const payload=JSON.parse(JSON.stringify(S.room.agreement));
  try{const r=await api(url(`projects/${S.room.project.id}/draft`),{revision:S.room.revision,agreement:payload},'PUT');
    const more=JSON.stringify(S.room.agreement)!==JSON.stringify(payload);S.room.revision=r.revision;S.room.flags=r.flags;S.room.activity=r.activity;
    if(!more){S.room=r;S.dirty=false;localStorage.removeItem(draftKey());}else persist();
    if($('save-status'))$('save-status').textContent=more?'Unsaved changes':'All changes saved';updateFlags();
  }finally{S.saving=false;}
}
setInterval(()=>{if(S.dirty&&navigator.onLine)run(saveDraft);},10000);
window.addEventListener('beforeunload',()=>persist());
async function openProject(pid){
  S.room=await api(url('projects/'+pid));S.dirty=false;S.section='basics';
  S.assurance=S.room.project.active_agreement_id?await api(url(`projects/${pid}/assurance`)):null;
  await loadWorkflow();
  await reconcilePlatformReturn();
  const local=localStorage.getItem(draftKey());if(local&&S.room.can_edit){try{const saved=JSON.parse(local);S.room.agreement=saved.agreement;S.dirty=true;toast(saved.revision===S.room.revision?'Restored your unsaved draft.':'Restored device draft. Review against the latest version before saving.');}catch{localStorage.removeItem(draftKey());}}
  renderProject();startUpdates();
}
function currentVersion(){return S.room?.versions.slice().sort((a,b)=>b.number-a.number)[0];}
function workspace(){return !!S.room?.project.active_agreement_id;}
const fieldTitles={'timeline.start_date':'Project start date','timeline.deadline':'Final completion deadline (including review and revisions)','payment.total_minor':'Total project price','payment.currency':'Project currency','due_date':'Milestone delivery date','review_days':'Client review days (1-5)','revision_rounds':'Included revision rounds','turnaround_days':'Days to complete each revision','amount_minor':'Milestone price'};
Object.assign(fieldTitles,{
  'auto_accept_days':'Automatic release review period',
  'auto_accept':'Automatic release after review',
  'timeline.milestones':'Milestone delivery and payment schedule',
  'scope.deliverables':'Work the freelancer will deliver',
  'scope.inclusions':'Work included in the agreed price',
  'scope.exclusions':'Work excluded from the agreed price',
  'scope.dependencies':'What the client must provide',
  'revisions.definition':'What counts as an included revision',
  'revisions.not_revision':'Changes that require a new quote',
  'revisions.change_rate_minor':'Price for additional work',
  'revisions.extra_round_minor':'Price per extra revision round',
  'payment.due_trigger':'When payment is released',
  'payment.deposit_percent':'Upfront deposit (% of total price)',
  'timeline.grace_days':'Extra days allowed for late delivery',
  'timeline.client_delay_extension':'How client delays affect delivery dates'
});
function fieldTitle(path){return fieldTitles[path]||fieldTitles[path.split('.').at(-1)]||specFor(path)?.label||label(path.split('.').at(-1));}
function fieldLocation(path,terms=S.room.agreement){
  const parts=path.split('.'),root=names[parts[0]]||parts[0];
  const row=/^\d+$/.test(parts[2]||'')?at(terms,parts.slice(0,3).join('.')):null;
  const context=row?`${specFor(parts.slice(0,2).join('.'))?.label||label(parts[1])} ${Number(parts[2])+1}${row.title||row.name?' · '+(row.title||row.name):''}`:'';
  return [root,context,fieldTitle(path)].filter(Boolean).join(' › ');
}
function fieldNavigationOptions(){
  const paths=[];
  for(const [section,fields] of Object.entries(S.schema.fields))for(const [key,spec] of Object.entries(fields)){
    const path=section+'.'+key;
    if(spec.children){(at(S.room.agreement,path)||[]).forEach((row,i)=>Object.keys(spec.children).forEach(child=>paths.push(path+'.'+i+'.'+child)));}
    else paths.push(path);
  }
  return paths.map(path=>`<option value="${esc(path)}">${esc(fieldLocation(path))}</option>`).join('');
}
function goToField(path){
  S.section=Object.entries(compactGroups).find(([,sections])=>sections.includes(path.split('.')[0]))?.[0]||'basics';
  S.tab='Agreement';renderProject();
  let target=$('field-'+path.replaceAll('.','-'));
  if(!target){
    const exported=workspace()&&!S.room.project.amendment?S.room.active_rendered:S.room.rendered;
    const ref=exported?.clause_map[path];target=ref?$('clause-'+ref):null;
  }
  if(!target){
    const parent=path.split('.');while(!target&&parent.length){parent.pop();target=$('field-'+parent.join('-'));}
  }
  if(!target){toast('Open '+(names[path.split('.')[0]]||'the agreement')+' to review this field.');return;}
  for(let node=target.parentElement;node;node=node.parentElement){if(node.tagName==='DETAILS')node.open=true;}
  if(target.tagName==='DETAILS')target.open=true;
  target.hidden=false;target.classList.add('field-jump-highlight');
  if(!target.matches('input,select,textarea,button,a'))target.tabIndex=-1;
  requestAnimationFrame(()=>{target.focus({preventScroll:true});target.scrollIntoView({block:'center',behavior:'smooth'});});
  setTimeout(()=>target.classList.remove('field-jump-highlight'),3500);
}
function amendmentSummary(){
  const r=S.room,old=activeTerms(),proposed=r.agreement;
  const flatten=(value,path='',out={})=>{if(value&&typeof value==='object'){for(const [k,v] of Object.entries(value))flatten(v,path?path+'.'+k:k,out);}else out[path]=value;return out;};
  const previous=flatten(old),next=flatten(proposed);
  const paths=[...new Set([...Object.keys(previous),...Object.keys(next)])].filter(path=>!path.startsWith('acknowledgments.')&&JSON.stringify(previous[path])!==JSON.stringify(next[path]));
  const display=(path,value,terms)=>value===undefined?'Not set':specFor(path)?.type==='money'?money(value,terms.payment.currency):typeof value==='boolean'?(value?'Yes':'No'):String(value);
  return `<section class="card amendment-comparison" aria-label="Proposed amendment changes"><div class="split-title"><h2>${r.can_edit&&r.project.draft_owner===S.role?'Your unsent amendment draft':'Proposed amendment'}</h2><span class="pill">${paths.length} changed fields</span></div><p class="help">Only changed fields are shown. The current signed agreement stays active until both parties sign.</p>${paths.length?`<div class="table-wrap"><table class="amendment-compare-table"><thead><tr><th scope="col">Changed field</th><th scope="col">Current</th><th scope="col">Proposed</th></tr></thead><tbody>${paths.map(path=>`<tr><th scope="row"><strong>${esc(fieldTitle(path))}</strong><small>${esc(fieldLocation(path,proposed).split(' › ').slice(0,-1).join(' · '))}</small></th><td>${esc(display(path,previous[path],old))}</td><td>${esc(display(path,next[path],proposed))}</td></tr>`).join('')}</tbody></table></div>`:'<p>No changed fields have been shared in this version.</p>'}<button id="review-amendment-details">${r.can_edit?'Edit amendment fields':'Read full proposed agreement'}</button></section>`;
}
function renderProject(){
  syncAssistantWidget();
  frame();const r=S.room,p=r.project;
  const tabs=workspace()?['Overview','Agreement','Milestones','Payments','Work Tracking','Change Requests','Terms Summary','Chat','Resolution','Activity','Amendments']:['Overview','Agreement','Negotiation History','Chat','Activity'];
  if(!tabs.includes(S.tab))S.tab='Overview';
  $('content').innerHTML=`<div class="page-top"><div><div class="eyebrow">${workspace()?'SIGNED WORKSPACE':'PROJECT AGREEMENT'} · ${esc(p.id.slice(0,8))}</div><h1>${esc(at(r.agreement,'basics.title')||'Untitled project')}</h1></div>${pill(p.status)}</div>${p.amendment?'<div class="notice">An amendment is being negotiated. The previous signed agreement remains active until both parties sign the replacement.</div>':''}${p.status!=='AGREED'&&r.versions.some(v=>v.voided&&v.signatures.length)?'<div class="notice danger">The agreement changed. Both parties must approve and sign it again.</div>':''}<div class="tabs" role="tablist" aria-label="Project tabs">${tabs.map(t=>`<button role="tab" aria-selected="${S.tab===t}" class="${S.tab===t?'active':''}" data-tab="${t}">${t}${t==='Chat'&&r.unread_count?` (${r.unread_count})`:''}</button>`).join('')}</div><div class="notice actions"><button id="ai-review">AI review of terms & conversation</button><button id="discuss-price">Discuss price / ask a question</button></div>${r.chat_price_conflicts?.length&&p.status!=='AGREED'?`<div class="notice danger" aria-label="Chat price questions"><strong>Conversation terms differ from this agreement</strong><p>Both parties must confirm the final agreement term for each question before signing.</p>${r.chat_price_conflicts.map((i,n)=>`<div class="card"><p>${esc(i.explanation)}</p><p>${esc(i.question)}</p><p>Confirmed by: ${['client','freelancer'].filter(role=>i.confirmed_by[role]?.reason).join(', ')||'neither party'}</p>${!i.confirmed_by[S.role]?.reason?`<button data-confirm-price="${n}">Confirm final agreement term</button>`:''}</div>`).join('')}</div>`:''}${chatTermBanner()}<div id="tab-content" role="tabpanel"></div><div id="action-bar"></div>`;
  bindChatTermBanner();
  if(p.amendment)document.querySelector('[data-tab="Amendments"]').classList.add('amendment-available');
  document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>run(async()=>{await saveDraft();S.tab=b.dataset.tab;renderProject();}));
  document.querySelectorAll('[data-tab]').forEach((b,index)=>b.onkeydown=e=>{let next=index;if(e.key==='ArrowRight')next=(index+1)%tabs.length;else if(e.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;else if(e.key==='Home')next=0;else if(e.key==='End')next=tabs.length-1;else return;e.preventDefault();S.tab=tabs[next];renderProject();document.querySelectorAll('[data-tab]')[next].focus();});
  if(p.withdrawal){$('tab-content').insertAdjacentHTML('beforebegin',`<div class="notice withdrawal-notice"><strong>Freelancer withdrawal ${p.withdrawal.status==='accepted'?'recorded':'awaiting acknowledgment'}</strong><p>${esc(p.withdrawal.reason)}</p><p>New funding is stopped. Previously submitted work keeps its agreed review and payment rules.</p><button id="withdrawal-chat">Discuss in project chat</button></div>`);$('withdrawal-chat').onclick=()=>{S.tab='Chat';renderProject();};}
  if(p.amendment&&S.room.amendment_locks){$('tab-content').insertAdjacentHTML('beforebegin',`<div class="notice"><strong>Amending remaining work</strong><p>Funded milestones ${S.room.amendment_locks.milestone_ids.map(esc).join(', ')} and their original terms are locked. New funding waits for both signatures.</p></div>`);}
  $('ai-review').onclick=()=>run(reviewWithAI);$('discuss-price').onclick=()=>{S.pinned='payment.total_minor';S.tab='Chat';renderProject();};
  document.querySelectorAll('[data-confirm-price]').forEach(b=>b.onclick=()=>{const issue=r.chat_price_conflicts[Number(b.dataset.confirmPrice)];reasonDialog('Confirm final agreement term',issue.question+' Explain why the amount in this version is final.',reason=>perform('confirm-chat-price',{flag_id:issue.id,version_hash:currentVersion().digest,reason}));});
  const tab=$('tab-content');
  if(S.tab==='Amendments'&&p.amendment){
    tab.innerHTML=amendmentSummary();
    $('review-amendment-details').onclick=()=>{S.tab='Agreement';renderProject();};
  }
  else if(S.tab==='Agreement')builder(tab);
  else if(S.tab==='Overview')overview(tab);
  else if(S.tab==='Chat')chat(tab);
  else if(S.tab==='Activity')activity(tab);
  else if(S.tab==='Resolution')workflowTab(tab);
  else if(S.tab==='Negotiation History')historyTab(tab);
  else if(S.tab==='Amendments')amendments(tab);
  else workspaceTab(tab,S.tab);
  actionBar();
  guardChatTermButtons();
}
function uiDate(value,withTime=false){
  if(!value)return '—';
  const date=new Date(value.length===10?value+'T00:00:00':value);
  if(Number.isNaN(date.getTime()))return String(value);
  return new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',year:'numeric',...(withTime?{hour:'2-digit',minute:'2-digit'}:{})}).format(date);
}
function metaBox(title,value){return `<div class="meta-box"><span>${esc(title)}</span><strong>${esc(value)}</strong></div>`;}
function paymentNotice(message){
  return `<div class="payment-alert" role="alert">${esc(message)}</div>`;
}
function paymentSchedule(m,p){
  const window=S.assurance?.funding_windows?.[m.id];
  const checkout=window&&!window.available&&window.earliest_checkout&&!p;
  const rows=(checkout?`<div class="schedule-row"><span>Checkout opens</span><strong>${esc(uiDate(window.earliest_checkout))}</strong></div><div class="schedule-row"><span>Checkout closes</span><strong>${esc(uiDate(window.delivery_date))}</strong></div>${window.reserve_days!==undefined?`<div class="schedule-row"><span>Review/revision/buffer</span><strong>${esc(window.reserve_days)} days</strong></div>`:''}${window.capture_by?`<div class="schedule-row"><span>Capture by</span><strong>${esc(uiDate(window.capture_by))}</strong></div>`:''}`:'')+
    (p?.review_deadline&&p.status!=='captured'?`<div class="schedule-row"><span>Review by</span><strong>${esc(uiDate(p.review_deadline,true))}</strong></div>`:'')+
    (p?.expires_at&&p.status!=='captured'?`<div class="schedule-row"><span>Authorization expires</span><strong>${esc(uiDate(p.expires_at,true))}</strong></div>`:'');
  return rows?`<aside class="schedule-box payment-schedule" aria-label="${esc(m.title)} dates">${rows}${checkout?`<small>Checkout dates are in UTC.${window.capture_by?' Authorization must cover that date.':''}</small>`:''}</aside>`:'';
}

function overview(container){
  const r=S.room,p=r.project,a=activeTerms();
  if(workspace()){
    const v=r.versions.find(v=>v.id===p.active_agreement_id),ms=a.timeline?.milestones||[],next=ms.find(m=>S.assurance?.payments.find(p=>p.milestone_id===m.id)?.payout?.status!=='paid'),remaining=Math.ceil((new Date(a.timeline.deadline)-new Date())/86400000);
    container.innerHTML=`<div class="kpis"><div class="kpi"><span>Total agreed price</span><strong>${money(a.payment.total_minor,a.payment.currency)}</strong></div><div class="kpi"><span>Currency</span><strong>${esc(a.payment.currency)}</strong></div><div class="kpi"><span>Days remaining</span><strong>${remaining}</strong></div><div class="kpi"><span>Milestones</span><strong>${ms.length}</strong></div><div class="kpi"><span>Signatures</span><strong>${v.signatures.length}/2</strong></div></div>
      <div class="workspace-grid overview-panels">
        <section class="card"><div class="split-title"><h2>Signed agreement</h2><span class="pill">Version ${v.number}</span></div>
          <div class="signature-grid">${v.signatures.map(signature=>`<div class="meta-box signature-box"><span>Signed by</span><strong>${esc(signature.typed_name)}</strong><time datetime="${esc(signature.created_at)}" title="${esc(signature.created_at)}">${esc(uiDate(signature.created_at,true))}</time></div>`).join('')}</div>
          <div class="meta-grid">${metaBox('Client',a.parties.client_name)}${metaBox('Freelancer',a.parties.freelancer_name)}${metaBox('Start date',uiDate(a.timeline.start_date))}${metaBox('Deadline',uiDate(a.timeline.deadline))}</div>
          <div class="compact-reference"><span>${esc(a.timeline.time_zone)}</span><details><summary>Agreement reference</summary><p>Agreement ${esc(v.id)}</p><p class="hash">SHA-256 ${esc(v.digest)}</p></details></div>
        </section>
        <section class="card"><div class="split-title"><h2>${next?'Next milestone':'Milestones complete'}</h2>${next?pill(S.assurance?.payments.find(x=>x.milestone_id===next?.id)?.status||'Not funded'):''}</div><h3>${esc(next?.title||'All milestone payouts are verified.')}</h3>${next?`<div class="meta-grid next-milestone-meta">${metaBox('Amount',money(next?.amount_minor,a.payment.currency))}${metaBox('Due date',uiDate(next?.due_date))}</div>`:''}<button id="open-payments">Open payments</button></section>
      </div>`;
    $('open-payments').onclick=()=>{S.tab='Payments';renderProject();};
  }else{
    const blockers=r.flags.filter(f=>f.severity==='BLOCKER').length,warnings=r.flags.filter(f=>f.severity==='WARNING'&&f.status==='open').length;
    container.innerHTML=`${p.status==='INVITED'&&S.role==='freelancer'?'<div class="notice"><strong>Project brief</strong><p>Review the client’s terms, then confirm your legal name, country, time zone and proposed scope. Opening the brief passes editing to you.</p></div>':''}<div class="workspace-grid"><div class="card"><h2>Project brief</h2><p>${esc(at(r.agreement,'basics.summary')||'Add a clear summary of your project.')}</p><p>${esc(at(r.agreement,'parties.client_name')||'Client')} ↔ ${esc(at(r.agreement,'parties.freelancer_name')||'Freelancer')}</p><p>${pill(p.status)}</p><button id="open-builder" class="primary">${r.can_edit?'Continue agreement builder':'Review agreement'}</button></div><div class="card"><h2>Agreement health</h2><p><strong>${blockers}</strong> blockers · <strong>${warnings}</strong> unacknowledged warnings</p><p class="muted">Blockers must be fixed in the fields. Each warning requires a reason from both parties.</p><p>Current version: ${currentVersion()?'v'+currentVersion().number:'Unsent draft'}</p>${currentVersion()?`<details class="advanced"><summary>Agreement reference</summary><p class="hash">${currentVersion().digest}</p></details>`:''}<p>Turn: <strong>${esc(p.turn)}</strong></p></div></div>`;
    $('open-builder').onclick=()=>{S.tab='Agreement';renderProject();};
    if($('load-example'))$('load-example').onclick=()=>run(async()=>{const r=await api(url('example-terms'));for(const [section,values] of Object.entries(r.agreement)){if(section==='acknowledgments')continue;for(const [key,value] of Object.entries(values)){if(['both',S.role].includes(fieldOwner(section+'.'+key)))put(S.room.agreement,section+'.'+key,value);}}S.dirty=true;persist();S.tab='Agreement';renderProject();toast('Example terms loaded. Review every field before inviting.');});
  }
}
function agreementValue(value,path,currency=S.room.agreement.payment?.currency){
  if(value===null||value===undefined)return '?';
  if(typeof value==='number'&&(specFor(path)?.type==='money'||path.endsWith('_minor')))return money(value,currency);
  if(Array.isArray(value))return value.map((item,i)=>agreementValue(item,path+'.'+i,currency)).join('; ');
  if(typeof value==='object')return Object.entries(value).map(([key,item])=>(specFor(path+'.'+key)?.label||label(key))+': '+agreementValue(item,path+'.'+key,value.currency||currency)).join('; ');
  return typeof value==='boolean'?(value?'Yes':'No'):String(value);
}
function money(n,currency){if(n===undefined)return '—';try{const places=new Intl.NumberFormat('en',{style:'currency',currency}).resolvedOptions().maximumFractionDigits;return new Intl.NumberFormat('en',{style:'currency',currency}).format(n/10**places);}catch{return `${n} ${currency} minor units`;}}
function activeTerms(){return S.assurance?.operational_agreement||S.room.versions.find(v=>v.id===S.room.project.active_agreement_id)?.payload||S.room.agreement;}
function currencyPrecision(){try{return new Intl.NumberFormat('en',{style:'currency',currency:at(S.room.agreement,'payment.currency')||'USD'}).resolvedOptions().maximumFractionDigits;}catch{return 2;}}
const compactGroups={basics:['basics','parties'],scope:['scope','revisions'],payment:['payment','timeline'],termination:['ip','confidentiality','communication','termination','attachments']};
const compactNames={basics:'1. Brief & people',scope:'2. Deliverables',payment:'3. Price & dates',termination:'4. Review terms'};
function fieldOwner(path){if(path.startsWith('scope.deliverables')||path.startsWith('revisions.'))return 'freelancer';if(path.startsWith('scope.dependencies')||path.startsWith('scope.inclusions'))return 'client';return specFor(path)?.owner||'both';}
function compactFields(){
  const sections=compactGroups[S.section]||[S.section];
  return sections.map(section=>{
    const fields=Object.entries(S.schema.fields[section]),essential=S.schema.essentials[section]||[];
    const html=([key,spec])=>fieldHtml(section+'.'+key,spec,at(S.room.agreement,section+'.'+key));
    const mine=fields.filter(([k])=>essential.includes(k)&&['both',S.role].includes(fieldOwner(section+'.'+k)));
    const rest=fields.filter(([k])=>!mine.some(([x])=>x===k));
    return '<section class="agreement-field-group"><h3>'+esc(names[section])+'</h3><p class="help">'+esc({timeline:'Plan delivery early enough to include every review, revision and the capture buffer.',payment:'Set one currency and match the milestone prices to the total.',scope:'Describe what is delivered and how it will be accepted.',revisions:'Define included changes, turnaround and extra work.'}[section]||'Review the terms in this group with your counterpart.')+'</p>'+mine.map(html).join('')+(rest.length?'<details class="advanced"><summary>More '+esc(names[section].toLowerCase())+' fields · '+rest.length+'</summary>'+rest.map(html).join('')+'</details>':'')+'</section>';
  }).join('');
}
function amendmentFieldLocked(path){
  const locked=S.room?.project.amendment&&S.room.amendment_locks;
  if(!locked)return false;
  for(const [collection,indices] of [['timeline.milestones',locked.milestone_indices],['scope.deliverables',locked.deliverable_indices]]){
    if(path===collection)return false;
    if(path.startsWith(collection+'.'))return indices.includes(Number(path.split('.')[2]));
  }
  return !locked.editable_fields.includes(path);
}
function repeatSettings(path,index,children,row){
  const fields=Object.entries(children).filter(([key])=>['id','quantity','currency','bumped_date','milestone_id','deliverable_ids'].includes(key)).map(([key,spec])=>fieldHtml(`${path}.${index}.${key}`,spec,row[key])).join('');
  return fields?`<details class="advanced"><summary>References, quantities & review settings</summary>${fields}</details>`:'';
}
function fieldHtml(path,spec,value){
  spec={...spec,label:fieldTitle(path)};
  if(amendmentFieldLocked(path))return `<div class="read-field locked-amendment-field" id="field-${path.replaceAll('.','-')}" tabindex="-1"><strong>${esc(spec.label)} <span class="pill">Locked</span></strong>${value===undefined||value===null?'Not specified':spec.type==='money'?esc(money(value,S.room.agreement.payment.currency)):typeof value==='boolean'?(value?'Yes':'No'):typeof value==='object'?readFields(value,path):spec.type==='money'?esc(money(value,S.room.agreement.payment.currency)):typeof value==='boolean'?(value?'Yes':'No'):esc(value)}</div>`;
  const owner=fieldOwner(path);if(owner!=='both'&&owner!==S.role)return `<div class="read-field" id="field-${path.replaceAll('.','-')}" tabindex="-1"><strong>${esc(spec.label)} · ${owner} fills this</strong>${value===undefined||value===null?'Waiting for '+owner:typeof value==='object'?readFields(value,path):spec.type==='money'?esc(money(value,S.room.agreement.payment.currency)):typeof value==='boolean'?(value?'Yes':'No'):esc(value)}<br><button type="button" class="quiet" data-discuss="${path}">Request a change / ask a question</button></div>`;

  const id='field-'+path.replaceAll('.','-'),help=id+'-help',errors=S.room.flags.filter(f=>f.field_path===path&&f.severity==='BLOCKER'),required=spec.required?' *':'';
  const attrs=`id="${id}" data-field="${path}" data-kind="${spec.type}" aria-describedby="${help} ${id}-error" aria-invalid="${!!errors.length}"`;
  let control='';
  if(path.endsWith('.auto_accept')||path.endsWith('.auto_accept_days'))return `<div class="field"><strong>${esc(path.endsWith('.auto_accept_days')?'Automatic release review period':'Automatic release after review')}</strong><p>${path.endsWith('.auto_accept_days')?esc(at(S.room.agreement,path.replace(/auto_accept_days$/,'review_days')))+' days':value===false?`This proposal must enable automatic release before signing. <button type="button" data-enable-auto="${esc(path)}">Enable automatic release</button>`:'Enabled. Delivery starts the review period; a timely objection pauses payment.'}</p></div>`;
  if(path==='payment.due_trigger')return '<div class="field"><strong>Payment release</strong><p>Automatic after the agreed review period, unless an objection pauses it.</p></div>';
  if(path.endsWith('.auto_accept'))spec={...spec,label:'Automatically release payment if the client stays silent?',helper:'Only after delivery and the signed review period. A timely objection pauses release. PayPal may still reject capture or payout.'};
  if(spec.type==='repeat')return `<div class="field" id="${id}" tabindex="-1"><h3>${esc(spec.label)}${required}</h3><div class="help">${esc(spec.helper)}</div>${(value||[]).map((row,i)=>`<fieldset class="repeat-row" id="field-${path.replaceAll('.','-')}-${i}"><legend>${esc(spec.label)} ${i+1}${row.title||row.name?' · '+esc(row.title||row.name):''}</legend>${Object.entries(spec.children).filter(([k])=>!['id','quantity','currency','bumped_date','milestone_id','deliverable_ids'].includes(k)).map(([k,s])=>fieldHtml(`${path}.${i}.${k}`,s,row[k])).join('')}${repeatSettings(path,i,spec.children,row)}<button class="remove" data-remove="${path}" data-index="${i}" type="button" ${amendmentFieldLocked(path+'.'+i)?'disabled title="Paid work is locked"':''}>Remove row</button></fieldset>`).join('')}<button data-add="${path}" type="button">＋ Add ${esc(spec.label.toLowerCase())}</button><div class="inline-error" id="${id}-error">${errors.map(e=>esc(e.message)).join(' ')}</div></div>`;
  if(spec.type==='boolean')control=`<select ${attrs}><option value="">Choose yes or no</option><option value="true" ${value===true?'selected':''}>Yes</option><option value="false" ${value===false?'selected':''}>No</option></select>`;
  else if(spec.type==='select'||spec.type==='country'||spec.type==='zone'||spec.type==='currency'||path.endsWith('milestone_id')){
    let options=spec.options;
    if(spec.type==='country')options=S.schema.countries.map(c=>({value:c.code,label:c.name}));
    if(spec.type==='zone')options=S.schema.time_zones;
    if(spec.type==='currency')options=S.schema.currencies;
    if(path.endsWith('milestone_id'))options=(S.room.agreement.timeline?.milestones||[]).map(m=>({value:m.id,label:`${m.id||'?'} · ${m.title||'Milestone'}`}));
    control=`<select ${attrs}><option value="">Choose…</option>${options.map(o=>`<option value="${esc(o.value??o)}" ${(o.value??o)===value?'selected':''}>${esc(o.label??label(o))}</option>`).join('')}</select>`;
  }else if(spec.type==='days')control=`<div id="${id}">${['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map((d,i)=>`<label class="check"><input type="checkbox" data-day="${path}" value="${i}" ${(value||[]).includes(i)?'checked':''}>${d}</label>`).join('')}</div>`;
  else if(path.endsWith('.deliverable_ids'))control=`<select ${attrs} multiple size="3">${(S.room.agreement.scope?.deliverables||[]).map(d=>`<option value="${esc(d.id)}" ${(value||[]).includes(d.id)?'selected':''}>${esc(d.id)} ? ${esc(d.name||'Deliverable')}</option>`).join('')}</select>`;
  else if(['long','tags','urls','dates'].includes(spec.type))control=`<textarea ${attrs} rows="${spec.type==='long'?3:2}" placeholder="${esc(Array.isArray(spec.example)?spec.example.join('\n'):spec.example)}">${esc(Array.isArray(value)?value.join('\n'):value||'')}</textarea>`;
  else control=`<input ${attrs} type="${spec.type==='date'?'date':spec.type==='url'?'url':['number','money'].includes(spec.type)?'number':'text'}" value="${esc(spec.type==='money'&&value!==undefined&&value!==null?(value/10**currencyPrecision()).toFixed(currencyPrecision()):value??'')}" ${['number','money'].includes(spec.type)?`min="0" step="${spec.type==='money'?10**(-currencyPrecision()):1}"`:''} ${spec.min_length?`minlength="${spec.min_length}"`:''} ${spec.max_length?`maxlength="${spec.max_length}"`:''} placeholder="${esc(spec.example)}">`;
  return `<div class="field"><label for="${id}">${esc(spec.label)}${required}</label>${control}<div class="help" id="${help}">${esc(spec.helper)} ${['tags','urls','dates'].includes(spec.type)?'One item per line. ':''}${spec.type==='money'?'Use the agreed project currency. ':''}${spec.example!==''?'Example: '+esc(spec.type==='money'?(spec.example/10**currencyPrecision()).toFixed(currencyPrecision()):Array.isArray(spec.example)?spec.example.join('; '):spec.example):''}${spec.owner!=='both'?' · '+esc(spec.owner)+' confirms first.':''}</div><div id="${id}-error" class="inline-error">${errors.map(e=>esc(e.message)).join(' ')}</div><button class="quiet" type="button" data-discuss="${path}">Discuss this field</button></div>`;
}
function builder(container){
  if(!S.room.can_edit){
    const active=workspace()&&!S.room.project.amendment?activeTerms():S.room.agreement;
    const exported=workspace()&&!S.room.project.amendment?S.room.active_rendered:S.room.rendered;
    if(exported){
      container.innerHTML=`<div class="card"><h2>${S.room.project.amendment?'Proposed amendment':'Final agreement'} · v${exported.version}</h2><details class="advanced"><summary>Agreement reference</summary><p class="hash">SHA-256 ${esc(exported.sha256)}</p></details><label for="clause-search">Search clauses</label><input id="clause-search" type="search" placeholder="Search the agreement"><div class="actions spaced"><a href="${url(`projects/${S.room.project.id}/export/pdf${S.room.project.amendment?'?version_id='+encodeURIComponent(currentVersion().id):''}`)}">Download PDF</a><a href="${url(`projects/${S.room.project.id}/export/json${S.room.project.amendment?'?version_id='+encodeURIComponent(currentVersion().id):''}`)}">Download JSON</a></div></div><div class="agreement-layout"><nav class="clause-nav" aria-label="Clause navigator">${Object.entries(S.schema.sections).map(([k,v])=>`<a href="#clause-${v[2]}">${esc(v[1])}</a>`).join('')}</nav><div class="card">${exported.html}</div></div><div id="global-flags" class="card flags"></div>`;
      $('clause-search').oninput=()=>{const q=$('clause-search').value.toLowerCase();document.querySelectorAll('.agreement-clauses section').forEach(f=>f.hidden=!f.textContent.toLowerCase().includes(q));};
      for(const c of exported.clauses){if(c.field_path&&specFor(c.field_path)){const section=$('clause-'+c.id);if(section){const button=document.createElement('button');button.className='quiet';button.dataset.discuss=c.field_path;button.textContent='Discuss this field';section.appendChild(button);}}}
      bindDiscuss();updateFlags();return;
    }
    container.innerHTML=`<div class="card"><div class="split-title"><h2>Agreement review</h2><span class="help">Read-only · ${esc(S.room.project.turn)} holds the editing turn</span></div><p class="help">Each milestone must fit its agreed funding, delivery and review window. Review the payment terms before signing.</p><label for="clause-search">Search clauses</label><input id="clause-search" type="search" placeholder="Search the agreement"><div class="actions spaced"><a href="${url(`projects/${S.room.project.id}/export/pdf${S.room.project.amendment?'?version_id='+encodeURIComponent(currentVersion().id):''}`)}">Download PDF</a><a href="${url(`projects/${S.room.project.id}/export/json${S.room.project.amendment?'?version_id='+encodeURIComponent(currentVersion().id):''}`)}">Download JSON</a></div></div><div class="agreement-layout"><nav class="clause-nav" aria-label="Clause navigator">${Object.entries(names).map(([k,n])=>`<a href="#review-${k}">${esc(n)}</a>`).join('')}</nav><div>${Object.entries(names).map(([k,n])=>`<div class="card" id="review-${k}"><h2>${esc(n)}</h2>${readFields(active[k]||{},k)}</div>`).join('')}</div></div><div id="global-flags" class="card flags"></div>`;
    $('clause-search').oninput=()=>{const q=$('clause-search').value.toLowerCase();document.querySelectorAll('.read-field').forEach(f=>f.hidden=!f.textContent.toLowerCase().includes(q));};
    container.querySelectorAll('[data-enable-auto]').forEach(b=>b.onclick=()=>{put(S.room.agreement,b.dataset.enableAuto,true);changed();builder(container);});
  bindDiscuss();updateFlags();return;
  }
  container.innerHTML=`<div class="builder-layout"><nav class="stepper" aria-label="Agreement sections"><div class="progress-info"><span id="completeness"></span><progress id="progress" max="100" value="0" aria-label="Agreement completeness"></progress></div>${Object.entries(compactNames).map(([k,n])=>`<button data-section="${k}" class="${k===S.section?'active':''}"><span class="status-dot" id="dot-${k}"></span>${esc(n)}</button>`).join('')}</nav><div class="card builder-card"><div class="eyebrow">YOUR PART OF THE AGREEMENT</div><h2>${esc(compactNames[S.section]||names[S.section])}</h2><p class="help">Fill only your fields. Your counterpart completes theirs. Default clauses are proposals you both review before signing.</p><p class="help">Payment timing, revisions and review periods are part of the terms both participants sign.</p><form id="agreement-form">${compactFields()}<div class="actions"><button type="button" id="previous-section">← Previous</button><button type="button" class="primary" id="next-section">Next section →</button></div></form></div><div id="global-flags" class="card flags" aria-label="Agreement validation flags"></div></div>`;
  container.insertAdjacentHTML('afterbegin',`<div class="card agreement-field-finder"><label for="agreement-field-jump">Find an agreement field</label><select id="agreement-field-jump"><option value="">Choose a section and field…</option>${fieldNavigationOptions()}</select></div>`);
  $('agreement-field-jump').onchange=e=>{if(e.target.value)goToField(e.target.value);};
  const changed=()=>{for(const m of S.room.agreement.timeline?.milestones||[])if(m.auto_accept)m.auto_accept_days=m.review_days;S.dirty=true;persist();if($('save-status'))$('save-status').textContent='Unsaved changes';clearTimeout(S.lint);S.lint=setTimeout(()=>run(async()=>{const a=JSON.stringify(S.room.agreement);const r=await api(url('validate'),{agreement:S.room.agreement,project_id:S.room.project.id});if(a===JSON.stringify(S.room.agreement)){S.room.flags=r.flags;updateFlags();}}),450);};
  document.querySelectorAll('[data-field]').forEach(input=>input.oninput=()=>{let v=input.value;const kind=input.dataset.kind;if(kind==='money'){const places=currencyPrecision(),parts=v.split('.');if(v==='')v=null;else if(!/^\d+(?:\.\d+)?$/.test(v)||(parts[1]||'').length>places){input.setCustomValidity('Enter an amount with at most '+places+' decimal places.');input.reportValidity();return;}else{v=Number(parts[0])*10**places+Number((parts[1]||'').padEnd(places,'0'));input.setCustomValidity('');}}else if(kind==='number')v=v===''?null:Number(v);else if(kind==='boolean')v=v===''?null:v==='true';else if(input.multiple)v=[...input.selectedOptions].map(o=>o.value);else if(['tags','urls','dates'].includes(kind))v=v.split('\n').map(x=>x.trim()).filter(Boolean);put(S.room.agreement,input.dataset.field,v);changed();});
  document.querySelectorAll('[data-day]').forEach(input=>input.onchange=()=>{put(S.room.agreement,input.dataset.day,[...document.querySelectorAll(`[data-day="${input.dataset.day}"]:checked`)].map(i=>Number(i.value)));changed();});
  document.querySelectorAll('[data-add]').forEach(b=>b.onclick=()=>{const items=at(S.room.agreement,b.dataset.add)||[];items.push(b.dataset.add==='scope.deliverables'?{id:'D'+(items.length+1),quantity:1,revision_rounds:2,milestone_id:'M1'}:b.dataset.add==='timeline.milestones'?{id:'M'+(items.length+1),title:'Delivery',review_days:3,auto_accept:true,auto_accept_days:3,currency:at(S.room.agreement,'payment.currency'),amount_minor:at(S.room.agreement,'payment.total_minor'),deliverable_ids:(S.room.agreement.scope?.deliverables||[]).map(d=>d.id)}:{});put(S.room.agreement,b.dataset.add,items);changed();builder(container);});
  document.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{at(S.room.agreement,b.dataset.remove).splice(Number(b.dataset.index),1);changed();builder(container);});
  const navigate=key=>{S.section=key;builder(container);};
  document.querySelectorAll('[data-section]').forEach(b=>b.onclick=()=>navigate(b.dataset.section));
  const keys=Object.keys(compactNames),idx=keys.indexOf(S.section);$('previous-section').disabled=idx===0;$('next-section').disabled=idx===keys.length-1;
  $('previous-section').onclick=()=>navigate(keys[idx-1]);$('next-section').onclick=()=>navigate(keys[idx+1]);
  container.querySelectorAll('[data-enable-auto]').forEach(b=>b.onclick=()=>{put(S.room.agreement,b.dataset.enableAuto,true);changed();builder(container);});
  bindDiscuss();updateFlags();
}
function readFields(obj,path){return Object.entries(obj).map(([k,v])=>{const p=path+'.'+k;if(v&&typeof v==='object')return `<div class="read-field" id="field-${p.replaceAll('.','-')}" tabindex="-1"><strong>${esc(label(k))}</strong>${readFields(v,p)}</div>`;const spec=specFor(p);return `<div class="read-field" id="field-${p.replaceAll('.','-')}" tabindex="-1"><strong>${esc(fieldTitle(p))}</strong>${esc(spec?.type==='money'?money(v,S.room.agreement.payment.currency):typeof v==='boolean'?(v?'Yes':'No'):v)}<br><button class="quiet" data-discuss="${p}">Discuss this field</button></div>`;}).join('');}
function specFor(path){const p=path.split('.');let spec=S.schema.fields[p[0]]?.[p[1]];if(spec?.children&&p[3])spec=spec.children[p[3]];return spec;}
function bindDiscuss(){document.querySelectorAll('[data-discuss]').forEach(b=>b.onclick=()=>{S.pinned=b.dataset.discuss;S.tab='Chat';renderProject();});}
function updateFlags(){
  const panel=$('global-flags');if(!S.room)return;
  const allFlags=S.room.flags;const flags=S.room.project.guided&&S.room.project.status==='DRAFT'?allFlags.filter(f=>['basics.title','basics.category','basics.summary','payment.currency','payment.total_minor','timeline.start_date','timeline.deadline','scope.inclusions','scope.dependencies'].includes(f.field_path)||f.field_path.startsWith('scope.dependencies.')):allFlags;const counts=severity=>flags.filter(f=>f.severity===severity&&f.status==='open').length;
  if(panel){panel.innerHTML=`<h2>Agreement flags</h2><p class="flag-count">${counts('BLOCKER')} blockers · ${counts('WARNING')} warnings · ${counts('INFO')} info</p>${flags.length?flags.map(f=>`<div class="flag ${f.severity==='WARNING'?'warning':''}"><strong>${esc(f.severity)} · ${esc(f.status)}</strong><p class="flag-location">${esc(fieldLocation(f.field_path))}</p>${fieldOwner(f.field_path)!=='both'&&fieldOwner(f.field_path)!==S.role?`<p class="help">${esc(label(fieldOwner(f.field_path)))} must correct this field. Request the change before final approval.</p>`:''}<p>${esc(f.problem||f.message)}</p><p><strong>How to fix:</strong> ${esc(f.solution||f.suggested_fix)}</p><button data-goto="${f.field_path}">Go to this field</button>${(f.related_fields||[]).map(path=>`<button class="quiet" data-goto="${esc(path)}">Open ${esc(fieldLocation(path))}</button>`).join('')}<details><summary>Why this matters</summary>${esc(f.why)}</details>${f.severity==='WARNING'&&f.status==='open'&&currentVersion()?`<button data-ack="${f.id}">Acknowledge with reason</button>`:''}</div>`).join(''):'<p class="muted">✓ No open issues.</p>'}`;
    panel.innerHTML+=Object.entries(S.room.project.disputes||{}).map(([path,d])=>`<div class="flag"><strong>DISPUTED · ${esc(fieldLocation(path))}</strong><p>${esc(d.reason)}</p><button data-goto="${esc(path)}">Go to disputed field</button>${S.room.can_edit?`<button data-resolve="${esc(path)}">Address or justify field</button>`:''}</div>`).join('');
    panel.querySelectorAll('[data-resolve]').forEach(b=>b.onclick=()=>reasonDialog('Address disputed field','Explain your change or justify the current value. The other party must review the sent version.',reason=>perform('resolve-field',{field_path:b.dataset.resolve,reason})));
    panel.querySelectorAll('[data-goto]').forEach(b=>b.onclick=()=>goToField(b.dataset.goto));
    panel.querySelectorAll('[data-ack]').forEach(b=>b.onclick=()=>reasonDialog('Acknowledge warning','Explain why you accept this specific wording.',reason=>perform('acknowledge',{flag_id:b.dataset.ack,reason})));
  }
  if($('progress')){
    let total=0,missing=0;
    function count(fields,prefix){for(const [key,spec] of Object.entries(fields)){const p=prefix+'.'+key;if(spec.required){total++;if(flags.some(f=>f.field_path===p&&f.message.includes('mandatory')))missing++;}if(spec.children)(at(S.room.agreement,p)||[]).forEach((_,i)=>count(spec.children,p+'.'+i));}}
    Object.entries(S.schema.fields).forEach(([k,fields])=>count(fields,k));const complete=Math.round((total-missing)/Math.max(total,1)*100);$('progress').value=complete;$('completeness').textContent=`${complete}% complete · ${total-missing}/${total} required`;
    Object.keys(names).forEach(k=>{const items=flags.filter(f=>f.field_path.startsWith(k+'.')&&f.status==='open');if($('dot-'+k))$('dot-'+k).className='status-dot '+(items.some(f=>f.severity==='BLOCKER')?'red':items.length?'amber':'');});
    document.querySelectorAll('[data-field]').forEach(i=>{const errors=flags.filter(f=>f.field_path===i.dataset.field&&f.severity==='BLOCKER');i.setAttribute('aria-invalid',String(!!errors.length));const msg=$(i.id+'-error');if(msg)msg.textContent=errors.map(f=>f.message).join(' ');});
  }
}
function actionBar(){
  const p=S.room.project,v=currentVersion();let buttons=[];
  const add=(action,text,cls='')=>buttons.push(`<button data-action="${action}" class="${cls}">${text}</button>`);
  if(p.withdrawal){if(p.active_agreement_id)add('pdf','Download PDF');if(S.role==='client'&&p.withdrawal.status==='requested')add('acknowledge-withdrawal','Acknowledge withdrawal','primary');}
  else if(p.status==='DRAFT'){add('save','Save draft');add('validate','Validate');add('invite','Invite freelancer','primary');}
  else if(p.status==='INVITED'){if(S.role==='client'){add('withdraw','Withdraw invitation');add('resend','Resend');if(S.room.can_edit){add('save','Save draft');add('send','Send revised brief','primary');}else add('edit-invitation','Edit draft');}else{add('open','Accept brief & fill my fields','primary');add('decline','Decline','danger');}}
  else if(['IN_NEGOTIATION','AMENDMENT_PROPOSED'].includes(p.status)){
    if(p.turn===S.role){if(S.room.can_edit){add('save','Save draft');add('send','Send counter','primary');add('handoff-editing','Pass editing turn');}else add('counter','Propose changes');add('reject','Reject fields');}else add('nudge','Nudge');
    add('approve','Approve version');if(p.amendment){if(p.turn===S.role)add('reject-amendment','Reject amendment','danger');}else add('cancel','Cancel project','danger');
  }else if(['READY_TO_SIGN','PARTIALLY_SIGNED'].includes(p.status)){add('review','Review final');if(!v?.signatures.some(s=>s.user_id===S.user.id))add('sign','Sign agreement','primary');add('counter','Edit & void consent');}
  else if(p.status==='AGREED'){add('pdf','Download PDF');add('amend',S.room.amendment_blocked_reason?'Agreement change options':S.room.amendment_locks?'Amend remaining milestones':'Propose amendment','primary');}
  else if(p.status==='EXPIRED'&&S.role==='client')add('invite','Re-invite freelancer','primary');
  if(S.role==='freelancer'&&!p.withdrawal&&!terminal.includes(p.status))add('freelancer-withdraw','Withdraw from project','danger');
  if(S.role==='client')add(p.hidden_by_client?'restore':'delete',p.hidden_by_client?'Restore project':p.status==='DRAFT'?'Delete draft':'Archive project','quiet');
  $('action-bar').innerHTML=`<div class="sticky-bar"><div class="turn"><strong>${p.status==='AGREED'?'Agreement signed':p.turn===S.role?'Your turn to review and respond':`Waiting for ${esc(p.turn)} to respond (${Math.max(0,Math.floor((Date.now()-new Date(p.updated_at))/86400000))} days)`}</strong><span id="save-status">${S.dirty?'Unsaved changes':S.room.can_edit?'All changes saved':''}</span></div><div class="actions">${buttons.join('')}</div></div>`;
  document.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>run(async()=>{
    const a=b.dataset.action;
    if(a==='handoff-editing'){await saveDraft();S.room=await api(url(`projects/${S.room.project.id}/handoff-editing`),{revision:S.room.revision});S.dirty=false;renderProject();toast('Editing passed to the other participant.');return;}
    if(a==='delete'){await removeProject(p.id,S.room.revision,p.status==='DRAFT');return;}
    if(a==='freelancer-withdraw'){reasonDialog('Withdraw from project','Explain why you cannot continue. Your reason will be shared in project chat and the client will be notified. New funding and new deliveries stop; previously submitted work keeps its review and payment rules.',reason=>perform(a,{reason}));return;}
    if(a==='acknowledge-withdrawal'){modal('Acknowledge withdrawal','<p>This acknowledges that remaining work will stop. Submitted work keeps its existing review and payment rules. No refund or payment is created by this action.</p><button class="primary" id="withdrawal-confirm">Acknowledge withdrawal</button>',()=>{$('withdrawal-confirm').onclick=()=>run(async()=>{await perform(a);$('dialog').close();});});return;}
    if(a==='save'){await saveDraft();toast('Draft saved.');return;}
    if(a==='amend'&&S.room.amendment_blocked_reason){S.tab='Amendments';renderProject();return;}
    if(a==='validate'){await saveDraft();const x=await api(url('validate'),{agreement:S.room.agreement,project_id:S.room.project.id});S.room.flags=x.flags;S.tab='Agreement';renderProject();return;}
    if(a==='invite'){modal('Invite a known freelancer',`<form id="invite-form">${input('recipient','Freelancer email or @handle',p.recipient_email||'')}<button class="primary">Send invitation</button></form>`,()=>{$('invite-form').onsubmit=e=>{e.preventDefault();run(async()=>{await perform('invite',{recipient:$('recipient').value});$('dialog').close();});};});return;}
    if(a==='sign'){
      const rules=S.room.payment_rules,blocked=S.room.flags.filter(f=>f.severity==='BLOCKER');
      modal('Sign this exact agreement',`<section class="notice" aria-label="Payment terms"><h3>Payment terms - review before signing</h3><p>${esc(rules.policy)}</p><div class="table-wrap"><table><thead><tr><th>Milestone</th><th>Delivery</th><th>Review, revisions + buffer</th><th>Planned capture by</th><th>Funding limit</th></tr></thead><tbody>${rules.milestones.map(m=>`<tr><td>${esc(m.title)} · ${m.fits?'Fits':'BLOCKED'}</td><td>${esc(m.due_date)}</td><td>${esc(m.reserve_days)} days</td><td>${esc(m.planned_capture_by)}</td><td>${esc(m.authorization_end)}</td></tr>`).join('')}</tbody></table></div><p>Actual provider funding deadlines are checked again at checkout and delivery. The signed payment policy defines collection, review, release and provider limits.</p></section>${blocked.length?`<div class="notice danger">Signing blocked: ${blocked.map(f=>esc(f.problem||f.message)).join(' ')}</div>`:''}<p class="hash">${v.digest}</p><form id="sign-form">${input('typed-name','Type your full name','','text',true,S.user.name)}<label class="check"><input id="payment-rules-confirmed" type="checkbox" required> I agree to the displayed funding and automatic-release terms, review period, seller fees and provider limits. Payment receipt must be verified separately.</label><button class="primary" ${blocked.length||rules.milestones.some(m=>!m.fits)?'disabled':''}>Sign agreement</button></form>`,()=>{$('sign-form').onsubmit=e=>{e.preventDefault();run(async()=>{await perform('sign',{typed_name:$('typed-name').value,version_hash:v.digest,payment_rules_confirmed:$('payment-rules-confirmed').checked});$('dialog').close();});};});return;
    }
    if(a==='decline'){modal('Decline project',`<form id="decline-form">${select('reason-code','Reason',S.schema.decline_reasons)}${input('reason-text','Additional details','','text',false)}<button class="danger">Decline project</button></form>`,()=>{$('decline-form').onsubmit=e=>{e.preventDefault();run(async()=>{await perform('decline',{reason_code:$('reason-code').value,reason:$('reason-text').value});$('dialog').close();});};});return;}
    if(['cancel','reject-amendment'].includes(a)){reasonDialog(label(a),'Give a reason that will be recorded in project activity.',reason=>perform(a,{reason}));return;}
    if(a==='reject'){rejectDialog();return;}
    if(a==='pdf'){location.assign(url(`projects/${p.id}/export/pdf`));return;}
    if(a==='review'){S.tab='Agreement';renderProject();return;}
    if(a==='approve'){
      await saveDraft();
      if(S.room.has_unshared_changes){
        if(!S.room.can_edit)throw Error('The updated draft must be shared by the participant editing it before approval.');
        await perform('send');
      }
      await perform('approve',{version_hash:currentVersion().digest});
      return;
    }
    await perform(a,{});
  }));
}
async function perform(action,extra={}){
  if(S.saving)throw Error('Autosave is in progress. Try again when it completes.');
  await saveDraft();const r=await api(url(`projects/${S.room.project.id}/actions`),{revision:S.room.revision,action,...extra});S.room=r;S.dirty=false;
  if(action==='delete'){go(`/${S.role}/dashboard`);return;}
  if(['amend','counter','open','edit-invitation'].includes(action))S.tab=action==='amend'?'Amendments':'Agreement';
  if(['freelancer-withdraw','acknowledge-withdrawal'].includes(action))S.tab='Chat';else if(r.project.status==='AGREED')S.tab='Overview';renderProject();toast(action==='send'?'Version sent. Previous proposal approvals and signatures are void.':action==='approve'&&r.project.status!=='READY_TO_SIGN'?'Version approved. Waiting for the other participant to approve.':label(action)+' recorded.');
  if(action==='approve'&&r.project.status==='READY_TO_SIGN')document.querySelector('[data-action="sign"]')?.click();
}
function reasonDialog(title,description,callback){modal(title,`<p>${esc(description)}</p><form id="reason-form"><div class="field"><label for="action-reason">Reason *</label><textarea id="action-reason" required maxlength="3000"></textarea></div><button class="primary">Record reason</button></form>`,()=>{$('reason-form').onsubmit=e=>{e.preventDefault();run(async()=>{await callback($('action-reason').value);$('dialog').close();});};});}
function rejectDialog(){
  const fields=[];function collect(obj,path=''){for(const [k,v] of Object.entries(obj)){const p=path?path+'.'+k:k;if(v&&typeof v==='object')collect(v,p);else if(specFor(p))fields.push(p);}}collect(S.room.agreement);
  let rows=0;
  const addRow=()=>{const i=rows++;$('decision-rows').insertAdjacentHTML('beforeend',`<fieldset class="repeat-row" data-decision-row="${i}"><legend>Field decision ${i+1}</legend>${select('decision-field-'+i,'Agreement field',fields)}${select('decision-'+i,'Decision',['reject','accept'])}<label for="decision-reason-${i}">Reason (required for rejection)</label><textarea id="decision-reason-${i}" maxlength="3000"></textarea></fieldset>`);};
  modal('Field-level decisions',`<p>Accept some fields and reject others in one response. Each rejection needs a reason.</p><form id="decision-form"><div id="decision-rows"></div><div class="actions"><button id="add-decision" type="button">Add another field</button><button class="primary">Send field decisions</button></div></form>`,()=>{addRow();$('add-decision').onclick=addRow;$('decision-form').onsubmit=e=>{e.preventDefault();run(async()=>{const decisions=[...document.querySelectorAll('[data-decision-row]')].map(row=>{const i=row.dataset.decisionRow;return {field_path:$('decision-field-'+i).value,decision:$('decision-'+i).value,reason:$('decision-reason-'+i).value};});await perform('reject',{decisions});$('dialog').close();});};});
}
function historyTab(container){
  const r=S.room;container.innerHTML=`<div class="workspace-grid"><div class="card"><h2>Version timeline</h2><div class="timeline">${r.versions.slice().reverse().map(v=>`<div class="card"><strong>v${v.number} · ${v.voided?'Voided proposal':v.id===r.project.active_agreement_id?'Active agreement':'Sent'}</strong><p>${esc(v.created_at)}<br>Author ${esc(r.members.find(m=>m.role===S.role&&v.created_by===S.user.id)?.name||'Counterpart')}</p><p class="hash">${v.digest}</p><p>${v.approvals.length} approvals · ${v.signatures.length} signature records</p></div>`).join('')}</div></div><div class="card"><h2>Field changes in current version</h2>${r.changes.map(c=>`<div class="read-field"><strong>${esc(c.field_path)}</strong><small>${esc(c.created_at)} · ${esc(c.actor_id)}</small><div class="diff"><pre>Previous: ${esc(agreementValue(c.before,c.field_path))}</pre><pre>Current: ${esc(agreementValue(c.after,c.field_path))}</pre></div></div>`).join('')||'<p>No sent changes yet.</p>'}</div></div>`;
}
function activity(container){container.innerHTML=`<div class="card"><h2>Project activity</h2>${S.room.activity.slice().reverse().map(e=>`<div class="read-field"><strong>${esc(label(e.kind))}</strong>${esc(e.data.message||e.data.reason||'')}<br><small>${esc(e.created_at)}${e.actor_id?' · '+esc(e.actor_id):' · System'}</small></div>`).join('')||'<p>No events yet.</p>'}</div>`;}
function chat(container){
  const p=S.room.project;if(!p.thread_id){container.innerHTML='<div class="card empty">Chat opens when an invitation is sent.</div>';return;}
  const readonly=terminal.includes(p.status)&&p.status!=='FREELANCER_WITHDRAWN';container.innerHTML=`<div class="card"><h2>Project chat</h2><p class="help">Messages are append-only. Accepted changes must be reflected in the approved agreement. Use Record term change for any agreement field.</p><div class="chat-log" id="chat-log">${S.room.messages.map(m=>`<div class="message ${m.kind==='system'?'system':m.user_id===S.user.id?'mine':''}">${m.field_path?`<small>Pinned to ${esc(m.field_path)}</small>`:''}${chatText(m.body)}${m.kind!=='system'?`<button class="quiet" data-record-chat-change="${esc(m.id)}">Record term change</button>`:''}<small>${m.kind==='system'?'System':m.user_id===S.user.id?'You':'Counterpart'} · ${new Date(m.created_at).toLocaleString()}</small></div>`).join('')||'<p class="empty">Start the conversation.</p>'}</div>${readonly?'<p class="notice">This project is closed. Chat is read-only.</p>':`<form id="chat-form">${S.pinned?`<p>Pinned to <strong>${esc(S.pinned)}</strong> <button type="button" id="unpin" class="quiet">Remove pin</button></p>`:''}<div class="field"><label for="chat-body">Message *</label><textarea id="chat-body" required maxlength="5000" placeholder="Ask a specific question about your agreement."></textarea></div><button class="primary">Send message</button></form>`}</div>`;
  $('chat-log').scrollTop=$('chat-log').scrollHeight;
  container.querySelectorAll('[data-record-chat-change]').forEach(b=>b.onclick=()=>recordChatTerm(b.dataset.recordChatChange));
  if(!readonly){$('chat-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api(url(`projects/${p.id}/chat`),{body:$('chat-body').value,field_path:S.pinned||null});S.pinned=null;S.room=await api(url(`projects/${p.id}`));chat(container);});};if($('unpin'))$('unpin').onclick=()=>{S.pinned=null;chat(container);};}
  run(async()=>{await api(url(`projects/${p.id}/chat/read`),{});S.room.unread_count=0;await refreshNotifications();});
}
function chatText(text){let out='',pos=0;for(const m of text.matchAll(/https?:\/\/[^\s<>"']+/g)){out+=esc(text.slice(pos,m.index))+`<a href="${esc(m[0])}" target="_blank" rel="noopener noreferrer">${esc(m[0])}</a>`;pos=m.index+m[0].length;}return out+esc(text.slice(pos));}
function workspaceTab(container,tab){
  const a=activeTerms(),ms=a.timeline?.milestones||[],ds=a.scope?.deliverables||[];
  if(tab==='Milestones')container.innerHTML=`<div class="card"><h2>Milestones</h2><div class="table-wrap"><table><thead><tr><th>Title / deliverables</th><th>Due date</th><th>Amount</th><th>Review</th><th>Payment state</th></tr></thead><tbody>${ms.map(m=>`<tr><td>${esc(m.title)}<br><small>${esc(m.deliverable_ids.join(', '))}</small></td><td>${esc(m.due_date)}</td><td>${money(m.amount_minor,a.payment.currency)}</td><td>${m.review_days} days</td><td>${pill(S.assurance?.payments.find(x=>x.milestone_id===m.id)?.status||'Not funded')}</td></tr>`).join('')}</tbody></table></div></div>`;
  if(tab==='Payments')paymentTab(container);
  if(tab==='Work Tracking')paymentTab(container,true);
  if(tab==='Change Requests')workflowTab(container,true);
  if(tab==='Terms Summary')container.innerHTML=termsSummary(a);
}
function summaryRow(title,value){
  if(value===undefined||value===null||value===''||(Array.isArray(value)&&!value.length))return '';
  const content=Array.isArray(value)?`<ul>${value.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`:esc(typeof value==='boolean'?(value?'Yes':'No'):value);
  return `<div class="summary-row"><dt>${esc(title)}</dt><dd>${content}</dd></div>`;
}
function termsSummary(a){
  const timeline=a.timeline||{},payment=a.payment||{},ip=a.ip||{},privacy=a.confidentiality||{},termination=a.termination||{};
  const deliverables=a.scope?.deliverables||[],milestones=timeline.milestones||[];
  const row=summaryRow;
  const revisions=deliverables.map(d=>`${d.name}: ${d.revision_rounds} ${d.revision_rounds===1?'round':'rounds'}`).join('; ');
  const reviews=milestones.map(m=>`${m.id}: ${m.review_days} days${m.auto_accept===true?' (Auto-accept)':m.auto_accept===false?' (No auto-accept)':''}`).join('; ');
  const ownership=[label(ip.transfer_timing),ip.portfolio_rights!==undefined?`Portfolio display rights: ${ip.portfolio_rights?'Yes':'No'}`:''].filter(Boolean).join(' | ');
  const privacyTerms=[privacy.nda!==undefined?`NDA: ${privacy.nda?'Yes':'No'}`:'',privacy.ai_policy?`AI tools usage: ${label(privacy.ai_policy)}`:''].filter(Boolean).join(' | ');
  return `<section class="card terms-card agreement-summary glance-summary"><h2>Your terms at a glance</h2>
    <div class="summary-keyfacts">${metaBox('Total project price',payment.total_minor!==undefined?money(payment.total_minor,payment.currency):'Not specified')}${metaBox('Final delivery deadline',uiDate(timeline.deadline))}${metaBox('Milestones',milestones.length)}</div>
    <dl class="summary-fields">
      ${row('Deliverables',deliverables.map(d=>d.name).join('; '))}
      ${row('Included revision rounds',revisions)}
      ${row('Payment release',milestones.length&&milestones.every(m=>m.auto_accept===true)?'Automatic after the agreed review period, unless a revision request or dispute pauses it.':label(payment.due_trigger))}
      ${row('Client review period',reviews)}
      ${row('Ownership transfer timing',ownership)}
      ${row('Termination notice',termination.notice_days!==undefined?`${termination.notice_days} days`:undefined)}
      ${row('Confidentiality & AI',privacyTerms)}
    </dl>
  </section>`;
}
async function refreshAssurance(){S.assurance=await api(url(`projects/${S.room.project.id}/assurance`));S.room=await api(url(`projects/${S.room.project.id}`));await loadWorkflow();renderProject();}
function recipientDialog(current,after){
  modal('Freelancer Sandbox payment recipient',`<p>Enter your PayPal Sandbox receiving account email from Developer Dashboard → Sandbox Accounts. Future payout requests will show this recipient to the client.</p><form id="recipient-form">${input('payout-email','PayPal Sandbox recipient email',current||'','email',true)}<label class="check"><input id="recipient-sandbox-confirm" type="checkbox" required> This is my Sandbox receiving account.</label><button class="primary">Save recipient</button></form>`,()=>{
    $('recipient-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api(url('payout-recipient'),{email:$('payout-email').value,sandbox_account_confirmed:$('recipient-sandbox-confirm').checked},'PUT');$('dialog').close();await after();});};
  });
}
function payoutControls(container,ms,payments,agreement){
  const recipient=S.assurance?.payout_recipient;
  container.querySelectorAll('[data-milestone-card]').forEach((card,index)=>{
    const milestone=ms[index],payment=payments.find(p=>p.milestone_id===milestone.id),payout=payment?.payout;
    if(!payment||payment.funding_model==='paypal_funded_v1'||payment.status!=='captured')return;
    if(payout?.status==='attention'&&!payout.batch_id&&S.role==='client')(card.querySelector('.payment-main')||card).insertAdjacentHTML('beforeend',`<button data-recover-payout="${index}">Recover original freelancer payout</button>`);
    (card.querySelector('.payment-main')||card).insertAdjacentHTML('beforeend',`<div class="payout-panel"><strong>Freelancer payout: ${esc(payout?.status==='paid'?'received':payout?.status||'not sent')}</strong><p>Recipient: ${esc(payout?.recipient_email||payment?.payout_recipient?.email||recipient||'Freelancer must configure a recipient')}</p>${payout?.transaction_id?`<p>PayPal transfer: ${esc(payout.transaction_id)}</p>`:''}${payout?.provider_status?`<p>PayPal status: ${esc(payout.provider_status)}</p>`:''}${payout?.attention?paymentNotice(payout.attention):''}${payout?.status==='unclaimed'?'<p>The recipient has not claimed the payout. Check the receiving Sandbox account.</p>':''}${!payout&&recipient&&S.role==='client'?`<button data-send-payout="${index}" class="primary">Send freelancer Sandbox payout</button>`:''}${payout?.batch_id?`<button data-check-payout="${index}">Check payout status</button>`:''}</div>`);
  });
  if(!S.assurance?.platform?.configured||payments.some(p=>p.funding_model!=='paypal_funded_v1'))container.insertAdjacentHTML('afterbegin',`<div class="notice"><strong>${S.assurance?.platform?.configured?'Receiving account for existing payouts':'Freelancer receiving account'}</strong><p>${esc(recipient||'No Sandbox recipient configured yet.')}</p>${S.role==='freelancer'?'<button id="workspace-recipient">Set Sandbox recipient</button>':''}</div>`);
  if($('workspace-recipient'))$('workspace-recipient').onclick=()=>recipientDialog(recipient,refreshAssurance);
  const base=`projects/${S.room.project.id}/assurance`;
  container.querySelectorAll('[data-send-payout]').forEach(b=>b.onclick=()=>{
    const m=ms[Number(b.dataset.sendPayout)],recipient=payments.find(p=>p.milestone_id===m.id)?.payout_recipient?.email||S.assurance?.payout_recipient;
    modal('Send freelancer Sandbox payout',`<p>Send <strong>${money(m.amount_minor,agreement.payment.currency)}</strong> from the Sandbox merchant to <strong>${esc(recipient)}</strong>.</p><form id="payout-form"><label class="check"><input id="payout-confirmed" type="checkbox" required> I confirm the recipient and Sandbox payout amount.</label><button class="primary">Send Sandbox payout</button></form>`,()=>{$('payout-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api(url(base+'/'+encodeURIComponent(m.id)+'/payout'),{recipient_email:recipient,sandbox_payout_confirmed:$('payout-confirmed').checked});$('dialog').close();await refreshAssurance();});};});
  });
  container.querySelectorAll('[data-check-payout]').forEach(b=>b.onclick=()=>run(async()=>{await api(url(base+'/'+encodeURIComponent(ms[Number(b.dataset.checkPayout)].id)+'/payout-status'),{});await refreshAssurance();}));
  container.querySelectorAll('[data-recover-payout]').forEach(b=>b.onclick=()=>{
    const m=ms[Number(b.dataset.recoverPayout)],payout=payments.find(p=>p.milestone_id===m.id).payout;
    modal('Recover original freelancer payout',`<p>Resume the original ${money(payout.amount_minor,payout.currency)} payout to ${esc(payout.recipient_email)} using the same payment reference. This does not charge the client again or change the recipient. Recovery is blocked outside the duplicate-protection window.</p><button id="recover-payout-confirm" class="primary">Recover original payout</button>`,()=>{$('recover-payout-confirm').onclick=()=>run(async()=>{await api(url(base+'/'+encodeURIComponent(m.id)+'/payout-recover'),{recipient_email:payout.recipient_email,sandbox_payout_confirmed:true});$('dialog').close();await refreshAssurance();});});
  });
}
function paymentTab(container,work=false){
  const a=activeTerms(),ms=a.timeline?.milestones||[],payments=S.assurance?.payments||[];
  container.innerHTML=`<section class="payment-heading"><h2>Payments</h2>${S.assurance?.worker_status!=="running"?'<p class="notice danger">Payment updates are temporarily unavailable. Please try again later.</p>':''}${!S.assurance?.enabled_currencies.includes(a.payment.currency)?`<p class="notice danger">${esc(a.payment.currency)} is not enabled for this Sandbox merchant. Both parties can sign an amendment in an enabled currency; FairFlow does not convert agreed amounts.</p>`:''}${location.search.includes('paypal_return=1')?'<p>Returned from PayPal? Select Verify buyer approval below.</p>':''}</section>${ms.map(m=>{const p=payments.find(x=>x.milestone_id===m.id);return `<section class="card milestone-payment" data-milestone-card="${esc(m.id)}"><div class="detail-layout"><div class="detail-main payment-main"><div class="split-title"><h2>${esc(m.title)}</h2>${pill(p?.status||'Not funded')}</div><div class="payment-primary"><strong class="payment-amount">${money(m.amount_minor,a.payment.currency)}</strong><span class="payment-due">Delivery due <strong>${esc(uiDate(m.due_date))}</strong></span></div>${p?.settlement_amount_minor!==null&&p?.settlement_amount_minor!==undefined?`<p><strong>Agreed partial settlement: ${money(p.settlement_amount_minor,a.payment.currency)}</strong></p>`:''}${p?.status==='late_pending'?'<p class="notice">Late delivery awaits a timing decision in Resolution. The review clock has not started.</p>':''}${p?.attention&&p.status!=='disbursing'?paymentNotice(p.attention):''}${p&&(p.authorization_id||p.capture_id||p.agreement_id)?`<details class="advanced"><summary>Payment reference</summary>${p.agreement_id?`<p>Agreement version: v${esc(S.room.versions.find(v=>v.id===p.agreement_id)?.number||'?')} <a href="${url(`projects/${S.room.project.id}/export/pdf?version_id=${encodeURIComponent(p.agreement_id)}`)}" target="_blank" rel="noopener noreferrer">View signed terms</a></p>`:''}${p.authorization_id?`<p>Authorization: ${esc(p.authorization_id)}${p.expires_at?' · Expires '+esc(p.expires_at):''}</p>`:''}${p.capture_id?`<p>Capture: ${esc(p.capture_id)}</p>`:''}</details>`:''}${(!p&&m.auto_accept&&S.assurance?.platform?.seller?.status!=='verified')?'<p>The freelancer must connect a receiving account before checkout.</p>':''}${p?.status==='captured'?'':p?.auto_release?'<p>Payment release: automatic after review, unless disputed</p>':m.auto_accept===true?'<p>Payment release: automatic after the agreed review period</p>':'<p>Legacy agreement: automatic release was not agreed. The signed terms are unchanged.</p>'}${p?.objection?`<section class="notice danger" aria-label="Client objection"><strong>Payment paused &middot; ${esc(label(p.objection.kind))}</strong><div class="help">Submitted by client${p.objection.at?' | '+esc(uiDate(p.objection.at,true)):''}${p.objection.deliverable_id?' | Deliverable '+esc(p.objection.deliverable_id):''}</div><div style="white-space:pre-wrap;overflow-wrap:anywhere;margin-top:8px">${esc(p.objection.reason)}</div></section>`:''}${p?.delivery_url?`<p class="delivery-link">Delivery: <a href="${esc(p.delivery_url)}" target="_blank" rel="noopener noreferrer">${esc(p.delivery_url)}</a><br>${esc(p.delivery_note||'')}</p>`:''}<div class="actions">${!p&&S.role==='client'?`<button data-pay-start="${esc(m.id)}" class="primary" ${S.assurance?.enabled_currencies.includes(a.payment.currency)?'':'disabled'}>Start Sandbox checkout</button>`:''}${p?.status==='awaiting_approval'&&S.role==='client'?`<a class="button" href="${esc(p.checkout_url)}">Approve in PayPal Sandbox</a><button data-pay-authorize="${esc(m.id)}">Verify buyer approval</button>`:''}${['authorized','funded','revision_requested'].includes(p?.status)&&S.role==='freelancer'&&(!S.room.project.withdrawal||p?.delivery_url)?`<button data-pay-deliver="${esc(m.id)}" class="primary">Submit delivery link</button>`:''}${p?.status==='in_review'&&S.role==='client'?`<button data-pay-object="${esc(m.id)}">Request revision / raise dispute</button>`:''}${p?.status==='disputed'?`<button data-pay-resolve="${esc(m.id)}">Confirm shared resolution</button>`:''}${['authorized','in_review','revision_requested','disputed'].includes(p?.status)&&S.role==='client'?`<button data-pay-renew="${esc(m.id)}">Refresh authorization honor period</button>`:''}${p?.status==='attention'&&S.role==='client'?`<button data-pay-reconcile="${esc(m.id)}">Check provider status</button>`:''}</div></div>${paymentSchedule(m,p)}</div></section>`;}).join('')}`;
  if(S.assurance?.payout_setup_error)container.insertAdjacentHTML('afterbegin',`<div class="notice danger" role="alert"><strong>Freelancer payout setup required</strong><p>${esc(S.assurance.payout_setup_error)}</p></div>`);
  payoutControls(container,ms,payments,a);
  const blockers=S.assurance?.funding_blockers||[];
  if(blockers.length){
    container.insertAdjacentHTML('afterbegin',`<div class="notice danger" role="alert"><h3>Update the agreement before funding</h3><ul>${blockers.map(f=>`<li><strong>${esc(f.field_path)}</strong>: ${esc(f.problem||f.message)} ${esc(f.solution||f.suggested_fix)}</li>`).join('')}</ul><button id="funding-agreement-options">${S.room.amendment_blocked_reason?'View agreement change options':'Propose amendment'}</button></div>`);
    $('funding-agreement-options').onclick=()=>{S.tab='Amendments';renderProject();};
  }
  container.querySelectorAll('[data-pay-start]').forEach(button=>{
    if(S.room.project.amendment){button.disabled=true;button.title='Both parties must finish signing or reject the amendment before new funding.';}
    if(S.room.project.work_closed||S.room.project.closed_milestones?.includes(button.dataset.payStart)){button.disabled=true;button.title='Remaining work is closed. Agree reopening in Resolution.';}
    if(S.room.project.withdrawal){button.disabled=true;button.title='New funding stopped following freelancer withdrawal.';}
    if(!ms.filter(m=>!S.room.amendment_locks?.milestone_ids.includes(m.id)).every(m=>m.auto_accept===true&&m.auto_accept_days===m.review_days)){button.disabled=true;button.title='Both parties must sign automatic-release terms before new funding.';}
    if(S.assurance?.platform?.enabled&&!S.assurance.platform.terms_agreed){button.disabled=true;button.title='The updated agreement must include upfront funding before payment.';}
    const index=ms.findIndex(m=>m.id===button.dataset.payStart);if(!button.dataset.payStart.startsWith('CO-')&&index>0&&ms.slice(0,index).some(m=>S.assurance?.payments.find(p=>p.milestone_id===m.id)?.payout?.status!=='paid')){button.disabled=true;button.title='Verify all previous milestone payouts before new funding.';}
    const window=S.assurance?.funding_windows?.[button.dataset.payStart];
    if(window&&!window.available&&window.earliest_checkout){
      button.disabled=true;

    }
    if(blockers.length){button.disabled=true;button.title='Resolve the agreement issues shown above before checkout.';}
    else if(!S.assurance?.payout_recipient){button.disabled=true;button.title='The freelancer must set a Sandbox receiving account before checkout.';}
  });
  const base=`projects/${S.room.project.id}/assurance`;
  container.insertAdjacentHTML('beforeend','<div class="card"><h2>Settlement history</h2><button id="settlement-history">View settlement history</button></div>');
  $('settlement-history').onclick=()=>run(async()=>{
    const report=await api(url(base+'/ledger'));
    modal('Settlement history',`<p>${esc(report.limitations)}</p>${report.entries.length?`<div class="table-wrap"><table><thead><tr><th>Recorded</th><th>Account</th><th>Amount</th><th>Reference</th></tr></thead><tbody>${report.entries.map(e=>`<tr><td>${esc(e.created_at)}</td><td>${esc(e.account.replaceAll('_',' '))}</td><td>${esc(new Intl.NumberFormat(undefined,{style:'currency',currency:e.currency}).format(e.amount_cents/100))}</td><td>${esc(e.external_id)}</td></tr>`).join('')}</tbody></table></div>`:'<p>No settlements have been recorded in the ledger yet.</p>'}`);
  });
  container.querySelectorAll('[data-pay-start]').forEach(b=>b.onclick=()=>run(async()=>{await api(url(base+'/start'),{milestone_id:b.dataset.payStart,recipient_email:S.assurance?.payout_recipient||null});await refreshAssurance();}));
  container.querySelectorAll('[data-pay-authorize]').forEach(b=>b.onclick=()=>run(async()=>{await api(url(base+'/'+encodeURIComponent(b.dataset.payAuthorize)+'/authorize'),{});await refreshAssurance();}));
  container.querySelectorAll('[data-pay-deliver]').forEach(b=>b.onclick=()=>{const id=b.dataset.payDeliver;modal('Submit milestone delivery',`<p>Submit the completed deliverables and preserve evidence of this version. Uploaded files remain available to both participants.</p><form id="delivery-form">${input('delivery-url','Delivery URL','','url',true)}<div class="field"><label for="delivery-note">What was delivered *</label><textarea id="delivery-note" required minlength="10" maxlength="3000"></textarea></div><div class="field"><label for="delivery-files">Evidence files (up to 5; 5 MiB each)</label><input id="delivery-files" type="file" multiple></div><fieldset class="delivery-checklist"><legend>Completed deliverables</legend>${(a.scope.deliverables||[]).filter(d=>d.milestone_id===id).map(d=>`<label><input type="checkbox" data-delivery-check value="${esc(d.id)}" ${S.workflow?.enabled?'required':''}> ${esc(d.name)}</label>`).join('')}</fieldset><button class="primary">Submit for client review</button></form>`,()=>{$('delivery-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api(url(base+'/deliver'),{milestone_id:id,url:$('delivery-url').value,note:$('delivery-note').value,file_ids:await workflowUpload($('delivery-files')),checklist:[...document.querySelectorAll('[data-delivery-check]:checked')].map(b=>b.value)});$('dialog').close();await refreshAssurance();});};});});
  container.querySelectorAll('[data-pay-renew]').forEach(b=>b.onclick=()=>run(async()=>{await api(url(base+'/'+encodeURIComponent(b.dataset.payRenew)+'/reauthorize'),{});await refreshAssurance();}));
  container.querySelectorAll('[data-pay-resolve]').forEach(b=>b.onclick=()=>{
    const p=payments.find(p=>p.milestone_id===b.dataset.payResolve);
    modal('Confirm shared resolution',`<p>Both parties must confirm the same text. A fresh full review period starts only after both confirm and sufficient authorization time remains.</p><form id="resolution-form">${input('resolution-reason','Agreed resolution',p?.resolution?.reason||'')}<button class="primary">Confirm resolution</button></form>`,()=>{$('resolution-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api(url(base+'/'+encodeURIComponent(b.dataset.payResolve)+'/resolve'),{reason:$('resolution-reason').value});$('dialog').close();await refreshAssurance();});};});
  });
  container.querySelectorAll('[data-pay-object]').forEach(b=>b.onclick=()=>{
    const mid=b.dataset.payObject,ds=(a.scope?.deliverables||[]).filter(d=>d.milestone_id===mid);
    modal('Pause payment with an objection',`<form id="objection-form">${select('objection-kind','Action',[{value:'revision',label:'Request an included revision'},...(S.workflow?.enabled?[{value:'defect',label:'Report a defect in agreed functionality (no revision round)'}]:[]),{value:'dispute',label:'Raise a dispute; pause payment'}])}${select('objection-deliverable','Deliverable',ds.map(d=>({value:d.id,label:d.name})))}${input('objection-reason','Explain what needs to change','','text',true,'At least 10 characters. Disputes require human resolution; AI cannot release payment.')}<button class="primary">Submit objection</button></form>`,()=>{$('objection-form').onsubmit=e=>{e.preventDefault();run(async()=>{await api(url(base+'/'+encodeURIComponent(mid)+'/object'),{kind:$('objection-kind').value,reason:$('objection-reason').value,deliverable_id:$('objection-deliverable').value});$('dialog').close();await refreshAssurance();});};});
  });
  container.querySelectorAll('[data-pay-reconcile]').forEach(b=>b.onclick=()=>run(async()=>{const result=await api(url(base+'/'+encodeURIComponent(b.dataset.payReconcile)+'/reconcile'),{});modal('Provider status',`<p>${esc(result.message)}</p><p>Status: ${esc(result.provider_status||'unknown')} · ID: ${esc(result.provider_id||'unknown')}</p>`); }));
  fundedPaymentControls(container);
  if(work)workTrackingLayout(container,a,ms,payments);else paymentWorkLinks(container);
}
function amendments(container){
  if(S.room.amendment_blocked_reason){
    container.innerHTML=`<div class="card"><h2>Agreement change options</h2><p>${esc(S.room.amendment_blocked_reason)}</p><p>Resolve the existing payment first. An amendment for remaining work will stay in this project and needs both signatures.</p><button id="review-locked-agreement">Review signed agreement</button><button id="review-amendment-payments">Open payments</button></div>`;
    $('review-locked-agreement').onclick=()=>{S.tab='Agreement';renderProject();};$('review-amendment-payments').onclick=()=>{S.tab='Payments';renderProject();};return;
  }
  const locked=S.room.amendment_locks?.milestone_ids||[];
  container.innerHTML=`<div class="card"><h2>Amendments</h2><p>${locked.length?'Amend the remaining unfunded milestones in this project. Funded milestones, their deliverables, currency and shared legal terms stay locked.':'Propose a change to the signed terms.'} Both parties must approve and sign the new version.</p>${locked.length?`<p class="help">Funded / locked: ${locked.map(esc).join(', ')}</p>`:''}<button class="primary" id="propose-amendment">${locked.length?'Amend remaining milestones':'Propose amendment'}</button></div>`;
  $('propose-amendment').onclick=()=>run(()=>perform('amend'));
}

function stopUpdates(){if(S.events)S.events.close();S.events=null;clearInterval(S.poll);S.poll=null;}
function startUpdates(){
  stopUpdates();const pid=S.room.project.id;
  const refresh=async()=>{
    if(!S.user||Date.now()-S.lastActivity>29*60000){stopUpdates();return;}
    if(S.dirty||S.saving||$('dialog').open||document.querySelector('[data-field]:focus')||$('chat-body')?.value)return;
    try{const r=await api(url(`projects/${pid}`));if(S.room?.project.id!==pid)return;const pay=r.project.active_agreement_id?await api(url(`projects/${pid}/assurance`)):null;if(r.revision!==S.room.revision||r.messages.length!==S.room.messages.length||JSON.stringify(pay)!==JSON.stringify(S.assurance)){const scrollX=window.scrollX,scrollY=window.scrollY;
      const active=document.activeElement,focusId=active?.id,selectionStart=active?.selectionStart,selectionEnd=active?.selectionEnd;
      const log=$('chat-log'),chatTop=log?.scrollTop,chatBottom=log?log.scrollHeight-log.clientHeight-log.scrollTop<24:false;
      S.room=r;S.assurance=pay;await loadWorkflow();renderProject();
      if(focusId){const field=$(focusId);if(field){field.focus({preventScroll:true});if(typeof selectionStart==='number'&&field.setSelectionRange)field.setSelectionRange(selectionStart,selectionEnd);}}
      const newLog=$('chat-log');if(newLog&&chatTop!==undefined)newLog.scrollTop=chatBottom?newLog.scrollHeight:chatTop;
      window.scrollTo(scrollX,scrollY);}}catch(e){$('connection').textContent=navigator.onLine?'Updates paused. Reload to retry.':'Offline. Your local draft is preserved.';}
  };
  S.events=new EventSource(url(`projects/${pid}/events`));S.events.addEventListener('update',()=>refresh());S.events.onerror=()=>{$('connection').textContent=navigator.onLine?'':'Offline. Changes are saved on this device.';};
  S.poll=setInterval(refresh,8000);
}
async function route(){
  persist();stopUpdates();S.room=null;S.dirty=false;$('error').textContent='';$('content').innerHTML='<p class="loading">Loading your workspace…</p>';
  const parts=location.pathname.split('/').filter(Boolean);S.role=parts[0]==='freelancer'?'freelancer':'client';
  if(S.user?.role&&S.user.role!==S.role){location.assign(`/${S.user.role}/dashboard`);return;}
  frame();refreshNotifications();const page=parts[1]||'login';
  try{
    if(!S.user){authForm(page==='signup'?'signup':'login');if(location.hash.startsWith('#reset='))resetForm(location.hash.slice(7));return;}
    if(!S.user.verified&&page!=='mail'){go(`/${S.role}/mail`);return;}
    if(page==='login'||page==='signup'){go(`/${S.role}/dashboard`);return;}
    if(!S.schema&&page!=='mail')S.schema=await api(url('agreement-schema'));
    if(page==='projects'&&parts[2]){let tab=new URLSearchParams(location.search).get('tab');if(tab==='Deliverables')tab='Work Tracking';if(['Overview','Agreement','Milestones','Payments','Work Tracking','Change Requests','Terms Summary','Chat','Resolution','Activity','Amendments'].includes(tab))S.tab=tab;await openProject(parts[2]);return;}
    if(page==='support'){await supportQueue();return;}
    if(page==='new'){await newProject();return;}
    if(page==='mail'){await mail();return;}
    if(page==='inbox'){await inbox();return;}
    if(page==='profile'){await profile();return;}
    if(page==='notifications'){await notifications();return;}
    await dashboard();
  }catch(e){$('content').innerHTML='<div class="card empty"><h2>This page could not be loaded.</h2><p>Reload the page to retry.</p></div>';fail(e);}finally{syncAssistantWidget();}
}
async function boot(){try{const me=await api('/api/auth/me');S.user=me.user;S.csrf=me.csrf_token;S.emailMode=me.email_mode;if(S.user.role&&location.pathname==='/'){history.replaceState({},'',`/${S.user.role}/dashboard`);}}catch{}await route();}
boot();

async function reviewWithAI(){
  await saveDraft();const button=$('ai-review');button.disabled=true;button.textContent='Reviewing every field and the conversation?';
  try{const result=await api(url(`projects/${S.room.project.id}/ai-review`),{revision:S.room.revision});S.aiReview=result;
    modal('AI agreement review',`<p>${esc(result.summary)}</p>${result.issues.map((i,n)=>`<div class="card"><strong>${esc(i.field_path)}</strong><p>${esc(i.explanation)}</p><p>${esc(i.question)}</p><button data-ai-question="${n}">Ask in project chat</button></div>`).join('')}${result.suggestions.map((i,n)=>`<div class="card"><strong>${esc(i.field_path)}</strong><p>${esc(i.reason)}</p><pre class="ai-value">${esc(agreementValue(JSON.parse(i.proposed_value_json),i.field_path))}</pre><button data-ai-apply="${n}" ${!S.room.can_edit||!['both',S.role].includes(fieldOwner(i.field_path))?'disabled':''}>Review & apply this suggestion</button></div>`).join('')}`,()=>{
      document.querySelectorAll('[data-ai-question]').forEach(b=>b.onclick=()=>run(async()=>{const i=result.issues[Number(b.dataset.aiQuestion)];await api(url(`projects/${S.room.project.id}/chat`),{body:'AI-assisted clarification: '+i.question,field_path:i.field_path});$('dialog').close();S.tab='Chat';S.room=await api(url('projects/'+S.room.project.id));renderProject();}));
      document.querySelectorAll('[data-ai-apply]').forEach(b=>b.onclick=()=>run(async()=>{if(S.room.revision!==result.revision)throw Error('Review is stale. Run AI review again.');const i=result.suggestions[Number(b.dataset.aiApply)];const candidate=JSON.parse(JSON.stringify(S.room.agreement));put(candidate,i.field_path,JSON.parse(i.proposed_value_json));const updated=await api(url(`projects/${S.room.project.id}/draft`),{revision:S.room.revision,agreement:candidate,review_conversation_hash:result.conversation_hash,review_agreement_hash:result.agreement_hash},'PUT');S.room=updated;S.dirty=false;$('dialog').close();S.tab='Agreement';renderProject();toast('Suggestion applied to your draft. Review validation flags and send it for counterpart approval.');}));
    });
  }finally{if($('ai-review')){$('ai-review').disabled=false;$('ai-review').textContent='AI review of terms & conversation';}}
}
