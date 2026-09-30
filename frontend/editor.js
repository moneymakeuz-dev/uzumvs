import { api, confirmAction, copyText, downloadCards, element, iconButton, icons, jsonBody, toast } from './ui.js';

const LANGUAGES = [['uz', "O'zbekcha"], ['ru', 'Русский']];
const TEXT_FIELDS = [
  { name: 'title', label: 'Sarlavha', ru: 'Название', max: 200, required: true, hint: 'Turi, brend, model va asosiy xususiyat' },
  { name: 'short_description', label: 'Qisqa tavsif', ru: 'Краткое описание', max: 1000, rows: 3, hint: '1-2 gap' },
  { name: 'description', label: 'Tavsif', ru: 'Описание', max: 10000, rows: 10, required: true },
  { name: 'suggested_category', label: 'Kategoriya taklifi', ru: 'Категория', max: 200, optional: true, hint: 'Aniq kategoriyani Uzum kabinetida tanlang' },
  { name: 'color', label: 'Rang', ru: 'Цвет', max: 100, optional: true },
  { name: 'material', label: 'Material', ru: 'Материал', max: 150, optional: true },
];
const SOURCES = { seller: 'Sotuvchi', visible: 'Rasmda', inferred: 'Taxmin', unknown: "Noma'lum" };
const state = { card: null, draft: null, notes: '', language: 'uz', dirty: false, resolved: new Set(),
  locked: false, saving: false, wasActive: false, pendingKeys: new Map() };

const clone = value => structuredClone(value);
const cardUrl = suffix => `/api/cards/${state.card.id}${suffix}`;

function reviewsFor(name, language = state.language) {
  return state.card.unresolved_reviews.filter(item => {
    const [field, itemLanguage] = item.path.split('.');
    return field === name && (!itemLanguage || itemLanguage === language);
  });
}

function warningsFor(name, language = state.language) {
  return state.card.rule_warnings.filter(item => {
    const [field, itemLanguage] = item.path.split('.');
    return field === name && (!itemLanguage || itemLanguage === language);
  });
}

function reviewCount(language) {
  return state.card.unresolved_reviews.filter(item => !item.path.split('.')[1] || item.path.split('.')[1] === language).length;
}

function setSaveState() {
  const label = document.querySelector('#save-state');
  const button = document.querySelector('[data-action="save"]');
  if (label) label.textContent = state.saving ? 'Saqlanmoqda...' : state.dirty ? "Saqlanmagan o'zgarishlar" : 'Saqlangan';
  label?.classList.toggle('dirty', state.dirty);
  if (button) button.disabled = !state.dirty || state.saving || state.locked;
}

function markDirty() {
  state.dirty = true;
  setSaveState();
}

function setLocked(locked) {
  state.locked = locked;
  for (const node of [document.querySelector('#editor'), document.querySelector('.notes-block')]) {
    if (node) node.inert = locked;
  }
  document.querySelector('#editor')?.classList.toggle('locked', locked);
  document.querySelectorAll('[data-lock]').forEach(button => { button.disabled = locked; });
  setSaveState();
}

function fieldNotes(name) {
  const notes = reviewsFor(name).map(item => {
    const note = element('div', 'review-note');
    const check = element('label', 'review-check');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = state.resolved.has(item.id);
    box.addEventListener('change', () => {
      if (box.checked) state.resolved.add(item.id); else state.resolved.delete(item.id);
      markDirty();
    });
    check.append(box, document.createTextNode('Tekshirdim'));
    note.append(element('span', 'note-mark', '!'), element('span', 'note-text', item.reason), check);
    return note;
  });
  return notes.concat(warningsFor(name).map(item => element('div', 'rule-note', `Uzum qoidasi: ${item.message}`)));
}

function fieldHead(label, inputId, copyValue, regenPath, bothLanguages = false) {
  const head = element('div', 'field-head');
  const labelNode = element('label', '', label);
  labelNode.htmlFor = inputId;
  labelNode.append(element('span', 'lang-code', bothLanguages ? 'UZ + RU' : state.language.toUpperCase()));
  const tools = element('div', 'field-tools');
  tools.append(iconButton('copy', `${label}: nusxalash`, () => copyText(copyValue())));
  const regenLabel = bothLanguages ? `${label}: ikkala tilda qayta yaratish` : `${label}: qayta yaratish`;
  tools.append(iconButton('refresh-cw', regenLabel, () => regenerate(regenPath)));
  head.append(labelNode, tools);
  return head;
}

