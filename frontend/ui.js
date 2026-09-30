import { createIcons, Layers2, SquarePlus, LayoutGrid, Settings2, Sparkles, ArrowUpRight,
  LogOut, Menu, ChevronRight, ArrowRight, ArrowLeft, Eye, EyeOff, ImagePlus, Image,
  Trash2, GripVertical, Check, Copy, Download, RefreshCw, Search, ChevronLeft, ChevronDown,
  X, CircleCheck, LoaderCircle, TriangleAlert, Info, SlidersHorizontal, FileSpreadsheet,
  Plus, Mail, LockKeyhole, ShieldCheck, MoreHorizontal, CheckCheck, Maximize2 } from 'lucide';

export function icons() {
  createIcons({ icons: { Layers2, SquarePlus, LayoutGrid, Settings2, Sparkles, ArrowUpRight,
    LogOut, Menu, ChevronRight, ArrowRight, ArrowLeft, Eye, EyeOff, ImagePlus, Image,
    Trash2, GripVertical, Check, Copy, Download, RefreshCw, Search, ChevronLeft, ChevronDown,
    X, CircleCheck, LoaderCircle, TriangleAlert, Info, SlidersHorizontal, FileSpreadsheet,
    Plus, Mail, LockKeyhole, ShieldCheck, MoreHorizontal, CheckCheck, Maximize2 }, attrs: { 'aria-hidden': 'true' } });
}

let toastTimer;
export function toast(message, danger = false) {
  const element = document.querySelector('#toast');
  if (!element) return;
  element.textContent = message;
  element.classList.toggle('danger', danger);
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.hidden = true; }, 6000);
}

export class ApiError extends Error {
  constructor(error, status) {
    super(error.message || "So'rov bajarilmadi.");
    this.code = error.code;
    this.fields = error.fields || {};
    this.status = status;
  }
}

export async function api(url, options = {}) {
  const headers = new Headers(options.headers);
  headers.set('X-CSRF-Token', document.querySelector('meta[name="csrf-token"]')?.content || '');
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  let response;
  try { response = await fetch(url, { ...options, headers, credentials: 'same-origin' }); }
  catch { throw new ApiError({ code: 'network_error', message: "Aloqa uzildi. Saqlangan holatni tekshirib, qayta urinib ko'ring." }, 0); }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ error: { message: "Xizmat vaqtincha ishlamayapti." } }));
    throw new ApiError(payload.error || {}, response.status);
  }
  if (options.download) return response.blob();
  return response.status === 204 || response.status === 202 && !response.headers.get('content-type') ? null : response.json();
}

export function jsonBody(value) { return JSON.stringify(value); }

export async function confirmAction(message, title = 'Amalni tasdiqlang') {
  const dialog = document.querySelector('#confirm-dialog');
  dialog.querySelector('#confirm-title').textContent = title;
  dialog.querySelector('#confirm-message').textContent = message;
  dialog.returnValue = 'cancel';
  dialog.showModal();
  return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true }));
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast('Nusxa olindi.'); }
  catch { toast("Nusxa olinmadi. Matnni qo'lda belgilang.", true); }
}

export async function downloadCards(cardIds, format) {
  const blob = await api('/api/exports', { method: 'POST', body: jsonBody({ card_ids: cardIds, format }), download: true });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `karto-cards.${format}`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('Fayl tayyor.');
}

export function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function iconButton(name, label, handler) {
  const button = element('button', 'icon-button');
  button.type = 'button';
  button.title = label;
  button.setAttribute('aria-label', label);
  const icon = document.createElement('i');
  icon.dataset.lucide = name;
  button.append(icon);
  button.addEventListener('click', handler);
  return button;
}
