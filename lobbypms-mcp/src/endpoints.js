// Write endpoint map. EVERY entry is unverified until confirmed against the LobbyPMS API docs
// or a dev probe (see API_NOTES.md). A tool whose endpoint has verified:false refuses to run
// unless env.ALLOW_UNVERIFIED_WRITES === 'true' (dev probing only; never set in production).
//
// To finish the mapping: fill in method/path/body builder from the docs, set verified:true.
// `build(input)` returns the request body; `path(input)` returns the path.

export const WRITE_ENDPOINTS = {
  create_booking: {
    verified: false,
    method: 'POST',
    path: () => '/bookings',
    build: (i) => ({
      category_id: i.category_id,
      room_id: i.room_id,
      start_date: i.start_date,
      end_date: i.end_date,
      total_guests: i.guests,
      holder: {
        name: i.client.name,
        email: i.client.email,
        phone: i.client.phone,
        country: i.client.country,
        document: i.client.document,
      },
      note: i.note,
      channel: i.channel,
      status: i.status,
    }),
  },
  // Grouped multi-bed booking. Leave verified:false if the API has no group support;
  // import_hostelworld_booking then falls back to linked single bookings.
  create_group_booking: {
    verified: false,
    method: 'POST',
    path: () => '/bookings',
    build: (i) => ({ ...WRITE_ENDPOINTS.create_booking.build(i), rooms: i.beds }),
  },
  cancel_booking: {
    verified: false,
    method: 'DELETE',
    path: (i) => `/bookings/${i.booking_id}`,
    build: (i) => (i.reason ? { reason: i.reason } : undefined),
  },
  check_in: {
    verified: false,
    method: 'POST',
    path: (i) => `/bookings/${i.booking_id}/check-in`,
    build: () => undefined,
  },
  check_out: {
    verified: false,
    method: 'POST',
    path: (i) => `/bookings/${i.booking_id}/check-out`,
    build: () => undefined,
  },
  add_payment: {
    verified: false,
    method: 'POST',
    path: (i) => `/bookings/${i.booking_id}/payments`,
    build: (i) => ({ amount: i.amount, payment_method_id: i.method_id, note: i.note }),
  },
  add_sale: {
    verified: false,
    method: 'POST',
    path: (i) => `/bookings/${i.booking_id}/sales`,
    build: (i) => ({ service_id: i.service_id, quantity: i.quantity }),
  },
  update_client: {
    verified: false,
    method: 'PUT',
    path: (i) => `/clients/${i.client_id}`,
    build: (i) => i.fields,
  },
};

export function endpointReady(env, key) {
  const ep = WRITE_ENDPOINTS[key];
  if (!ep) return { ok: false, reason: `No endpoint defined for ${key}` };
  if (ep.verified) return { ok: true, ep };
  if (env.ALLOW_UNVERIFIED_WRITES === 'true') return { ok: true, ep, unverified: true };
  return {
    ok: false,
    reason: `Endpoint for ${key} is not verified yet (see API_NOTES.md). ` +
      'Map it from the LobbyPMS API docs and set verified:true in src/endpoints.js.',
  };
}