function fieldMeta(hint, counter) {
  const meta = element('div', 'field-meta');
  meta.append(element('span', 'hint', hint || ''), counter);
  return meta;
}

function textField(field) {
  const language = state.language;
  const inputId = `field-${field.name}`;
  const wrapper = element('div', 'field');
  const input = element(field.rows ? 'textarea' : 'input');
  input.id = inputId;
  if (field.rows) input.rows = field.rows;
  input.maxLength = field.max;
  input.lang = language === 'uz' ? 'uz-Latn' : 'ru';
  input.value = state.draft[field.name][language] ?? '';
  input.required = Boolean(field.required);
  const counter = element('span', 'counter');
  const updateCounter = () => { counter.textContent = `${input.value.length} / ${field.max}`; };
  input.addEventListener('input', () => {
    state.draft[field.name][language] = input.value;
    updateCounter();
    markDirty();
  });
  updateCounter();
  const regenPath = field.optional ? field.name : `${field.name}.${language}`;
  wrapper.append(fieldHead(field.label, inputId, () => input.value, regenPath, field.optional),
    input, fieldMeta(field.hint, counter), ...fieldNotes(field.name));
  return wrapper;
}

function parseKeywords(text) {
  const seen = new Set();
  const keywords = [];
  for (const part of text.split(/[,\n]/)) {
    const value = part.trim().slice(0, 64);
    if (value && !seen.has(value.toLocaleLowerCase())) {
      seen.add(value.toLocaleLowerCase());
      keywords.push(value);
    }
  }
  return keywords.slice(0, 20);
}

function keywordsField() {
  const language = state.language;
  const wrapper = element('div', 'field');
  const input = element('textarea');
  input.id = 'field-keywords';
  input.rows = 2;
  input.value = state.draft.keywords[language].join(', ');
  const counter = element('span', 'counter', `${state.draft.keywords[language].length} / 20`);
  input.addEventListener('input', () => {
    state.draft.keywords[language] = parseKeywords(input.value);
    counter.textContent = `${state.draft.keywords[language].length} / 20`;
    markDirty();
  });
  wrapper.append(fieldHead("Kalit so'zlar", input.id, () => state.draft.keywords[language].join(', '), `keywords.${language}`),
    input, fieldMeta("Vergul bilan ajrating. Mahsulotga aloqasiz so'z qo'shmang.", counter), ...fieldNotes('keywords'));
  return wrapper;
}

function newAttribute() {
  const keys = new Set(state.draft.attributes.map(item => item.key));
  let index = 1;
  while (keys.has(`custom_${index}`)) index += 1;
  return { key: `custom_${index}`, name: { uz: '', ru: '' }, value: { uz: '', ru: '' }, source: 'seller' };
}

function attributeRow(attribute, index, language) {
  const row = element('div', 'attribute-row');
  const name = element('input');
  const value = element('input');
  name.id = `attribute-${index}-name`;
  name.maxLength = 100;
  name.placeholder = 'Nomi';
  name.value = attribute.name[language];
  name.setAttribute('aria-label', `${index + 1}-xususiyat nomi`);
  value.maxLength = 500;
  value.placeholder = 'Qiymati';
  value.value = attribute.value[language];
  value.setAttribute('aria-label', `${index + 1}-xususiyat qiymati`);
  name.addEventListener('input', () => { attribute.name[language] = name.value; markDirty(); });
  value.addEventListener('input', () => { attribute.value[language] = value.value; markDirty(); });
  const source = element('span', `source-badge ${attribute.source || 'seller'}`, SOURCES[attribute.source] || SOURCES.seller);
  row.append(name, value, source, iconButton('trash-2', `${index + 1}-xususiyatni o'chirish`, () => {
    state.draft.attributes.splice(index, 1);
    markDirty();
    render();
  }));
  return row;
}

function attributesText(language) {
  return state.draft.attributes.map(item => `${item.name[language]}: ${item.value[language]}`).join('\n');
}

function attributesField() {
  const language = state.language;
  const wrapper = element('div', 'field attributes-field');
  const list = element('div', 'attribute-list');
  state.draft.attributes.forEach((attribute, index) => list.append(attributeRow(attribute, index, language)));
  if (!state.draft.attributes.length) list.append(element('p', 'muted', "Xususiyatlar hali yo'q."));
  const add = element('button', 'button secondary compact-button');
  add.type = 'button';
  add.id = 'attributes-add';
  add.disabled = state.draft.attributes.length >= 30;
  add.append(document.createElement('i'), document.createTextNode("Xususiyat qo'shish"));
  add.firstChild.dataset.lucide = 'plus';
  add.addEventListener('click', () => {
    state.draft.attributes.push(newAttribute());
    markDirty();
    render();
    document.querySelector(`#attribute-${state.draft.attributes.length - 1}-name`)?.focus();
  });
  wrapper.append(fieldHead('Xususiyatlar', add.id, () => attributesText(language), 'attributes', true),
    list, add, ...fieldNotes('attributes'));
  return wrapper;
}

