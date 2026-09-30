import { api, element, iconButton, icons, jsonBody, toast } from './ui.js';

export function initUpload() {
  const form = document.querySelector('#new-card-form');
  if (!form) return;
  const input = form.querySelector('#images');
  const shelf = form.querySelector('#image-shelf');
  const dropzone = form.querySelector('.dropzone');
  const submit = form.querySelector('button[type="submit"]');
  let files = [], urls = [], busy = false, draftKey = crypto.randomUUID(), dirty = false;
  const updateSubmit = () => { submit.disabled = busy || !files.length || !form.querySelector('#consent').checked; };
  const changed = () => { dirty = true; draftKey = crypto.randomUUID(); updateSubmit(); };
  const render = () => {
    urls.forEach(url => URL.revokeObjectURL(url)); urls = []; shelf.replaceChildren();
    files.forEach((file, index) => {
      const tile = element('div', 'upload-tile');
      const photo = element('img'); photo.alt = file.name; photo.src = URL.createObjectURL(file); urls.push(photo.src);
      const tools = element('div', 'upload-tools');
      if (index) tools.append(iconButton('arrow-left', 'Oldinga surish', () => {
        [files[index - 1], files[index]] = [files[index], files[index - 1]]; changed(); render();
      }));
      tools.append(iconButton('trash-2', "Rasmni o'chirish", () => { files.splice(index, 1); changed(); render(); }));
      tile.append(photo, element('span', 'image-number', String(index + 1)), tools); shelf.append(tile);
    });
    form.querySelector('#image-count').textContent = `${files.length} / 5`;
    dropzone.classList.toggle('compact', files.length > 0); icons(); updateSubmit();
  };
  const addFiles = incoming => {
    if (busy) return;
    if (files.length + incoming.length > 5) return toast("Ko'pi bilan 5 ta rasm tanlang.", true);
    for (const file of incoming) {
      if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024) {
        return toast('JPEG, PNG yoki WebP; har biri 10 MiB gacha.', true);
      }
    }
    files.push(...incoming); changed(); render();
  };
  input.addEventListener('change', () => { addFiles([...input.files]); input.value = ''; });
  dropzone.addEventListener('dragover', event => { event.preventDefault(); dropzone.classList.add('dragging'); });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragging'));
  dropzone.addEventListener('drop', event => { event.preventDefault(); dropzone.classList.remove('dragging'); addFiles([...event.dataTransfer.files]); });
  form.addEventListener('input', changed);
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy || !form.reportValidity()) return;
    busy = true; updateSubmit(); submit.setAttribute('aria-busy', 'true');
    const body = new FormData(); files.forEach(file => body.append('images[]', file));
    body.set('seller_notes', form.querySelector('#seller-notes').value);
    body.set('consent_version', form.dataset.consentVersion);
    try {
      const card = await api('/api/cards', { method: 'POST', headers: { 'Idempotency-Key': draftKey }, body });
      try { await api(`/api/cards/${card.id}/generations`, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: jsonBody({ operation: 'initial', expected_version: card.version }) }); }
      catch (error) { sessionStorage.setItem('karto-notice', error.message); }
      dirty = false; location.assign(`/cards/${card.id}`);
    } catch (error) { toast(error.message, true); busy = false; updateSubmit(); }
    finally { submit.removeAttribute('aria-busy'); }
  });
  render();
}
