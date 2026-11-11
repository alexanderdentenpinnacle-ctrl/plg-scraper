// LobbyPMS API client + helpers. No secrets here: token comes from env.LOBBY_API_TOKEN.

export const TZ = 'America/Lima';

export class LobbyError extends Error {
  constructor(status, body, path) {
    super(`LobbyPMS ${status} on ${path}: ${typeof body === 'string' ? body : JSON.stringify(body)}`);
    this.status = status;
    this.body = body;
  }
}

export function makeClient(env, fetchImpl = fetch) {
  const base = (env.LOBBY_BASE_URL || 'https://api.lobbypms.com/api/v1').replace(/\/$/, '');
  const token = env.LOBBY_API_TOKEN;

  async function request(method, path, { params, body } = {}) {
    if (!token) throw new Error('LOBBY_API_TOKEN secret is not set');
    const url = new URL(base + (path.startsWith('/') ? path : '/' + path));
    for (const [k, v] of Object.entries(params || {})) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v));
    }
    const res = await fetchImpl(url.toString(), {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/json',
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!res.ok) throw new LobbyError(res.status, data, path);
    return data;
  }

  return {
    get: (path, params) => request('GET', path, { params }),
    write: (method, path, body) => request(method, path, { body }),
  };
}

// ---------- dates ----------
export function todayLima(now = new Date()) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
}
export function addDays(ymd, n) {
  const d = new Date(ymd + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
export function nightsBetween(start, end) {
  return Math.round((Date.parse(end + 'T12:00:00Z') - Date.parse(start + 'T12:00:00Z')) / 86400000);
}
export function assertYmd(v, name) {
  if (typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(v) || isNaN(Date.parse(v))) {
    throw new Error(`${name} must be YYYY-MM-DD`);
  }
  return v;
}
// Detail endpoint returns UTC timestamps ("2026-11-11 05:00:00" == midnight Lima). Convert to local date.
export function limaDate(ts) {
  if (!ts) return null;
  const d = new Date(ts.replace(' ', 'T') + 'Z');
  return isNaN(d) ? ts : todayLima(d);
}
export function limaDateTime(ts) {
  if (!ts) return null;
  const d = new Date(ts.replace(' ', 'T') + 'Z');
  if (isNaN(d)) return ts;
  return new Intl.DateTimeFormat('sv-SE', { timeZone: TZ, dateStyle: 'short', timeStyle: 'short' }).format(d);
}

// ---------- pagination ----------
export async function getAllPages(client, path, params, maxPages = 20) {
  const out = [];
  for (let page = 1; page <= maxPages; page++) {
    const r = await client.get(path, { ...params, page });
    out.push(...(r?.data || []));
    const total = r?.meta?.total_pages || 1;
    if (page >= total) break;
  }
  return out;
}

// ---------- normalization ----------
const num = (v) => (v === null || v === undefined || v === '' ? null : Number(v));

function normClient(c) {
  if (!c) return null;
  return {
    client_id: c.id_cliente ?? null,
    name: c.nombre ?? '',
    surname: c.primer_apellido ?? '',
    second_surname: c.segundo_apellido ?? '',
    email: c.email ?? '',
    phone: c.telefono ?? '',
    country: c.pais ?? '',
    document_type: c.tipo_documento ?? '',
    document: c.identificacion ?? '',
    address: c.direccion ?? null,
  };
}

export function normalizeBookingDetail(d) {
  if (!d) return d;
  return {
    booking_id: d.id_reserva,
    status: d.estatus,
    created_at: limaDateTime(d.fecha_creacion),
    checkin: limaDate(d.fecha_ingreso),
    checkout: limaDate(d.fecha_salida),
    checked_in_at: d.checkin_realizado ? limaDateTime(d.checkin_realizado) : null,
    checked_out_at: d.checkout_realizado ? limaDateTime(d.checkout_realizado) : null,
    category: d.categoria,
    room: d.cuarto,
    people: d.numero_personas,
    plan: d.plan,
    note: d.nota,
    accommodation_total: num(d.total_alojamiento),
    tax: num(d.impuesto),
    nightly_rates: (d.tarifas_por_dia || []).map((t) => ({ date: limaDate(t.dia), price: num(t.precio) })),
    discounts: d.descuentos || [],
    payments: d.ingresos || [],
    sales: d.ventas || [],
    guests: d.huespedes || [],
    client: normClient(d.cliente),
    billing_client: normClient(d.factura_cliente),
    group: d.grupo || [],
    channel: d.agencia ?? null,
    policies: d.policies || [],
    deleted_at: d.deleted_at,
  };
}

// List endpoint already returns English keys; trim to what matters.
export function summarizeListBooking(b) {
  return {
    booking_id: b.booking_id,
    created: b.creation_date,
    category_id: b.category?.category_id,
    category: b.category?.name,
    room: b.assigned_room?.name,
    checkin: b.start_date,
    checkout: b.end_date,
    holder: b.holder?.name,
    holder_email: b.holder?.email || '',
    holder_phone: b.holder?.phone || '',
    holder_country: b.holder?.country || '',
    channel: Array.isArray(b.channel) && b.channel.length ? b.channel : (b.channel && !Array.isArray(b.channel) ? b.channel : null),
    guests: b.total_guests,
    note: b.note,
    paid: b.paid_out,
    total: b.total_to_pay,
    checked_in: b.checked_in,
    checked_out: b.checked_out,
  };
}

export function flattenAvailability(data) {
  const out = {};
  for (const day of data || []) {
    out[day.date] = {};
    for (const c of day.categories || []) {
      out[day.date][c.category_id] = {
        name: c.name,
        available: c.available_rooms,
        price: c.prices?.[0]?.value ?? null,
        prices: c.prices,
        restrictions: c.restrictions,
      };
    }
  }
  return out;
}
