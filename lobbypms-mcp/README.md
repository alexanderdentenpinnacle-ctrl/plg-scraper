# lobbypms-mcp v2

MCP server (Cloudflare Worker) for LobbyPMS at Makalu Lodge. Endpoint: `POST /mcp` (also `/`). Health check: `GET /health`.

**Read tools:** `list_rooms`, `get_rates`, `get_availability`, `list_bookings`, `get_booking`, `list_products`, `list_payment_methods`, `data_quality_report`, `lobby_get`
**Write tools:** gated by `WRITE_ENABLED` and the flags in `src/endpoints.js`. The LobbyPMS API only supports **creating bookings** (`create_booking`, `import_hostelworld_booking`, dry-run by default). `cancel_booking`, `check_in`, `check_out`, `add_payment`, `add_sale` and `update_client` have no API route and return a dashboard-only message (see `API_NOTES.md`). `lobby_write` is the raw passthrough.

## Ship it (production — needs Alex's OK)
```bash
npm test
npx wrangler secret put LOBBY_API_TOKEN      # only if the account/worker doesn't already have it
npx wrangler deploy                          # read tools + fixes; WRITE_ENABLED stays "false"
```
Enable writes later (only `POST /bookings` exists; see the open category-id issue in `API_NOTES.md` first): set `WRITE_ENABLED = "true"` in `wrangler.toml` and deploy again.

Before the first deploy, confirm that `LOBBY_BASE_URL` in `wrangler.toml` matches what the current production worker uses. If the old worker had its own auth on the MCP URL, set the same value with `wrangler secret put MCP_AUTH_TOKEN`.
