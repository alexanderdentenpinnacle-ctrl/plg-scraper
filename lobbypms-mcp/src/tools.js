import {
  addDays, assertYmd, flattenAvailability, getAllPages, nightsBetween,
  normalizeBookingDetail, summarizeListBooking, todayLima,
} from './lobby.js';
import { endpointReady, WRITE_ENDPOINTS } from './endpoints.js';

const CONFIRM = 'Confirm with the user before calling.';
const writesOn = (env) => env.WRITE_ENABLED === 'true';

// ---------------- read helpers ----------------
async function getBooking(client, id) {
  if (!id) throw new Error('booking_id is required');
  const r = await client.get(`/bookings/${encodeURIComponent(id)}`);
  return normalizeBookingDetail(r?.data);
}

async function availability(client, start, end) {
  const r = await client.get('/available-rooms', { start_date: start, end_date: end });
  return flattenAvailability(r?.data);
}

// Nights are start..checkout-1; /available-rooms end_date is inclusive.
async function nightsAvailability(client, checkin, checkout) {
  const last = addDays(checkout, -1);
  return availability(client, checkin, last);
}

async function listBookings(client, a = {}) {
  const today = todayLima();
  const params = {};
  if (a.created_from || a.created_to) {
    params.creation_date_from = assertYmd(a.created_from || a.created_to, 'created_from');
    params.creation_date_to = assertYmd(a.created_to || a.created_from, 'created_to');
  }
  if (a.checkout_from || a.checkout_to) {
    params.check_out_from = assertYmd(a.checkout_from || a.checkout_to, 'checkout_from');
    params.check_out_to = assertYmd(a.checkout_to || a.checkout_from, 'checkout_to');
  }
  if (a.checkin_from || a.checkin_to || !Object.keys(params).length) {
    params.check_in_from = a.checkin_from ? assertYmd(a.checkin_from, 'checkin_from') : addDays(today, -30);
    params.check_in_to = a.checkin_to ? assertYmd(a.checkin_to, 'checkin_to') : addDays(today, 90);
  }
  let rows = (await getAllPages(client, '/bookings', params)).map(summarizeListBooking);
  if (a.search) {
    const q = String(a.search).toLowerCase();
    rows = rows.filter((b) => `${b.holder} ${b.note} ${b.holder_email}`.toLowerCase().includes(q));
  }
  if (a.status) {
    // List rows carry no status field; resolve via detail (capped).
    const want = String(a.status).toLowerCase();
    const slice = rows.slice(0, 60);
    const details = await Promise.all(slice.map((b) => getBooking(client, b.booking_id).catch(() => null)));
    rows = slice.filter((b, i) => details[i]?.status?.toLowerCase() === want)
      .map((b, i) => ({ ...b, status: want }));
  }
  return { filters: params, count: rows.length, bookings: rows };
}

// ---------------- write helpers ----------------
async function runWrite(env, client, key, input) {
  if (!writesOn(env)) throw new Error('Writes are disabled (WRITE_ENABLED is not "true").');
  const ready = endpointReady(env, key);
  if (!ready.ok) throw new Error(ready.reason);
  const { ep } = ready;
  return client.write(ep.method, ep.path(input), ep.build(input));
}

function extractBookingIds(resp) {
  // POST /bookings returns {"booking":{"booking_id":..,"room_id":..}}.
  const d = resp?.booking ?? resp?.data ?? resp;
  const pick = (x) => x?.booking_id ?? x?.id_reserva ?? x?.id;
  if (Array.isArray(d)) return d.map(pick).filter(Boolean);
  if (Array.isArray(d?.bookings)) return d.bookings.map(pick).filter(Boolean);
  const one = pick(d);
  return one ? [one] : [];
}

function validateClient(c) {
  if (!c || typeof c !== 'object') throw new Error('client is required');
  if (!c.name || !String(c.name).trim()) throw new Error('client.name is required');
  if (c.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(c.email)) throw new Error('client.email is invalid');
  if (/\(bed \d+\/\d+\)/i.test(c.name)) throw new Error('Do not put "(bed x/y)" in the client name');
}

const money = (n) => Number(n).toFixed(2);

