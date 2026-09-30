import htmx from 'htmx.org';
import { icons, toast } from './ui.js';
import { initUpload } from './upload.js';
import { bindHistoryActions, initHistory } from './history.js';
import { initProfile } from './profile.js';
import { initEditor } from './editor.js';

window.htmx = htmx;
htmx.config.allowEval = false;
htmx.config.includeIndicatorStyles = false;
const backgroundRequest = event => event.detail.elt?.id === 'job-status';

function start() {
  icons(); initUpload(); initHistory(); bindHistoryActions(); initProfile(); initEditor();
  document.body.addEventListener('htmx:configRequest', event => {
    event.detail.headers['X-CSRF-Token'] = document.querySelector('meta[name="csrf-token"]').content;
  });
  document.body.addEventListener('htmx:afterSwap', () => { icons(); initHistory(); });
  document.body.addEventListener('htmx:responseError', event => {
    if (!backgroundRequest(event)) toast("So'rov bajarilmadi. Sahifani yangilang.", true);
  });
  document.body.addEventListener('htmx:sendError', event => {
    if (!backgroundRequest(event)) toast("Aloqa uzildi. Internet ulanishini tekshiring.", true);
  });
  document.addEventListener('click', event => {
    const toggle = event.target.closest('[data-action="show-password"]');
    if (toggle) { const input = toggle.closest('.password-field').querySelector('input');
      input.type = input.type === 'password' ? 'text' : 'password'; toggle.setAttribute('aria-pressed', String(input.type === 'text')); }
    const menu = event.target.closest('[data-action="menu"]');
    if (menu) { const open = document.body.classList.toggle('menu-open'); menu.setAttribute('aria-expanded', String(open)); }
  });
  document.querySelectorAll('[data-busy-form]').forEach(form => form.addEventListener('submit', () => {
    const button = form.querySelector('button[type="submit"]'); if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
  }));
  const notice = sessionStorage.getItem('karto-notice');
  if (notice) { sessionStorage.removeItem('karto-notice'); toast(notice, true); }
}

document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', start) : start();
