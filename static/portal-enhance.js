'use strict';
(function () {

  /* ---- Watch #content for route changes ---- */
  document.addEventListener('DOMContentLoaded', function () {
    var contentEl = document.getElementById('content');
    if (!contentEl) return;
    onContentChange(contentEl);
    var observer = new MutationObserver(function () { onContentChange(contentEl); });
    observer.observe(contentEl, { childList: true, subtree: false });
  });

  function onContentChange(contentEl) {
    var isAuth = !!contentEl.querySelector('.auth-layout');
    document.body.classList.toggle('page-auth', isAuth);
    if (isAuth) {
      injectHero(contentEl);
      injectPasswordEye(contentEl);
      injectForgotInline(contentEl);
    }
  }

  function injectHero(contentEl) {
    if (contentEl.querySelector('.fairflow-hero')) return;
    var layout = contentEl.querySelector('.auth-layout');
    if (!layout) return;

    var hero = document.createElement('div');
    hero.className = 'fairflow-hero';
    var logo = document.createElement('img');
    logo.src = '/static/FairFlow%20Logo.png';
    logo.alt = 'FairFlow';
    logo.className = 'hero-main-logo';
    var tag = document.createElement('p');
    tag.className = 'hero-tagline';
    tag.textContent = 'CLEAR SCOPE. SHARED CONFIDENCE.';
    hero.appendChild(logo);
    hero.appendChild(tag);
    layout.insertBefore(hero, layout.firstChild);
  }

  function injectPasswordEye(contentEl) {
    var pwInput = document.getElementById('password');
    if (!pwInput || pwInput.closest('.pw-wrap')) return;

    var wrap = document.createElement('div');
    wrap.className = 'pw-wrap';
    pwInput.parentNode.insertBefore(wrap, pwInput);
    wrap.appendChild(pwInput);

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'pw-eye-btn';
    btn.setAttribute('aria-label', 'Show password');
    btn.setAttribute('aria-controls', pwInput.id);
    btn.setAttribute('aria-pressed', 'false');
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/><path class="eye-slash" d="m3 3 18 18"/></svg>';
    btn.addEventListener('click', function () {
      var show = pwInput.type === 'password';
      pwInput.type = show ? 'text' : 'password';
      btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      btn.setAttribute('aria-pressed', String(show));
    });
    wrap.appendChild(btn);
  }

  function injectForgotInline(contentEl) {
    if (document.querySelector('.ff-forgot-inline')) return;
    var forgotOrig = document.getElementById('forgot');
    if (!forgotOrig) return;

    var pwField = contentEl.querySelector('input[type="password"]');
    if (!pwField) return;
    var fieldDiv = pwField.closest('.field');
    if (!fieldDiv) return;

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ff-forgot-inline';
    btn.textContent = 'Forgot password?';
    btn.addEventListener('click', function () { forgotOrig.click(); });
    fieldDiv.after(btn);
  }

})();