// Expected Lobby total for N beds/rooms over the stay, using the per-night price for `people`.
function expectedTotal(avail, categoryId, checkin, checkout, units, peoplePerUnit = 1) {
  let total = 0;
  const nights = [];
  for (let d = checkin; d < checkout; d = addDays(d, 1)) {
    const c = avail[d]?.[categoryId];
    if (!c) throw new Error(`No availability data for category ${categoryId} on ${d}`);
    const p = c.prices?.find((x) => x.people === peoplePerUnit) ?? c.prices?.[0];
    nights.push({ date: d, available: c.available, price: p?.value ?? null });
    total += (p?.value ?? 0) * units;
  }
  return { total, nights };
}

// ---------------- tool definitions ----------------
const str = (description) => ({ type: 'string', description });
const date = (description) => ({ type: 'string', pattern: '^\\d{4}-\\d{2}-\\d{2}$', description });
const int = (description) => ({ type: 'integer', description });

const clientSchema = {
  type: 'object',
  properties: {
    name: str('Guest full name (no "(bed x/y)")'),
    email: str('Email'), phone: str('Phone'), country: str('Country (ISO code or name)'),
    document: str('ID/passport number (optional)'),
  },
  required: ['name'],
};

export const TOOLS = [
  // ---------- read ----------
  {
    name: 'list_rooms',
    description: 'List room categories with beds/rooms, capacity and type.',
    inputSchema: { type: 'object', properties: {} },
    handler: async (_a, { client }) => (await client.get('/rooms'))?.data ?? [],
  },
  {
    name: 'get_rates',
    description: 'Availability and nightly price per category for a date range (inclusive). Returns date -> category_id -> {available, price}.',
    inputSchema: { type: 'object', properties: { start_date: date('First night'), end_date: date('Last night (inclusive)') }, required: ['start_date', 'end_date'] },
    handler: async (a, { client }) => availability(client, assertYmd(a.start_date, 'start_date'), assertYmd(a.end_date, 'end_date')),
  },
  {
    name: 'get_availability',
    description: 'Availability and price for every category for a single night.',
    inputSchema: { type: 'object', properties: { date: date('Night (YYYY-MM-DD)') }, required: ['date'] },
    handler: async (a, { client }) => {
      const d = assertYmd(a.date, 'date');
      return (await availability(client, d, d))[d] ?? {};
    },
  },
  {
    name: 'list_bookings',
    description: 'List bookings. Defaults to check-ins from today-30 to today+90 (Lima time). Filters: check-in range, check-out range, creation range, free-text search over holder/note (e.g. a Hostelworld ref), status (slower: resolves each booking).',
    inputSchema: {
      type: 'object',
      properties: {
        checkin_from: date('Check-in from'), checkin_to: date('Check-in to'),
        checkout_from: date('Check-out from'), checkout_to: date('Check-out to'),
        created_from: date('Created from'), created_to: date('Created to'),
        search: str('Case-insensitive text to match in holder name, email or note'),
        status: str('e.g. provisional, confirmada'),
      },
    },
    handler: (a, { client }) => listBookings(client, a),
  },
  {
    name: 'get_booking',
    description: 'Full booking detail with English field names: status, checkin, checkout, checked_in_at, checked_out_at, nightly_rates, payments, sales, guests, client, group, channel.',
    inputSchema: { type: 'object', properties: { booking_id: int('Booking ID') }, required: ['booking_id'] },
    handler: (a, { client }) => getBooking(client, a.booking_id),
  },
  {
    name: 'list_products',
    description: 'POS products/services (service_id, name, price, stock).',
    inputSchema: { type: 'object', properties: {} },
    handler: async (_a, { client }) => (await client.get('/products'))?.data ?? [],
  },
  {
    name: 'list_payment_methods',
    description: 'Payment methods configured in LobbyPMS (id + name).',
    inputSchema: { type: 'object', properties: {} },
    handler: async (_a, { client }) => ((await client.get('/payment-methods'))?.data ?? [])
      .map((m) => ({ id: m.id_forma_pago, name: m.nombre, deleted: !!m.eliminado })),
  },
  {
    name: 'data_quality_report',
    description: 'Read-only report: bookings created on a date that are provisional / lack channel / lack contact info / are split per bed; past arrivals not checked in; Hostelworld price mismatches. Never changes anything.',
    inputSchema: { type: 'object', properties: { created_on: date('Creation date to audit (default 2026-11-10)') } },
    handler: (a, ctx) => dataQualityReport(a, ctx),
  },
  {
    name: 'lobby_get',
    description: 'Raw read-only GET to any LobbyPMS path, for exploration.',
    inputSchema: { type: 'object', properties: { path: str('e.g. /bookings'), params: { type: 'object', additionalProperties: true } }, required: ['path'] },
    handler: (a, { client }) => client.get(a.path, a.params),
  },

  // ---------- write (gated) ----------
  {
    name: 'create_booking',
    write: true,
    description: `Create a booking after checking availability (POST /bookings, verified). One booking = one bed in dorms; Lobby auto-assigns the bed. Email/phone/country go into the note. ${CONFIRM}`,
    inputSchema: {
      type: 'object',
      properties: {
        category_id: int('Room category ID'), room_id: int('Specific bed/room ID (optional)'),
        start_date: date('Check-in'), end_date: date('Check-out'),
        guests: int('Number of guests'), client: clientSchema,
        note: str('Booking note'), channel: str('Channel/agency name (optional)'),
      },
      required: ['category_id', 'start_date', 'end_date', 'guests', 'client'],
    },
    handler: async (a, { env, client }) => {
      assertYmd(a.start_date, 'start_date'); assertYmd(a.end_date, 'end_date');
      if (nightsBetween(a.start_date, a.end_date) < 1) throw new Error('end_date must be after start_date');
      if (!(a.guests >= 1)) throw new Error('guests must be >= 1');
      validateClient(a.client);
      const avail = await nightsAvailability(client, a.start_date, a.end_date);
      for (let d = a.start_date; d < a.end_date; d = addDays(d, 1)) {
        const c = avail[d]?.[a.category_id];
        if (!c) throw new Error(`Unknown category ${a.category_id}`);
        if (c.available < 1) throw new Error(`No availability in ${c.name} on ${d}`);
      }
      const resp = await runWrite(env, client, 'create_booking', a);
      const ids = extractBookingIds(resp);
      return { created: ids, bookings: await Promise.all(ids.map((id) => getBooking(client, id))), raw: ids.length ? undefined : resp };
    },
  },
  {
    name: 'cancel_booking',
    write: true,
    description: `NOT AVAILABLE in the LobbyPMS API (dashboard-only); calling it returns where to do it in LobbyPMS. Cancel a booking. ${CONFIRM}`,
    inputSchema: { type: 'object', properties: { booking_id: int('Booking ID'), reason: str('Reason') }, required: ['booking_id'] },
    handler: async (a, ctx) => {
      await getBooking(ctx.client, a.booking_id);
      const resp = await runWrite(ctx.env, ctx.client, 'cancel_booking', a);
      return { result: resp, booking: await getBooking(ctx.client, a.booking_id).catch(() => 'deleted') };
    },
  },
  ...['check_in', 'check_out'].map((key) => ({
    name: key,
    write: true,
    description: `NOT AVAILABLE in the LobbyPMS API (dashboard-only); calling it returns where to do it in LobbyPMS. ${key === 'check_in' ? 'Check in' : 'Check out'} a booking. ${CONFIRM}`,
    inputSchema: { type: 'object', properties: { booking_id: int('Booking ID') }, required: ['booking_id'] },
    handler: async (a, ctx) => {
      const b = await getBooking(ctx.client, a.booking_id);
      if (key === 'check_in' && b.checked_in_at) throw new Error(`Already checked in at ${b.checked_in_at}`);
      if (key === 'check_out' && b.checked_out_at) throw new Error(`Already checked out at ${b.checked_out_at}`);
      await runWrite(ctx.env, ctx.client, key, a);
      return getBooking(ctx.client, a.booking_id);
    },
  })),
  {
    name: 'add_payment',
    write: true,
    description: `NOT AVAILABLE in the LobbyPMS API (dashboard-only); calling it returns where to do it in LobbyPMS. Record a payment (S/) on a booking. method is a payment method name (e.g. "Cash") or id. ${CONFIRM}`,
    inputSchema: { type: 'object', properties: { booking_id: int('Booking ID'), amount: { type: 'number', description: 'Amount in PEN' }, method: str('Payment method name or id'), note: str('Note') }, required: ['booking_id', 'amount', 'method'] },
    handler: async (a, ctx) => {
      if (!(a.amount > 0)) throw new Error('amount must be > 0');
      await getBooking(ctx.client, a.booking_id);
      const methods = (await ctx.client.get('/payment-methods'))?.data ?? [];
      const m = methods.find((x) => String(x.id_forma_pago) === String(a.method) || x.nombre?.toLowerCase() === String(a.method).toLowerCase());
      if (!m) throw new Error(`Unknown payment method "${a.method}". Options: ${methods.map((x) => x.nombre).join(', ')}`);
      await runWrite(ctx.env, ctx.client, 'add_payment', { ...a, method_id: m.id_forma_pago });
      return getBooking(ctx.client, a.booking_id);
    },
  },
  {
    name: 'add_sale',
    write: true,
    description: `NOT AVAILABLE in the LobbyPMS API (dashboard-only); calling it returns where to do it in LobbyPMS. Add a POS product/service to a booking. ${CONFIRM}`,
    inputSchema: { type: 'object', properties: { booking_id: int('Booking ID'), service_id: int('Product service_id'), quantity: int('Quantity') }, required: ['booking_id', 'service_id', 'quantity'] },
    handler: async (a, ctx) => {
      if (!(a.quantity >= 1)) throw new Error('quantity must be >= 1');
      await getBooking(ctx.client, a.booking_id);
      const products = (await ctx.client.get('/products'))?.data ?? [];
      const p = products.find((x) => String(x.service_id) === String(a.service_id));
      if (!p) throw new Error(`Unknown service_id ${a.service_id}`);
      if (!p.infinite_inventory && p.stock !== null && p.stock < a.quantity) throw new Error(`Only ${p.stock} ${p.name} in stock`);
      await runWrite(ctx.env, ctx.client, 'add_sale', a);
      return getBooking(ctx.client, a.booking_id);
    },
  },
  {
    name: 'update_client',
    write: true,
    description: `NOT AVAILABLE in the LobbyPMS API (dashboard-only); calling it returns where to do it in LobbyPMS. Update the client (holder) on a booking: name, email, phone, country, document. ${CONFIRM}`,
    inputSchema: { type: 'object', properties: { booking_id: int('Booking ID'), fields: { type: 'object', additionalProperties: true } }, required: ['booking_id', 'fields'] },
    handler: async (a, ctx) => {
      const b = await getBooking(ctx.client, a.booking_id);
      if (!b.client?.client_id) throw new Error('Booking has no client record');
      if (a.fields.name) validateClient({ ...a.fields });
      await runWrite(ctx.env, ctx.client, 'update_client', { ...a, client_id: b.client.client_id });
      return getBooking(ctx.client, a.booking_id);
    },
  },
  {
    name: 'import_hostelworld_booking',
    write: true,
    description: `Import a Hostelworld reservation: dedupes on the ref, checks beds are free, flags price mismatches, then creates one grouped booking (or linked bookings) with contact info and a standard note. dry_run defaults to true and only returns the plan. ${CONFIRM}`,
    inputSchema: {
      type: 'object',
      properties: {
        ref: str('Hostelworld ref, e.g. 319555-581073359'),
        guest_name: str('Guest name'), email: str('Email'), phone: str('Phone'), country: str('Country'),
        checkin: date('Check-in'), checkout: date('Check-out'),
        category_id: int('Lobby category ID'), beds: int('Beds (or rooms, for private categories)'),
        hw_total: { type: 'number', description: 'Hostelworld total price (S/)' },
        deposit: { type: 'number', description: 'Deposit paid on Hostelworld (S/)' },
        dry_run: { type: 'boolean', description: 'Default true. Set false only after the user confirms the plan.' },
      },
      required: ['ref', 'guest_name', 'checkin', 'checkout', 'category_id', 'beds', 'hw_total', 'deposit'],
    },
    handler: (a, ctx) => importHostelworld(a, ctx),
  },
  {
    name: 'lobby_write',
    write: true,
    description: `Raw POST/PUT/PATCH/DELETE to LobbyPMS. For API mapping only. ${CONFIRM}`,
    inputSchema: { type: 'object', properties: { method: { type: 'string', enum: ['POST', 'PUT', 'PATCH', 'DELETE'] }, path: str('Path'), body: { type: 'object', additionalProperties: true } }, required: ['method', 'path'] },
    handler: async (a, { env, client }) => {
      if (!writesOn(env)) throw new Error('Writes are disabled (WRITE_ENABLED is not "true").');
      return client.write(a.method, a.path, a.body);
    },
  },
];

