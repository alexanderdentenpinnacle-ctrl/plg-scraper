# LobbyPMS API Notes (Makalu Lodge)

Probed 2026-11-11 through the live connector (read-only). Auth: `Authorization: Bearer <token>` (Worker secret `LOBBY_API_TOKEN`).
Unknown paths return `404 {"error":"Resource Not Found."}` for **every** method, so a GET can't tell you whether a POST route exists.

## Read endpoints (verified)

| Method | Path | Params | Notes |
|---|---|---|---|
| GET | `/rooms` | none | 10 categories with `rooms[]` (bed/room ids) |
| GET | `/bookings` | **one of**: `creation_date_from`+`creation_date_to`, `check_in_from`+`check_in_to`, `check_out_from`+`check_out_to` (YYYY-MM-DD); `page` | Without a date filter it returns 422 `FILTERS`. 50/page, `meta.total_pages`. List rows use **English** keys (`start_date`, `end_date`, `holder`, `channel`, `checked_in`, `total_to_pay`, `paid_out`) and have **no status field**. |
| GET | `/bookings/{id}` | none | **Spanish** keys (`estatus`, `fecha_ingreso`, …). Timestamps are UTC: `2026-11-11 05:00:00` is midnight in Lima. |
| GET | `/available-rooms` | `start_date`, `end_date` (**both inclusive**) | Per day, per category: `available_rooms`, `prices[{people,value}]`, `restrictions` |
| GET | `/products` | none | `service_id`, `name`, `value`, `infinite_inventory`, `stock`. Current items: Beer 605376 (S/10), Laundry 605386 (S/5). |
| GET | `/payment-methods` | none | `id_forma_pago`, `nombre`. Only one exists: **Cash = 48070**. |
| GET | `/channels` | none | Returns `{"channels":[]}`. No channels/agencies configured, so "Hostelworld" can't be assigned until one exists. |
| GET | `/rate-plans` | none | Empty |

Probed and **404**: `/rates`, `/clients`, `/clients/{id}`, `/agencies`, `/booking`, `/bookings/{id}/check-in`, `/bookings/{id}/checkin`, `/bookings/{id}/payments`, `/bookings/{id}/sales`, `/bookings/{id}/cancel`, plus check-in filter variants `check_in_date_from`, `checkin_date_from`, `start_date_from`, `arrival_date_from`. These may still exist as POST routes.

## Write endpoints: NOT YET MAPPED

The official docs weren't reachable from the build environment, which only allows allowlisted hosts. Every write is configured in `src/endpoints.js` with `verified: false`, and the tools refuse to call them until it's set to `true`.

| Action | Status | Current guess (src/endpoints.js) |
|---|---|---|
| create booking | unverified | `POST /bookings` |
| grouped multi-bed booking | unverified | `POST /bookings` with `rooms` |
| cancel / delete booking | unverified | `DELETE /bookings/{id}` |
| confirm status (`provisional` to confirmed) | unverified | probe: `PUT /bookings/{id}` `{estatus}` |
| check-in | unverified | `POST /bookings/{id}/check-in` |
| check-out | unverified | `POST /bookings/{id}/check-out` |
| add payment | unverified | `POST /bookings/{id}/payments` |
| add sale | unverified | `POST /bookings/{id}/sales` |
| update client | unverified | `PUT /clients/{id}` |
| set channel/agency | **blocked**: `/channels` is empty; create "Hostelworld" in the staff panel first | |

### How to finish the mapping
1. Open the API docs (staff panel: Settings → Users, permissions and API → API Access) and fill in the table above with method, path, body and a sample response.
2. Or run the dev probe: `npx wrangler secret put LOBBY_API_TOKEN --env dev`, then `npm run deploy:dev`, then `DEV_MCP_URL=https://lobbypms-mcp-dev.<sub>.workers.dev/mcp node scripts/probe-writes.mjs`.
   It creates one booking for 2027-12-01 → 2027-12-02 (Deluxe, client `API TEST - DELETE`), tries each write on it, and deletes it in a `finally` block. Results go to `probe-results.json`.
   **Note:** the dev worker talks to the same live property, since there is no sandbox property.
3. For each confirmed write, update `src/endpoints.js` (path/body) and set `verified: true`.
4. Any action that only exists in the staff dashboard gets listed here as **dashboard-only**. No cookie/session scraping without Alex's go-ahead.

## Data findings (2026-11-11, read-only)

Hostelworld takes a 15% deposit, so the implied HW total = "due at hostel" / 0.85. Compared with Lobby's totals for bookings created 2026-11-10:

| HW ref | Guest | Check-in | Lobby total | HW total (implied) | Diff |
|---|---|---|---|---|---|
| 319555-581073359 | Sarah Eger, 5 beds Deluxe | 2026-11-15 | S/150 | S/125 | +25 |
| 319555-581067096 | Sientje Fonferek, 2 beds | 2026-11-11 | S/100 | S/80 | +20 |
| 319555-581006553 | Caro Van der Hallen, 2 beds | 2026-11-07 | S/280 | S/260 | +20 |
| 319555-581087768 | Richard Connolly | 2026-11-11 | S/50 | S/40 | +10 |
| 319555-581068194 | Marlene Graeser | 2026-11-10 | S/25 | S/20 | +5 |

All other refs match. Run `data_quality_report` for the live version.
