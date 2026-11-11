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

Probed and **404**: `/rates`, `/clients`, `/clients/{id}`, `/agencies`, `/booking`, `/bookings/{id}/check-in`, `/bookings/{id}/checkin`, `/bookings/{id}/payments`, `/bookings/{id}/sales`, `/bookings/{id}/cancel`, plus check-in filter variants `check_in_date_from`, `checkin_date_from`, `start_date_from`, `arrival_date_from`. Ruled out as write routes too (see below): a Laravel route that exists answers a wrong method with 405, not 404.

## Write endpoints (mapped 2026-11-11)

**Bottom line: API v1 has exactly one write route, `POST /bookings`.** Everything else on the list is dashboard-only.

How this was mapped: the official docs (`app.lobbypms.com/api-docs`) need separate HTTP credentials that Alex's staff login doesn't open, so the map comes from probing the live API through the connector. The API is Laravel, so a path that exists answers a wrong method with **405 Method Not Allowed**, and a path that doesn't exist answers **404 `Resource Not Found.`** for every method. A GET to a write-only route therefore shows whether it exists without writing anything. All probes used empty bodies, nonexistent ids, or GETs. No real booking was changed and no test booking was created, because the API can't delete one afterwards.

| Action | Verified result | Method / path | Evidence |
|---|---|---|---|
| **create booking** | ✅ **supported** | `POST /bookings` | 22 bookings created 2026-11-10, HTTP 201 `{"booking":{"booking_id":22588357,"room_id":953637}}` |
| grouped multi-bed booking | ❌ not supported | none | One POST is one bed/room. Dorm categories have capacity 1, so `total_adults: 2` gives 422 `MAXIMUM_CAPACITY` "The room capacity is 1 guests." Create one booking per bed. |
| edit booking | ❌ dashboard-only | none | `PUT`, `PATCH` and `POST /bookings/{id}` all return 405, so the route is GET-only |
| cancel / delete booking | ❌ dashboard-only | none | `DELETE /bookings/{id}` returns 405. `/bookings/{id}/cancel`, `/bookings/{id}/cancellation`, `/cancellations`, `/cancel-booking` and `/bookings-cancel` return 404 |
| status (provisional to confirmed) | ❌ dashboard-only | none | `/bookings/{id}` is GET-only and `/bookings/{id}/status` returns 404. API-created bookings come out `provisional` |
| check-in | ❌ dashboard-only | none | `/bookings/{id}/check-in`, `/checkin`, `/check-in` and `/checkin` all 404 |
| check-out | ❌ dashboard-only | none | `/bookings/{id}/check-out` returns 404 |
| payment | ❌ dashboard-only | none | `/bookings/{id}/payments` and `/payments` return 404. `POST /payment-methods` returns 405 (list only) |
| sale (POS product) | ❌ dashboard-only | none | `/bookings/{id}/sales`, `/bookings/{id}/products` and `/sales` return 404. `POST /products` returns 405 |
| client create/update | ❌ dashboard-only | none | `/clients` and `/customers` return 404 on every method. On create, only `holder_name` is confirmed, so the connector puts email, phone and country into the note |
| channel / agency | ❌ dashboard-only | none | `POST /channels` returns 405 (list only, currently empty). create has no confirmed channel field. The connector writes "Hostelworld &lt;ref&gt;" into the note |

### `POST /bookings`: verified request
```json
{ "category_id": 53737, "start_date": "2026-11-15", "end_date": "2026-11-16",
  "total_adults": 1, "holder_name": "Guest Name", "note": "Hostelworld 319555-… | due at hostel S/…" }
```
- Required, in validation order: `category_id`, `start_date`, `end_date`, `total_adults`, `holder_name` ("required when customer document is not present", so a customer document field exists but its name is unknown). `note` is optional.
- Response is **201** `{"booking":{"booking_id":…,"room_id":…}}`. Lobby picks the bed/room itself.
- Errors are 422 `{"error_code":"INPUT_PARAMETERS"|"MAXIMUM_CAPACITY","error":"…"}`.
- Not confirmed (don't send): `room_id`, holder email/phone/country, channel, status.

### ⚠️ Open issue: create is rejecting every category today
On 2026-11-11 every `category_id`, alone or with dates (2027-02-10, which is available), returns 422 "The selected category id is invalid." On 2026-11-10 the same bodies returned 201. The 11-10 session also saw this error briefly before creates succeeded. Possible causes are a setting changed in the panel, the trial account state, or something on LobbyPMS's side. Check with LobbyPMS support (and ask them for the docs credentials) before relying on API creates.

### Other routes found while probing
`GET /invoices` and `GET /occupancy` exist (both say "The start date field is required."). There is no `/api/v2`.

### Connector status
- `src/endpoints.js`: `create_booking` has `verified: true`. Every other action has `supported: false`, and its tool refuses with a "Dashboard-only: …" message before sending any write.
- **The currently deployed (old) worker behind the `lobbypms` connector has writes ON.** Its raw `lobby_write` forwarded a test POST (404 on a nonexistent path) on 2026-11-11. The v2 worker in this folder ships with `WRITE_ENABLED = "false"`.

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
