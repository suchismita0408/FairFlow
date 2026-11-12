// A separate workspace per browser; role switching uses a private HttpOnly cookie.
(async () => {
  const request = async (body) => {
    const response = await fetch('/api/auth/judge-demo', {
      method: body ? 'POST' : 'GET', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : undefined
    });
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Please check the demo account fields.');
    return data;
  };
  let state;
  try { state = await request(); } catch { return; }
  if (!state.enabled) return;
  const bar = document.createElement('section');
  bar.className = 'judge-demo-bar';
  bar.setAttribute('aria-label', 'Judge demo');
  const intro = document.createElement('div');
  intro.innerHTML = state.active
    ? `<strong>Judge demo: ${state.settings.payment_mode === 'sandbox' ? 'PayPal Sandbox' : 'Quick walkthrough'}</strong><p>Explore both sides of your sample agreement. ${state.settings.payment_mode === 'sandbox' ? 'Test accounts and fake balances only.' : 'No PayPal transactions.'}</p>`
    : '<strong>Explore FairFlow in one click</strong><p>Get a sample agreement and switch between client and freelancer. No signup or PayPal account needed. Payments are simulated.</p>';
  bar.append(intro);
  const feedback = document.createElement('p');
  feedback.setAttribute('role', 'status');
  const enter = async (role, action = 'start', settings = {}) => {
    bar.querySelectorAll('button').forEach(button => button.disabled = true);
    feedback.textContent = 'Opening your demo...';
    try {
      const result = await request({role, action, payment_mode: state.settings?.payment_mode || 'simulated', ...settings});
      location.assign(result.redirect);
    } catch (error) {
      feedback.textContent = error.message;
      bar.querySelectorAll('button').forEach(button => button.disabled = false);
      throw error;
    }
  };
  const button = (text, fn) => {
    const b = document.createElement('button'); b.type = 'button'; b.textContent = text;
    b.addEventListener('click', () => Promise.resolve(fn()).catch(() => {})); bar.append(b);
  };
  if (!state.active) {
    button('Try quick demo', () => enter('client'));
    if (state.sandbox_ready) button('Try PayPal Sandbox', () => enter('client', 'start', {payment_mode: 'sandbox'}));
  }
  else {
    for (const role of ['client', 'freelancer']) button('Open ' + role + ' view', () => {
      if (typeof S !== 'undefined' && S.dirty && !confirm('Switch views? Save your draft first if you want to keep your edits.')) return;
      return enter(role, 'switch');
    });
  }
  const dialog = document.createElement('dialog');
  dialog.className = 'judge-demo-dialog';
  dialog.innerHTML = '<h2>Demo participants and payment details</h2><p>Customize the participants in your quick walkthrough. Payments are simulated; these emails do not connect PayPal accounts.</p><form><label>Client / buyer name<input name="client_name" required maxlength="100"></label><label>Freelancer / seller name<input name="freelancer_name" required maxlength="100"></label><label>Buyer email (optional)<input name="buyer_email" type="email"></label><label>Seller / receiver email (optional)<input name="receiver_email" type="email"></label><p>You can edit the agreement terms inside the project. No agreement is automatically signed. Email addresses are demo labels only. No PayPal payments are made.</p><p class="judge-demo-error" role="alert"></p><button type="submit">Save and open demo</button> <button type="button" data-close>Cancel</button></form>';
  const form = dialog.querySelector('form');
  for (const [key, value] of Object.entries({client_name: 'Demo Client', freelancer_name: 'Demo Freelancer', payment_mode: 'simulated', ...state.settings})) {
    if (form.elements.namedItem(key)) form.elements.namedItem(key).value = value || '';
  }
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  form.onsubmit = async event => {
    event.preventDefault();
    const submit = form.querySelector('[type=submit]'); submit.disabled = true;
    const settings = Object.fromEntries(new FormData(form));
    settings.payment_mode = state.active ? state.settings.payment_mode : 'simulated';
    settings.buyer_email ||= null; settings.receiver_email ||= null;
    try { await enter(location.pathname.startsWith('/freelancer') ? 'freelancer' : 'client', state.active ? 'update' : 'start', settings); }
    catch (error) { dialog.querySelector('[role=alert]').textContent = error.message; submit.disabled = false; }
  };
  button(state.active ? 'Edit demo accounts' : 'Customize demo accounts', () => dialog.showModal());
  if (state.active && state.sandbox_ready && state.settings.payment_mode !== 'sandbox') {
    button('Open PayPal Sandbox demo', () => enter('client', 'start', {payment_mode: 'sandbox'}));
  }
  if (state.active && state.settings.payment_mode === 'sandbox') {
    button('Open quick walkthrough', () => enter('client', 'start', {payment_mode: 'simulated'}));
  }
  bar.append(feedback);
  document.body.prepend(bar);
  document.body.append(dialog);
})();
