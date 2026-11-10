'use strict';
const el = id => document.getElementById(id),
  esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (n, currency = state.room?.project.currency || 'USD') => new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(n / 100);
const state = {
  user: null,
  csrf: '',
  room: null,
  tab: 'Smart Proposal',
  health: {},
  currencies: { currencies: [] },
  draft: null,
  portalRole: localStorage.getItem('fairflow_portal') || 'client',
  pollInterval: null
};

function currencyDecimals(code) { return state.currencies.currencies.find(c => c.code === code)?.decimals ?? 2 }
function priceValue(value, code) {
  const text = String(value).trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(text)) throw Error('Enter a non-negative amount with at most two decimal places.');
  const [whole, fraction = ''] = text.split('.'), amount = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (!Number.isSafeInteger(amount) || amount > 100000000) throw Error('Amount exceeds the supported limit.');
  if (currencyDecimals(code) === 0 && amount % 100) throw Error(code + ' requires whole-unit amounts.');
  return amount;
}
function currencyInputs() {
  const whole = currencyDecimals(el('currency')?.value || 'USD') === 0;
  document.querySelectorAll('#price,#rate,.mp').forEach(x => { x.step = whole ? '1' : '0.01'; x.min = whole ? '1' : '0.01' });
}
function toast(s) {
  el('toast').textContent = s;
  el('toast').style.display = 'block';
  clearTimeout(state.toast);
  state.toast = setTimeout(() => el('toast').style.display = 'none', 5000);
}
async function api(path, body) {
  const r = await fetch('/api' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const data = await r.json();
  if (!r.ok) throw Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  return data;
}
async function busy(button, fn) {
  if (button) button.disabled = true;
  try { await fn(); }
  catch (e) { toast(e.message); }
  finally { if (button) button.disabled = false; }
}
function field(id, label, value = '', type = 'text') { return `<label>${label}<input id="${id}" type="${type}" value="${esc(value)}" required></label>` }
function area(id, label, value = '', rows = 3) { return `<label>${label}<textarea id="${id}" rows="${rows}" required>${esc(value)}</textarea></label>` }
function pill(v) { return `<span class="pill">${esc(String(v || '').replaceAll('_', ' '))}</span>` }
function modal(html) { el('modal-body').innerHTML = html; el('modal').showModal(); }

function identity() {
  const roleName = state.portalRole === 'client' ? 'Client Platform' : 'Freelancer Studio';
  const roleIcon = state.portalRole === 'client' ? '👔' : '💻';
  el('identity').textContent = state.user ? `${roleIcon} ${state.user.name} (${roleName}) · ${state.user.email}` : 'Not signed in';
  const badgeSidebar = el('portal-badge-sidebar');
  if (badgeSidebar) badgeSidebar.textContent = `${roleIcon} ${roleName.toUpperCase()}`;
  el('notice').textContent = state.user ?
    (state.user.verified ? `Two-Sided Platform · ${roleName} · USD · ` + (state.health.payment_mode === 'demo' ? 'PayPal Hold Escrow Simulation' : 'PayPal Sandbox test payments') : 'Verify your email using Local email preview before creating or joining projects.')
    : 'Sign in to your own account. Choose Client or Freelancer portal.';
}

function authForm(mode = 'login') {
  state.room = null;
  const isClient = state.portalRole === 'client';
  el('content').innerHTML = `
    <div class="auth-card">
      <div class="portal-choice">
        <button type="button" class="portal-tab-btn ${isClient ? 'active' : ''}" id="role-client">👔 Client (Hire & Fund Escrow)</button>
        <button type="button" class="portal-tab-btn ${!isClient ? 'active' : ''}" id="role-freelancer">💻 Freelancer (Deliver & Pitch)</button>
      </div>
      <p class="role-desc">
        ${isClient ? '<b>Client Portal:</b> Post project vision, review smart proposals, and authorize PayPal escrow holds safely with zero risk.' : '<b>Freelancer Studio:</b> Translate client vision into concrete deliverables, set plain-English guardrails, and receive protected payments.'}
      </p>

      <div class="demo-login-box" style="margin-bottom:18px;padding:12px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;text-align:center;">
        <div style="font-weight:700;font-size:13px;color:#166534;margin-bottom:8px;">⚡ Instant 1-Click Demo Sign-In</div>
        <div style="display:flex;gap:10px;">
          <button type="button" class="primary" id="btn-demo-client" style="flex:1;background:#0284c7;font-size:12px;padding:8px 6px;">👔 Client Portal Demo</button>
          <button type="button" class="primary" id="btn-demo-freelancer" style="flex:1;background:#7c3aed;font-size:12px;padding:8px 6px;">💻 Freelancer Studio Demo</button>
        </div>
        <div style="font-size:11px;color:#15803d;margin-top:8px;">
          Default credentials: <code>client@fairflow.com</code> / <code>Password-123456!</code>
        </div>
      </div>

      <h2>${mode === 'register' ? (isClient ? 'Create Client Account' : 'Create Freelancer Account') : 'Welcome back.'}</h2>
      <form id="auth-form">
        ${mode === 'register' ? field('name', 'Your name') : ''}
        ${field('email', 'Email', isClient ? 'client@fairflow.com' : 'freelancer@fairflow.com', 'email')}
        ${field('password', 'Password (12+ characters for new accounts)', 'Password-123456!', 'password')}
        <button class="primary full" type="submit">${mode === 'register' ? 'Create account' : 'Sign in'}</button>
      </form>
      <div class="form-actions">
        <button class="text-button" id="switch-auth">${mode === 'register' ? 'Already registered? Sign in' : 'Create an account'}</button>
        <button class="text-button" id="forgot">Forgot password?</button>
      </div>
      <p class="form-note">PayPal Hackathon project. Two-sided flow connected to PayPal escrow adapters.</p>
    </div>
  `;
  if (el('btn-demo-client')) {
    el('btn-demo-client').onclick = () => busy(el('btn-demo-client'), async () => {
      const r = await api('/auth/demo-login', { role: 'client' });
      state.user = r.user;
      state.csrf = r.csrf_token;
      state.portalRole = 'client';
      localStorage.setItem('fairflow_portal', 'client');
      identity();
      await home();
      await hashAction();
      toast('Signed in as Demo Client!');
    });
  }
  if (el('btn-demo-freelancer')) {
    el('btn-demo-freelancer').onclick = () => busy(el('btn-demo-freelancer'), async () => {
      const r = await api('/auth/demo-login', { role: 'freelancer' });
      state.user = r.user;
      state.csrf = r.csrf_token;
      state.portalRole = 'freelancer';
      localStorage.setItem('fairflow_portal', 'freelancer');
      identity();
      await home();
      await hashAction();
      toast('Signed in as Demo Freelancer!');
    });
  }
  el('role-client').onclick = () => { state.portalRole = 'client'; localStorage.setItem('fairflow_portal', 'client'); authForm(mode); };
  el('role-freelancer').onclick = () => { state.portalRole = 'freelancer'; localStorage.setItem('fairflow_portal', 'freelancer'); authForm(mode); };
  el('switch-auth').onclick = () => authForm(mode === 'register' ? 'login' : 'register');
  el('forgot').onclick = forgot;
  el('auth-form').onsubmit = e => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const body = { email: el('email').value, password: el('password').value };
      if (mode === 'register') body.name = el('name').value;
      const r = await api('/auth/' + mode, body);
      state.user = r.user;
      state.csrf = r.csrf_token;
      identity();
      await home();
      await hashAction();
    });
  };
}

function forgot() {
  modal(`<h2>Reset your password</h2><form id="forgot-form">${field('reset-email', 'Account email', '', 'email')}<button class="primary">Request reset link</button></form><p class="form-note">In local development, reset messages appear in the local mail store. A real sender is required for public use.</p><div id="forgot-token-preview" style="margin-top:12px;"></div>`);
  el('forgot-form').onsubmit = e => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const r = await api('/auth/forgot-password', { email: el('reset-email').value });
      toast(r.message);
      if (r.reset_url) {
        el('forgot-token-preview').innerHTML = `<div style="padding:10px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;font-size:13px;"><b style="color:#166534;">Local mode password reset:</b><br><a href="${esc(r.reset_url)}" style="color:#2563eb;font-weight:600;">👉 Click here to set your new password</a></div>`;
      } else {
        el('modal').close();
      }
    });
  };
}

