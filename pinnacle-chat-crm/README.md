# Pinnacle Chat + CRM

Twenty CRM running inside a private, Discord-style team chat (Matrix + Element).
Each chat room can have a **CRM tab**: the full Twenty app inside the room. New leads, deals and tasks post automatically into a `#leads` room.

| URL | What it is |
|---|---|
| `https://chat.DOMAIN` | The chat app (servers/rooms/DMs; desktop + mobile apps too) |
| `https://crm.DOMAIN` | Twenty CRM (also opens as a tab inside chat rooms) |
| `matrix.DOMAIN`, `hooks.DOMAIN` | Back end only |

Everything runs on one VPS. Cost is about $12–24/mo (4 GB RAM minimum, 8 GB recommended).

## 1. Server + DNS (about 10 minutes)
1. Create an Ubuntu 24.04 VPS (e.g. Hetzner CPX31 or DigitalOcean 8 GB) and install Docker:
   `curl -fsSL https://get.docker.com | sh`
2. In Cloudflare DNS, add **A records** for `chat`, `crm`, `matrix` and `hooks` pointing to the VPS IP.
   Set them to **DNS only (grey cloud)** so the server can issue its own HTTPS certificates.

## 2. Install
```bash
git clone <this repo> && cd pinnacle-chat-crm
sudo ./setup.sh          # asks for your domain, generates all passwords, starts everything
```
The first boot takes about 2 minutes while Twenty sets up its database.

## 3. Accounts
- **CRM:** open `https://crm.DOMAIN` and create the workspace. The first user is the admin.
- **Chat admin:**
  `docker compose exec synapse register_new_matrix_user -c /data/homeserver.yaml -a -u alex http://localhost:8008`
- **Each agent:** run the same command without `-a`, using their username. Public sign-up is off.
- Agents log in at `https://chat.DOMAIN`. They can also use the Element desktop or mobile app, where they'll need to enter the server `matrix.DOMAIN`.

## 4. Add the CRM tab to a room
In any room (for example `#sales`), type:
```
/addwidget https://crm.DOMAIN
```
Then go to **Room info → Extensions → the widget → Pin** to make it a tab, or **Maximise** to make the room "just the CRM".
Log in to Twenty once inside the tab. Use email and password, because Google sign-in pop-ups don't work inside the embedded tab.

## 5. Lead alerts into `#leads`
1. Create a bot user:
   `docker compose exec synapse register_new_matrix_user -c /data/homeserver.yaml -u crmbot -p <password> --no-admin http://localhost:8008`
2. Get its token:
   `curl -s -XPOST http://localhost/_matrix/client/v3/login -H 'Host: matrix.DOMAIN' -d '{"type":"m.login.password","identifier":{"type":"m.id.user","user":"crmbot"},"password":"<password>"}'`
   (or run the same request from your laptop against `https://matrix.DOMAIN`). Copy the `access_token` value.
3. Create a `#leads` room in chat and invite `@crmbot:matrix.DOMAIN`. Accept the invite as the bot:
   `curl -s -XPOST "https://matrix.DOMAIN/_matrix/client/v3/join/<room id>" -H "Authorization: Bearer <token>"`
   The room ID is under **Room settings → Advanced** and looks like `!abc123:matrix.DOMAIN`.
4. Put the token and room ID in `.env` (`MATRIX_BOT_TOKEN`, `MATRIX_LEADS_ROOM_ID`), then run `docker compose up -d bridge`.
5. In Twenty, go to **Settings → APIs & Webhooks → Create webhook**, paste the URL that setup.sh printed (`https://hooks.DOMAIN/twenty/<secret>`), and save.

Default alerts are new people (leads), new opportunities (deals) and new tasks. To change them, edit `FORWARD_EVENTS` in `.env`, for example adding `opportunity.updated`.

## Maintenance
- **Update:** `docker compose pull && docker compose up -d`
- **Backups:** turn on your VPS provider's snapshots (about $2–5/mo). That covers all data.
- **Privacy:** federation is off, so the chat can't talk to outside Matrix servers. Only accounts you create can log in.
