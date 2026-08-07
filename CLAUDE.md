# CLAUDE.md — Project context for GHL SMS Automation

> This file exists so any Claude Code instance (web or local) picks up the full
> context of this project without re-deriving it. If you're a local Claude Code
> session opened in this repo: read this first, then `SMS_SETUP.md`.

## Goal

Build an assistant that learns how the owner (Alex, alexdenten@gmail.com)
responds to SMS conversations in **GoHighLevel (GHL)**, offers suggested
replies in his voice, and eventually auto-sends for trusted conversation types.

## The three-phase rollout

1. **Listen & learn** ← *current phase.* Capture every inbound (customer) and
   outbound (Alex's) SMS to build a corpus of how he actually responds. No AI
   replies yet. **Code for this is written** (`sms.js`) but **not yet deployed.**
2. **Suggest.** On each inbound SMS, Claude drafts a reply in Alex's voice and
   surfaces it for review. Every message Alex actually sends teaches the voice —
   the gap between the suggestion and what he sends is the core learning signal.
3. **Automate.** Flip trusted conversation types to auto-send, with guardrails
   (business hours, STOP/opt-out & TCPA handling, escalate sensitive topics —
   claims, complaints, policy changes — to a human).

## Architecture — three layers, DO NOT conflate them

| Layer | What | Where it MUST run | Depends on Alex's computer? |
|-------|------|-------------------|-----------------------------|
| **Listener** | Catches SMS webhooks, later sends replies via GHL API | **Cloud** (Cloud Run) | No — 24/7, independent of any laptop |
| **Build tool** | Claude Code (web or local) — writes & deploys the code | Anywhere | Only while actively editing |
| **Review loop** | Surfaces suggestions to Alex for approval | **Web** (Anthropic-hosted, e.g. scheduled wake-ups) | No — must survive laptop being off |

**Hard requirement:** the automation must run whether or not Alex's computer is
on. So the always-on pieces (listener + review loop) live in the cloud / on the
web — never on the local machine. Deploying *from* the local machine is fine
(that's where the gcloud/API creds are); *hosting* there is not.

## Decisions already made (don't relitigate these)

- **GHL access:** Location API key (single sub-account), not an agency/OAuth app.
- **Integration method for phase 1:** GHL **Workflow → Webhook action** (trigger
  "Customer Replied" for inbound, "Outbound Message" for outbound). No
  marketplace app / approval needed. Graduate to OAuth later if multi-location.
- **Review surface:** Alex wants it "within this chat / a live element,"
  accessible on phone + computer, constantly learning.
  - A **live Artifact dashboard was ruled out**: Claude Artifacts are sandboxed
    (strict CSP, no external fetch) and can't reach the GHL listener; GHL isn't a
    claude.ai connector. A live artifact would be a mockup, not real data.
  - **Chosen approach:** a **self-refreshing Claude web conversation** driven by
    scheduled wake-ups (Routines/triggers). Every ~15–20 min during business
    hours the chat wakes, pulls new messages, and posts suggested replies inline.
    Alex reviews in the Claude app (synced across phone + laptop by default) and
    approves; Claude sends via the GHL API. Tradeoff: polling cadence, not
    instant — acceptable for SMS.
- **Email/Slack notification was considered and set aside** in favor of the
  in-chat review loop above.

## What's built

- `sms.js` — Express router mounted at `/sms` in `server.js`. Endpoints:
  `POST /sms/inbound`, `POST /sms/outbound`, `POST /sms/event`, `GET /sms/health`.
  Normalizes GHL's loosely-shaped payload, responds immediately, persists to
  stdout + optional Google Sheet sink. Auth via `SMS_WEBHOOK_SECRET`
  (header `x-webhook-secret` or `?secret=`).
- `sheet-sink.gs` — Google Apps Script that appends each message to a Google
  Sheet (durable, human-viewable storage).
- `SMS_SETUP.md` — deploy + GHL workflow + Sheet sink walkthrough.
- The pre-existing `plg-scraper` (email enrichment) still lives in `server.js`
  and is untouched by this work.

## Env vars

| Var | Purpose |
|-----|---------|
| `SMS_WEBHOOK_SECRET` | Shared secret GHL must send. Enforced if set. |
| `SHEETS_WEBHOOK_URL` | Google Apps Script web-app URL for durable storage (optional). |
| `SCRAPER_SECRET` | (existing) auth for the `/scrape` enrichment endpoint. |

## Immediate next steps (in order)

1. **Deploy the service to Cloud Run** (see `SMS_SETUP.md`; deploy from the
   machine that has gcloud + creds). Set `SMS_WEBHOOK_SECRET`.
2. **Build the two GHL workflows** (inbound + outbound) pointing at the deployed
   `/sms/inbound` and `/sms/outbound` URLs with the secret.
3. **(Optional) Wire the Google Sheet sink** for durable, viewable storage.
4. Confirm messages are landing (logs + sheet). Let the corpus build for a few days.
5. **Then Phase 2:** build the suggestion loop (draft reply in Alex's voice) +
   the self-refreshing review conversation. This needs the listener deployed and
   GHL API read/send access wired first.

## Branch

Work happens on `claude/ghl-sms-automation-lbcovu`. Push there; don't stack on
merged history.