// ---------------- Hostelworld import ----------------
export async function importHostelworld(a, { env, client }) {
  const dryRun = a.dry_run !== false;
  const ref = String(a.ref || '').trim();
  if (!/^\d+-\d+$/.test(ref)) throw new Error('ref must look like 319555-581073359');
  assertYmd(a.checkin, 'checkin'); assertYmd(a.checkout, 'checkout');
  const nights = nightsBetween(a.checkin, a.checkout);
  if (nights < 1) throw new Error('checkout must be after checkin');
  if (!(a.beds >= 1)) throw new Error('beds must be >= 1');
  if (!(a.hw_total >= 0) || !(a.deposit >= 0) || a.deposit > a.hw_total) throw new Error('hw_total/deposit invalid');
  const clientInfo = { name: a.guest_name, email: a.email, phone: a.phone, country: a.country };
  validateClient(clientInfo);

  // 1. Dedupe: any booking whose note mentions the ref, check-in within ±7 days.
  const existing = await listBookings(client, {
    checkin_from: addDays(a.checkin, -7), checkin_to: addDays(a.checkin, 7), search: ref,
  });
  if (existing.count) {
    return { status: 'duplicate', message: `Hostelworld ${ref} already exists in Lobby; nothing created.`, existing: existing.bookings };
  }

  // 2. Availability.
  const avail = await nightsAvailability(client, a.checkin, a.checkout);
  const rooms = (await client.get('/rooms'))?.data ?? [];
  const cat = rooms.find((r) => r.category_id === a.category_id);
  if (!cat) throw new Error(`Unknown category_id ${a.category_id}`);
  const isPrivate = cat.type === 'privada';
  const { total: lobbyTotal, nights: perNight } = expectedTotal(avail, a.category_id, a.checkin, a.checkout, a.beds, isPrivate ? Math.min(2, cat.capacity || 2) : 1);
  const short = perNight.filter((n) => n.available < a.beds);
  if (short.length) {
    return { status: 'no_availability', message: `Not enough ${isPrivate ? 'rooms' : 'beds'} in ${cat.name}: ${short.map((n) => `${n.date} has ${n.available}`).join(', ')}; need ${a.beds}.`, per_night: perNight };
  }

  // 3. Price check + note.
  const due = a.hw_total - a.deposit;
  const note = `Hostelworld ${ref} | deposit S/${money(a.deposit)} | due at hostel S/${money(due)}`;
  const priceCheck = {
    hostelworld_total: a.hw_total,
    lobby_total: lobbyTotal,
    formula: `${perNight.map((n) => n.price).join('+')} x ${a.beds} ${isPrivate ? 'room(s)' : 'bed(s)'}`,
    mismatch: Math.abs(lobbyTotal - a.hw_total) > 0.5,
  };

  // Grouped only once group support is confirmed in the docs; otherwise linked bookings.
  const groupReady = { ok: WRITE_ENDPOINTS.create_group_booking.supported !== false && WRITE_ENDPOINTS.create_group_booking.verified };
  const base = {
    category_id: a.category_id, start_date: a.checkin, end_date: a.checkout,
    client: clientInfo, note, channel: 'Hostelworld', status: 'confirmada',
  };
  const plan = {
    mode: a.beds > 1 && groupReady.ok ? 'grouped' : (a.beds > 1 ? `${a.beds} linked bookings` : 'single'),
    category: cat.name, nights, beds: a.beds, note, client: clientInfo, channel: 'Hostelworld',
  };

  if (dryRun) {
    return {
      status: 'dry_run', plan, price_check: priceCheck, per_night: perNight,
      warnings: [
        ...(priceCheck.mismatch ? [`Price mismatch: Hostelworld S/${money(a.hw_total)} vs Lobby S/${money(lobbyTotal)}. Confirm which is right before importing.`] : []),
        ...(!writesOn(env) ? ['WRITE_ENABLED is off; a real import will be refused.'] : []),
      ],
    };
  }

  if (!writesOn(env)) throw new Error('Writes are disabled (WRITE_ENABLED is not "true").');
  let ids = [];
  if (plan.mode === 'grouped') {
    ids = extractBookingIds(await runWrite(env, client, 'create_group_booking', { ...base, guests: a.beds, beds: a.beds }));
  } else {
    for (let i = 0; i < a.beds; i++) {
      ids.push(...extractBookingIds(await runWrite(env, client, 'create_booking', { ...base, guests: isPrivate ? Math.min(2, cat.capacity || 2) : 1 })));
    }
  }
  const bookings = await Promise.all(ids.map((id) => getBooking(client, id)));
  return { status: 'created', plan, price_check: priceCheck, created: ids, bookings };
}