async function home() {
  if (state.pollInterval) { clearInterval(state.pollInterval); state.pollInterval = null; }
  if (!state.user) { authForm(); return; }
  state.room = null;
  identity();
  if (!state.user.verified) {
    el('content').innerHTML = `
      <div class="empty-state">
        <h2>Check your local email preview.</h2>
        <p>Open the verification link, or click below to immediately verify this account.</p>
        <div style="display:flex;gap:10px;justify-content:center;margin-top:12px;">
          <button class="primary" id="instant-verify-btn" style="background:#15803d;">⚡ Instant Verify Account</button>
          <button class="secondary" id="verify-mail">Open local email preview</button>
        </div>
      </div>
    `;
    el('verify-mail').onclick = () => busy(null, mail);
    el('instant-verify-btn').onclick = () => busy(el('instant-verify-btn'), async () => {
      const msgs = await api('/auth/mail');
      const v = msgs.find(m => m.body.includes('#verify='));
      if (v) {
        const token = v.body.split('#verify=')[1];
        await api('/auth/verify', { token });
        state.user.verified = true;
        toast('Account verified!');
        await home();
      } else {
        toast('No verification email found. Click Open local email preview.');
      }
    });
    return;
  }
  const projects = await api('/platform/projects');
  const isClient = state.portalRole === 'client';
  const roleIcon = isClient ? '👔' : '💻';
  const roleTitle = isClient ? 'Client Workspace' : 'Freelancer Studio';
  const roleDesc = isClient ? 'Manage your hiring, plain-English vision, and authorize PayPal holds.' : 'Draft smart proposals with Gemini AI, set guardrails, and deliver work.';

  const actionsHtml = isClient
    ? `<div style="display:flex;gap:8px;">
         <button class="primary" id="btn-two-sided-create" style="background:#2563eb;font-weight:600;">✨ Start Two-Sided Project</button>
         <button class="secondary" id="create-project">＋ Advanced Agreement</button>
       </div>`
    : `<button class="primary" id="create-project">＋ New agreement</button>`;

  el('content').innerHTML = `
    <div class="section-heading">
      <div>
        <span class="portal-tag ${isClient ? 'client' : 'freelancer'}">${roleIcon} ${roleTitle}</span>
        <h2>My projects <span>${projects.length}</span></h2>
        <p style="margin:4px 0 0;font-size:12px;color:#64748b;">${roleDesc}</p>
      </div>
      ${actionsHtml}
    </div>
    <div class="project-list">
      ${projects.map(p => {
        const sp = p.smart_proposal;
        const spBadge = sp?.status === 'hold_authorized'
          ? '<span class="pill" style="background:#dcfce7;color:#15803d;font-weight:600;">✓ Hold Authorized</span>'
          : (sp?.status === 'sent_to_client' ? '<span class="pill" style="background:#fef3c7;color:#b45309;font-weight:600;">Proposal Ready</span>' : '');
        return `
          <button class="project-card" data-project="${p.id}">
            <div class="card-top">
              <h3>${esc(p.contract.title)}</h3>
              <div>${pill(p.status)} ${spBadge}</div>
            </div>
            <p>${esc(p.client_name)} (Client) · ${esc(p.freelancer_name)} (Freelancer)</p>
            <div class="card-bottom">
              <b>${money(p.contract.price_cents, p.currency || 'USD')}</b>
              <span>${p.milestones.length} milestone${p.milestones.length === 1 ? '' : 's'}</span>
            </div>
          </button>
        `;
      }).join('')}
    </div>
    ${projects.length ? '' : `
      <div class="empty-state">
        <h2>Welcome to the ${roleTitle}.</h2>
        <p>Create a project or accept an invitation from your local email preview to experience the two-sided collaborative flow.</p>
        <div style="margin-top:14px;display:flex;gap:10px;justify-content:center;">
          ${isClient ? '<button class="primary" id="btn-two-sided-first" style="background:#2563eb;font-weight:600;">✨ Start Two-Sided Project</button>' : ''}
          <button class="${isClient ? 'secondary' : 'primary'}" id="create-first-project">＋ ${isClient ? 'Advanced Agreement' : 'Start New Project Agreement'}</button>
        </div>
      </div>
    `}
  `;
  el('create-project').onclick = () => createForm();
  if (el('create-first-project')) el('create-first-project').onclick = () => createForm();
  if (el('btn-two-sided-create')) el('btn-two-sided-create').onclick = () => twoSidedModal();
  if (el('btn-two-sided-first')) el('btn-two-sided-first').onclick = () => twoSidedModal();
  document.querySelectorAll('[data-project]').forEach(b => b.onclick = () => busy(b, () => openRoom(b.dataset.project)));
}


function twoSidedModal() {
  modal(`
    <h2>✨ Start Two-Sided Collaborative Project</h2>
    <p>Describe your project vision in plain English. Your freelancer will use Gemini AI to draft concrete deliverables, plain-English guardrails, and delivery timeline (strictly capped at 21 days).</p>
    <form id="quick-two-sided-form">
      ${field('qts-title', 'Project Title', 'Bakery Landing Page', 'text')}
      ${field('qts-counterparty', 'Freelancer Email', '', 'email')}
      <label><b>The Big Picture:</b> In your own words, what is the goal of this milestone?
        <textarea id="qts-goal" rows="3" placeholder="e.g. I need a landing page that captures emails for my new bakery." required>I need a landing page that captures emails for my new bakery.</textarea>
      </label>
      <label style="margin-top:10px;"><b>Assets & Inspiration:</b> Drop Figma links, reference websites, or brand colors
        <textarea id="qts-assets" rows="2" placeholder="e.g. Figma: https://figma.com/..., Colors: warm gold and cream">Figma: https://figma.com/file/bakery-preview, Colors: warm gold and cream, Ref: sweetgreen.com</textarea>
      </label>
      <div class="form-actions" style="margin-top:16px;">
        <button class="primary full" type="submit">Launch Collaborative Workspace</button>
      </div>
    </form>
  `);
  el('quick-two-sided-form').onsubmit = (e) => busy(e.submitter, async () => {
    e.preventDefault();
    const title = el('qts-title').value.trim();
    const counterparty_email = el('qts-counterparty').value.trim();
    const client_goal = el('qts-goal').value.trim();
    const assets_inspiration = el('qts-assets').value.trim();
    if (!title || !counterparty_email || !client_goal) {
      toast('Please provide project title, freelancer email, and your goal.');
      return;
    }
    const r = await api('/platform/projects/two-sided-quickstart', {
      title, counterparty_email, client_goal, assets_inspiration
    });
    el('modal').close();
    state.room = r;
    state.tab = 'Smart Proposal';
    renderRoom();
    toast('🎉 Collaborative workspace launched! Invitation sent to freelancer.');
  });
}

async function mail() {
  if (!state.user) throw Error('Sign in to view your own local email.');
  const messages = await api('/auth/mail');
  modal(`<h2>Your local email preview</h2><p>Development preview for ${esc(state.user.email)}. No external messages were sent.</p>${messages.reverse().map(m => {
    let link = '';
    try {
      const u = new URL(m.body);
      if (u.origin === location.origin) link = `<a class="secondary" href="${esc(m.body)}">Open account / invitation link</a>`;
    } catch { }
    return `<article class="room-panel"><h3>${esc(m.subject)}</h3>${link || `<p>${esc(m.body)}</p>`}<small>${esc(new Date(m.created_at).toLocaleString())}</small></article>`;
  }).join('') || '<p>No messages yet.</p>'}`);
}

async function hashAction() {
  const params = new URLSearchParams(location.hash.slice(1));
  if (params.has('reset')) {
    const token = params.get('reset');
    modal(`<h2>Choose a new password</h2><form id="reset-form">${field('new-password', 'New password', '', 'password')}<button class="primary">Reset password</button></form>`);
    el('reset-form').onsubmit = e => {
      e.preventDefault();
      busy(e.submitter, async () => {
        await api('/auth/reset-password', { token, password: el('new-password').value });
        history.replaceState({}, '', '/');
        el('modal').close();
        state.user = null;
        state.csrf = '';
        identity();
        authForm();
        toast('Password changed. Sign in again.');
      });
    };
    return;
  }
  if (!state.user) return;
  if (params.has('verify')) {
    await api('/auth/verify', { token: params.get('verify') });
    const me = await api('/auth/me');
    state.user = me.user;
    state.csrf = me.csrf_token;
    el('modal').close();
    history.replaceState({}, '', '/');
    await home();
    toast('Email verified.');
  }
  if (params.has('invite')) {
    if (!state.user.verified) {
      toast('Verify your account, then open the invitation link again.');
      return;
    }
    const r = await api('/platform/invitations/accept', { token: params.get('invite') });
    el('modal').close();
    history.replaceState({}, '', '/');
    state.room = r;
    state.tab = 'Agreement';
    renderRoom();
    toast('You joined the project. Review the Smart Proposal before funding.');
  }
}

const SAMPLE = 'Client: Build a responsive coffee-brand landing page, with hero, menu, about and contact details. No database or customer accounts. Freelancer: $900 for the project, with 2 revisions per milestone. Additional work is $50 per hour. Client: Agreed.';

