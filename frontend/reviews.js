import { api, confirmAction, element, jsonBody, toast } from './ui.js';

function busy(button, value) {
  button.disabled = value;
  value ? button.setAttribute('aria-busy', 'true') : button.removeAttribute('aria-busy');
}

function markInvalid(form, fields = {}) {
  form.querySelectorAll('[aria-invalid]').forEach(input => input.removeAttribute('aria-invalid'));
  for (const name of Object.keys(fields)) form.elements[name]?.setAttribute('aria-invalid', 'true');
}

function shopRules(form) {
  return [...form.querySelectorAll('[data-shop-id]')].map(row => ({ shop_id: row.dataset.shopId,
    text_method: row.querySelector('[data-shop-method="text_method"]').value,
    empty_method: row.querySelector('[data-shop-method="empty_method"]').value }));
}

// Do'kon qatori: alohida usul tanlangan bo'lsa belgilanadi; pastdagi yig'indi va "qaytarish" tugmasi yangilanadi.
function markShops(form) {
  const rows = [...form.querySelectorAll('[data-shop-id]')];
  if (!rows.length) return;
  let custom = 0;
  for (const row of rows) {
    const selects = [...row.querySelectorAll('[data-shop-method]')];
    selects.forEach(select => select.classList.toggle('custom', select.value !== 'default'));
    const changed = selects.some(select => select.value !== 'default');
    row.classList.toggle('custom', changed);
    row.querySelector('[data-shop-badge]').hidden = !changed;
    custom += changed;
  }
  form.querySelector('[data-shop-summary]').textContent = custom ? `${custom} ta do'konda alohida sozlama` : "Hamma do'konlar umumiy sozlamada";
  form.querySelector('[data-action="reset-shops"]').hidden = !custom;
}

// Umumiy sozlama o'zgarsa do'konlardagi "Umumiy sozlama (...)" yozuvi ham o'zgaradi.
function syncDefaults(form, general) {
  const label = general.selectedOptions[0].dataset.short;
  form.querySelectorAll(`[data-shop-method="${general.name}"] [data-default]`)
    .forEach(option => { option.textContent = `Umumiy sozlama (${label})`; });
}

async function saveSettings(form) {
  const auto = form.elements.auto_reply.checked;
  if (auto && !form.elements.auto_reply.defaultChecked && !await confirmAction(
    "Yangi sharhlarga javob siz ko'rmasdan Uzum'ga yuboriladi. Javoblar Uzum moderatsiyasidan o'tadi.", 'Avtomatik javobni yoqish')) return;
  const submit = form.querySelector('button[type="submit"]');
  busy(submit, true);
  try {
    await api('/api/review-settings', { method: 'PUT', body: jsonBody({ auto_reply: auto,
      text_method: form.elements.text_method.value, empty_method: form.elements.empty_method.value,
      backlog: form.elements.backlog.checked, shops: shopRules(form) }) });
    location.reload();
  } catch (error) { toast(error.message, true); busy(submit, false); }
}

async function draftReply(button) {
  const form = button.closest('.review-reply-form');
  const method = button.dataset.reviewDraft;
  busy(button, true);
  try {
    const reply = await api(`/api/reviews/${form.dataset.reviewId}/draft`, { method: 'POST', body: jsonBody({
      method, template_id: method === 'template' ? form.elements.template_id.value || null : null }) });
    form.elements.text.value = reply.text;
    form.querySelector('[data-review-note]').textContent = `${reply.method_label} · ${reply.status_label} — tahrirlab, keyin yuboring`;
  } catch (error) { toast(error.message, true); }
  finally { busy(button, false); }
}

async function sendReply(form) {
  if (!form.reportValidity()) return;
  const submit = form.querySelector('button[type="submit"]');
  busy(submit, true);
  try {
    const reply = await api(`/api/reviews/${form.dataset.reviewId}/reply`, { method: 'POST', body: jsonBody({ text: form.elements.text.value }) });
    form.replaceWith(element('p', 'notice success', `Javob yuborildi: ${reply.text}`));
    toast("Javob yuborildi. Uzum uni moderatsiyadan so'ng e'lon qiladi.");
  } catch (error) { toast(error.message, true); busy(submit, false); }
}

async function saveTemplate(form) {
  const values = Object.fromEntries(new FormData(form));
  const id = form.dataset.templateId;
  const submit = form.querySelector('button[type="submit"]');
  busy(submit, true);
  try {
    await api(id ? `/api/review-templates/${id}` : '/api/review-templates', { method: id ? 'PUT' : 'POST', body: jsonBody({
      name: values.name, rating_min: Number(values.rating_min), rating_max: Number(values.rating_max),
      text_uz: values.text_uz, text_ru: values.text_ru }) });
    markInvalid(form);
    if (!id) { location.reload(); return; }
    toast('Shablon saqlandi.');
  } catch (error) { markInvalid(form, error.fields); toast(error.message, true); }
  finally { busy(submit, false); }
}

export function initReviews() {
  const settings = document.querySelector('#review-settings-form');
  if (!settings || settings.dataset.bound) return;
  settings.dataset.bound = 'true';
  document.addEventListener('submit', event => {
    const form = event.target;
    if (form === settings) { event.preventDefault(); saveSettings(form); }
    else if (form.matches('.review-reply-form')) { event.preventDefault(); sendReply(form); }
    else if (form.matches('.review-template-form')) { event.preventDefault(); saveTemplate(form); }
  });
  settings.addEventListener('change', event => {
    if (event.target.matches('[data-general]')) syncDefaults(settings, event.target);
    if (event.target.matches('[data-shop-method]')) markShops(settings);
  });
  document.addEventListener('click', async event => {
    const draft = event.target.closest('[data-review-draft]');
    if (draft) draftReply(draft);
    const remove = event.target.closest('[data-delete-template]');
    if (remove) {
      const form = remove.closest('.review-template-form');
      if (!await confirmAction("Shablon o'chiriladi. Avval yuborilgan javoblar o'zgarmaydi.", "Shablonni o'chirish")) return;
      try { await api(`/api/review-templates/${form.dataset.templateId}`, { method: 'DELETE' }); form.remove(); toast("Shablon o'chirildi."); }
      catch (error) { toast(error.message, true); }
    }
    if (event.target.closest('[data-action="reset-shops"]')) {
      settings.querySelectorAll('[data-shop-method]').forEach(select => { select.value = 'default'; });
      markShops(settings);
      toast("Hamma do'konlar umumiy sozlamaga qaytarildi. Saqlashni unutmang.");
    }
    const run = event.target.closest('[data-action="run-reviews"]');
    if (run) {
      busy(run, true);
      try { await api('/api/reviews/run', { method: 'POST' }); toast('Tekshiruv boshlandi, natija bir necha daqiqada shu sahifada chiqadi.'); }
      catch (error) { toast(error.message, true); }
      finally { setTimeout(() => busy(run, false), 30_000); }
    }
  });
}
