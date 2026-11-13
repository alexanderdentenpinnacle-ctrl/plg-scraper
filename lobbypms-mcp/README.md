# lobbypms-mcp v2

MCP server (Cloudflare Worker) for LobbyPMS at Makalu Lodge. Endpoint: `POST /mcp` (also `/`). Health check: `GET /health`.

**Read tools:** `list_rooms`, `get_rates`, `get_availability`, `list_bookings`, `get_booking`, `list_products`, `list_payment_methods`, `data_quality_report`, `lobby_get`
**Read tools:** also `get_room_status`, `list_channels`, `list_rate_plans`, `list_document_types`, `list_payment_links`.

**Write tools:** gated by `WRITE_ENABLED` and the flags in `src/endpoints.js` (mapped from the official docs, see `API_NOTES.md`): `create_booking`, `import_hostelworld_booking` (dry-run by default), `cancel_booking` (API-created bookings only), `add_sale`, `create_client`, `create_payment_link`, `delete_payment_link`, `create_block`, `delete_block`, `lobby_write`. `check_in`, `check_out`, `add_payment` and `update_client` aren't in LobbyPMS's API and return a dashboard-only message.

## Ship it (production — needs Alex's OK)
```bash
npm test
npx wrangler secret put LOBBY_API_TOKEN      # only if the account/worker doesn't already have it
npx wrangler deploy                          # read tools + fixes; WRITE_ENABLED stays "false"
```
Enable writes later (see the open category-id issue in `API_NOTES.md` first): set `WRITE_ENABLED = "true"` in `wrangler.toml` and deploy again.

Before the first deploy, confirm that `LOBBY_BASE_URL` in `wrangler.toml` matches what the current production worker uses. If the old worker had its own auth on the MCP URL, set the same value with `wrangler secret put MCP_AUTH_TOKEN`.