async function createForm(existing = null) {
  const blueprint = await api('/platform/agreement-fields');
  if (!state.user?.verified) { toast('Verify your email first.'); return; }
  state.draft = existing;
  modal(`<h2>${existing ? 'Propose revised terms' : 'Create a shared agreement'}</h2><p>Review every clause. Both parties must accept the exact version before funding.</p><form id="project-form"><section class="creation-step"><h3>1. Participants</h3><label>Your role<select id="creator-role" required><option value="">Choose your role</option><option value="client" ${state.portalRole === 'client' ? 'selected' : ''}>Client</option><option value="freelancer" ${state.portalRole === 'freelancer' ? 'selected' : ''}>Freelancer</option></select></label>${field('counterparty', 'Other participant’s email', '', 'email')}<div class="two-col">${field('client-name', 'Client display name', state.portalRole === 'client' ? state.user.name : '')}${field('freelancer-name', 'Freelancer display name', state.portalRole === 'freelancer' ? state.user.name : '')}</div></section><section class="creation-step"><h3>2. Draft the work</h3><p>${state.health.ai_mode === 'gemini' ? 'AI extraction: Gemini. Review every proposed term.' : 'Extraction: offline demo rules. These rules can miss meaning and do not establish that the conversation reached agreement.'}</p>${area('chat', 'Conversation', existing?.source_conversation || existing?.contract.task_description || '', 4)}${field('count', 'Number of milestones', 3, 'number')}<div class="form-actions"><button type="button" class="secondary" id="sample">Use complete demo example</button><button type="button" class="primary" id="extract">Extract and plan</button></div></section><p id="draft-feedback" class="warning" role="alert" hidden></p><div id="terms" hidden><section class="creation-step"><h3>3. Review the baseline</h3><label>Agreement currency<select id="currency">${(state.health.currency_options || ['USD']).map(c => `<option value="${c}" ${c === (existing?.currency || 'USD') ? 'selected' : ''}>${c}</option>`).join('')}</select></label><p class="form-note">Changing currency clears prices for explicit re-entry; this does not convert a budget. PayPal supplies applicable conversion rates and fees at eligible checkout. FairFlow adds no FX markup. Country and account restrictions apply.</p>${field('title', 'Project name')}${area('scope', 'Scope')}<div class="three-col">${field('price', 'Total in agreement currency', 0, 'number')}${field('revisions', 'Revisions per milestone', 2, 'number')}${field('rate', 'Hourly rate in agreement currency for proposed additions', 50, 'number')}</div>${area('deliverables', 'Deliverables, one per line')}${area('exclusions', 'Exclusions, one per line')}</section><section class="creation-step"><h3>4. Define the agreement</h3>${area('acceptance', 'Objective acceptance criteria')}${area('defect', 'Defect correction policy')}${area('revision-policy', 'Included revision definition')}</section><section class="creation-step"><h3>5. Review milestone prices and work items</h3><p class="form-note">Prices must sum to the total. Optional priced work items use one line each: title | currency amount | acceptance criteria. If left empty, the entire milestone is one priced item.</p><div id="plan"></div></section><section class="creation-step"><h3>6. Complete the agreement terms</h3><p><strong>Demo examples are illustrative, including their example jurisdiction. Replace them with terms appropriate for your actual participants.</strong></p><p>Every field is required. Explain any non-applicable term. These terms are recorded for both participants; review them with appropriate advice before real use.</p>${Object.entries(blueprint.fields).map(([key, label]) => area('term-' + key, label, existing?.details?.[key] || '', 2)).join('')}<p>Platform rules: calendar days from authorization, five-day delivery reviews, revisions per milestone, joint dispute resolution and separately approved additions. No automatic legal dispute resolution.</p></section><section class="creation-step"><h3>7. Final consistency review</h3><p>Validate all fields, then submit. Editing any field requires validation again.</p><button class="secondary" type="button" id="validate-agreement">Validate complete agreement</button><div id="agreement-errors" role="alert"></div></section><button class="primary full" type="submit">${existing ? 'Propose new version' : 'Create project and invitation'}</button></div></form>`);
  const form = el('project-form');
  el('count').min = '1'; el('count').max = '12'; el('count').step = '1';
  el('revisions').min = '0'; el('revisions').max = '100'; el('revisions').step = '1';
  const invalidate = () => { state.draft = null; el('terms').hidden = true; el('draft-feedback').hidden = false; el('draft-feedback').textContent = 'Conversation or milestone count changed. Extract again before creating an agreement.'; };
  el('chat').oninput = invalidate; el('count').oninput = invalidate;
  el('sample').onclick = () => {
    invalidate();
    el('chat').value = SAMPLE;
    if (blueprint.example) for (const key of Object.keys(blueprint.fields)) el('term-' + key).value = blueprint.example[key];
    el('acceptance').value = 'The page works at the agreed mobile and desktop widths, with all agreed content present.';
    el('defect').value = 'Failures to meet accepted criteria must be corrected without consuming presentation revisions.';
    el('revision-policy').value = 'A revision is one consolidated round of presentation changes within the agreed deliverables.';
  };
  el('extract').onclick = () => busy(el('extract'), async () => {
    const source = el('chat').value, count = Number(el('count').value);
    state.draft = null; el('terms').hidden = true; el('draft-feedback').hidden = false; el('draft-feedback').textContent = 'Checking the conversation and drafting proposed terms...';
    try {
      const d = await api('/agile/draft', { chat_history: source, milestone_count: count });
      if (el('project-form') !== form) return;
      if (el('chat').value !== source || Number(el('count').value) !== count) { invalidate(); return; }
      fillTerms(d);
      el('draft-feedback').textContent = (state.health.ai_mode === 'gemini' ? 'Gemini API draft. ' : 'Offline demo draft. ') + 'Review the final price and scope; both participants must accept independently.';
    } catch (error) {
      if (el('project-form') === form) { state.draft = null; el('terms').hidden = true; el('draft-feedback').textContent = error.message; }
      throw error;
    }
  });
  el('currency').onchange = () => {
    for (const id of ['price', 'rate']) el(id).value = '';
    document.querySelectorAll('.mp,.mi').forEach(x => x.value = '');
    currencyInputs();
    toast('Enter agreed amounts in the selected currency. No automatic conversion was applied.');
  };
  const collectAgreement = () => {
    if (!state.draft || el('chat').value !== (state.draft.source_conversation || state.draft.contract.task_description)) throw Error('Conversation changed. Extract and review the current terms before creating an agreement.');
    const milestones = [...document.querySelectorAll('.plan-edit')].map(r => ({ title: r.querySelector('.mt').value, scope: r.querySelector('.ms').value, amount_cents: priceValue(r.querySelector('.mp').value, el('currency').value), duration_days: Number(r.querySelector('.md').value) }));
    const items = [...document.querySelectorAll('.plan-edit')].map((r, i) => {
      const value = r.querySelector('.mi').value.trim();
      return value ? value.split('\n').filter(Boolean).map(line => {
        const bits = line.split('|');
        if (bits.length !== 3) throw Error('Each work item needs title | currency amount | acceptance criteria.');
        return { title: bits[0].trim(), amount_cents: priceValue(bits[1], el('currency').value), acceptance: bits[2].trim() };
      }) : [{ title: milestones[i].title, amount_cents: milestones[i].amount_cents, acceptance: milestones[i].scope }];
    });
    const contract = { title: el('title').value, task_description: el('scope').value, price_cents: priceValue(el('price').value, el('currency').value), allowed_revisions: Number(el('revisions').value), hourly_rate_cents: priceValue(el('rate').value, el('currency').value), deliverables: el('deliverables').value.split('\n').filter(Boolean), exclusions: el('exclusions').value.split('\n').filter(Boolean) };
    const clauses = [{ reference: 'DELIVERY', kind: 'deliverable', text: contract.deliverables.join('\n') }, { reference: 'ACCEPTANCE', kind: 'acceptance', text: el('acceptance').value }, { reference: 'DEFECTS', kind: 'defect_policy', text: el('defect').value }, { reference: 'REVISIONS', kind: 'revision_policy', text: el('revision-policy').value }];
    if (contract.exclusions.length) clauses.push({ reference: 'EXCLUSIONS', kind: 'exclusion', text: contract.exclusions.join('\n') });
    const details = Object.fromEntries(Object.keys(blueprint.fields).map(key => [key, el('term-' + key).value]));
    Object.assign(details, { review_days: 5, schedule_basis: 'calendar_days', dispute_process: 'joint_agreement', revision_basis: 'per_milestone' });
    const body = { details, source_conversation: el('chat').value, currency: el('currency').value, contract, milestones, work_items: items, creator_role: el('creator-role').value, counterparty_email: el('counterparty').value, client_name: el('client-name').value, freelancer_name: el('freelancer-name').value, clauses };
    return body;
  };
  const validateComplete = async () => {
    el('agreement-errors').innerHTML = '';
    try {
      const body = collectAgreement(), snapshot = JSON.stringify(body);
      const result = await api('/platform/agreements/validate', body);
      if (JSON.stringify(collectAgreement()) !== snapshot) { el('agreement-errors').textContent = 'Fields changed during validation. Validate the current agreement again.'; throw Error('Fields changed during validation. Validate the current agreement again.'); }
      if (!result.valid) { el('agreement-errors').innerHTML = '<p>Correct these fields before proceeding:</p><ul>' + result.issues.map(x => `<li>${esc(x.field)}: ${esc(x.message)}</li>`).join('') + '</ul>'; throw Error('Agreement validation failed. Review the listed fields.'); }
      el('agreement-errors').textContent = 'All required fields and supported consistency checks passed. ' + (result.semantic_review?.mode === 'gemini' ? 'Gemini semantic review: ' + (result.semantic_review.provider?.model || 'provider confirmed') + '. ' : 'Offline rules only. ') + 'Both participants must still review these terms.';
      return body;
    } catch (error) {
      if (!el('agreement-errors').textContent) el('agreement-errors').textContent = error.message;
      throw error;
    }
  };
  el('validate-agreement').onclick = () => busy(el('validate-agreement'), validateComplete);
  form.addEventListener('input', () => { el('agreement-errors').textContent = 'Fields changed. Validate the complete agreement again before proceeding.'; });
  el('project-form').onsubmit = e => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const body = await validateComplete();
      state.room = await api(existing ? `/platform/projects/${state.room.project.id}/agreements` : '/platform/projects', body);
      el('modal').close();
      state.tab = 'Agreement';
      renderRoom();
      toast('Agreement saved. Each participant must accept independently.');
    });
  };
  if (existing) {
    fillTerms(existing);
    el('creator-role').value = state.room.role;
    el('client-name').value = state.room.project.client_name;
    el('freelancer-name').value = state.room.project.freelancer_name;
    for (const [id, kind] of [['acceptance', 'acceptance'], ['defect', 'defect_policy'], ['revision-policy', 'revision_policy']])
      el(id).value = existing.clauses.find(c => c.kind === kind)?.text || '';
  }
}