// ---------------- Data quality report ----------------
const HW_REF = /Hostelworld\s+(\d+-\d+)/i;

export async function dataQualityReport(a, { client }) {
  const createdOn = a.created_on ? assertYmd(a.created_on, 'created_on') : '2026-11-10';
  const today = todayLima();
  const created = (await getAllPages(client, '/bookings', { creation_date_from: createdOn, creation_date_to: createdOn })).map(summarizeListBooking);

  const details = await Promise.all(created.map((b) => getBooking(client, b.booking_id).catch(() => null)));
  const provisional = created.filter((b, i) => details[i]?.status === 'provisional').map((b) => b.booking_id);
  const noChannel = created.filter((b, i) => !b.channel && !details[i]?.channel).map((b) => b.booking_id);
  const noContact = created.filter((b) => !b.holder_email && !b.holder_phone).map((b) => b.booking_id);

  // Split groups: same HW ref across >1 booking.
  const byRef = {};
  for (const b of created) {
    const ref = b.note?.match(HW_REF)?.[1];
    if (ref) (byRef[ref] ||= []).push(b);
  }
  const splitGroups = Object.entries(byRef).filter(([, list]) => list.length > 1)
    .map(([ref, list]) => ({ ref, bookings: list.map((b) => ({ booking_id: b.booking_id, holder: b.holder, room: b.room })) }));

  // Price mismatch: note says "due at hostel S/x" with 15% deposit model -> implied total = due / 0.85.
  const priceFlags = [];
  for (const [ref, list] of Object.entries(byRef)) {
    const lobby = list.reduce((s, b) => s + (b.total || 0), 0);
    const due = Number(list[0].note.match(/due at hostel S\/([\d.]+)/i)?.[1]);
    if (!due) continue;
    const implied = Math.round((due / 0.85) * 100) / 100;
    if (Math.abs(implied - lobby) > 1) {
      priceFlags.push({
        ref, holder: list[0].holder, checkin: list[0].checkin, lobby_total: lobby, implied_hw_total: implied,
        difference: Math.round((lobby - implied) * 100) / 100, due_at_hostel: due,
        bookings: list.map((b) => b.booking_id),
      });
    }
  }

  // Past arrivals not checked in (check-in in last 30 days, before today).
  const past = (await getAllPages(client, '/bookings', { check_in_from: addDays(today, -30), check_in_to: addDays(today, -1) }))
    .map(summarizeListBooking).filter((b) => !b.checked_in);

  return {
    audited_creation_date: createdOn,
    created_count: created.length,
    provisional: { count: provisional.length, booking_ids: provisional },
    missing_channel: { count: noChannel.length, booking_ids: noChannel },
    missing_contact_info: { count: noContact.length, booking_ids: noContact },
    split_multi_bed_groups: splitGroups,
    price_mismatches: priceFlags,
    past_arrivals_not_checked_in: past.map((b) => ({ booking_id: b.booking_id, holder: b.holder, checkin: b.checkin, checkout: b.checkout, category: b.category })),
    note: 'Report only. Nothing was changed.',
  };
}
