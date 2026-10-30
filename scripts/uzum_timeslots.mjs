// FBO yuk xati timeslotini kuzatish: seller.uzum.uz kabineti ishlatadigan API orqali.
// Uzum qoidalari: timeslot faqat "CREATED" holatida, ko'pi bilan 2 marta (remainingAmountOfUpdates)
// o'zgaradi; slotgacha 48 soatdan kam qolganda ko'chirish jarimaga tushishi mumkin.
const API = 'https://api-seller.uzum.uz/api/seller/shop';
const TASHKENT_OFFSET_MS = 5 * 3600_000;
const PAGE_SIZE = 20;

export function toMs(value) {
  const ms = typeof value === 'number' ? value : typeof value === 'string' ? Date.parse(value) : Number.NaN;
  return Number.isFinite(ms) ? ms : null;
}

export function tashkentDate(ms) {
  return new Date(ms + TASHKENT_OFFSET_MS).toISOString().slice(0, 10);
}

function inRange(ms, task) {
  const day = tashkentDate(ms);
  return day >= task.dateFrom && day <= task.dateTo;
}

export function summary(invoice) {
  return {
    id: invoice.id,
    number: String(invoice.invoiceNumber ?? invoice.id),
    status: invoice.invoiceStatus?.value ?? null,
    statusText: invoice.invoiceStatus?.text ?? null,
    stock: invoice.stock?.title ?? '',
    timeFrom: toMs(invoice.timeSlotReservation?.timeFrom),
    timeTo: toMs(invoice.timeSlotReservation?.timeTo),
    remainingUpdates: typeof invoice.remainingAmountOfUpdates === 'number' ? invoice.remainingAmountOfUpdates : null,
  };
}

// Ko'chirishga to'siq bo'lsa yakuniy natijani, aks holda null qaytaradi.
export function blocker(info, task, now, minLeadMs) {
  if (info.status !== 'CREATED') return { outcome: 'stopped', reason: 'status', detail: info.statusText ?? info.status };
  if (info.timeFrom === null) return null;
  if (inRange(info.timeFrom, task)) return { outcome: 'already' };
  if (info.remainingUpdates === 0) return { outcome: 'stopped', reason: 'limit' };
  if (info.timeFrom - now < minLeadMs) return { outcome: 'stopped', reason: 'too_late' };
  return null;
}

export function pickSlot(slots, task, currentFrom, now) {
  return slots
    .map(slot => ({ slot, ms: toMs(slot?.timeFrom) }))
    .filter(({ ms }) => ms !== null && ms > now && ms !== currentFrom && inRange(ms, task))
    .sort((a, b) => a.ms - b.ms)[0]?.slot ?? null;
}

function earliest(slots) {
  const times = slots.map(slot => toMs(slot?.timeFrom)).filter(ms => ms !== null);
  return times.length ? Math.min(...times) : null;
}

function errorCode(json) {
  return json?.errors?.[0]?.code ?? json?.error?.code ?? json?.code ?? null;
}

// call(method, url, body) -> { ok, status, json }; brauzer sessiyasi tokeni bilan bajariladi.
export async function listInvoices(call, shopId) {
  const invoices = [];
  for (let page = 0; page < 5; page += 1) {
    const response = await call('GET', `${API}/${shopId}/invoice?page=${page}&size=${PAGE_SIZE}&statuses=CREATED`);
    if (!response.ok || !Array.isArray(response.json)) throw new Error(`invoices_http_${response.status}`);
    invoices.push(...response.json.map(summary));
    if (response.json.length < PAGE_SIZE) break;
  }
  return invoices;
}

export async function checkTimeslots(call, shopId, tasks, { minLeadMs, dryRun = false, now = Date.now }) {
  const results = [];
  for (const task of tasks) {
    try {
      results.push({ watchId: task.watchId, ...await checkInvoice(call, `${API}/${shopId}`, task, minLeadMs, dryRun, now) });
    } catch (error) {
      results.push({ watchId: task.watchId, outcome: 'error', detail: String(error?.message ?? error).slice(0, 200) });
    }
  }
  return results;
}

async function checkInvoice(call, base, task, minLeadMs, dryRun, now) {
  const fetchInvoice = async () => {
    const response = await call('GET', `${base}/invoice/${task.invoiceId}`);
    if (!response.ok || !response.json?.id) throw new Error(`invoice_http_${response.status}`);
    return response.json;
  };
  const invoice = await fetchInvoice();
  const info = summary(invoice);
  const stop = blocker(info, task, now(), minLeadMs);
  if (stop) return { ...stop, invoice: info };
  const poolSource = invoice.stock?.poolSource;
  const response = await call('POST', `${base}/v2/invoice/time-slot/get`,
    { invoiceIds: [invoice.id], poolSource, timeFrom: now() + 3600_000 });
  if (!response.ok) throw new Error(`slots_http_${response.status}`);
  const slots = Array.isArray(response.json?.payload?.timeSlots) ? response.json.payload.timeSlots : [];
  const slot = pickSlot(slots, task, info.timeFrom, now());
  const nearest = earliest(slots);
  if (!slot) return { outcome: 'waiting', invoice: info, nearest };
  if (dryRun) return { outcome: 'dry_run', invoice: info, nearest, slot: toMs(slot.timeFrom) };
  const set = await call('POST', `${base}/v2/invoice/time-slot/set`,
    { timeFrom: slot.timeFrom, invoiceIds: [invoice.id], stockId: invoice.stock?.id, poolSource });
  if (!set.ok) {
    const code = errorCode(set.json);
    // time-slot-003: slotni boshqa sotuvchi oldinroq band qildi.
    return { outcome: code === 'time-slot-003' ? 'missed' : 'error', invoice: info, nearest,
      detail: `set_http_${set.status}${code ? ` ${code}` : ''}` };
  }
  const after = summary(await fetchInvoice());
  return { outcome: after.timeFrom === toMs(slot.timeFrom) ? 'changed' : 'unverified', invoice: after, slot: toMs(slot.timeFrom) };
}