function fillTerms(d) {
  state.draft = d;
  if (!d.currency) el('currency').value = 'USD';
  else el('currency').value = d.currency;
  for (const [id, key] of [['title', 'title'], ['scope', 'task_description']]) el(id).value = d.contract[key];
  el('price').value = d.contract.price_cents / 100;
  el('revisions').value = d.contract.allowed_revisions;
  el('rate').value = d.contract.hourly_rate_cents / 100;
  el('deliverables').value = d.contract.deliverables.join('\n');
  el('exclusions').value = d.contract.exclusions.join('\n');
  el('plan').innerHTML = d.milestones.map((m, i) => `<fieldset class="plan-edit plan-row"><legend>Milestone ${i + 1}</legend><label>Title<input class="mt" value="${esc(m.title)}" required></label><label>Scope & acceptance<textarea class="ms" required>${esc(m.scope)}</textarea></label><div class="two-col"><label>Amount<input class="mp" type="number" min="0.01" step="0.01" value="${m.amount_cents / 100}" required></label><label>Days (14–21)<input class="md" type="number" min="14" max="21" value="${m.duration_days}" required></label></div><label>Optional priced work items<textarea class="mi" rows="2" placeholder="Layout | 150.00 | Matches approved design">${esc((d.work_items?.[i] || []).map(x => `${x.title} | ${x.amount_cents / 100} | ${x.acceptance}`).join('\n'))}</textarea></label></fieldset>`).join('');
  el('terms').hidden = false;
  currencyInputs();
}

async function openRoom(pid) {
  state.room = await api('/platform/projects/' + pid);
  renderRoom();
}

function renderRoom() {
  identity();
  if (state.pollInterval) { clearInterval(state.pollInterval); state.pollInterval = null; }
  const r = state.room, p = r.project;
  const isClient = r.role === 'client';
  const roleBadge = isClient
    ? '<span class="badge-role client">👔 Client Workspace</span>'
    : '<span class="badge-role freelancer">💻 Freelancer Studio</span>';

  el('content').innerHTML = `
    <div class="room-head">
      <div>
        <span class="eyebrow">${roleBadge} · Partner: ${esc(isClient ? p.freelancer_name : p.client_name)}</span>
        <h2>${esc(p.contract.title)}</h2>
      </div>
      <div>${pill(p.status)} <button class="secondary" id="refresh-room">Refresh</button></div>
    </div>
    <div class="tabs">
      ${['Smart Proposal', 'Messages', 'Milestones', 'AI review', 'Agreement', 'Payments', 'Activity'].map(t => `<button class="tab ${state.tab === t ? 'active' : ''}" data-tab="${t}">${t}</button>`).join('')}
    </div>
    <div id="room-body"></div>
  `;

  document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => {
    state.tab = b.dataset.tab;
    renderRoom();
  });
  el('refresh-room').onclick = () => busy(el('refresh-room'), () => openRoom(p.id));

  const builders = {
    'Smart Proposal': smartProposalView,
    'Messages': messagesView,
    'Milestones': milestoneView,
    'AI review': decisionView,
    'Agreement': agreementView,
    'Payments': paymentsView,
    'Activity': activityView
  };

  el('room-body').innerHTML = (builders[state.tab] || smartProposalView)(r);
  bindRoom(r);

  if (state.tab === 'Messages') {
    state.pollInterval = setInterval(async () => {
      try {
        const updated = await api('/platform/projects/' + p.id);
        if (updated.messages.length !== state.room.messages.length) {
          state.room = updated;
          const stream = el('chat-stream');
          if (stream) {
            stream.innerHTML = updated.messages.map(renderChatBubble).join('') || '<p style="text-align:center;color:#94a3b8;margin:auto;">No messages yet.</p>';
            stream.scrollTop = stream.scrollHeight;
          }
        }
      } catch { }
    }, 3000);
  }
}

function renderChatBubble(m) {
  const isMine = m.user_id === state.user?.id;
  const roleLabel = m.role === 'client' ? '👔 Client' : (m.role === 'freelancer' ? '💻 Freelancer' : 'Participant');
  const isSystem = m.body.startsWith('📋') || m.body.startsWith('✅') || m.body.startsWith('💡');
  if (isSystem) {
    return `
      <div class="chat-bubble system">
        <strong>${esc(m.body)}</strong>
        <div class="chat-meta" style="justify-content:center;margin-top:4px;">${esc(new Date(m.created_at).toLocaleTimeString())}</div>
      </div>
    `;
  }
  return `
    <div class="chat-bubble ${isMine ? 'mine' : 'theirs'}">
      <div class="chat-meta">
        <span class="badge-role ${m.role || 'client'}">${roleLabel}</span>
        <span>${esc(new Date(m.created_at).toLocaleTimeString())}</span>
      </div>
      <p style="margin:4px 0;">${esc(m.body)}</p>
    </div>
  `;
}

function messagesView(r) {
  const p = r.project;
  const isFreelancer = r.role === 'freelancer';
  return `
    <article class="room-panel" style="padding:0;overflow:hidden;display:flex;flex-direction:column;height:550px;">
      <div style="padding:14px 18px;border-bottom:1px solid #e2e8f0;display:flex;justify-content:space-between;align-items:center;background:#f8fafc;">
        <div>
          <h3 style="margin:0;font-size:16px;">Two-Sided Real-Time Messaging</h3>
          <p style="margin:2px 0 0;font-size:12px;color:#64748b;">Communicate directly with role-tagged badges. Gemini AI can draft proposals from your discussion.</p>
        </div>
        ${isFreelancer ? '<button class="secondary" id="btn-chat-ai-draft" style="font-size:12px;padding:6px 12px;">✨ Draft Agreement with AI</button>' : ''}
      </div>
      <div class="chat-container" id="chat-stream" style="flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px;">
        ${r.messages.map(renderChatBubble).join('') || '<p style="text-align:center;color:#94a3b8;margin:auto;">No messages yet. Start the conversation below!</p>'}
      </div>
      <form id="chat-send-form" style="padding:12px 16px;border-top:1px solid #e2e8f0;display:flex;gap:10px;background:#fff;">
        <input type="text" id="chat-msg-input" placeholder="Type a message to your partner..." style="flex:1;" autocomplete="off" required />
        <button class="primary" type="submit">Send</button>
      </form>
    </article>
  `;
}

