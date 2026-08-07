// sms.js — GoHighLevel SMS listener (Phase 1: listen + log)
//
// Exposes an Express router that GHL calls via webhook whenever a message
// is sent or received. It normalizes GHL's (loosely-shaped) payload into a
// clean record and persists it so we can build a corpus of how you actually
// respond to customers. No AI replies happen here yet — this is pure capture.
//
// Routes (mount under /sms):
//   POST /sms/inbound   — customer -> you   (trigger: "Customer Replied")
//   POST /sms/outbound  — you -> customer   (trigger: "Outbound Message")
//   POST /sms/event     — either direction, with a `direction` field
//   GET  /sms/health    — liveness for this module
//
// Auth: send the shared secret as header `x-webhook-secret` or query `?secret=`.
// Set SMS_WEBHOOK_SECRET in the environment to enforce it.
//
// Durable storage (optional): set SHEETS_WEBHOOK_URL to a Google Apps Script
// web-app URL and each message is appended as a row to your Google Sheet.
// If unset, records still go to stdout (captured by Cloud Logging).

const express = require('express');
const fetch = require('node-fetch');

const router = express.Router();

const SECRET = (process.env.SMS_WEBHOOK_SECRET || '').trim();
const SHEETS_WEBHOOK_URL = (process.env.SHEETS_WEBHOOK_URL || '').trim();

// --- helpers ---------------------------------------------------------------

// Pull the first present, non-empty value across a list of candidate keys.
// GHL payloads vary by trigger type and by how you map fields in a workflow,
// so we probe several common spellings for each logical field.
function pick(obj, keys) {
  for (const k of keys) {
    const v = getPath(obj, k);
    if (v !== undefined && v !== null && String(v).trim() !== '') {
      return typeof v === 'string' ? v.trim() : v;
    }
  }
  return null;
}

// Supports dotted paths like "contact.phone".
function getPath(obj, path) {
  if (!obj) return undefined;
  if (path.indexOf('.') === -1) return obj[path];
  return path.split('.').reduce((o, part) => (o == null ? undefined : o[part]), obj);
}

// Turn whatever GHL sent into a stable, flat record.
function normalize(body, directionHint) {
  const rawDirection = pick(body, ['direction', 'messageDirection', 'type']);
  let direction = directionHint || null;
  if (!direction && rawDirection) {
    const d = String(rawDirection).toLowerCase();
    if (d.includes('inbound')) direction = 'inbound';
    else if (d.includes('outbound')) direction = 'outbound';
  }

  const firstName = pick(body, ['contact.firstName', 'first_name', 'firstName']);
  const lastName = pick(body, ['contact.lastName', 'last_name', 'lastName']);
  const fullName =
    pick(body, ['contact.name', 'full_name', 'fullName', 'contactName', 'name']) ||
    [firstName, lastName].filter(Boolean).join(' ') ||
    null;

  return {
    receivedAt: new Date().toISOString(),
    direction: direction || 'unknown',
    body: pick(body, ['message.body', 'body', 'message', 'text', 'sms', 'messageBody']),
    contactId: pick(body, ['contact.id', 'contactId', 'contact_id']),
    contactName: fullName,
    phone: pick(body, ['contact.phone', 'phone', 'from', 'to', 'phoneNumber']),
    conversationId: pick(body, ['conversationId', 'conversation_id', 'conversation.id']),
    messageId: pick(body, ['messageId', 'message_id', 'message.id', 'id']),
    locationId: pick(body, ['locationId', 'location_id', 'location.id']),
    messageType: pick(body, ['messageType', 'message_type', 'channel']) || 'SMS',
  };
}

// Fire-and-forget durable append. Never block the webhook response on it.
async function persist(record) {
  // Always emit structured JSON — Cloud Logging (and `docker logs`) capture this.
  console.log('SMS_EVENT ' + JSON.stringify(record));

  if (!SHEETS_WEBHOOK_URL) return;
  try {
    const resp = await fetch(SHEETS_WEBHOOK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(record),
      timeout: 8000,
    });
    if (!resp.ok) {
      console.warn(`Sheet sink returned HTTP ${resp.status}`);
    }
  } catch (e) {
    console.error('Sheet sink error:', e.message);
  }
}

function authorized(req) {
  if (!SECRET) return true; // no secret configured => open (fine for first tests)
  const provided = (req.headers['x-webhook-secret'] || req.query.secret || '')
    .toString()
    .trim();
  return provided === SECRET;
}

// One handler, reused for all three routes.
function handle(directionHint) {
  return async (req, res) => {
    if (!authorized(req)) {
      return res.status(401).json({ error: 'Unauthorized' });
    }
    const record = normalize(req.body || {}, directionHint);

    // Respond immediately so GHL never times out or retries.
    res.status(200).json({ ok: true, direction: record.direction });

    // Persist after responding.
    persist(record).catch((e) => console.error('persist failed:', e.message));
  };
}

// --- routes ----------------------------------------------------------------

router.get('/health', (req, res) => res.json({ status: 'ok', module: 'sms' }));

router.post('/inbound', handle('inbound'));
router.post('/outbound', handle('outbound'));
router.post('/event', handle(null)); // direction taken from the payload

module.exports = router;
