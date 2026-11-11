import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { callTool, listTools } from '../src/index.js';
import { normalizeBookingDetail, todayLima, addDays } from '../src/lobby.js';

// ---- fixtures shaped like real LobbyPMS responses (probed 2026-11-11) ----
const listRow = (id, o = {}) => ({
  booking_id: id, creation_date: '2026-11-10 19:35:42',
  category: { category_id: 53737, name: 'Deluxe 6 Bed Mixed Dorm Ensuite' },
  assigned_room: { type: 'compartida', name: 'Cama 1' }, channel: [],
  start_date: '2026-11-15', end_date: '2026-11-16',
  holder: { client_id: 1, name: 'Sarah Eger (bed 1/5)', email: '', phone: '', country: '' },
  note: 'Hostelworld 319555-581073359 | H6 | 5 beds | due at hostel S/106.25 total',
  total_guests: 1, paid_out: 0, total_to_pay: 30, checked_in: false, checked_out: false, ...o,
});
const detail = (id) => ({
  id_reserva: id, fecha_creacion: '2026-11-11 00:35:28', estatus: 'provisional',
  fecha_ingreso: '2026-11-15 05:00:00', fecha_salida: '2026-11-16 05:00:00',
  checkin_realizado: null, checkout_realizado: null, nota: 'x', total_alojamiento: '30.00',
  numero_personas: 1, ingresos: [], tarifas_por_dia: [{ id: 1, dia: '2026-11-15 05:00:00', precio: '30.00' }],
  cliente: { id_cliente: 1, nombre: 'Sarah Eger', email: '', telefono: '', pais: '' },
  huespedes: [], ventas: [], grupo: [], agencia: null, categoria: 'Deluxe', cuarto: 'Cama 1',
});
const avail = (date, deluxeAvail = 6) => ({
  date, categories: [
    { category_id: 53737, name: 'Deluxe 6 Bed Mixed Dorm Ensuite', available_rooms: deluxeAvail, prices: [{ people: 1, value: 30 }], restrictions: {} },
    { category_id: 53731, name: 'Standard Twin', available_rooms: 1, prices: [{ people: 1, value: 55 }, { people: 2, value: 55 }], restrictions: {} },
  ],
});

function fakeFetch(routes, calls = []) {
  return async (url, init = {}) => {
    const u = new URL(url);
    calls.push({ method: init.method || 'GET', path: u.pathname.replace('/api/v1', ''), params: Object.fromEntries(u.searchParams), body: init.body });
    const key = `${init.method || 'GET'} ${u.pathname.replace('/api/v1', '')}`;
    const h = routes[key];
    if (!h) return new Response(JSON.stringify({ error: 'Resource Not Found.' }), { status: 404 });
    const out = typeof h === 'function' ? h(u, init) : h;
    return new Response(JSON.stringify(out), { status: 200 });
  };
}
const env = { LOBBY_API_TOKEN: 't', LOBBY_BASE_URL: 'https://api.lobbypms.com/api/v1' };
const parse = (r) => JSON.parse(r.content[0].text);

test('get_booking normalizes Spanish keys to English, Lima dates', () => {
  const b = normalizeBookingDetail(detail(5));
  assert.equal(b.status, 'provisional');
  assert.equal(b.checkin, '2026-11-15');
  assert.equal(b.checkout, '2026-11-16');
  assert.equal(b.checked_in_at, null);
  assert.deepEqual(b.nightly_rates, [{ date: '2026-11-15', price: 30 }]);
  for (const k of ['payments', 'sales', 'guests', 'client', 'group', 'channel']) assert.ok(k in b, k);
  assert.equal(b.client.name, 'Sarah Eger');
});

test('list_bookings never sends an unfiltered request; defaults to check-in -30/+90', async () => {
  const calls = [];
  const f = fakeFetch({ 'GET /bookings': { data: [listRow(1)], meta: { total_pages: 1 } } }, calls);
  const r = parse(await callTool('list_bookings', {}, env, f));
  const t = todayLima();
  assert.equal(calls[0].params.check_in_from, addDays(t, -30));
  assert.equal(calls[0].params.check_in_to, addDays(t, 90));
  assert.equal(r.count, 1);
});

test('list_bookings creation filter + paginates', async () => {
  const calls = [];
  const f = fakeFetch({ 'GET /bookings': (u) => ({ data: [listRow(Number(u.searchParams.get('page')))], meta: { total_pages: 2 } }) }, calls);
  const r = parse(await callTool('list_bookings', { created_from: '2026-11-10', created_to: '2026-11-10' }, env, f));
  assert.equal(calls[0].params.creation_date_from, '2026-11-10');
  assert.equal(calls[0].params.check_in_from, undefined);
  assert.equal(r.count, 2);
});