function smartProposalView(r) {
  const p = r.project, sp = p.smart_proposal || {};
  const isClient = r.role === 'client';
  const cv = sp.client_vision || {};
  const statusPill = sp.status === 'hold_authorized'
    ? '<span class="pill" style="background:#dcfce7;color:#15803d;font-weight:bold;">✓ ESCROW HOLD ACTIVE</span>'
    : (sp.status === 'sent_to_client'
      ? '<span class="pill" style="background:#fef3c7;color:#b45309;font-weight:bold;">PROPOSAL SENT TO CLIENT</span>'
      : '<span class="pill">DRAFTING PROPOSAL</span>');

  if (isClient) {
    return `
      <article class="room-panel">
        <div class="card-top">
          <div>
            <span class="portal-tag client">👔 Client Contribution</span>
            <h3>1. The Client's Contribution (Plain English & Vision)</h3>
          </div>
        </div>
        <p class="form-note">In your own words, describe your project vision and drop links to assets or inspirations.</p>
        <div class="vision-card">
          <label><b>The Big Picture:</b> A simple text box: "In your own words, what is the goal of this milestone?"
            <textarea id="client-vision-goal" rows="2" placeholder="e.g., I need a landing page that captures emails for my new bakery.">${esc(cv.goal || '')}</textarea>
          </label>
          <label style="margin-top:10px;"><b>Assets & Inspiration:</b> A place to drop Figma links, reference websites, or brand colors
            <textarea id="client-vision-assets" rows="2" placeholder="e.g., Figma: https://figma.com/..., Ref: stripe.com, Colors: warm gold and cream">${esc(cv.assets_inspiration || '')}</textarea>
          </label>
          <div style="margin-top:12px;">
            <button class="secondary" id="btn-save-vision">Save Project Vision & Assets</button>
          </div>
        </div>
      </article>

      <div class="checkout-card">
        <div class="checkout-header">
          <div>
            <span class="portal-tag client">Frictionless Checkout</span>
            <h3>2. The FairFlow Smart Proposal</h3>
          </div>
          <div>${statusPill}</div>
        </div>

        <div class="checkout-section">
          <h4>Section 1: The Goal <span>(Auto-filled by AI from chat)</span></h4>
          <div class="item-label">Project: ${esc(sp.section1?.project_title || p.contract.title)}</div>
          <div class="item-desc"><b>Phase 1 Milestone:</b> ${esc(sp.section1?.milestone_phase || 'Frontend Design & Build')}</div>
          <div class="item-desc" style="margin-top:6px;font-style:italic;">"${esc(sp.section1?.goal_summary || p.contract.task_description)}"</div>
        </div>

        <div class="checkout-section">
          <h4>Section 2: What You Get (The Deliverables) <span>(Added by freelancer · Read-only)</span></h4>
          ${(sp.section2?.deliverables || p.contract.deliverables || []).map(d => `
            <div class="deliverable-item">
              <span class="check-icon">✓</span>
              <span>${esc(d)}</span>
            </div>
          `).join('')}
        </div>

        <div class="checkout-section">
          <h4>Section 3: The Rules of Engagement <span>(Simple dropdowns & plain English)</span></h4>
          <div class="item-desc"><b>Revisions Included:</b> <span class="pill">${sp.section3?.revisions_included ?? p.contract.allowed_revisions} Rounds</span></div>
          <div class="item-desc" style="margin:8px 0;"><b>What counts as a revision?</b> "${esc(sp.section3?.revision_rules || 'Tweaking colors, changing images, or adjusting text sizes. New pages or new buttons are extra.')}"</div>
          <div style="margin-top:10px;">
            <div style="font-size:12px;font-weight:600;color:#991b1b;margin-bottom:6px;">Out of Scope (Not Included):</div>
            ${(sp.section3?.out_of_scope || p.contract.exclusions || []).map(o => `
              <div class="scope-out-item">
                <span class="x-icon">✕</span>
                <span>${esc(o)}</span>
              </div>
            `).join('')}
          </div>
        </div>

        <div class="checkout-section">
          <h4>Section 4: Escrow & Timeline</h4>
          <div class="checkout-price-row">
            <div>
              <div class="checkout-price-label">Milestone Price</div>
              <small style="color:#64748b;">Funds held in escrow; zero risk to you</small>
            </div>
            <div class="checkout-price-val">${money(sp.section4?.price_cents || p.contract.price_cents, p.currency || 'USD')}</div>
          </div>
          <div class="calc-date">
            📅 Delivery Date: ${esc(sp.section4?.delivery_date || ('within ' + (p.milestones[0]?.duration_days || 16) + ' days from authorization'))}
          </div>
        </div>

        <div style="margin-top:24px;">
          ${sp.status === 'sent_to_client' ? `
            <button class="paypal-auth-btn" id="btn-authorize-paypal-hold">
              🅿 Authorize PayPal Hold (${money(sp.section4?.price_cents || p.contract.price_cents, p.currency || 'USD')})
            </button>
            <p class="paypal-note">🔒 PayPal Authorization Hold: Places a temporary hold on your PayPal payment method. No money is captured until you verify and approve the deliverable.</p>
          ` : sp.status === 'hold_authorized' ? `
            <div class="badge-success">
              ✓ PayPal Escrow Hold Authorized (${money(sp.section4?.price_cents || p.contract.price_cents, p.currency || 'USD')}) — Milestone is Active!
            </div>
            <div style="text-align:center;margin-top:12px;">
              <button class="secondary" data-tab-nav="Milestones">View Deliverables & Milestones ↗</button>
            </div>
          ` : `
            <div class="warning">
              <b>Waiting for Freelancer:</b> The freelancer is preparing the deliverables checklist and guardrails. You can chat with them in the Messages tab or update your Vision above.
            </div>
          `}
        </div>
      </div>
    `;
  } else {
    const deliverables = sp.section2?.deliverables || ['3-Section Landing Page (Home, About, Contact)', 'Mobile and Tablet Responsive Design', 'Integration with Mailchimp for the email form'];
    const outOfScope = sp.section3?.out_of_scope || ['No logo design.', 'No backend database setup.', 'Does not include writing the marketing text.', 'Does not include buying the domain name.'];
    const duration = sp.section4?.duration_days || 16;
    const price = (sp.section4?.price_cents ? sp.section4.price_cents / 100 : (p.contract.price_cents / 100 || 450)).toFixed(2);

    return `
      <article class="room-panel">
        <div class="card-top">
          <div>
            <span class="portal-tag freelancer">💻 Freelancer Studio</span>
            <h3>Client's Contribution (Plain English & Vision)</h3>
          </div>
        </div>
        <div class="vision-card">
          <p><b>The Big Picture:</b> "${esc(cv.goal || 'Client has not provided vision text yet.')}"</p>
          <p><b>Assets & Inspiration:</b> "${esc(cv.assets_inspiration || 'None attached yet.')}"</p>
        </div>
        <div style="margin-top:16px;">
          <button class="ai-draft-btn" id="btn-trigger-ai-draft">✨ Draft Agreement with AI</button>
          <p class="form-note">Gemini reads your chat history and client vision, automatically extracting deliverables, guardrails, price, and <= 21 days timeline without contradiction.</p>
        </div>
      </article>

      <div class="canvas-card">
        <div class="checkout-header">
          <div>
            <span class="portal-tag freelancer">The Mechanics & Boundaries</span>
            <h3>The Freelancer's Contribution (Two-Sided Canvas)</h3>
          </div>
          <div>${statusPill}</div>
        </div>

        <form id="canvas-form">
          <div class="checkout-section">
            <h4>Section 1: The Goal <span>(Auto-filled by AI from chat/vision)</span></h4>
            <div class="two-col">
              <label>Project Title
                <input id="canvas-title" value="${esc(sp.section1?.project_title || p.contract.title || 'Bakery Landing Page')}" required>
              </label>
              <label>Phase 1 Milestone
                <input id="canvas-phase" value="${esc(sp.section1?.milestone_phase || 'Frontend Design & Build')}" required>
              </label>
            </div>
            <label style="margin-top:8px;">Goal Summary
              <textarea id="canvas-goal" rows="2" required>${esc(sp.section1?.goal_summary || p.contract.task_description || 'I need a landing page that captures emails for my new bakery.')}</textarea>
            </label>
          </div>

          <div class="checkout-section">
            <h4>Section 2: The Checklist (The Mechanics) <span>(Translating vision into concrete deliverables)</span></h4>
            <div id="canvas-deliv-container">
              ${deliverables.map((d, i) => `
                <div class="canvas-item-row" data-deliv-idx="${i}">
                  <input class="canvas-deliv-input" value="${esc(d)}" required>
                  <button type="button" class="btn-del-item" title="Remove deliverable">✕</button>
                </div>
              `).join('')}
            </div>
            <button type="button" class="btn-add-item" id="btn-add-deliv">＋ Add Deliverable</button>
          </div>

          <div class="checkout-section">
            <h4>Section 3: The "Not Included" Guardrails <span>(Plain-English boundaries instead of tech stacks)</span></h4>
            <div class="two-col">
              <label>Revisions Included
                <select id="canvas-revisions">
                  ${[1, 2, 3, 4, 5].map(n => `<option value="${n}" ${n === (sp.section3?.revisions_included ?? 2) ? 'selected' : ''}>${n} Rounds</option>`).join('')}
                </select>
              </label>
              <label>What counts as a revision?
                <input id="canvas-rev-rules" value="${esc(sp.section3?.revision_rules || 'Tweaking colors, changing images, or adjusting text sizes. New pages or new buttons are extra.')}" required>
              </label>
            </div>
            <div style="margin-top:12px;">
              <label style="font-weight:600;color:#991b1b;">Out of Scope (Not Included) Guardrails:</label>
              <div id="canvas-scope-container" style="margin-top:6px;">
                ${outOfScope.map((o, i) => `
                  <div class="canvas-item-row" data-scope-idx="${i}">
                    <input class="canvas-scope-input" value="${esc(o)}" required>
                    <button type="button" class="btn-del-item" title="Remove guardrail">✕</button>
                  </div>
                `).join('')}
              </div>
              <button type="button" class="btn-add-item" id="btn-add-scope">＋ Add Guardrail</button>
            </div>
          </div>

          <div class="checkout-section">
            <h4>Section 4: Escrow & Timeline <span>(Strict UI limit: <= 21 days)</span></h4>
            <div class="two-col">
              <label>Milestone Price ($)
                <input id="canvas-price" type="number" step="0.01" min="1" value="${price}" required>
              </label>
              <label>Delivery Duration in Calendar Days (1 to 21 max)
                <input id="canvas-days" type="number" min="1" max="21" value="${duration}" required>
              </label>
            </div>
            <div id="canvas-calc-preview" class="calc-date">
              Delivery Date: ${esc(sp.section4?.delivery_date || 'Nov 18, 2026 (16 days from today - safely within the 21-day platform limit)')}
            </div>
            <p id="canvas-warn-21" class="warning" hidden>Platform strictly limits milestone delivery to 21 days maximum.</p>
          </div>

          <div style="margin-top:24px;">
            ${sp.status === 'hold_authorized' ? `
              <div class="badge-success">
                ✓ PayPal Escrow Hold Authorized by Client · Work is Active!
              </div>
            ` : `
              <button class="primary full" type="submit" id="btn-send-to-client">
                Send to Client
              </button>
              <p class="form-note">Client reads the plain-English summary, sees the price, and clicks "Authorize PayPal Hold".</p>
            `}
          </div>
        </form>
      </div>
    `;
  }
}

function updateDeliveryDatePreview() {
  const daysInput = el('canvas-days');
  const preview = el('canvas-calc-preview');
  const warn = el('canvas-warn-21');
  if (!daysInput || !preview) return;
  let days = parseInt(daysInput.value, 10);
  if (isNaN(days) || days < 1) days = 1;
  if (days > 21) {
    if (warn) warn.hidden = false;
    days = 21;
    daysInput.value = 21;
  } else {
    if (warn) warn.hidden = true;
  }
  const target = new Date();
  target.setDate(target.getDate() + days);
  const options = { month: 'short', day: 'numeric', year: 'numeric' };
  preview.textContent = `Delivery Date: ${target.toLocaleDateString('en-US', options)} (${days} days from today - safely within the 21-day platform limit)`;
}

