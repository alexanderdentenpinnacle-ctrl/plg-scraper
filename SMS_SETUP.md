# GHL SMS Automation — Phase 1: Listen & Learn

This is the first of three phases:

1. **Listen & learn** ← *you are here.* Capture every inbound (customer) and
   outbound (your) SMS so we build a corpus of how you actually respond. No AI
   replies yet.
2. **Suggest.** On each inbound SMS, Claude drafts a reply in your voice and
   drops it into GHL as an internal note for you to approve/edit/ignore.
3. **Automate.** Flip trusted conversation types to auto-send, with guardrails.

The listener is `sms.js`, mounted at `/sms` inside the existing service.

---

## What runs where

```
Customer texts you
        |
        v
   GoHighLevel  --(workflow: Webhook action)-->  POST /sms/inbound  --+
   GoHighLevel  --(workflow: Webhook action)-->  POST /sms/outbound --+
                                                                      |
                                                                      v
                                                          listener service (sms.js)
                                                                      |
                                                       +--------------+--------------+
                                                       v                             v
                                                stdout (Cloud Logging)     Google Sheet (durable)
```

The listener responds to GHL in milliseconds, then persists the record so a
slow sheet write can never cause GHL to time out or retry.

---

## Step 1 — Deploy the service

It's a standard Node container (`Dockerfile`, port 8080). Any container host
works. On **Google Cloud Run**:

```bash
gcloud run deploy plg-scraper \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars SMS_WEBHOOK_SECRET=<pick-a-long-random-string>
```

Note the service URL it prints, e.g. `https://plg-scraper-xxxx.run.app`.

Test it:

```bash
curl https://plg-scraper-xxxx.run.app/sms/health
# {"status":"ok","module":"sms"}
```

### Environment variables

| Var | Required | Purpose |
|-----|----------|---------|
| `SMS_WEBHOOK_SECRET` | recommended | Shared secret GHL must send as `x-webhook-secret` header or `?secret=` query. If unset, the endpoint is open (fine only for a first smoke test). |
| `SHEETS_WEBHOOK_URL` | optional | Google Apps Script web-app URL for durable storage (Step 3). If unset, records still land in logs. |

---

## Step 2 — Wire up GHL (Location API key setup)

You'll build two simple workflows in your GHL sub-account. No app approval needed.

### Inbound (customer → you)

1. **Automation → Workflows → Create Workflow → Start from scratch.**
2. **Trigger:** *Customer Replied* → filter **Reply Channel = SMS**.
3. **Add Action → Webhook.**
   - Method: `POST`
   - URL: `https://plg-scraper-xxxx.run.app/sms/inbound?secret=<your-secret>`
     (or add a custom header `x-webhook-secret: <your-secret>`)
   - In the custom-data / payload builder, map:
     - `body` → `{{message.body}}`
     - `contactId` → `{{contact.id}}`
     - `contactName` → `{{contact.name}}`
     - `phone` → `{{contact.phone}}`
     - `conversationId` → `{{message.conversation_id}}` (if available)
4. **Publish** the workflow.

### Outbound (you → customer)

Repeat with a second workflow using the **Outbound Message** trigger (channel
SMS), posting to `.../sms/outbound`. This is what lets Claude learn *your*
voice, not just the customer's questions.

> The normalizer accepts many field-name variants, so exact mapping isn't
> critical — but `body`, `contactId`, and `phone` are the ones worth getting
> right.

---

## Step 3 — Durable storage in a Google Sheet (recommended)

So the conversation corpus survives restarts and you can eyeball it:

1. Create a Google Sheet.
2. **Extensions → Apps Script**, paste `sheet-sink.gs`, save.
3. **Deploy → New deployment → Web app**; execute as **Me**, access **Anyone**.
4. Copy the web-app URL → set it as `SHEETS_WEBHOOK_URL` on the service and
   redeploy.

Every message now appends a row: timestamp, direction, name, phone, body, IDs.

---

## Step 4 — Confirm it's live

Text the GHL number (or reply to a test contact). Within a second or two you
should see:

- a `SMS_EVENT {...}` line in the service logs, and
- a new row in the **SMS Log** sheet.

Once messages are flowing in for a few days, we have enough to start Phase 2
(suggested replies). Tell me when the log is filling up and I'll build it.

---

## Endpoints reference

| Method | Path | Direction | Use |
|--------|------|-----------|-----|
| GET | `/sms/health` | — | Liveness check |
| POST | `/sms/inbound` | inbound | Customer → you |
| POST | `/sms/outbound` | outbound | You → customer |
| POST | `/sms/event` | from payload's `direction` | Either, if you prefer one workflow |
