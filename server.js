const express = require('express');
const fetch = require('node-fetch');
const cheerio = require('cheerio');

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 8080;
const SECRET = process.env.SCRAPER_SECRET || '';

const HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
  'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
  'Accept-Language': 'en-US,en;q=0.9',
  'Accept-Encoding': 'gzip, deflate, br',
  'Cache-Control': 'no-cache',
  'Referer': 'https://www.google.com/',
  'Sec-Fetch-Dest': 'document',
  'Sec-Fetch-Mode': 'navigate',
  'Sec-Fetch-Site': 'cross-site',
};

app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'plg-scraper' });
});

app.post('/scrape', async (req, res) => {
  // Auth check
  const secret = (req.headers['x-scraper-secret'] || '').trim();
  if (SECRET && secret !== SECRET) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  const { firstName, lastName, phone, state } = req.body;
  if (!firstName && !lastName && !phone) {
    return res.status(400).json({ error: 'Need at least name or phone' });
  }

  let email = null;
  let source = null;

  // Strategy 1: TruePeopleSearch by phone
  if (!email && phone) {
    const result = await tryTruePeoplePhone(phone);
    if (result) { email = result; source = 'TruePeopleSearch (phone)'; }
  }

  // Strategy 2: TruePeopleSearch by name + state
  if (!email && firstName && lastName) {
    const result = await tryTruePeopleName(firstName, lastName, state);
    if (result) { email = result; source = 'TruePeopleSearch (name)'; }
  }

  // Strategy 3: FastPeopleSearch by phone
  if (!email && phone) {
    const result = await tryFastPeoplePhone(phone);
    if (result) { email = result; source = 'FastPeopleSearch (phone)'; }
  }

  // Strategy 4: FastPeopleSearch by name
  if (!email && firstName && lastName) {
    const result = await tryFastPeopleName(firstName, lastName, state);
    if (result) { email = result; source = 'FastPeopleSearch (name)'; }
  }

  console.log(`[${new Date().toISOString()}] ${firstName} ${lastName} (${phone}) → ${email || 'not found'} ${source ? `via ${source}` : ''}`);
  res.json({ email, source });
});

async function tryTruePeoplePhone(phone) {
  try {
    const clean = phone.replace(/\D/g, '');
    const url = `https://www.truepeoplesearch.com/results?phoneno=${clean}`;
    const html = await fetchHtml(url);
    return extractEmail(html);
  } catch (e) { console.error('TPS phone error:', e.message); return null; }
}

async function tryTruePeopleName(firstName, lastName, state) {
  try {
    const name = encodeURIComponent(`${firstName} ${lastName}`);
    const loc = state ? encodeURIComponent(state) : '';
    const url = `https://www.truepeoplesearch.com/results?name=${name}&citystatezip=${loc}`;
    const html = await fetchHtml(url);
    return extractEmail(html);
  } catch (e) { console.error('TPS name error:', e.message); return null; }
}

async function tryFastPeoplePhone(phone) {
  try {
    const clean = phone.replace(/\D/g, '');
    const url = `https://www.fastpeoplesearch.com/phone/${clean}`;
    const html = await fetchHtml(url);
    return extractEmail(html);
  } catch (e) { console.error('FPS phone error:', e.message); return null; }
}

async function tryFastPeopleName(firstName, lastName, state) {
  try {
    const slug = `${firstName}-${lastName}`.toLowerCase().replace(/\s+/g, '-');
    const loc = state ? `_${encodeURIComponent(state)}` : '';
    const url = `https://www.fastpeoplesearch.com/name/${encodeURIComponent(slug)}${loc}`;
    const html = await fetchHtml(url);
    return extractEmail(html);
  } catch (e) { console.error('FPS name error:', e.message); return null; }
}

async function fetchHtml(url) {
  const resp = await fetch(url, {
    headers: HEADERS,
    redirect: 'follow',
    timeout: 10000,
  });
  if (!resp.ok) {
    console.warn(`HTTP ${resp.status} for ${url}`);
    return '';
  }
  return resp.text();
}

function extractEmail(html) {
  if (!html) return null;
  const emailRegex = /[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g;
  const matches = html.match(emailRegex) || [];
  const blacklist = [
    'truepeoplesearch.com', 'fastpeoplesearch.com', 'whitepages.com',
    'example.com', 'test.com', 'cloudflare.com', 'google.com',
    'sentry.io', 'amazonaws.com', 'noreply', 'no-reply', 'support@',
    'wixpress.com', 'jquery.com', 'schema.org',
  ];
  for (const email of matches) {
    const lower = email.toLowerCase();
    if (blacklist.some(b => lower.includes(b))) continue;
    if (/^[a-z0-9._%+\-]{3,}@[a-z0-9.\-]+\.(com|net|org|edu|gov|us|info)$/.test(lower)) {
      return lower;
    }
  }
  return null;
}

app.listen(PORT, () => {
  console.log(`PLG Scraper running on port ${PORT}`);
});
