import assert from 'node:assert/strict';
import test from 'node:test';
import { blocker, checkTimeslots, pickSlot, tashkentDate } from './uzum_timeslots.mjs';

const HOUR = 3600_000;
const at = iso => Date.parse(iso);
const NOW = at('2026-10-30T07:00:00Z');
const task = { watchId: 'w1', invoiceId: 5, dateFrom: '2026-11-03', dateTo: '2026-11-04' };
const current = { timeFrom: at('2026-11-14T04:00:00Z'), timeTo: at('2026-11-14T06:00:00Z') };

function invoice(overrides = {}) {
  return { id: 5, invoiceNumber: 'N-5', invoiceStatus: { value: 'CREATED' }, remainingAmountOfUpdates: 2,
    stock: { id: 9, title: 'Toshkent', poolSource: 'POOL' }, timeSlotReservation: current, ...overrides };
}

function fakeCabinet(slots, { setResponse } = {}) {
  const state = { invoice: invoice(), calls: [] };
  const call = async (method, url, body) => {
    state.calls.push({ method, url, body });
    if (method === 'GET' && url.endsWith('/invoice/5')) return { ok: true, status: 200, json: state.invoice };
    if (url.endsWith('/time-slot/get')) return { ok: true, status: 200, json: { payload: { timeSlots: slots } } };
    if (url.endsWith('/time-slot/set')) {
      if (setResponse) return setResponse;
      state.invoice = invoice({ timeSlotReservation: { timeFrom: body.timeFrom, timeTo: body.timeFrom + 2 * HOUR } });
      return { ok: true, status: 200, json: {} };
    }
    return { ok: false, status: 404, json: null };
  };
  return { state, call };
}

test('Toshkent sanasi UTC+5 bo\'yicha hisoblanadi', () => {
  assert.equal(tashkentDate(at('2026-11-02T19:30:00Z')), '2026-11-03');
  assert.equal(tashkentDate(at('2026-11-03T18:59:00Z')), '2026-11-03');
});

test('oraliqdagi eng erta slot tanlanadi, oraliqdan tashqaridagilar emas', () => {
  const slots = [
    { timeFrom: at('2026-11-02T18:00:00Z') }, // Toshkentda 2-noyabr 23:00
    { timeFrom: at('2026-11-04T05:00:00Z') },
    { timeFrom: at('2026-11-03T09:00:00Z') },
    { timeFrom: at('2026-11-05T05:00:00Z') },
  ];
  assert.equal(pickSlot(slots, task, current.timeFrom, NOW).timeFrom, at('2026-11-03T09:00:00Z'));
  assert.equal(pickSlot(slots.slice(3), task, current.timeFrom, NOW), null);
  assert.equal(pickSlot([{ timeFrom: '2026-11-03T09:00:00Z' }], task, null, NOW).timeFrom, '2026-11-03T09:00:00Z');
});

test('holat, limit va jarima muddati ko\'chirishni to\'xtatadi', () => {
  const info = (fields = {}) => ({ status: 'CREATED', timeFrom: current.timeFrom, remainingUpdates: 2, ...fields });
  assert.deepEqual(blocker(info({ status: 'ACCEPTED' }), task, NOW, 48 * HOUR), { outcome: 'stopped', reason: 'status', detail: 'ACCEPTED' });
  assert.equal(blocker(info({ status: 'ACCEPTED', statusText: 'Принята на складе' }), task, NOW, 48 * HOUR).detail, 'Принята на складе');
  assert.deepEqual(blocker(info({ remainingUpdates: 0 }), task, NOW, 48 * HOUR), { outcome: 'stopped', reason: 'limit' });
  assert.deepEqual(blocker(info({ timeFrom: NOW + 47 * HOUR }), task, NOW, 48 * HOUR), { outcome: 'stopped', reason: 'too_late' });
  assert.deepEqual(blocker(info({ timeFrom: at('2026-11-04T05:00:00Z') }), task, NOW, 48 * HOUR), { outcome: 'already' });
  assert.equal(blocker(info(), task, NOW, 48 * HOUR), null);
  assert.equal(blocker(info({ timeFrom: null, remainingUpdates: 0 }), task, NOW, 48 * HOUR), null);
});

test('bo\'shagan slot band qilinadi va natija qayta o\'qib tasdiqlanadi', async () => {
  const slot = at('2026-11-03T09:00:00Z');
  const { state, call } = fakeCabinet([{ timeFrom: slot, timeTo: slot + 2 * HOUR }]);
  const [result] = await checkTimeslots(call, '77', [task], { minLeadMs: 48 * HOUR, now: () => NOW });
  assert.equal(result.outcome, 'changed');
  assert.equal(result.invoice.timeFrom, slot);
  const set = state.calls.find(item => item.url.endsWith('/time-slot/set'));
  assert.equal(set.url, 'https://api-seller.uzum.uz/api/seller/shop/77/v2/invoice/time-slot/set');
  assert.deepEqual(set.body, { timeFrom: slot, invoiceIds: [5], stockId: 9, poolSource: 'POOL' });
  const get = state.calls.find(item => item.url.endsWith('/time-slot/get'));
  assert.deepEqual(get.body, { invoiceIds: [5], poolSource: 'POOL', timeFrom: NOW + HOUR });
});

test('mos slot bo\'lmasa kutadi va eng yaqin bo\'sh slotni aytadi', async () => {
  const later = at('2026-11-10T05:00:00Z');
  const { state, call } = fakeCabinet([{ timeFrom: later }, { timeFrom: at('2026-11-12T05:00:00Z') }]);
  const [result] = await checkTimeslots(call, '77', [task], { minLeadMs: 48 * HOUR, now: () => NOW });
  assert.equal(result.outcome, 'waiting');
  assert.equal(result.nearest, later);
  assert.ok(!state.calls.some(item => item.url.endsWith('/time-slot/set')));
});

test('slotni boshqa sotuvchi olib qo\'ysa missed, sinov rejimida set chaqirilmaydi', async () => {
  const slots = [{ timeFrom: at('2026-11-03T09:00:00Z') }];
  const conflict = { ok: false, status: 400, json: { errors: [{ code: 'time-slot-003' }] } };
  const missed = fakeCabinet(slots, { setResponse: conflict });
  const [first] = await checkTimeslots(missed.call, '77', [task], { minLeadMs: 48 * HOUR, now: () => NOW });
  assert.equal(first.outcome, 'missed');

  const dry = fakeCabinet(slots);
  const [second] = await checkTimeslots(dry.call, '77', [task], { minLeadMs: 48 * HOUR, dryRun: true, now: () => NOW });
  assert.equal(second.outcome, 'dry_run');
  assert.ok(!dry.state.calls.some(item => item.url.endsWith('/time-slot/set')));
});

test('bitta yuk xatidagi xato boshqalarini to\'xtatmaydi', async () => {
  const { call } = fakeCabinet([]);
  const results = await checkTimeslots(call, '77', [{ ...task, invoiceId: 404, watchId: 'w0' }, task],
    { minLeadMs: 48 * HOUR, now: () => NOW });
  assert.deepEqual(results.map(item => item.outcome), ['error', 'waiting']);
  assert.equal(results[0].detail, 'invoice_http_404');
});
