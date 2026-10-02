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
  locked: false, saving: false, wasActive: false, pendingKeys: new Map(), uzumTemplate: null, uzumCatalog: null };

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

async function openUzumExport() {
  if (state.dirty) {
    if (!await confirmAction("Uzum fayliga faqat saqlangan matn kiradi. Avval saqlaymizmi?", "Saqlanmagan o'zgarishlar")) return;
    if (!await save()) return;
  }
  if (state.card.unresolved_reviews.length) {
    toast('Uzum faylidan oldin belgilangan maydonlarni tekshirib, saqlang.', true);
    return;
  }
  const form = document.querySelector('#uzum-export-form');
  form.reset();
  state.uzumTemplate = null;
  state.uzumCatalog = null;
  document.querySelector('#uzum-category-id').value = '';
  document.querySelector('#uzum-category-search').disabled = true;
  document.querySelector('#uzum-category-options').hidden = true;
  document.querySelector('#uzum-export-submit').disabled = true;
  document.querySelector('#uzum-download-file').disabled = true;
  document.querySelector('#uzum-template-category-note').textContent = '';
  document.querySelector('#uzum-shop').replaceChildren(new Option('Avval shablonni tanlang', ''));
  document.querySelector('#uzum-shop').disabled = true;
  document.querySelector('#uzum-sku-group').value = `K${state.card.id.replaceAll('-', '').slice(0, 12)}`;
  document.querySelector('#uzum-brand-options').replaceChildren(new Option('Отсутствует бренд', 'Отсутствует бренд'));
  document.querySelector('#uzum-country-options').replaceChildren();
  document.querySelector('#uzum-export-dialog').showModal();
}

async function loadUzumTemplate(file) {
  const submit = document.querySelector('#uzum-export-submit');
  submit.disabled = true;
  state.uzumTemplate = null;
  state.uzumCatalog = null;
  document.querySelector('#uzum-category-search').disabled = true;
  document.querySelector('#uzum-category-id').value = '';
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.xlsm') || file.size > 10 * 1024 * 1024) {
    toast('Yangi Uzum .xlsm shablonini tanlang (10 MiB gacha).', true);
    return;
  }
  const body = new FormData();
  body.append('template', file);
  try {
    state.uzumCatalog = await api(cardUrl('/uzum-template/catalog'), { method: 'POST', body });
    state.uzumTemplate = file;
    const shopSelect = document.querySelector('#uzum-shop');
    shopSelect.replaceChildren(new Option('Do‘konni tanlang', ''),
      ...state.uzumCatalog.shops.map(shop => new Option(shop.name, shop.id)));
    shopSelect.disabled = false;
    if (state.uzumCatalog.shops.length === 1) shopSelect.value = state.uzumCatalog.shops[0].id;
    const countries = document.querySelector('#uzum-country-options');
    countries.replaceChildren(...state.uzumCatalog.countries.map(country => new Option(country, country)));
    document.querySelector('#uzum-category-search').disabled = false;
    const recommendation = state.uzumCatalog.recommended_category;
    const categorySearch = document.querySelector('#uzum-category-search');
    const categoryId = document.querySelector('#uzum-category-id');
    if (recommendation) {
      categorySearch.value = recommendation.path;
      categoryId.value = recommendation.id;
      document.querySelector('#uzum-category-recommendation').textContent =
        `Avtomatik tavsiya (${Math.round(recommendation.score * 100)}% moslik): tekshirib tasdiqlang.`;
    } else {
      document.querySelector('#uzum-category-recommendation').textContent = 'Aniq moslik topilmadi, kategoriyani qidiring.';
    }
    updateUzumCategoryActions();
    toast('Shablon o‘qildi. Kategoriya va sotuvchi ma’lumotlarini tanlang.');
  } catch (error) {
    toast(error.message, true);
  }
}

function updateUzumCategoryActions() {
  const selectedId = document.querySelector('#uzum-category-id').value;
  const preparedId = state.uzumCatalog?.selected_category_id;
  const ready = Boolean(state.uzumTemplate && selectedId && preparedId && selectedId === preparedId);
  document.querySelector('#uzum-export-submit').disabled = !ready;
  document.querySelector('#uzum-download-file').disabled = !ready;
  const preparedPath = state.uzumCatalog?.selected_category_path || 'Aniq emas';
  document.querySelector('#uzum-template-category-note').textContent = preparedId
    ? `Excel makrosida tayyorlangan kategoriya: ${preparedPath} | ${preparedId}${ready ? ' · Mos' : ''}`
    : 'Shablonda Excel makrosi bilan tanlangan kategoriya topilmadi. Excel’da kategoriyani tanlab, saqlangan shablonni qayta yuklang.';
  if (selectedId && preparedId && selectedId !== preparedId) {
    document.querySelector('#uzum-template-category-note').textContent +=
      ' Tanlangan kategoriya bilan mos emas; upload bloklandi. Excel’da Karto tanlagan kategoriyani tanlab, shablonni qayta yuklang.';
  }
}

