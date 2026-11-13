// Write endpoint map for LobbyPMS API v1 (https://api.lobbypms.com/api/v1).
// Source: official docs at app.lobbypms.com/api (read 2026-11-13) + live probing (2026-11-10/11).
//
//   verified: 'docs+live' -> in the docs AND confirmed by a real 2xx response.
//   verified: 'docs'      -> in the official docs; request shape copied from them, not yet live-tested.
//   supported: false      -> not in the docs and no route found. Tools refuse with a dashboard-only message.
//
// Note: per the docs, LobbyPMS answers 404 (not 405) for a wrong HTTP method on an existing path,
// so a 404 from a probe does not prove a route is missing.

const clean = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== ''));

export const CANCELLATION_REASONS = {
  NS: 'No show',
  RC: 'Room change',
  RE: 'Registration errors',
  TTC: 'Timely cancellation by the client',
  CC: 'Client not reachable',
  OTH: 'Other',
};

export const WRITE_ENDPOINTS = {
  // Docs: "Crear reservas". Live: 22 bookings created 2026-11-10 -> 201 {"booking":{"booking_id","room_id"}}.
  // If nights can't fit one room, the response adds booking.distributed[] (one booking per segment).
  // holder_name is required unless customer_document (+ customer_nationality) points at an existing client.
  // Dorm categories have capacity 1: total_adults > 1 -> 422 MAXIMUM_CAPACITY, so book one per bed.
  create_booking: {
    verified: 'docs+live',
    supported: true,
    method: 'POST',
    path: () => '/bookings',
    build: (i) => {
      const contact = [i.client?.email, i.client?.phone, i.client?.country].filter(Boolean).join(' | ');
      const note = [i.note, !i.customer_document && contact && `Contact: ${contact}`].filter(Boolean).join(' | ');
      return clean({
        category_id: i.category_id,
        start_date: i.start_date,
        end_date: i.end_date,
        total_adults: i.guests,
        total_children: i.children,
        holder_name: i.customer_document ? undefined : i.client?.name,
        customer_document: i.customer_document,
        customer_nationality: i.customer_nationality,
        rates_per_day: i.rates_per_day, // [{date, price}] exactly one per night, else 422 RATES_PER_DAY
        note: note || undefined,
        payment: i.payment, // amount <= booking total; method isn't selectable
        channel: i.channel_id, // id from GET /channels
        plan_id: i.plan_id, // id from GET /rate-plans
      });
    },
  },
  // No group body. One POST = one room/bed; book N beds as N bookings.
  create_group_booking: { verified: 'docs', supported: false, dashboard: 'No group booking in the API: create one booking per bed (import_hostelworld_booking does this).' },

  // Docs: "Cancelar reserva". Only bookings created through the API (else UNAUTHORIZED_BOOKING), not checked in
  // (BOOKING_CHECK_IN_COMPLETE), and without payments/extra charges (RESTRICTED_RESERVATION).
  cancel_booking: {
    verified: 'docs',
    supported: true,
    method: 'POST',
    path: (i) => `/cancel-booking/${i.booking_id}`,
    build: (i) => clean({ cancellation_reason: i.reason_code, description: i.description }),
  },

  // Docs: "Agregar consumos". product_id = service_id from GET /products.
  add_sale: {
    verified: 'docs',
    supported: true,
    method: 'POST',
    path: () => '/booking/add-product-service',
    build: (i) => ({
      booking_id: i.booking_id,
      items: [clean({ product_id: i.service_id, cant: i.quantity, inventory_center_id: i.inventory_center_id })],
    }),
  },

  // Docs: "Crear cliente". type 1 = person, 2 = company. Returns {"status":"success"} (no id).
  create_client: {
    verified: 'docs',
    supported: true,
    method: 'POST',
    path: (i) => `/customer/${i.type === 'company' ? 2 : 1}`,
    build: (i) => clean({
      customer_document: i.document,
      customer_nationality: i.nationality,
      name: i.name,
      surname: i.surname,
      second_surname: i.second_surname,
      document_id: i.document_type_id, // from GET /documents
      birthdate: i.birthdate,
      gender: i.gender,
      phone: i.phone, // with country code, e.g. +51...
      email: i.email,
      activities: i.occupation,
      address: i.address,
      note: i.note,
    }),
  },

  // Docs: "Enlaces de pago". amount >= 6 and <= outstanding debt; one OPEN link per booking;
  // needs a payment system configured in LobbyPMS. Default expiry 24h.
  create_payment_link: {
    verified: 'docs',
    supported: true,
    method: 'POST',
    path: () => '/payment/link',
    build: (i) => clean({ booking_id: i.booking_id, amount: i.amount, expire_in: i.expire_in }),
  },
  delete_payment_link: {
    verified: 'docs',
    supported: true,
    method: 'DELETE',
    path: (i) => `/payment/link/${encodeURIComponent(i.link_id)}`,
    build: () => undefined,
  },

  // Docs: "Crear bloqueo" / "Eliminar bloqueo". Default hold 60 minutes.
  create_block: {
    verified: 'docs',
    supported: true,
    method: 'POST',
    path: () => '/block',
    build: (i) => clean({ category_id: i.category_id, start_date: i.start_date, end_date: i.end_date, number_rooms: i.number_rooms, time: i.minutes, note: i.note }),
  },
  delete_block: {
    verified: 'docs',
    supported: true,
    method: 'DELETE',
    path: (i) => `/block/${i.block_id}`,
    build: () => undefined,
  },

  // ---- Not in the official docs: dashboard-only ----
  add_payment: { verified: 'docs', supported: false, dashboard: 'Record cash/card payments in LobbyPMS. The API can only take a payment amount at booking creation (create_booking.payment) or send a payment link (create_payment_link).' },
  edit_booking: { verified: 'docs', supported: false, dashboard: 'Edit the booking in the LobbyPMS calendar.' },
  confirm_status: { verified: 'docs', supported: false, dashboard: 'Change the booking status in the LobbyPMS calendar.' },
  check_in: { verified: 'docs', supported: false, dashboard: 'Check in from the LobbyPMS calendar or the Lobby Staff app.' },
  check_out: { verified: 'docs', supported: false, dashboard: 'Check out from the LobbyPMS calendar or the Lobby Staff app.' },
  update_client: { verified: 'docs', supported: false, dashboard: 'Edit the client in LobbyPMS. The API can only create clients (create_client).' },
  set_channel: { verified: 'docs', supported: false, dashboard: 'The channel can only be set when the booking is created (create_booking.channel_id). Change it later in LobbyPMS.' },
};

export function endpointReady(env, key) {
  const ep = WRITE_ENDPOINTS[key];
  if (!ep) return { ok: false, reason: `No endpoint defined for ${key}` };
  if (ep.supported === false) {
    return { ok: false, reason: `LobbyPMS's API has no route for ${key} (official docs, 2026-11-13). Dashboard-only: ${ep.dashboard}` };
  }
  if (ep.verified) return { ok: true, ep };
  if (env.ALLOW_UNVERIFIED_WRITES === 'true') return { ok: true, ep, unverified: true };
  return { ok: false, reason: `Endpoint for ${key} is not verified yet (see API_NOTES.md).` };
}
