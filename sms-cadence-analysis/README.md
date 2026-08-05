# SMS cadence analyzer (GoHighLevel + Twilio)

Read-only analysis of outbound-SMS performance for aged mortgage-protection
cadences. It takes the blended "7% response rate" apart into the numbers that
actually tell you whether the cadence works — and does **not** write anything
back to GHL, send any message, or deploy anything. Only `GET` requests.

## What it answers

1. **Touch-level decay** — reply rate attributable to each touch (1→5) in the
   sequence. Each inbound reply is attributed to the most recent preceding
   outbound send. Because insurance replies average ~3.2 days, a reply that
   lands within 24h of a bordering send is flagged **ambiguous** and excluded
   from the decay table (reported separately) rather than silently
   mis-attributed.
2. **Denominator check** — sent vs delivered vs failed/undelivered, Twilio
   error codes surfaced (esp. 30007 carrier-filtered, 30003 unreachable,
   30008 unknown), and reply rate on **both** sent and delivered denominators.
3. **Reply classification** — every inbound reply bucketed into
   **OPT-OUT / NEGATIVE / NEUTRAL / POSITIVE**. Opt-out uses high-recall
   keyword + natural-language matching (compliance surface). The rest are
   classified by Claude (`claude-sonnet-4-6` by default), batched. Raw reply
   text is included in the CSV so you can spot-check the classifier.
   **The headline metric is POSITIVE reply rate**, not blended replies.
4. **Message length / encoding** — real segment count per outbound message
   (GSM-7 = 160/153, UCS-2 = 70/67; a single emoji / curly quote / em dash
   forces UCS-2), cross-tabbed against positive reply rate by segment count
   and by character-count bucket.

## Setup

```bash
cd sms-cadence-analysis
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill in GHL_API_KEY, GHL_LOCATION_ID, ANTHROPIC_KEY
set -a; . ./.env; set +a  # export them into the environment
```

## Run

```bash
python analyze_sms.py --start 2026-07-01 --end 2026-07-31
# skip the AI classifier (opt-out keywords only, no Anthropic calls):
python analyze_sms.py --start 2026-07-01 --end 2026-07-31 --no-ai
```

Flags: `--location-id` (overrides env), `--workflows` (see note below),
`--out DIR` (default `out/`), `--model`, `--no-ai`.

Outputs a printed summary plus CSVs in `out/`:
`delivery_denominator.csv`, `touch_decay.csv`, `replies_classified.csv`,
`segment_crosstab.csv`, `outbound_messages.csv`.

## Assumptions / things to verify

- **API shape.** Endpoints, headers, and auth follow the task spec
  (`GET /conversations/search`, `GET /conversations/{id}/messages`, base
  `https://services.leadconnectorhq.com`, header `Version: 2021-07-28`,
  `Authorization: Bearer <token>`). GHL has changed its API before — the
  pagination and field parsing live in clearly marked helpers (`GHL`,
  `parse_msg`, `_find_error_code`) and are defensive about field names. The
  date window is **also** enforced client-side, so results stay correct even
  if a server-side date filter behaves unexpectedly. Verify against current
  GHL docs before trusting an end-to-end run.
- **Workflow/campaign filtering.** GHL does not expose a workflow/campaign id
  on individual messages, so `--workflows` cannot reliably filter by campaign;
  default is `all` (all SMS). This matches the known limitation in the
  existing SMS dashboard.
- **Segment cross-tab buckets.** The task spec was truncated mid-sentence at
  section 4 ("by character-count bucket ("), so the character buckets
  (1–160 / 161–320 / 321–480 / 481+) were chosen to mirror SMS segment
  boundaries. Change `CHAR_BUCKETS` in `analyze_sms.py` if you meant different
  cutoffs, and resend the rest of section 4 (and any sections 5+) to extend.
- **Rendered vs template body.** Segment counts are computed on the message
  body as returned by GHL, which is already the post-merge-field rendered
  text, per the spec.
- **Secrets** come only from the environment / `.env`; nothing is hardcoded.
  Do not commit `.env`.