function languageTabs() {
  const list = element('div', 'language-tabs');
  list.setAttribute('role', 'tablist');
  list.setAttribute('aria-label', 'Kartochka tili');
  for (const [code, label] of LANGUAGES) {
    const tab = element('button', 'language-tab', label);
    tab.type = 'button';
    tab.id = `tab-${code}`;
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-controls', 'language-panel');
    tab.setAttribute('aria-selected', String(code === state.language));
    tab.tabIndex = code === state.language ? 0 : -1;
    const count = reviewCount(code);
    if (count) tab.append(element('span', 'tab-count', String(count)));
    tab.addEventListener('click', () => switchLanguage(code));
    tab.addEventListener('keydown', event => {
      if (['ArrowLeft', 'ArrowRight'].includes(event.key)) switchLanguage(code === 'uz' ? 'ru' : 'uz', true);
    });
    list.append(tab);
  }
  return list;
}

function switchLanguage(code, focus = false) {
  state.language = code;
  render();
  if (focus) document.querySelector(`#tab-${code}`)?.focus();
}

function reviewSummary() {
  const total = state.card.unresolved_reviews.length;
  const box = element('div', 'review-summary');
  if (!total) {
    box.classList.add('clear');
    box.append(element('span', 'note-mark ok', '✓'), element('span', '', "Tekshiruv talab qiladigan maydon yo'q."));
    return box;
  }
  const pending = state.resolved.size ? ` ${state.resolved.size} tasi saqlashda tasdiqlanadi.` : '';
  box.append(element('span', 'note-mark', '!'), element('span', '',
    `${total} ta maydonni tekshiring. Eksportdan oldin har birini tuzating yoki tasdiqlang.${pending}`));
  return box;
}

function render() {
  const root = document.querySelector('#editor');
  root.replaceChildren();
  if (!state.draft) {
    const empty = element('div', 'editor-empty');
    empty.append(element('h2', '', 'Kartochka matni hali yaratilmagan'),
      element('p', '', "Rasmlar saqlangan. Izohni tekshirib, yuqoridagi tugma orqali yaratishni boshlang."));
    root.append(empty);
    return;
  }
  const panel = element('div', 'field-list');
  panel.id = 'language-panel';
  panel.setAttribute('role', 'tabpanel');
  panel.setAttribute('aria-labelledby', `tab-${state.language}`);
  TEXT_FIELDS.forEach(field => panel.append(textField(field)));
  panel.append(keywordsField(), attributesField());
  root.append(languageTabs(), reviewSummary(), panel);
  icons();
}

function clean(value, optional) {
  const text = (value ?? '').trim();
  return optional && !text ? null : text;
}

function contentPayload() {
  const content = {};
  for (const field of TEXT_FIELDS) {
    const value = state.draft[field.name];
    content[field.name] = { uz: clean(value.uz, field.optional), ru: clean(value.ru, field.optional) };
  }
  content.keywords = { uz: parseKeywords(state.draft.keywords.uz.join(',')), ru: parseKeywords(state.draft.keywords.ru.join(',')) };
  content.attributes = state.draft.attributes.map(({ key, name, value }) => ({
    key, name: { uz: name.uz.trim(), ru: name.ru.trim() }, value: { uz: value.uz.trim(), ru: value.ru.trim() },
  }));
  return content;
}

function validationProblem() {
  for (const [code] of LANGUAGES) {
    for (const field of TEXT_FIELDS.filter(item => item.required)) {
      if (!(state.draft[field.name][code] || '').trim()) {
        return { language: code, id: `field-${field.name}`, message: `${field.label} (${code.toUpperCase()}) bo'sh bo'lmasligi kerak.` };
      }
    }
    const index = state.draft.attributes.findIndex(item => !item.name[code].trim() || !item.value[code].trim());
    if (index >= 0) {
      return { language: code, id: `attribute-${index}-name`, message: `${index + 1}-xususiyatning ${code.toUpperCase()} nomi va qiymatini kiriting.` };
    }
  }
  return null;
}