function agreementView(r) {
  const p = r.project, current = r.agreements.find(a => a.id === p.agreement_id), accepted = current ? current.acceptances.some(a => a.user_id === state.user.id) : false, issues = current ? (current.validation_issues || []) : [];
  if (!current) return `<article class="room-panel"><p>No formal agreement version created yet. Propose or generate one in Smart Proposal.</p></article>`;
  return `<article class="room-panel"><div class="card-top"><h3>Agreement version ${current.number}</h3>${pill(current.status)}</div>${issues.length ? `<div class="warning" role="alert"><b>Agreement needs clarification</b>${issues.map(issue => `<p>${esc(issue)}</p>`).join('')}<p>Propose revised terms with a clear current-terms summary before acceptance or funding.</p></div>` : ''}<p class="scope">${esc(current.payload.contract.task_description)}</p><p>${money(p.contract.price_cents, p.currency || 'USD')} baseline · ${p.contract.allowed_revisions} revisions per milestone</p>${current.payload.clauses.map(c => `<div class="clause"><code>${esc(c.reference)} · ${esc(c.kind)}</code><p>${esc(c.text)}</p></div>`).join('')}<h3>Recorded agreement terms</h3>${Object.entries(current.payload.details || {}).map(([key, value]) => `<div class="clause"><b>${esc(key.replaceAll('_', ' '))}</b><p>${esc(value)}</p></div>`).join('')}<h3>Milestone schedule and priced acceptance items</h3>${current.payload.milestones.map((m, i) => `<div class="clause"><b>${esc(m.title)} - ${money(m.amount_cents, p.currency || 'USD')} - ${m.duration_days} calendar days from authorization</b><p>${esc(m.scope)}</p>${(current.payload.work_items?.[i] || []).map(x => `<p>${esc(x.title)} - ${money(x.amount_cents, p.currency || 'USD')}: ${esc(x.acceptance)}</p>`).join('')}</div>`).join('')}<p class="compact">Fingerprint: ${esc(current.digest.slice(0, 20))}</p><div class="clause"><h3>Acceptance record</h3>${current.acceptances.map(a => `<p>${esc(a.role)} - account ${esc(a.user_id)} - ${esc(new Date(a.created_at).toLocaleString())}</p>`).join('') || '<p>No participant has accepted this version yet.</p>'}</div><div class="form-actions"><button class="secondary" data-do="download-agreement">Download agreement record</button><button class="primary" data-do="accept-version" ${accepted || issues.length ? 'disabled' : ''}>${issues.length ? 'Clarification required' : accepted ? '✓ You accepted this version' : 'Accept this exact agreement'}</button>${p.payments.length ? '' : '<button class="secondary" data-do="revise">Propose revised terms</button>'}</div><p class="form-note">Each party signs in and accepts independently. Your acceptance cannot be recorded by the other participant.</p></article><article class="room-panel"><h3>Version history</h3>${r.agreements.map(v => `<details><summary>Version ${v.number} · ${esc(v.status)} · ${v.acceptances.length}/2 acceptances</summary>${v.payload.clauses.map(c => `<p><b>${esc(c.reference)}</b> ${esc(c.text)}</p>`).join('')}</details>`).join('')}</article>`;
}

function downloadAgreement(r) {
  const v = r.agreements.find(a => a.id === r.project.agreement_id), p = v.payload, c = p.currency || 'USD';
  const lines = ['FairFlow agreement record', `Version: ${v.number}`, `Status: ${v.status}`, `SHA-256: ${v.digest}`, `Project: ${p.contract.title}`, `Total: ${money(p.contract.price_cents, c)}`, `Revisions per milestone: ${p.contract.allowed_revisions}`, `Scope: ${p.contract.task_description}`, '', ...p.clauses.map(x => `${x.reference} (${x.kind}): ${x.text}`), '', ...Object.entries(p.details || {}).map(([key, value]) => `${key}: ${value}`), '', ...p.milestones.flatMap((m, i) => [`${m.title}: ${money(m.amount_cents, c)}, ${m.duration_days} calendar days from authorization`, m.scope, ...(p.work_items?.[i] || []).map(x => `${x.title}: ${money(x.amount_cents, c)}; acceptance: ${x.acceptance}`)]), '', 'Acceptance records', ...v.acceptances.map(a => `${a.role}: account ${a.user_id}; ${a.created_at}; digest ${a.digest}`), '', 'Validation issues', ...(v.validation_issues || [])];
  const url = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' })), link = document.createElement('a');
  link.href = url; link.download = `fairflow-agreement-v${v.number}.txt`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function milestoneView(r) {
  const p = r.project, client = r.role === 'client';
  return `<article class="room-panel"><h3>Saved payment method</h3>${pill(p.vault.status)}<p>Only the client can save a method or start checkout. Future authorizations remain limited to accepted work.</p>${client && p.accepted.length === 2 ? (p.vault.status === 'active' ? '<button class="secondary" data-do="vault-revoke">Revoke future funding consent</button>' : p.vault.status === 'approval_required' ? `${p.vault.approval_url ? `<a class="secondary" href="${esc(p.vault.approval_url)}" target="_blank" rel="noopener noreferrer">Approve in PayPal</a>` : ''}<button class="primary" data-do="vault-confirm">${state.health.payment_mode === 'demo' ? 'Simulate PayPal save approval' : 'Verify PayPal save approval'}</button>` : `<label class="check-row"><input id="vault-agree" type="checkbox">I agree to saving my payment method for the accepted milestone plan.</label><button class="primary" data-do="vault-setup">Save with explicit consent</button>`) : ''}</article>${p.milestones.map(m => {
    const pay = [...p.payments].reverse().find(x => x.milestone_id === m.id);
    return `<article class="room-panel" data-milestone="${m.id}"><div class="milestone-title"><h3>${esc(m.title)}</h3>${pill(m.status)}</div><p class="scope">${esc(m.scope)}</p><div class="milestone-meta"><b>${money(m.amount_cents)}</b><span>${m.duration_days} work days</span><span>${m.revisions_used}/${p.contract.allowed_revisions} revisions</span></div><div class="funding-indicator ${esc(m.funding_signal)}">${m.work_allowed ? 'Authorization confirmed; work is active. Capture remains conditional.' : 'Work is not currently enabled for this milestone.'}</div>${m.last_error ? `<p class="error">${esc(m.last_error)}</p>` : ''}${m.review_deadline ? `<div class="timer">Review deadline: ${esc(new Date(m.review_deadline).toLocaleString())}</div>` : ''}${m.items?.map(x => `<div class="work-item"><b>${esc(x.title)} · ${money(x.amount_cents)}</b><p class="compact">${esc(x.acceptance)}</p></div>`).join('') || ''}<div class="form-actions">${client && p.accepted.length === 2 && ['queued', 'billing_failed', 'authorization_expired'].includes(m.status) ? `<button class="primary" data-do="fund" data-mid="${m.id}">Authorize milestone</button>` : ''}${client && m.status === 'awaiting_payment' ? `${pay?.checkout_url ? `<a class="secondary" href="${esc(pay.checkout_url)}" target="_blank" rel="noopener noreferrer">PayPal checkout</a>` : ''}<button class="primary" data-do="confirm-payment" data-mid="${m.id}">${state.health.payment_mode === 'demo' ? 'Simulate buyer approval' : 'Verify buyer approval'}</button>` : ''}${!client && ['active', 'revision_requested'].includes(m.status) ? `<button class="primary" data-do="delivery-form" data-mid="${m.id}">Submit work</button>` : ''}${client && m.status === 'in_review' ? `<button class="primary" data-do="approve" data-mid="${m.id}">Approve & capture payment</button>` : ''}<button class="secondary" data-do="evidence-form" data-mid="${m.id}">Add evidence</button>${['active', 'in_review', 'revision_requested', 'disputed'].includes(m.status) ? `<button class="secondary" data-do="proposal-form" data-mid="${m.id}">Propose a joint action</button>` : ''}</div>${m.deliveries.map(d => `<p><a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">Delivered work ↗</a><br>${esc(d.notes)}</p>`).join('')}</article>`;
  }).join('')}<article class="room-panel"><h3>Emergency pause</h3>${p.emergency_paused ? '<button class="secondary" data-do="resume">Resume and request fresh authorization</button>' : `${area('pause-reason', 'Reason for pausing', '', 2)}<button class="secondary danger" data-do="pause">Pause work and void open authorizations</button>`}</article>${state.health.payment_mode === 'demo' ? '<details class="demo-controls"><summary>Demo-only project clock</summary><p>No real payment time changes.</p><button class="secondary" data-do="advance">Advance five days</button></details>' : ''}`;
}

function decisionView(r) {
  const p = r.project;
  return `${r.role === 'client' ? `<article class="room-panel"><h3>Ask for an evidence-based review</h3><form id="decision-form"><label>Milestone<select id="decision-mid">${p.milestones.map(m => `<option value="${m.id}">${esc(m.title)}</option>`).join('')}</select></label>${area('decision-request', 'Change request or reported defect')}<label class="check-row"><input id="decision-dispute" type="checkbox">I dispute the current delivery; pause its settlement while we review it.</label><p class="form-note">Evidence already attached to this milestone will be supplied to the reviewer.</p><button class="primary">Request advisory review</button></form></article>` : ''}${r.decisions.map(d => `<article class="review-result"><div class="card-top"><h3>${esc(d.result.category.replaceAll('_', ' '))}</h3>${pill(d.status)}</div><p><b>${esc(d.request)}</b><br>${esc(d.result.explanation)}</p><p>Supporting clauses: ${d.result.clause_ids.map(esc).join(', ') || 'None; clarification required'}</p>${d.result.clause_ids.map(id => { const v = r.agreements.find(a => a.id === d.agreement_id); const c = v?.payload.clauses.find(x => x.reference === id); return c ? `<p class="callout"><b>${esc(id)}</b> ${esc(c.text)}</p>` : ''; }).join('')}<p>${esc(d.result.recommended_action)}</p>${d.result.missing_information.map(x => `<p class="warning">${esc(x)}</p>`).join('')}<p class="evaluation">Advisory only · ${esc(d.model_trace.mode)} · ${esc(d.model_trace.provider?.model || 'offline rules')}</p><button class="secondary" data-do="review-form" data-decision="${d.id}" data-mid="${d.milestone_id}">Propose reviewed outcome</button></article>`).join('')}${actionsView(r)}<article class="room-panel"><h3>Evidence library</h3>${r.evidence.map(e => `<p><a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.title)} ↗</a><br>${esc(e.description)}<br><small>Evidence ${esc(e.id.slice(0, 12))}</small></p>`).join('') || '<p>No evidence attached yet.</p>'}</article>`;
}

