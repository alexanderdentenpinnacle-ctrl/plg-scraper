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

Probed and **404**: `/rates`, `/clients`, `/clients/{id}`, `/agencies`, `/booking`, `/bookings/{id}/check-in`, `/bookings/{id}/checkin`, `/bookings/{id}/payments`, `/bookings/{id}/sales`, `/bookings/{id}/cancel`, plus check-in filter variants `check_in_date_from`, `checkin_date_from`, `start_date_from`, `arrival_date_from`. Note: LobbyPMS returns 404 for a wrong method on an existing path, so these probes only show there's no GET route. See the docs-based table below for the real write routes.

## Write endpoints (official docs, 2026-11-13)

Source: the official docs at **app.lobbypms.com/api** (Alex got access 2026-11-13). Base URL `https://api.lobbypms.com/api/v1`. Auth: the API token (docs show an `api_token` param; Bearer works too) plus **IP allow-listing** in Settings → Users, permissions and API → API Access.

> Correction to the 2026-11-11 probe notes: per the docs, LobbyPMS answers **404 for a wrong HTTP method** on an existing path. So a GET probe returning 404 did **not** prove a write route was missing. Cancel, client and sales routes exist; they just have different paths than guessed.

"Verified" below means the request shape was copied from the official docs. **Live** means a real call also succeeded.

| Action | Status | Method / path | Key body fields | Notes |
|---|---|---|---|---|
| **Create booking** | ✅ docs + **live** | `POST /bookings` | `category_id`, `start_date`, `end_date`, `total_adults`, `holder_name`; optional `total_children`, `customer_document`+`customer_nationality` (existing client instead of holder_name), `rates_per_day[{date,price}]`, `note`, `payment`, `channel`, `plan_id` | 201 `{"booking":{"booking_id","room_id"}}`. If nights can't fit one room it adds `distributed[]` (one booking per segment). `rates_per_day` must have exactly one entry per night, else 422 `RATES_PER_DAY`. `payment` ≤ total, and the payment method can't be picked. |
| Grouped multi-bed booking | ❌ not in API | none | | One POST = one room/bed. Book N beds as N bookings. |
| **Cancel booking** | ✅ docs | `POST /cancel-booking/{booking_id}` | `cancellation_reason` (NS, RC, RE, TTC, CC, OTH), `description` | 200 `{"cancel_booking":id}`. **Only bookings created by the API** (`UNAUTHORIZED_BOOKING`), not checked in (`BOOKING_CHECK_IN_COMPLETE`), with no payments/extra charges (`RESTRICTED_RESERVATION`). Others: cancel in LobbyPMS. |
| Edit booking | ❌ not in API | none | | Dashboard-only. |
| Status (provisional → confirmed) | ❌ not in API | none | | Dashboard-only. |
| Check-in | ❌ not in API | none | | Dashboard or Lobby Staff app. `GET /rooms/status` shows today's check-ins. |
| Check-out | ❌ not in API | none | | Dashboard or Lobby Staff app. |
| Payment | ⚠️ partial | `payment` on create; `POST /payment/link` | link: `booking_id`, `amount` (≥ 6, ≤ debt), `expire_in` (Y-m-d) | No "record cash payment" endpoint. A payment link needs a payment system configured in Lobby. One OPEN link per booking. `GET /payment/link?booking_id=` lists links; `DELETE /payment/link/{uuid}` removes an OPEN one. |
| **Sale (POS product)** | ✅ docs | `POST /booking/add-product-service` | `booking_id`, `items[{product_id, cant, inventory_center_id?}]` | `product_id` = `service_id` from `GET /products`. 200 `{"sale":{"id","total"}}`. |
| **Client** | ✅ create only | `POST /customer/1` (person) or `/customer/2` (company) | `customer_document`, `customer_nationality` (ISO 3166-1), `name`, `surname` (person); optional `document_id` (from `GET /documents`), `birthdate`, `gender`, `phone` (+country code), `email`, `activities`, `address`, `note` | 200 `{"status":"success"}`, no id returned. **No update endpoint.** Attach bookings via `customer_document` + `customer_nationality` on create. |
| **Channel** | ✅ on create only | `channel` field of `POST /bookings` | id from `GET /channels` | Makalu currently has **no channels** (`/channels` is empty). Create "Hostelworld" in LobbyPMS first; `import_hostelworld_booking` then sends it automatically. It can't be changed later through the API. |
| Block rooms | ✅ docs | `POST /block`; `DELETE /block/{block_id}` | `category_id`, `start_date`, `end_date`, `number_rooms`, `time` (minutes, default 60), `note` | Temporary hold, e.g. while a guest pays. |

New read endpoints from the docs: `GET /rooms/status`, `GET /documents`, `GET /daily-occupancy`, `GET /api/v2/available-rooms` (prices per rate plan). `/occupancy` and `/daily-occupancy` take UTC datetimes that equal 00:00 at the property (05:00:00 UTC for Lima), with a max 90-day range.

### Connector status (`src/endpoints.js`)
- `verified: 'docs+live'`: `create_booking`.
- `verified: 'docs'` (shape from the docs, not yet called live): `cancel_booking`, `add_sale`, `create_client`, `create_payment_link`, `delete_payment_link`, `create_block`, `delete_block`.
- `supported: false` (tool refuses before sending, with a dashboard-only message): `add_payment`, `edit_booking`, `confirm_status`, `check_in`, `check_out`, `update_client`, `set_channel`, `create_group_booking`.
- New tools: `create_client`, `create_payment_link`, `list_payment_links`, `delete_payment_link`, `create_block`, `delete_block`, `get_room_status`, `list_channels`, `list_rate_plans`, `list_document_types`.
- Live testing is now safe to clean up: a test booking created by the API **can be cancelled** with `POST /cancel-booking/{id}` (reason `RE`). Not done yet; needs Alex's OK.

### ⚠️ Open issue: create rejects every category (since 2026-11-11)
Every `category_id` returns 422 "The selected category id is invalid." The same bodies returned 201 on 2026-11-10. Asked LobbyPMS support on WhatsApp 2026-11-11.

### Deployed connector
The currently deployed (old) worker behind the `lobbypms` connector has **writes ON**. The v2 worker in this folder ships with `WRITE_ENABLED = "false"`.

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