function showUzumCategories(query) {
  const list = document.querySelector('#uzum-category-options');
  const selected = document.querySelector('#uzum-category-id');
  selected.value = '';
  updateUzumCategoryActions();
  list.replaceChildren();
  const normalized = query.trim().toLocaleLowerCase();
  if (!state.uzumCatalog || normalized.length < 2) { list.hidden = true; return; }
  const matches = state.uzumCatalog.categories.filter(item =>
    item.path.toLocaleLowerCase().includes(normalized) || item.title.toLocaleLowerCase().includes(normalized));
  for (const item of matches.slice(0, 15)) {
    const option = document.createElement('button');
    option.type = 'button';
    option.setAttribute('role', 'option');
    option.textContent = `${item.path} | ${item.id}`;
    option.addEventListener('click', () => {
      document.querySelector('#uzum-category-search').value = item.path;
      selected.value = item.id;
      list.hidden = true;
      updateUzumCategoryActions();
    });
    list.append(option);
  }
  if (!matches.length) list.append(element('div', 'quiet', 'Kategoriya topilmadi.'));
  list.hidden = false;
}

function collectUzumPayload() {
  const categoryId = document.querySelector('#uzum-category-id').value;
  if (!categoryId) { toast('Ro‘yxatdan aniq kategoriyani tanlang.', true); return null; }
  return {
    expected_version: state.card.version,
    category_id: Number(categoryId),
    sku_group: document.querySelector('#uzum-sku-group').value,
    brand: document.querySelector('#uzum-brand').value,
    country: document.querySelector('#uzum-country').value,
    ikpu: document.querySelector('#uzum-ikpu').value,
    photo_urls: document.querySelector('#uzum-photo-urls').value.split(/\r?\n/).map(value => value.trim()).filter(Boolean),
    sale_price: Number(document.querySelector('#uzum-sale-price').value),
    list_price: Number(document.querySelector('#uzum-list-price').value),
    weight_g: Number(document.querySelector('#uzum-weight').value),
    height_mm: Number(document.querySelector('#uzum-height').value),
    width_mm: Number(document.querySelector('#uzum-width').value),
    length_mm: Number(document.querySelector('#uzum-length').value),
    model: document.querySelector('#uzum-model').value,
    barcode: document.querySelector('#uzum-barcode').value,
    color: document.querySelector('#uzum-color').value,
    size: document.querySelector('#uzum-size').value,
  };
}

function uzumUploadBody(payload) {
  const body = new FormData();
  body.append('template', state.uzumTemplate);
  body.append('payload', JSON.stringify(payload));
  return body;
}

async function downloadUzumFile() {
  const form = document.querySelector('#uzum-export-form');
  if (!form.reportValidity()) return;
  if (state.dirty && !await save()) return;
  const payload = collectUzumPayload();
  if (!payload) return;
  const download = document.querySelector('#uzum-download-file');
  download.disabled = true;
  try {
    const blob = await api(cardUrl('/uzum-import-file'), { method: 'POST', body: uzumUploadBody(payload), download: true });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `uzum-${state.card.id}.xlsm`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast('XLSM tayyor. Uni Uzum kabinetidagi “Загрузить из файла” orqali yuboring.');
  } catch (error) {
    toast(error.message, true);
  } finally {
    download.disabled = false;
  }
}

async function publishUzumFile(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  if (state.dirty && !await save()) return;
  const payload = collectUzumPayload();
  const shopSelect = document.querySelector('#uzum-shop');
  if (!payload || !shopSelect.value) { toast('Uzum do‘konini va kategoriyani tanlang.', true); return; }
  const shopName = shopSelect.selectedOptions[0].textContent;
  if (!await confirmAction(`“${shopName}” do‘koniga import fayli yuboriladi va undagi tovarlar yaratiladi. Davom etasizmi?`, 'Uzum’ga yuborish')) return;
  const submit = document.querySelector('#uzum-export-submit');
  const download = document.querySelector('#uzum-download-file');
  submit.disabled = true;
  download.disabled = true;
  submit.textContent = 'Uzum’ga yuborilmoqda…';
  toast('Brauzer oynasi ochiladi. CAPTCHA yoki SMS tasdig‘i chiqsa, o‘sha oynada bajaring.');
  try {
    const body = uzumUploadBody({ ...payload, shop_id: shopSelect.value });
    const result = await api(cardUrl('/uzum-publish'), { method: 'POST', body });
    document.querySelector('#uzum-export-dialog').close();
    toast(result.message || 'Uzum upload so‘rovi qabul qilindi.');
  } catch (error) {
    toast(error.message, true);
  } finally {
    submit.disabled = false;
    submit.replaceChildren(Object.assign(document.createElement('i'), { dataset: { lucide: 'upload' } }), document.createTextNode('Uzum’ga yuborish'));
    download.disabled = false;
    icons();
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
    'generate-initial': generateInitial, 'uzum-import': openUzumExport,
    'download-uzum-file': downloadUzumFile,
    'close-uzum': () => document.querySelector('#uzum-export-dialog')?.close() };
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
  document.querySelector('#uzum-template')?.addEventListener('change', event => loadUzumTemplate(event.target.files?.[0]));
  document.querySelector('#uzum-category-search')?.addEventListener('input', event => showUzumCategories(event.target.value));
  document.querySelector('#uzum-export-form')?.addEventListener('submit', publishUzumFile);
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