function actionsView(r) {
  return `<article class="room-panel"><h3>Joint proposals</h3><p>Both participants must accept the same proposal. No support account can override your decision.</p>${r.actions.map(a => `<div class="review-result"><div class="card-top"><b>${esc(a.kind.replaceAll('_', ' '))}</b>${pill(a.status)}</div><p>${esc(a.payload.reason)}</p>${a.payload.category ? `<p>Reviewed classification: ${esc(a.payload.category)}</p>` : ''}<p>Amount: ${money(a.payload.amount_cents || 0)} · ${a.votes.length}/2 accepted</p><div class="form-actions">${a.status === 'proposed' ? `<button class="primary" data-do="vote" data-aid="${a.id}" ${a.votes.some(v => v.user_id === state.user.id) ? 'disabled' : ''}>${a.votes.some(v => v.user_id === state.user.id) ? 'You accepted' : 'Accept exact proposal'}</button><button class="secondary" data-do="reject" data-aid="${a.id}">Reject proposal</button>` : ''}</div></div>`).join('') || '<p>No proposals yet.</p>'}</article>`;
}

function paymentsView(r) {
  const p = r.project, captured = p.payments.reduce((s, x) => s + x.captures.reduce((n, c) => n + c.amount_cents, 0), 0), refunded = p.payments.reduce((s, x) => s + (x.refund_operations || []).filter(o => o.status === 'done').reduce((n, o) => n + o.amount_cents, 0), 0);
  return `<div class="payment-grid"><article><small>CAPTURED</small><strong>${money(captured)}</strong></article><article><small>REFUNDED</small><strong>${money(refunded)}</strong></article><article><small>LEDGER BALANCE</small><strong>${money(r.ledger.reduce((s, e) => s + e.amount_cents, 0))}</strong></article></div><article class="room-panel"><h3>Payment records</h3><p>Captures are merchant transactions. Freelancer payout onboarding and live marketplace approval remain deployment requirements.</p><button class="secondary" data-do="reconcile">Queue provider reconciliation</button>${p.external_payment_issue ? `<p class="error">${esc(p.external_payment_issue)}</p>` : ''}${p.payments.map(pay => `<div class="review-result">${pill(pay.status)}<b> ${money(pay.amount_cents)}</b>${pay.captures.map(c => `<p>Capture ${esc(c.id)} · ${money(c.amount_cents)}<br><button class="secondary" data-do="refund-form" data-mid="${pay.milestone_id}" data-capture="${esc(c.id)}">Propose refund</button></p>`).join('')}</div>`).join('')}<div class="scroll-table"><table class="room-table"><thead><tr><th>Account</th><th>Amount</th><th>Reference</th></tr></thead><tbody>${r.ledger.map(e => `<tr><td>${esc(e.account)}</td><td>${money(e.amount_cents)}</td><td>${esc(e.external_id)}</td></tr>`).join('')}</tbody></table></div></article>${actionsView(r)}`;
}

function activityView(r) {
  return `<article class="room-panel"><h3>Project activity</h3><div class="audit">${[...r.project.events].reverse().map(e => `<div>${esc(e.message)}<small>${esc(new Date(e.at).toLocaleString())}</small></div>`).join('')}</div></article><article class="room-panel"><h3>Durable jobs</h3>${r.jobs.map(j => `<p>${esc(j.kind)} · ${esc(j.status)} · attempt ${j.attempts}${j.last_error ? '<br>' + esc(j.last_error) : ''}</p>`).join('')}</article>`;
}

function proposalForm(r, mid, kind = 'dispute_resolution', decisionId = null, captureId = null) {
  const m = r.project.milestones.find(x => x.id === mid);
  modal(`<h2>Propose a joint decision</h2><p>Submitting records your acceptance. The other participant must accept the same terms before they are applied.</p><form id="proposal-form"><label>Action<select id="proposal-kind">${['decision', 'dispute_resolution', 'reconciliation', 'refund', 'partial_items'].map(k => `<option value="${k}" ${k === kind ? 'selected' : ''}>${k.replaceAll('_', ' ')}</option>`).join('')}</select></label><label>Reviewed classification<select id="proposal-category"><option value="">Not applicable</option>${['defect', 'included_revision', 'additional_scope', 'client_dependency', 'ambiguous', 'disputed_evidence'].map(k => `<option value="${k}" ${r.decisions.find(d => d.id === decisionId)?.result.category === k ? 'selected' : ''}>${k.replaceAll('_', ' ')}</option>`).join('')}</select></label>${area('proposal-reason', 'Reason and agreed outcome')}${field('proposal-amount', 'Proposed currency amount (zero for non-financial decisions)', 0, 'number')}<label>Evidence for priced-item settlement<select id="proposal-evidence"><option value="">Choose evidence</option>${r.evidence.filter(e => e.milestone_id === mid).map(e => `<option value="${e.id}">${esc(e.title)}</option>`).join('')}</select></label><p>For priced-item settlement, select the delivered items:</p>${(m.items || []).map(x => `<label class="check-row"><input type="checkbox" name="item" value="${x.id}">${esc(x.title)} · ${money(x.amount_cents)}</label>`).join('')}<button class="primary">Propose for mutual acceptance</button></form>`);
  el('proposal-amount').step = currencyDecimals(r.project.currency || 'USD') === 0 ? '1' : '0.01';
  el('proposal-form').onsubmit = e => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const body = { kind: el('proposal-kind').value, milestone_id: mid, reason: el('proposal-reason').value, amount_cents: priceValue(el('proposal-amount').value, r.project.currency || 'USD'), decision_id: decisionId, category: el('proposal-category').value || null, capture_id: captureId, evidence_id: el('proposal-evidence').value || null, item_ids: [...document.querySelectorAll('[name=item]:checked')].map(x => x.value) };
      state.room = await api(`/platform/projects/${r.project.id}/actions`, body);
      el('modal').close();
      state.tab = 'AI review';
      renderRoom();
    });
  };
}