function applyCard(card) {
  state.card = card;
  state.draft = card.content ? clone(card.content) : null;
  state.notes = card.seller_notes;
  state.resolved.clear();
  state.dirty = false;
  document.querySelector('#card-version').textContent = `v${card.version}`;
  if (card.content) document.querySelector('#card-heading').textContent = card.content.title.uz;
  document.querySelector('#conflict').hidden = true;
  render();
  setSaveState();
}

async function save() {
  if (state.saving || state.locked) return false;
  const problem = state.draft ? validationProblem() : null;
  if (problem) {
    toast(problem.message, true);
    if (problem.language !== state.language) switchLanguage(problem.language);
    document.querySelector(`#${problem.id}`)?.focus();
    return false;
  }
  const body = { expected_version: state.card.version, resolve_review_ids: [...state.resolved] };
  if (state.notes !== state.card.seller_notes) body.seller_notes = state.notes;
  if (state.draft) body.content = contentPayload();
  state.saving = true;
  setSaveState();
  try {
    applyCard(await api(cardUrl(''), { method: 'PATCH', body: jsonBody(body) }));
    toast('Saqlandi.');
    return true;
  } catch (error) {
    if (error.code === 'version_conflict') await showConflict();
    else toast(error.message, true);
    return false;
  } finally {
    state.saving = false;
    setSaveState();
  }
}

function differences(server) {
  const items = [];
  const add = (label, theirs, mine) => {
    if (JSON.stringify(theirs ?? '') !== JSON.stringify(mine ?? '')) items.push({ label, theirs, mine });
  };
  add('Sotuvchi izohi', server.seller_notes, state.notes);
  if (server.content && state.draft) {
    const mine = contentPayload();
    for (const field of TEXT_FIELDS) {
      for (const [code] of LANGUAGES) add(`${field.label} (${code.toUpperCase()})`, server.content[field.name][code], mine[field.name][code]);
    }
    for (const [code] of LANGUAGES) add(`Kalit so'zlar (${code.toUpperCase()})`, server.content.keywords[code].join(', '), mine.keywords[code].join(', '));
    add('Xususiyatlar', server.content.attributes.map(({ key, name, value }) => ({ key, name, value })), mine.attributes);
  }
  return items;
}

function preview(value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? '');
  return text.length > 160 ? `${text.slice(0, 160)}...` : text || "(bo'sh)";
}

async function showConflict() {
  let server;
  try {
    server = await api(cardUrl(''));
  } catch (error) {
    toast(error.message, true);
    return;
  }
  const panel = document.querySelector('#conflict');
  panel.replaceChildren(element('h2', '', "Kartochka boshqa oynada o'zgargan"),
    element('p', '', `Sizning matningiz saqlanmadi, lekin shu sahifada turibdi. Serverdagi versiya: v${server.version}.`));
  const list = element('dl', 'conflict-list');
  for (const item of differences(server).slice(0, 8)) {
    list.append(element('dt', '', item.label), element('dd', '', `Serverda: ${preview(item.theirs)}`), element('dd', 'mine', `Sizda: ${preview(item.mine)}`));
  }
  const actions = element('div', 'conflict-actions');
  const keep = element('button', 'button primary', "Mening o'zgarishlarimni saqlash");
  const reload = element('button', 'button secondary', 'Server versiyasini ochish');
  keep.type = reload.type = 'button';
  keep.addEventListener('click', async () => {
    const draft = state.draft;
    const notes = state.notes;
    const resolved = [...state.resolved].filter(id => server.unresolved_reviews.some(item => item.id === id));
    Object.assign(state, { card: server, draft, notes, resolved: new Set(resolved) });
    panel.hidden = true;
    await save();
  });
  reload.addEventListener('click', () => { state.dirty = false; location.reload(); });
  actions.append(keep, reload);
  panel.append(list, actions);
  panel.hidden = false;
  panel.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
}

function refreshStatus() {
  window.htmx.ajax('GET', `/cards/${state.card.id}/status`, { target: '#job-status', swap: 'outerHTML' });
}

async function startJob(request) {
  const body = { ...request, expected_version: state.card.version };
  const signature = JSON.stringify(body);
  const key = state.pendingKeys.get(signature) || crypto.randomUUID();
  state.pendingKeys.set(signature, key);
  try {
    await api(cardUrl('/generations'), { method: 'POST', headers: { 'Idempotency-Key': key }, body: jsonBody(body) });
    state.pendingKeys.delete(signature);
    state.wasActive = true;
    setLocked(true);
    refreshStatus();
  } catch (error) {
    if (error.status !== 0) state.pendingKeys.delete(signature);
    toast(error.message, true);
  }
}

