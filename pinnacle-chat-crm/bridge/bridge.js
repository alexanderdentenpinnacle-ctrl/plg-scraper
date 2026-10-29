// Receives Twenty CRM webhooks and posts a short message into a chat room.
// Twenty webhook URL: https://hooks.DOMAIN/twenty/<HOOK_SECRET>
const http = require('http');
const { randomUUID } = require('crypto');

const { MATRIX_URL, MATRIX_TOKEN, MATRIX_ROOM_ID, HOOK_SECRET, CRM_URL } = process.env;
const FORWARD = new Set((process.env.FORWARD_EVENTS || '').split(',').map((s) => s.trim()).filter(Boolean));

function describe(event, record = {}) {
  const [object, action] = event.split('.');
  const name =
    [record.name?.firstName, record.name?.lastName].filter(Boolean).join(' ') ||
    (typeof record.name === 'string' && record.name) ||
    record.title ||
    'Untitled';
  const details = [
    record.phones?.primaryPhoneNumber,
    record.emails?.primaryEmail,
    record.stage,
    record.amount?.amountMicros != null &&
      `$${(record.amount.amountMicros / 1e6).toLocaleString('en-US')}`,
    record.dueAt && `due ${record.dueAt.slice(0, 10)}`,
  ].filter(Boolean);
  const link = record.id ? `${CRM_URL}/object/${object}/${record.id}` : CRM_URL;
  const label = { person: 'lead', opportunity: 'deal', task: 'task' }[object] || object;
  return {
    body: `New ${label} ${action}: ${name}${details.length ? ` (${details.join(' · ')})` : ''}\n${link}`,
    html: `<b>${label[0].toUpperCase() + label.slice(1)} ${action}:</b> <a href="${link}">${escape(name)}</a>` +
      (details.length ? ` — ${escape(details.join(' · '))}` : ''),
  };
}

function escape(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
}

async function post({ body, html }) {
  const url = `${MATRIX_URL}/_matrix/client/v3/rooms/${encodeURIComponent(MATRIX_ROOM_ID)}/send/m.room.message/${randomUUID()}`;
  const res = await fetch(url, {
    method: 'PUT',
    headers: { Authorization: `Bearer ${MATRIX_TOKEN}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ msgtype: 'm.text', body, format: 'org.matrix.custom.html', formatted_body: html }),
  });
  if (!res.ok) throw new Error(`Matrix ${res.status}: ${await res.text()}`);
}

http
  .createServer((req, res) => {
    if (req.method !== 'POST' || !HOOK_SECRET || req.url !== `/twenty/${HOOK_SECRET}`) {
      res.writeHead(404).end();
      return;
    }
    let raw = '';
    req.on('data', (chunk) => (raw += chunk));
    req.on('end', async () => {
      res.writeHead(200).end('ok');
      try {
        const payload = JSON.parse(raw);
        const event = payload.eventName || '';
        if (!FORWARD.has(event)) return;
        if (!MATRIX_TOKEN || !MATRIX_ROOM_ID) return console.warn('Bot token/room not configured yet');
        await post(describe(event, payload.record));
      } catch (err) {
        console.error(err.message);
      }
    });
  })
  .listen(8080, () => console.log('bridge listening on 8080'));
