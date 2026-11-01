import { api, confirmAction, jsonBody, toast } from './ui.js';

function busy(button, value) {
  button.disabled = value;
  value ? button.setAttribute('aria-busy', 'true') : button.removeAttribute('aria-busy');
}

export function initUzum() {
  const form = document.querySelector('#uzum-connect-form');
  form?.addEventListener('submit', async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const submit = form.querySelector('button[type="submit"]');
    busy(submit, true);
    form.querySelectorAll('[aria-invalid]').forEach(input => input.removeAttribute('aria-invalid'));
    try {
      await api('/api/uzum/account', { method: 'POST', body: jsonBody({ api_token: form.elements.api_token.value,
        login: form.elements.login.value, password: form.elements.password.value }) });
      location.assign(form.dataset.next || '/cards/new');
    } catch (error) {
      for (const name of Object.keys(error.fields || {})) form.elements[name]?.setAttribute('aria-invalid', 'true');
      toast(error.message, true);
      busy(submit, false);
    }
  });
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-action="uzum-disconnect"]');
    if (!button) return;
    if (!await confirmAction("Uzum tokeni, logini va paroli o'chiriladi, faol timeslot kuzatuvlari to'xtaydi.", "Uzum'ni uzish")) return;
    busy(button, true);
    try { await api('/api/uzum/account', { method: 'DELETE' }); location.reload(); }
    catch (error) { toast(error.message, true); busy(button, false); }
  });
}