function bindRoom(r) {
  const p = r.project, base = '/platform/projects/' + p.id, ag = '/agile/projects/' + p.id;
  document.querySelectorAll('[data-do]').forEach(b => b.onclick = () => busy(b, async () => {
    const action = b.dataset.do, mid = b.dataset.mid;
    let path, body = {};
    switch (action) {
      case 'download-agreement': downloadAgreement(r); return;
      case 'accept-version': {
        const v = r.agreements.find(a => a.id === p.agreement_id);
        path = base + '/agreements/' + v.id + '/accept';
        body = { digest: v.digest };
        break;
      }
      case 'revise': createForm(r.agreements.find(a => a.id === p.agreement_id).payload); return;
      case 'vault-setup': if (!el('vault-agree').checked) throw Error('Explicit client consent is required.'); path = ag + '/vault/setup'; body = { agreed: true, plan_hash: p.plan_hash }; break;
      case 'vault-confirm': path = ag + '/vault/confirm'; break;
      case 'vault-revoke': path = ag + '/vault/revoke'; break;
      case 'fund': case 'confirm-payment': case 'approve': path = ag + '/milestones/' + mid + '/' + action; break;
      case 'pause': path = ag + '/pause'; body = { request: el('pause-reason').value }; break;
      case 'resume': path = ag + '/resume'; break;
      case 'advance': path = ag + '/demo/advance'; body = { days: 5 }; break;
      case 'reconcile': path = base + '/reconcile'; break;
      case 'vote': {
        const a = r.actions.find(x => x.id === b.dataset.aid);
        path = base + '/actions/' + a.id + '/accept';
        body = { digest: a.digest };
        break;
      }
      case 'reject': path = base + '/actions/' + b.dataset.aid + '/reject'; break;
      case 'review-form': proposalForm(r, mid, 'decision', b.dataset.decision); return;
      case 'proposal-form': proposalForm(r, mid); return;
      case 'refund-form': proposalForm(r, mid, 'refund', null, b.dataset.capture); return;
      case 'evidence-form': modal(`<h2>Attach delivery evidence</h2><form id="evidence-form">${field('evidence-title', 'Title')}${field('evidence-url', 'Evidence URL', '', 'url')}${area('evidence-description', 'What this evidence demonstrates')}<button class="primary">Save immutable evidence record</button></form>`); el('evidence-form').onsubmit = e => { e.preventDefault(); busy(e.submitter, async () => { await api(base + '/evidence', { milestone_id: mid, title: el('evidence-title').value, url: el('evidence-url').value, description: el('evidence-description').value }); el('modal').close(); await openRoom(p.id); }); }; return;
      case 'delivery-form': modal(`<h2>Submit work for review</h2><p>The five-day review starts after this submission is recorded.</p><form id="delivery-form">${field('delivery-url', 'Delivery URL', '', 'url')}${area('delivery-notes', 'Submission notes')}<button class="primary">Submit milestone</button></form>`); el('delivery-form').onsubmit = e => { e.preventDefault(); busy(e.submitter, async () => { await api(ag + '/milestones/' + mid + '/deliver', { url: el('delivery-url').value, notes: el('delivery-notes').value }); el('modal').close(); await openRoom(p.id); }); }; return;
      default: return;
    }
    await api(path, body);
    await openRoom(p.id);
    toast('Project updated.');
  }));

  if (el('btn-save-vision')) {
    el('btn-save-vision').onclick = () => busy(el('btn-save-vision'), async () => {
      const goal = el('client-vision-goal').value.trim();
      const assets = el('client-vision-assets').value.trim();
      state.room = await api(`${base}/client-vision`, { goal, assets_inspiration: assets });
      renderRoom();
      toast('Project vision saved.');
    });
  }

  if (el('btn-authorize-paypal-hold')) {
    el('btn-authorize-paypal-hold').onclick = () => busy(el('btn-authorize-paypal-hold'), async () => {
      const res = await api(`${base}/proposal/authorize-hold`, {});
      if (res.checkout_url) {
        window.open(res.checkout_url, '_blank');
        toast('Opening PayPal checkout to authorize hold...');
      } else {
        state.room = res;
        renderRoom();
        toast('✓ PayPal Escrow Hold Authorized! Milestone is active and funded.');
      }
    });
  }

  const triggerDraft = async btn => {
    await busy(btn, async () => {
      toast('Gemini reading chat & vision to draft proposal...');
      const res = await api(`${base}/smart-draft`, { chat_history: '', client_goal: '', assets_inspiration: '', currency: p.currency || 'USD' });
      state.room = res;
      state.tab = 'Smart Proposal';
      renderRoom();
      toast('✨ Gemini populated the Two-Sided Canvas!');
    });
  };
  if (el('btn-trigger-ai-draft')) el('btn-trigger-ai-draft').onclick = () => triggerDraft(el('btn-trigger-ai-draft'));
  if (el('btn-chat-ai-draft')) el('btn-chat-ai-draft').onclick = () => triggerDraft(el('btn-chat-ai-draft'));

  if (el('canvas-days')) {
    el('canvas-days').oninput = updateDeliveryDatePreview;
  }

  if (el('btn-add-deliv')) {
    el('btn-add-deliv').onclick = () => {
      const container = el('canvas-deliv-container');
      const div = document.createElement('div');
      div.className = 'canvas-item-row';
      div.innerHTML = `<input class="canvas-deliv-input" placeholder="e.g., Mobile and Tablet Responsive Design" required><button type="button" class="btn-del-item" title="Remove">✕</button>`;
      div.querySelector('.btn-del-item').onclick = () => div.remove();
      container.appendChild(div);
    };
  }

  if (el('btn-add-scope')) {
    el('btn-add-scope').onclick = () => {
      const container = el('canvas-scope-container');
      const div = document.createElement('div');
      div.className = 'canvas-item-row';
      div.innerHTML = `<input class="canvas-scope-input" placeholder="e.g., No backend database setup." required><button type="button" class="btn-del-item" title="Remove">✕</button>`;
      div.querySelector('.btn-del-item').onclick = () => div.remove();
      container.appendChild(div);
    };
  }

  document.querySelectorAll('.btn-del-item').forEach(b => {
    b.onclick = () => b.closest('.canvas-item-row')?.remove();
  });

  if (el('canvas-form')) {
    el('canvas-form').onsubmit = e => {
      e.preventDefault();
      busy(e.submitter, async () => {
        const deliverables = [...document.querySelectorAll('.canvas-deliv-input')].map(x => x.value.trim()).filter(Boolean);
        if (!deliverables.length) throw Error('Provide at least one concrete deliverable.');
        const outOfScope = [...document.querySelectorAll('.canvas-scope-input')].map(x => x.value.trim()).filter(Boolean);
        if (!outOfScope.length) throw Error('Provide at least one out-of-scope guardrail.');

        const delivSet = new Set(deliverables.map(d => d.toLowerCase()));
        for (const item of outOfScope) {
          if (delivSet.has(item.toLowerCase())) {
            throw Error(`Contradiction detected: "${item}" cannot be both a deliverable and out of scope.`);
          }
        }

        let days = parseInt(el('canvas-days').value, 10);
        if (isNaN(days) || days < 1) days = 1;
        if (days > 21) throw Error('Platform strictly limits milestone delivery to 21 days maximum.');

        const priceCents = priceValue(el('canvas-price').value, p.currency || 'USD');
        const target = new Date();
        target.setDate(target.getDate() + days);
        const options = { month: 'short', day: 'numeric', year: 'numeric' };
        const deliveryDateStr = `${target.toLocaleDateString('en-US', options)} (${days} days from today - safely within the 21-day platform limit)`;

        const proposalPayload = {
          section1: {
            project_title: el('canvas-title').value.trim(),
            milestone_phase: el('canvas-phase').value.trim(),
            goal_summary: el('canvas-goal').value.trim()
          },
          section2: {
            deliverables
          },
          section3: {
            revisions_included: parseInt(el('canvas-revisions').value, 10),
            revision_rules: el('canvas-rev-rules').value.trim(),
            out_of_scope: outOfScope
          },
          section4: {
            price_cents: priceCents,
            duration_days: days,
            delivery_date: deliveryDateStr
          },
          client_vision: p.smart_proposal?.client_vision || null,
          status: 'sent_to_client'
        };

        state.room = await api(`${base}/proposal/submit`, proposalPayload);
        renderRoom();
        toast('Smart Proposal sent to client for review and PayPal hold authorization.');
      });
    };
  }

  document.querySelectorAll('[data-tab-nav]').forEach(b => {
    b.onclick = () => {
      state.tab = b.dataset.tabNav;
      renderRoom();
    };
  });

  if (el('chat-send-form')) {
    const chatInput = el('chat-msg-input');
    chatInput.onkeydown = e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        el('btn-submit-chat').click();
      }
    };
    el('chat-send-form').onsubmit = e => {
      e.preventDefault();
      const body = chatInput.value.trim();
      if (!body) return;
      busy(el('btn-submit-chat'), async () => {
        chatInput.value = '';
        state.room = await api(`${base}/messages`, { body });
        const stream = el('chat-stream');
        if (stream) {
          stream.innerHTML = state.room.messages.map(renderChatBubble).join('');
          stream.scrollTop = stream.scrollHeight;
        }
      });
    };
    const stream = el('chat-stream');
    if (stream) stream.scrollTop = stream.scrollHeight;
  }

  if (el('message-form')) {
    el('message-form').onsubmit = e => {
      e.preventDefault();
      busy(e.submitter, async () => {
        await api(base + '/messages', { body: el('message').value });
        await openRoom(p.id);
      });
    };
  }

  if (el('decision-form')) {
    el('decision-form').onsubmit = e => {
      e.preventDefault();
      busy(e.submitter, async () => {
        const mid = el('decision-mid').value;
        await api(base + '/decisions', {
          milestone_id: mid,
          request: el('decision-request').value,
          evidence_ids: r.evidence.filter(x => x.milestone_id === mid).map(x => x.id),
          dispute_current_work: el('decision-dispute').checked
        });
        await openRoom(p.id);
      });
    };
  }
}

el('home').onclick = () => busy(null, home);
el('new').onclick = () => createForm();
el('mail').onclick = () => busy(null, mail);
el('close').onclick = () => el('modal').close();
if (el('portal-switch')) {
  el('portal-switch').onclick = () => {
    state.portalRole = state.portalRole === 'client' ? 'freelancer' : 'client';
    localStorage.setItem('fairflow_portal', state.portalRole);
    identity();
    if (state.room) renderRoom();
    else home();
    toast('Switched to ' + (state.portalRole === 'client' ? 'Client Workspace' : 'Freelancer Studio'));
  };
}
el('logout').onclick = () => busy(null, async () => {
  if (state.pollInterval) { clearInterval(state.pollInterval); state.pollInterval = null; }
  await api('/auth/logout', {});
  state.user = null;
  state.csrf = '';
  state.room = null;
  identity();
  authForm();
});
window.addEventListener('hashchange', () => busy(null, hashAction));

(async () => {
  try {
    [state.health, state.currencies] = await Promise.all([api('/health'), api('/currencies')]);
    el('environment').textContent = state.health.payment_mode === 'demo' ? 'DEMO · PayPal Escrow Hold Simulation' : 'PAYPAL SANDBOX';
    try {
      const me = await api('/auth/me');
      state.user = me.user;
      state.csrf = me.csrf_token;
    } catch { }
    identity();
    await home();
    await hashAction();
  } catch (e) {
    toast(e.message);
  }
})();
