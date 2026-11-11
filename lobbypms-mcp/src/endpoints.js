// Write endpoint map for LobbyPMS API v1 (https://api.lobbypms.com/api/v1).
// Mapped 2026-11-11 by probing the live API (see API_NOTES.md for the evidence per entry).
//
//   verified: true   -> request shape confirmed by a real 2xx response.
//   supported: false -> the API has no route for this action (404 on every method, or 405
//                       on the resource). Tools refuse with a "dashboard-only" message.
//
// The public docs (app.lobbypms.com/api-docs) sit behind separate credentials we don't have.
// If support sends them and a new route shows up, add it here with verified:true.

export const WRITE_ENDPOINTS = {
  // Verified: 22 bookings created 2026-11-10 with exactly this body, HTTP 201
  // {"booking":{"booking_id":22588357,"room_id":953637}}. Lobby auto-assigns the bed/room.
  // Required: category_id, start_date, end_date, total_adults, holder_name
  // ("holder name is required when customer document is not present"). note is optional.
  // Dorm categories have capacity 1: total_adults > 1 gives 422 MAXIMUM_CAPACITY, so book one per bed.
  create_booking: {
    verified: true,
    supported: true,
    method: 'POST',
    path: () => '/bookings',
    build: (i) => {
      const contact = [i.client?.email, i.client?.phone, i.client?.country].filter(Boolean).join(' | ');
      const note = [i.note, contact && `Contact: ${contact}`].filter(Boolean).join(' | ');
      return {
        category_id: i.category_id,
        start_date: i.start_date,
        end_date: i.end_date,
        total_adults: i.guests,
        holder_name: i.client.name,
        ...(note ? { note } : {}),
      };
    },
  },
  // No group/multi-room body exists: one POST /bookings = one bed/room.
  // import_hostelworld_booking creates linked single bookings instead.
  create_group_booking: { verified: false, supported: false, dashboard: 'Group bookings: create one booking per bed (done automatically by import_hostelworld_booking).' },

  // /bookings/{id} is GET-only: PUT, PATCH, POST and DELETE all return 405.
  edit_booking: { verified: true, supported: false, dashboard: 'Edit the booking in the LobbyPMS calendar.' },
  cancel_booking: { verified: true, supported: false, dashboard: 'Cancel or delete the booking in the LobbyPMS calendar.' },
  confirm_status: { verified: true, supported: false, dashboard: 'Change the booking status in the LobbyPMS calendar.' },
  // No route: /bookings/{id}/check-in|checkin|check-out|status|payments|sales|products|guests|note
  // and top-level /check-in, /checkin, /payments, /sales all 404 (Laravel would 405 if the path existed).
  check_in: { verified: true, supported: false, dashboard: 'Check in from the LobbyPMS calendar or the Lobby Staff app.' },
  check_out: { verified: true, supported: false, dashboard: 'Check out from the LobbyPMS calendar or the Lobby Staff app.' },
  add_payment: { verified: true, supported: false, dashboard: 'Record the payment on the booking in LobbyPMS (Cash = method 48070). /payment-methods is GET-only.' },
  add_sale: { verified: true, supported: false, dashboard: 'Add the product to the booking from the LobbyPMS POS. /products is GET-only.' },
  // /clients and /customers 404 on every method.
  update_client: { verified: true, supported: false, dashboard: 'Edit the client on the booking in LobbyPMS. On create, contact details go into the note.' },
  // /channels is GET-only (POST 405) and currently empty; create_booking has no channel field.
  set_channel: { verified: true, supported: false, dashboard: 'Create the channel/agency (e.g. Hostelworld) and assign it in LobbyPMS. The API can only list channels.' },
};

export function endpointReady(env, key) {
  const ep = WRITE_ENDPOINTS[key];
  if (!ep) return { ok: false, reason: `No endpoint defined for ${key}` };
  if (ep.supported === false) {
    return { ok: false, reason: `LobbyPMS's API has no route for ${key} (checked 2026-11-11). Dashboard-only: ${ep.dashboard}` };
  }
  if (ep.verified) return { ok: true, ep };
  if (env.ALLOW_UNVERIFIED_WRITES === 'true') return { ok: true, ep, unverified: true };
  return {
    ok: false,
    reason: `Endpoint for ${key} is not verified yet (see API_NOTES.md). ` +
      'Map it and set verified:true in src/endpoints.js.',
  };
}
