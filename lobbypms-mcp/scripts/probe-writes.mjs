// SUPERSEDED 2026-11-11: the write map is done (API_NOTES.md). Only POST /bookings exists, with body
// {category_id,start_date,end_date,total_adults,holder_name,note}, and the API cannot delete a booking,
// so this script's cleanup step would fail and leave the test booking behind. Don't run it.
// Write-endpoint discovery against the DEV worker only.
// Usage: DEV_MCP_URL=https://lobbypms-mcp-dev.<sub>.workers.dev/mcp [MCP_AUTH_TOKEN=...] node scripts/probe-writes.mjs
//
// Creates ONE test booking (2027-12-01 -> 2027-12-02, Deluxe dorm, client "API TEST - DELETE"),
// tries each candidate write on it via lobby_write, records every response, and ALWAYS deletes
// that booking at the end. It never touches any other booking ID.
// Paste the findings from probe-results.json into API_NOTES.md and src/endpoints.js.
import { writeFileSync } from 'node:fs';

const URL_ = process.env.DEV_MCP_URL;
if (!URL_ || !/-dev\./.test(URL_)) {
  console.error('Set DEV_MCP_URL to the *dev* worker (name must contain "-dev.").');
  process.exit(1);
}
const headers = { 'Content-Type': 'application/json', ...(process.env.MCP_AUTH_TOKEN ? { Authorization: `Bearer ${process.env.MCP_AUTH_TOKEN}` } : {}) };
let rpcId = 0;
async function tool(name, args) {
  const r = await fetch(URL_, { method: 'POST', headers, body: JSON.stringify({ jsonrpc: '2.0', id: ++rpcId, method: 'tools/call', params: { name, arguments: args } }) });
  const j = await r.json();
  const text = j.result?.content?.[0]?.text ?? JSON.stringify(j);
  let data; try { data = JSON.parse(text); } catch { data = text; }
  return { error: !!j.result?.isError, data };
}
const write = (method, path, body) => tool('lobby_write', { method, path, body });

const TEST = { start: '2027-12-01', end: '2027-12-02', category_id: 53737, name: 'API TEST - DELETE' };
const results = [];
const log = (step, req, res) => { results.push({ step, req, res }); console.log(step, res.error ? 'ERR' : 'OK', JSON.stringify(res.data).slice(0, 300)); };

// Candidate request shapes. Add more as the docs suggest.
const createCandidates = [
  ['POST', '/bookings', { category_id: TEST.category_id, start_date: TEST.start, end_date: TEST.end, total_guests: 1, holder: { name: TEST.name, email: 'apitest@example.com' }, note: 'API TEST - DELETE' }],
  ['POST', '/bookings', { categoria: TEST.category_id, fecha_ingreso: TEST.start, fecha_salida: TEST.end, numero_personas: 1, cliente: { nombre: TEST.name }, nota: 'API TEST - DELETE' }],
];

let bookingId = null;
try {
  for (const [m, p, b] of createCandidates) {
    const res = await write(m, p, b);
    log('create', { m, p, b }, res);
    const d = res.data?.data ?? res.data;
    bookingId = d?.booking_id ?? d?.id_reserva ?? d?.id ?? null;
    if (!res.error && bookingId) break;
  }
  if (!bookingId) throw new Error('Could not create a test booking; check probe-results.json and the docs.');

  // Safety: confirm we're operating on our own booking.
  const own = await tool('get_booking', { booking_id: bookingId });
  if (own.data?.client?.name !== TEST.name) throw new Error(`Booking ${bookingId} is not the test booking; aborting.`);
  log('get_after_create', { bookingId }, own);

  const id = bookingId;
  const steps = [
    ['update_client', 'PUT', `/clients/${own.data.client.client_id}`, { email: 'apitest@example.com', telefono: '+51000000000', pais: 'PE' }],
    ['update_client_alt', 'PUT', `/bookings/${id}`, { cliente: { email: 'apitest@example.com', telefono: '+51000000000', pais: 'PE' } }],
    ['confirm_status', 'PUT', `/bookings/${id}`, { estatus: 'confirmada' }],
    ['check_in', 'POST', `/bookings/${id}/check-in`, {}],
    ['check_in_alt', 'PUT', `/bookings/${id}`, { checkin: true }],
    ['add_payment', 'POST', `/bookings/${id}/payments`, { amount: 1, payment_method_id: 48070, note: 'API TEST' }],
    ['add_sale', 'POST', `/bookings/${id}/sales`, { service_id: 605376, quantity: 1 }],
    ['check_out', 'POST', `/bookings/${id}/check-out`, {}],
  ];
  for (const [name, m, p, b] of steps) {
    log(name, { m, p, b }, await write(m, p, b));
    log(`${name}:verify`, { bookingId: id }, await tool('get_booking', { booking_id: id }));
  }
} catch (e) {
  console.error('Probe error:', e.message);
} finally {
  if (bookingId) {
    const del = await write('DELETE', `/bookings/${bookingId}`);
    log('delete', { bookingId }, del);
    const after = await tool('get_booking', { booking_id: bookingId });
    log('verify_deleted', { bookingId }, after);
    if (!after.error && !after.data?.deleted_at) console.error(`!! Test booking ${bookingId} may still exist. Delete it in the staff panel.`);
  }
  writeFileSync('probe-results.json', JSON.stringify(results, null, 2));
  console.log('Wrote probe-results.json');
}
