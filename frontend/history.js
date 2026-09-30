import { api, confirmAction, downloadCards, toast } from './ui.js';

const selected = new Set();

function updateSelection() {
  const bar = document.querySelector('#bulk-actions');
  if (!bar) return;
  bar.hidden = selected.size === 0;
  document.querySelector('#selected-count').textContent = `${selected.size} ta tanlangan`;
  document.querySelectorAll('[data-select-card]').forEach(input => { input.checked = selected.has(input.value); });
}

export function initHistory() {
  const root = document.querySelector('#history-content');
  if (!root) return;
  updateSelection();
  if (root.dataset.bound) return;
  root.dataset.bound = 'true';
  root.addEventListener('change', event => {
    if (!event.target.matches('[data-select-card]')) return;
    if (event.target.checked && selected.size >= 100) { event.target.checked = false; return toast('Eng ko\'pi 100 ta kartochka tanlang.', true); }
    event.target.checked ? selected.add(event.target.value) : selected.delete(event.target.value);
    updateSelection();
  });
  root.addEventListener('click', async event => {
    const remove = event.target.closest('[data-delete-card]');
    if (remove) {
      if (!await confirmAction("Kartochka va rasmlar o'chiriladi. Sarflangan limit qaytmaydi.", "Kartochkani o'chirish")) return;
      try { await api(`/api/cards/${remove.dataset.deleteCard}?expected_version=${remove.dataset.version}`, { method: 'DELETE' });
        selected.delete(remove.dataset.deleteCard); location.reload(); }
      catch (error) { toast(error.message, true); }
    }
  });
}

export function bindHistoryActions() {
  const bar = document.querySelector('#bulk-actions');
  if (!bar) return;
  bar.addEventListener('click', async event => {
    const button = event.target.closest('[data-bulk-format]');
    if (button) { button.disabled = true; try { await downloadCards([...selected], button.dataset.bulkFormat); }
      catch (error) { toast(error.message, true); } finally { button.disabled = false; } }
    if (event.target.closest('[data-clear-selection]')) { selected.clear(); updateSelection(); }
  });
  document.body.addEventListener('htmx:beforeRequest', event => {
    if (event.detail.elt?.id === 'history-filter') { selected.clear(); updateSelection(); }
  });
}
