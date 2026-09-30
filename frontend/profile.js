import { api, confirmAction, jsonBody, toast } from './ui.js';

export function initProfile() {
  const passwordForm = document.querySelector('#password-form');
  passwordForm?.addEventListener('submit', async event => {
    event.preventDefault(); if (!passwordForm.reportValidity()) return;
    const button = passwordForm.querySelector('button[type="submit"]'); button.disabled = true;
    try { await api('/api/me/password', { method: 'POST', body: jsonBody({
      current_password: passwordForm.elements.current_password.value, new_password: passwordForm.elements.new_password.value,
    }) }); location.assign('/auth/login?notice=reset'); }
    catch (error) { toast(error.message, true); button.disabled = false; }
  });
  const deleteForm = document.querySelector('#delete-account-form');
  deleteForm?.addEventListener('submit', async event => {
    event.preventDefault(); if (!deleteForm.reportValidity()) return;
    if (!await confirmAction("Barcha kartochkalar va rasmlar o'chiriladi. Bu amalni qaytarib bo'lmaydi.", "Akkauntni o'chirish")) return;
    const button = deleteForm.querySelector('button[type="submit"]'); button.disabled = true;
    try { await api('/api/me', { method: 'DELETE', body: jsonBody({ current_password: deleteForm.elements.current_password.value, confirm: true }) });
      location.assign('/auth/login'); }
    catch (error) { toast(error.message, true); button.disabled = false; }
  });
}