async function saveBeforeJob() {
  if (!state.dirty) return true;
  if (!await confirmAction("Saqlanmagan o'zgarishlar bor. Avval ular saqlanadi.", "O'zgarishlarni saqlash")) return false;
  return save();
}

async function regenerate(path) {
  const message = path ? "Tanlangan maydon qayta yaratiladi. Boshqa maydonlar o'zgarmaydi, 1 ta qayta yaratish limiti ishlatiladi."
    : "Butun matn qayta yaratiladi va qo'lda qilingan tahrirlar almashtiriladi. 1 ta qayta yaratish limiti ishlatiladi.";
  if (!await confirmAction(message, 'Qayta yaratish') || !await saveBeforeJob()) return;
  await startJob(path ? { operation: 'regenerate_field', field_path: path } : { operation: 'regenerate_all' });
}

async function generateInitial() {
  if (await saveBeforeJob()) await startJob({ operation: 'initial' });
}

async function cancelJob(jobId) {
  try {
    await api(`/api/jobs/${jobId}/cancel`, { method: 'POST' });
    toast('Vazifa bekor qilindi. Limit qaytarildi.');
  } catch (error) {
    toast(error.message, true);
  }
  refreshStatus();
}

async function exportCard(format) {
  if (state.dirty) {
    if (!await confirmAction("Eksportga faqat saqlangan versiya kiradi. Avval saqlaymizmi?", "Saqlanmagan o'zgarishlar")) return;
    if (!await save()) return;
  }
  if (state.card.unresolved_reviews.length) {
    toast('Eksportdan oldin belgilangan maydonlarni tekshirib, saqlang.', true);
    return;
  }
  try {
    await downloadCards([state.card.id], format);
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteCard() {
  if (!await confirmAction("Kartochka va uning rasmlari o'chiriladi. Sarflangan limit qaytmaydi.", "Kartochkani o'chirish")) return;
  try {
    await api(cardUrl(`?expected_version=${state.card.version}`), { method: 'DELETE' });
    state.dirty = false;
    location.assign('/cards');
  } catch (error) {
    toast(error.message, true);
  }
}

function copyAll() {
  const language = state.language;
  const labels = TEXT_FIELDS.map(field => [field.name, language === 'uz' ? field.label : field.ru]);
  const lines = labels.filter(([name]) => state.draft[name][language]).map(([name, label]) => `${label}:\n${state.draft[name][language]}`);
  if (state.draft.attributes.length) lines.push(`${language === 'uz' ? 'Xususiyatlar' : 'Характеристики'}:\n${attributesText(language)}`);
  if (state.draft.keywords[language].length) lines.push(`${language === 'uz' ? "Kalit so'zlar" : 'Ключевые слова'}:\n${state.draft.keywords[language].join(', ')}`);
  copyText(lines.join('\n\n'));
}

function statusChanged() {
  const panel = document.querySelector('#job-status');
  if (!panel) return;
  const active = ['queued', 'running'].includes(panel.dataset.status);
  if (state.wasActive && !active && Number(panel.dataset.version) !== state.card.version) {
    state.dirty = false;
    location.reload();
    return;
  }
  state.wasActive = active;
  setLocked(active);
}

function bindActions() {
  const actions = { save, 'copy-all': copyAll, 'regenerate-all': () => regenerate(null), 'delete-card': deleteCard,
    'generate-initial': generateInitial };
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-action]');
    if (!button || button.disabled) return;
    const action = button.dataset.action;
    if (actions[action]) actions[action]();
    if (action === 'export') exportCard(button.dataset.format);
    if (action === 'cancel-job') cancelJob(button.dataset.jobId);
  });
  document.querySelectorAll('.thumbnail').forEach(button => button.addEventListener('click', () => {
    document.querySelector('#main-image').src = button.dataset.image;
    document.querySelector('#main-image').alt = `${button.dataset.index}-mahsulot rasmi`;
    document.querySelectorAll('.thumbnail').forEach(item => {
      item.classList.toggle('active', item === button);
      item.setAttribute('aria-pressed', String(item === button));
    });
  }));
  document.querySelector('#seller-notes-edit')?.addEventListener('input', event => { state.notes = event.target.value; markDirty(); });
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); if (state.dirty) save(); }
  });
  window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
  document.body.addEventListener('htmx:afterSettle', statusChanged);
}

export function initEditor() {
  const data = document.querySelector('#card-data');
  if (!data) return;
  const card = JSON.parse(data.textContent);
  Object.assign(state, { card, draft: card.content ? clone(card.content) : null, notes: card.seller_notes });
  bindActions();
  render();
  statusChanged();
}