test('get_rates calls /available-rooms and flattens', async () => {
  const f = fakeFetch({ 'GET /available-rooms': { data: [avail('2026-11-15', 1)] } });
  const r = parse(await callTool('get_rates', { start_date: '2026-11-15', end_date: '2026-11-15' }, env, f));
  assert.equal(r['2026-11-15'][53737].available, 1);
  assert.equal(r['2026-11-15'][53737].price, 30);
});

test('write tools refuse when WRITE_ENABLED is off and say confirm in description', async () => {
  for (const t of listTools(env).filter((t) => t.annotations.destructiveHint)) {
    assert.match(t.description, /Confirm with the user before calling\./, t.name);
  }
  const f = fakeFetch({ 'GET /bookings/5': { data: detail(5) } });
  const r = await callTool('check_in', { booking_id: 5 }, env, f);
  assert.equal(r.isError, true);
  assert.match(r.content[0].text, /WRITE_ENABLED/);
});

test('unsupported writes refuse with a dashboard-only message even with WRITE_ENABLED', async () => {
  for (const [tool, args] of [['check_in', { booking_id: 5 }], ['check_out', { booking_id: 5 }], ['cancel_booking', { booking_id: 5 }]]) {
    const calls = [];
    const f = fakeFetch({ 'GET /bookings/5': { data: detail(5) } }, calls);
    const r = await callTool(tool, args, { ...env, WRITE_ENABLED: 'true' }, f);
    assert.equal(r.isError, true, tool);
    assert.match(r.content[0].text, /Dashboard-only/, tool);
    assert.ok(calls.every((c) => c.method === 'GET'), `${tool} must not send a write`);
  }
});

test('create_booking sends the verified POST /bookings body and reads back the new id', async () => {
  const calls = [];
  const f = fakeFetch({
    'GET /available-rooms': { data: [avail('2026-11-15', 6)] },
    'POST /bookings': { booking: { booking_id: 77, room_id: 953627 } },
    'GET /bookings/77': { data: detail(77) },
  }, calls);
  const r = parse(await callTool('create_booking', {
    category_id: 53737, start_date: '2026-11-15', end_date: '2026-11-16', guests: 1,
    client: { name: 'Test Guest', email: 't@example.com' }, note: 'Hostelworld 1-2',
  }, { ...env, WRITE_ENABLED: 'true' }, f));
  const post = calls.find((c) => c.method === 'POST');
  assert.deepEqual(JSON.parse(post.body), {
    category_id: 53737, start_date: '2026-11-15', end_date: '2026-11-16', total_adults: 1,
    holder_name: 'Test Guest', note: 'Hostelworld 1-2 | Contact: t@example.com',
  });
  assert.deepEqual(r.created, [77]);
});

const hwArgs = {
  ref: '319555-581073359', guest_name: 'Sarah Eger', email: 's@example.com', phone: '+1', country: 'DE',
  checkin: '2026-11-15', checkout: '2026-11-16', category_id: 53737, beds: 5, hw_total: 125, deposit: 18.75,
};
const rooms = { data: [{ category_id: 53737, name: 'Deluxe', type: 'compartida', capacity: 6 }, { category_id: 53731, name: 'Twin', type: 'privada', capacity: 2 }] };

test('import_hostelworld_booking dedupes on ref', async () => {
  const f = fakeFetch({ 'GET /bookings': { data: [listRow(1)], meta: { total_pages: 1 } } });
  const r = parse(await callTool('import_hostelworld_booking', hwArgs, env, f));
  assert.equal(r.status, 'duplicate');
});

test('import_hostelworld_booking fails clearly without enough beds', async () => {
  const f = fakeFetch({
    'GET /bookings': { data: [], meta: { total_pages: 1 } },
    'GET /available-rooms': { data: [avail('2026-11-15', 1)] },
    'GET /rooms': rooms,
  });
  const r = parse(await callTool('import_hostelworld_booking', hwArgs, env, f));
  assert.equal(r.status, 'no_availability');
  assert.match(r.message, /need 5/);
});

