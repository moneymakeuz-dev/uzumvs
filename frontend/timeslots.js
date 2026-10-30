import { api, confirmAction, jsonBody, toast } from './ui.js';

const tashkentToday = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tashkent' }).format(new Date());

function refreshWatches() {
  window.htmx.ajax('GET', '/timeslots', { target: '#timeslot-watches', select: '#timeslot-watches', swap: 'outerHTML' });
}

function busy(button, value) {
  button.disabled = value;
  value ? button.setAttribute('aria-busy', 'true') : button.removeAttribute('aria-busy');
}

export function initTimeslots() {
  const form = document.querySelector('#timeslot-form');
  if (!form || form.dataset.bound) return;
  form.dataset.bound = 'true';
  const shop = form.querySelector('#timeslot-shop');
  const invoice = form.querySelector('#timeslot-invoice');
  const load = form.querySelector('[data-action="load-invoices"]');
  const note = form.querySelector('#timeslot-invoice-note');
  const from = form.querySelector('#timeslot-date-from');
  const to = form.querySelector('#timeslot-date-to');
  const submit = form.querySelector('button[type="submit"]');
  let invoices = [];
  from.min = to.min = tashkentToday();
  const sync = () => { submit.disabled = !shop.value || !invoice.value || !from.value || !to.value; };
  const resetInvoices = () => {
    invoices = [];
    invoice.replaceChildren(new Option('Avval yuk xatlarini yuklang', ''));
    invoice.disabled = true;
    note.textContent = '';
  };

  api('/api/uzum/shops').then(({ shops }) => {
    shop.replaceChildren(...shops.map(item => new Option(`${item.name} (${item.id})`, item.id)));
    shop.disabled = load.disabled = !shops.length;
    sync();
  }).catch(error => { shop.replaceChildren(new Option("Do'konlar topilmadi", '')); toast(error.message, true); });

  shop.addEventListener('change', () => { resetInvoices(); sync(); });
  invoice.addEventListener('change', () => {
    const item = invoices.find(entry => String(entry.id) === invoice.value);
    const remaining = item?.remaining_updates ?? null;
    note.textContent = item ? `Joriy slot: ${item.current_slot || 'tanlanmagan'}${remaining === null ? '' : ` · Uzum'da qolgan ko'chirishlar: ${remaining}`}` : '';
    sync();
  });
  from.addEventListener('change', () => {
    to.min = from.value || tashkentToday();
    if (from.value && (!to.value || to.value < from.value)) to.value = from.value;
    sync();
  });
  to.addEventListener('change', sync);

  load.addEventListener('click', async () => {
    if (!shop.value) return;
    busy(load, true);
    resetInvoices();
    note.textContent = "Uzum kabinetidan olinmoqda, bu bir daqiqagacha cho'zilishi mumkin…";
    try {
      ({ invoices } = await api(`/api/uzum/invoices?shop_id=${encodeURIComponent(shop.value)}`));
      invoice.replaceChildren(new Option(invoices.length ? 'Yuk xatini tanlang' : "«Создана» holatidagi yuk xati yo'q", ''),
        ...invoices.map(item => new Option(`№ ${item.number} · ${item.stock || 'ombor'} · ${item.current_slot || "slot yo'q"}`, String(item.id))));
      invoice.disabled = !invoices.length;
      note.textContent = '';
    } catch (error) { note.textContent = ''; toast(error.message, true); }
    finally { busy(load, false); sync(); }
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const item = invoices.find(entry => String(entry.id) === invoice.value);
    if (!item) return;
    busy(submit, true);
    try {
      await api('/api/timeslot-watches', { method: 'POST', body: jsonBody({ shop_id: shop.value, invoice_id: item.id,
        invoice_number: item.number, stock_title: item.stock, date_from: from.value, date_to: to.value }) });
      toast('Kuzatuv boshlandi. Birinchi tekshiruv natijasi shu yerda chiqadi.');
      refreshWatches();
    } catch (error) { toast(error.message, true); }
    finally { busy(submit, false); sync(); }
  });

  document.addEventListener('click', async event => {
    const cancel = event.target.closest('[data-cancel-watch]');
    if (cancel) {
      if (!await confirmAction("Yuk xati endi kuzatilmaydi. Uzum'dagi joriy slot o'zgarmaydi.", 'Kuzatuvni bekor qilish')) return;
      try { await api(`/api/timeslot-watches/${cancel.dataset.cancelWatch}/cancel`, { method: 'POST' }); refreshWatches(); }
      catch (error) { toast(error.message, true); }
    }
    const check = event.target.closest('[data-action="check-timeslots"]');
    if (check) {
      busy(check, true);
      try { await api('/api/timeslot-watches/check', { method: 'POST' }); toast('Tekshiruv boshlandi, natija bir necha daqiqada yangilanadi.'); }
      catch (error) { toast(error.message, true); }
      finally { setTimeout(() => busy(check, false), 30_000); }
    }
  });
}