test('import_hostelworld_booking dry run flags price mismatch and builds note', async () => {
  const f = fakeFetch({
    'GET /bookings': { data: [], meta: { total_pages: 1 } },
    'GET /available-rooms': { data: [avail('2026-11-15', 6)] },
    'GET /rooms': rooms,
  });
  const r = parse(await callTool('import_hostelworld_booking', hwArgs, env, f));
  assert.equal(r.status, 'dry_run');
  assert.equal(r.price_check.lobby_total, 150);
  assert.equal(r.price_check.mismatch, true);
  assert.equal(r.plan.note, 'Hostelworld 319555-581073359 | deposit S/18.75 | due at hostel S/106.25');
  assert.equal(r.plan.client.name, 'Sarah Eger');
});

test('import_hostelworld_booking creates linked bookings when enabled (no group support)', async () => {
  const calls = [];
  let n = 100;
  const f = fakeFetch({
    'GET /bookings': { data: [], meta: { total_pages: 1 } },
    'GET /available-rooms': { data: [avail('2026-11-15', 6)] },
    'GET /rooms': rooms,
    'POST /bookings': () => ({ booking: { booking_id: ++n, room_id: 953627 } }),
    'GET /bookings/101': { data: detail(101) }, 'GET /bookings/102': { data: detail(102) },
  }, calls);
  const r = parse(await callTool('import_hostelworld_booking', { ...hwArgs, beds: 2, hw_total: 60, deposit: 9, dry_run: false },
    { ...env, WRITE_ENABLED: 'true' }, f));
  assert.equal(r.status, 'created');
  assert.deepEqual(r.created, [101, 102]);
  const posts = calls.filter((c) => c.method === 'POST').map((c) => JSON.parse(c.body));
  assert.equal(posts.length, 2);
  assert.equal(posts[0].holder_name, 'Sarah Eger');
  assert.equal(posts[0].total_adults, 1);
  assert.match(posts[0].note, /^Hostelworld 319555-581073359 .* Contact: s@example.com \| \+1 \| DE$/);
  assert.equal(posts[0].channel, undefined);
});

test('client names with "(bed x/y)" are rejected', async () => {
  const r = await callTool('import_hostelworld_booking', { ...hwArgs, guest_name: 'Sarah (bed 1/5)' }, env, fakeFetch({}));
  assert.equal(r.isError, true);
});

test('data_quality_report finds split groups and the 319555-581073359 mismatch', async () => {
  const rows = [1, 2, 3, 4, 5].map((i) => listRow(i, { holder: { name: `Sarah Eger (bed ${i}/5)`, email: '', phone: '' } }));
  const f = fakeFetch({
    'GET /bookings': (u) => (u.searchParams.get('creation_date_from')
      ? { data: rows, meta: { total_pages: 1 } }
      : { data: [listRow(9, { start_date: '2026-11-09', end_date: '2026-11-11', note: '' })], meta: { total_pages: 1 } }),
    ...Object.fromEntries([1, 2, 3, 4, 5].map((i) => [`GET /bookings/${i}`, { data: detail(i) }])),
  });
  const r = parse(await callTool('data_quality_report', {}, env, f));
  assert.equal(r.provisional.count, 5);
  assert.equal(r.missing_contact_info.count, 5);
  assert.equal(r.split_multi_bed_groups[0].bookings.length, 5);
  assert.equal(r.price_mismatches[0].ref, '319555-581073359');
  assert.equal(r.price_mismatches[0].implied_hw_total, 125);
  assert.equal(r.price_mismatches[0].lobby_total, 150);
  assert.equal(r.past_arrivals_not_checked_in.length, 1);
});

test('MCP JSON-RPC: initialize, tools/list, notification', async () => {
  const req = (body) => new Request('https://x/mcp', { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } });
  const init = await (await worker.fetch(req({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {} }), env)).json();
  assert.equal(init.result.serverInfo.name, 'lobbypms-mcp');
  const list = await (await worker.fetch(req({ jsonrpc: '2.0', id: 2, method: 'tools/list' }), env)).json();
  const names = list.result.tools.map((t) => t.name);
  for (const n of ['get_rates', 'list_bookings', 'get_booking', 'get_availability', 'list_products', 'create_booking',
    'cancel_booking', 'check_in', 'check_out', 'add_payment', 'add_sale', 'update_client', 'import_hostelworld_booking']) {
    assert.ok(names.includes(n), n);
  }
  const note = await worker.fetch(req({ jsonrpc: '2.0', method: 'notifications/initialized' }), env);
  assert.equal(note.status, 202);
});

test('MCP_AUTH_TOKEN is enforced when set', async () => {
  const r = await worker.fetch(new Request('https://x/mcp', { method: 'POST', body: '{}' }), { ...env, MCP_AUTH_TOKEN: 'k' });
  assert.equal(r.status, 401);
});
