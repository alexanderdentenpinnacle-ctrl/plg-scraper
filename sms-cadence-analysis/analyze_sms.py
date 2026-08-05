#!/usr/bin/env python3
"""
SMS cadence performance analyzer for GoHighLevel (LeadConnector) + Twilio.

READ-ONLY. This script only issues GET requests against the GHL conversations
API. It never writes back to GHL, never sends a message, and deploys nothing.

It decomposes real outbound-SMS performance for aged mortgage-protection
cadences (day 1/3/6/10/14 touches) into the numbers a blended "7% response
rate" hides:

  1. TOUCH-LEVEL DECAY   - reply rate attributable to each touch in sequence,
                           with slow-reply ambiguity flagged, not buried.
  2. DENOMINATOR CHECK   - sent vs delivered vs failed, Twilio error codes,
                           reply rate on BOTH sent and delivered denominators.
  3. REPLY CLASSIFICATION- OPT-OUT (high-recall keywords) / NEGATIVE / NEUTRAL
                           / POSITIVE. Headline metric = POSITIVE reply rate.
  4. MESSAGE LENGTH      - real segment count per outbound message (GSM-7 vs
                           UCS-2), cross-tabbed against positive reply rate.

Outputs a set of CSVs plus a printed summary.

Config comes from the environment / CLI, never hardcoded:
    GHL_API_KEY        location-level Private Integration token (Bearer)
    GHL_LOCATION_ID    location id (or --location-id)
    ANTHROPIC_KEY      Anthropic key for the reply classifier (optional; run
                       --no-ai to skip and keyword-only the opt-outs)

API details are taken from the task spec. GHL has changed its API before, so
the exact pagination/field shapes are isolated in clearly marked helpers and
the date window is ALSO enforced client-side, so the analysis stays correct
even if a server-side date filter behaves differently. Verify the endpoints
against current GHL docs before trusting a run end to end.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

# --------------------------------------------------------------------------- #
# Config / constants
# --------------------------------------------------------------------------- #
GHL_BASE = "https://services.leadconnectorhq.com"
GHL_VERSION = "2021-07-28"
DEFAULT_MODEL = "claude-sonnet-4-6"  # user-chosen classifier model
PAGE_LIMIT = 100
AI_BATCH = 15  # replies classified per Anthropic call ("batch the calls")

# Twilio error codes worth calling out explicitly (spec) + a few common kin.
TWILIO_ERROR_NOTES = {
    "30003": "unreachable / powered off",
    "30005": "unknown destination handset",
    "30006": "landline or unreachable carrier",
    "30007": "carrier filtered (spam block)",
    "30008": "unknown error",
    "21610": "recipient previously opted out (STOP)",
    "21614": "not a valid mobile number",
}

# High-recall OPT-OUT detection. Compliance surface: err toward catching one.
OPTOUT_KEYWORDS_EXACT = {
    "stop", "stopall", "unsubscribe", "quit", "end", "cancel", "revoke",
    "optout", "opt-out", "opt out", "remove", "unsub",
}
OPTOUT_PHRASES = [
    "remove me", "take me off", "don't text", "dont text", "do not text",
    "stop texting", "stop messaging", "stop contacting", "leave me alone",
    "no more texts", "no more messages", "unsubscribe me", "take my name off",
    "delete my number", "quit texting",
]

REPLY_CATEGORIES = ("OPT-OUT", "NEGATIVE", "NEUTRAL", "POSITIVE")

# Character-count buckets for the length cross-tab. Spec was truncated mid
# sentence ("by character-count bucket ("); these mirror SMS segment
# boundaries and are easy to change here.
CHAR_BUCKETS = [
    (0, 160, "1-160"),
    (161, 320, "161-320"),
    (321, 480, "321-480"),
    (481, 10**9, "481+"),
]


# --------------------------------------------------------------------------- #
# GSM-7 / UCS-2 segment math
# --------------------------------------------------------------------------- #
_GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ ÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
# Extension-table chars occupy TWO septets each.
_GSM_EXT = set("^{}\\[~]|€\f")


def segment_info(body: str) -> dict:
    """Return encoding + real segment count for an already-rendered SMS body.

    GSM-7: 160 chars single, 153 per part. UCS-2: 70 single, 67 per part.
    A single non-GSM character (emoji, curly quote, em dash) forces UCS-2 for
    the whole message.
    """
    body = body or ""
    is_gsm = all((c in _GSM_BASIC or c in _GSM_EXT) for c in body)
    if is_gsm:
        units = sum(2 if c in _GSM_EXT else 1 for c in body)  # septets
        single, multi = 160, 153
        encoding = "GSM-7"
    else:
        # UTF-16 code units (surrogate pairs count as 2, like real UCS-2 SMS).
        units = len(body.encode("utf-16-le")) // 2
        single, multi = 70, 67
        encoding = "UCS-2"
    if units == 0:
        segments = 0
    elif units <= single:
        segments = 1
    else:
        segments = math.ceil(units / multi)
    return {
        "encoding": encoding,
        "char_len": len(body),
        "units": units,
        "segments": segments,
    }


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Msg:
    id: str
    contact_id: str
    conversation_id: str
    direction: str            # "outbound" | "inbound"
    ts: float                 # epoch seconds
    body: str
    status: str               # delivered / sent / failed / undelivered / ...
    error_code: str           # "" if none
    raw_type: str


@dataclass
class ReplyRow:
    contact_id: str
    ts: float
    body: str
    attributed_touch: int     # 1-based; 0 if no preceding outbound
    ambiguous: bool
    category: str = "UNCLASSIFIED"


# --------------------------------------------------------------------------- #
# GHL client (read-only)
# --------------------------------------------------------------------------- #
class GHL:
    def __init__(self, token: str, location_id: str):
        self.location_id = location_id
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Version": GHL_VERSION,
            "Accept": "application/json",
        })

    def _get(self, path: str, params: dict) -> dict:
        url = f"{GHL_BASE}{path}"
        delay = 2.0
        for attempt in range(6):
            r = self.s.get(url, params=params, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                # Respect Retry-After if present, else exponential backoff.
                wait = float(r.headers.get("Retry-After", 0) or 0) or delay
                sys.stderr.write(
                    f"  [rate/err {r.status_code}] retry in {wait:.0f}s "
                    f"({attempt+1}/6)\n"
                )
                time.sleep(wait)
                delay = min(delay * 2, 32)
                continue
            if not r.ok:
                raise RuntimeError(f"GHL {r.status_code} {path}: {r.text[:300]}")
            return r.json()
        raise RuntimeError(f"GHL retries exhausted for {path}")

    def iter_conversations(self, start_ms: int):
        """Yield conversation dicts, newest first, stopping once we pass the
        start of the window. Pagination uses the documented startAfter cursor
        with a page-number fallback; dedup guards either path."""
        seen: set[str] = set()
        page = 1
        cursor_date = None
        cursor_id = None
        while True:
            params = {
                "locationId": self.location_id,
                "limit": PAGE_LIMIT,
                "sortBy": "last_message_date",
                "sort": "desc",
            }
            if cursor_date is not None:
                params["startAfterDate"] = cursor_date
                if cursor_id:
                    params["startAfter"] = cursor_id
            else:
                params["page"] = page
            data = self._get("/conversations/search", params)
            convs = data.get("conversations") or data.get("conversation") or []
            if not convs:
                return
            progressed = False
            oldest = None
            for c in convs:
                cid = c.get("id")
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                progressed = True
                last = _first_num(c, ("lastMessageDate", "dateUpdated",
                                      "dateAdded", "last_message_date"))
                oldest = last if oldest is None else min(oldest, last or oldest)
                yield c
            if not progressed:
                return
            # Early stop: everything on this page is older than the window.
            if oldest is not None and oldest < start_ms:
                return
            last_c = convs[-1]
            cursor_date = _first_num(
                last_c, ("lastMessageDate", "dateUpdated", "dateAdded",
                         "last_message_date"))
            cursor_id = last_c.get("id")
            page += 1
            if cursor_date is None and page > 500:
                return  # safety valve

    def iter_messages(self, conversation_id: str):
        """Yield message dicts for a conversation, following lastMessageId."""
        last_id = None
        for _ in range(1000):  # hard cap per conversation
            params = {"limit": PAGE_LIMIT}
            if last_id:
                params["lastMessageId"] = last_id
            data = self._get(
                f"/conversations/{conversation_id}/messages", params)
            block = data.get("messages", data)
            msgs = block.get("messages") if isinstance(block, dict) else block
            msgs = msgs or []
            if not msgs:
                return
            for m in msgs:
                yield m
            has_more = (isinstance(block, dict)
                        and block.get("nextPage") in (True, "true"))
            last_id = msgs[-1].get("id")
            if not has_more or not last_id:
                return


# --------------------------------------------------------------------------- #
# Parsing helpers (defensive about GHL field shapes)
# --------------------------------------------------------------------------- #
def _first_num(d: dict, keys) -> float | None:
    for k in keys:
        v = d.get(k)
        if v in (None, ""):
            continue
        ms = _to_ms(v)
        if ms is not None:
            return ms
    return None


def _to_ms(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v) if v > 1e11 else float(v) * 1000.0
    if isinstance(v, str):
        s = v.strip()
        if s.isdigit():
            n = float(s)
            return n if n > 1e11 else n * 1000.0
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp() * 1000.0
        except ValueError:
            return None
    return None


def _find_error_code(m: dict) -> str:
    """Hunt for a Twilio-style error code anywhere in the message payload."""
    candidates = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                lk = str(k).lower()
                if "error" in lk or lk in ("code", "errorcode"):
                    if isinstance(v, (str, int)):
                        candidates.append(str(v))
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(m)
    for c in candidates:
        digits = "".join(ch for ch in c if ch.isdigit())
        if digits in TWILIO_ERROR_NOTES or (len(digits) == 5
                                            and digits.startswith("3")):
            return digits
    return ""


def is_sms(m: dict) -> bool:
    mt = str(m.get("messageType") or m.get("type") or "").upper()
    return "SMS" in mt


def parse_msg(m: dict) -> Msg | None:
    ts_ms = _first_num(m, ("dateAdded", "dateUpdated", "date"))
    if ts_ms is None:
        return None
    direction = str(m.get("direction") or "").lower()
    if direction not in ("inbound", "outbound"):
        # Some payloads flag direction only via type prefixes.
        direction = "outbound" if "OUT" in str(m.get("type", "")).upper() \
            else "inbound"
    return Msg(
        id=str(m.get("id") or ""),
        contact_id=str(m.get("contactId") or ""),
        conversation_id=str(m.get("conversationId") or ""),
        direction=direction,
        ts=ts_ms / 1000.0,
        body=m.get("body") or m.get("text") or "",
        status=str(m.get("status") or "").lower(),
        error_code=_find_error_code(m),
        raw_type=str(m.get("messageType") or m.get("type") or ""),
    )


# --------------------------------------------------------------------------- #
# Reply classification
# --------------------------------------------------------------------------- #
def keyword_optout(body: str) -> bool:
    t = (body or "").strip().lower()
    if not t:
        return False
    # Exact single-word replies ("STOP", "quit") — the classic keyword surface.
    words = {w.strip(".,!?;: ") for w in t.split()}
    if words & OPTOUT_KEYWORDS_EXACT:
        return True
    return any(p in t for p in OPTOUT_PHRASES)


def classify_with_ai(replies: list[ReplyRow], model: str, api_key: str) -> None:
    """Fill .category for replies not already OPT-OUT, using batched Anthropic
    calls. Keyword matching is too brittle for real reply text, so the
    NEGATIVE/NEUTRAL/POSITIVE split is delegated to the model."""
    try:
        import anthropic
    except ImportError:
        sys.stderr.write("  anthropic SDK not installed; skipping AI step.\n")
        return
    client = anthropic.Anthropic(api_key=api_key)

    tool = {
        "name": "record_reply_classifications",
        "description": "Record the category for each inbound SMS reply.",
        "input_schema": {
            "type": "object",
            "properties": {
                "classifications": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "index": {"type": "integer"},
                            "category": {"type": "string",
                                         "enum": list(REPLY_CATEGORIES)},
                        },
                        "required": ["index", "category"],
                    },
                }
            },
            "required": ["classifications"],
        },
    }
    guide = (
        "You classify inbound SMS replies from aged mortgage-protection "
        "insurance leads. Categories:\n"
        "- OPT-OUT: any request to stop/unsubscribe or 'remove me'.\n"
        "- NEGATIVE: not interested, already covered, wrong number, hostile.\n"
        "- NEUTRAL: identity/clarifying only ('who is this?', 'what is this').\n"
        "- POSITIVE: any question about coverage, price, or scheduling, or "
        "affirmative interest.\n"
        "Return exactly one category per reply via the tool, keyed by index."
    )

    pending = [r for r in replies if r.category != "OPT-OUT"]
    for i in range(0, len(pending), AI_BATCH):
        batch = pending[i:i + AI_BATCH]
        listing = "\n".join(
            f"[{j}] {r.body.strip()[:400]!r}" for j, r in enumerate(batch))
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=1500,
                thinking={"type": "disabled"},
                tools=[tool],
                tool_choice={"type": "tool",
                             "name": "record_reply_classifications"},
                messages=[{"role": "user",
                           "content": f"{guide}\n\nReplies:\n{listing}"}],
            )
        except Exception as e:  # noqa: BLE001 - keep the run going
            sys.stderr.write(f"  AI batch failed ({e}); left UNCLASSIFIED.\n")
            continue
        got = {}
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                for c in block.input.get("classifications", []):
                    got[c.get("index")] = c.get("category")
        for j, r in enumerate(batch):
            cat = got.get(j)
            if cat in REPLY_CATEGORIES:
                r.category = cat
        sys.stderr.write(
            f"  classified {min(i + AI_BATCH, len(pending))}/{len(pending)}\n")


# --------------------------------------------------------------------------- #
# Core analysis
# --------------------------------------------------------------------------- #
@dataclass
class Analysis:
    outbound: list[Msg] = field(default_factory=list)
    inbound: list[Msg] = field(default_factory=list)
    replies: list[ReplyRow] = field(default_factory=list)
    # touch -> stats
    touch_sends: dict = field(default_factory=lambda: defaultdict(int))
    touch_delivered: dict = field(default_factory=lambda: defaultdict(int))
    touch_replies: dict = field(default_factory=lambda: defaultdict(int))
    touch_positive: dict = field(default_factory=lambda: defaultdict(int))


def build_analysis(messages: list[Msg]) -> Analysis:
    a = Analysis()
    by_contact: dict[str, list[Msg]] = defaultdict(list)
    for m in messages:
        by_contact[m.contact_id].append(m)

    for contact, msgs in by_contact.items():
        msgs.sort(key=lambda x: x.ts)
        # Number outbound touches 1..N; remember each send's time + touch no.
        outbound_times: list[float] = []
        touch_no = 0
        for m in msgs:
            if m.direction == "outbound":
                touch_no += 1
                m_touch = touch_no
                outbound_times.append(m.ts)
                a.outbound.append(m)
                a.touch_sends[m_touch] += 1
                if m.status in ("delivered", "read"):
                    a.touch_delivered[m_touch] += 1
            else:
                a.inbound.append(m)

        # Attribute each inbound reply to the most recent preceding outbound.
        for m in msgs:
            if m.direction != "inbound":
                continue
            # index of last outbound at or before reply time
            prev = [t for t in outbound_times if t <= m.ts]
            if not prev:
                a.replies.append(ReplyRow(contact, m.ts, m.body, 0, False))
                continue
            attributed = len(prev)  # 1-based touch number
            t_prev = prev[-1]
            # subsequent send after the reply?
            nxt = [t for t in outbound_times if t > m.ts]
            t_next = nxt[0] if nxt else None
            # Insurance replies avg ~3.2 days, so a fast reply right after a
            # send that itself closely followed an earlier send is ambiguous;
            # so is a reply landing <24h before the next scheduled touch.
            day = 86400.0
            ambiguous = False
            if attributed >= 2 and (m.ts - t_prev) < day:
                ambiguous = True
            if t_next is not None and (t_next - m.ts) < day:
                ambiguous = True
            a.replies.append(
                ReplyRow(contact, m.ts, m.body, attributed, ambiguous))
    return a


def score_touches(a: Analysis) -> None:
    a.touch_replies.clear()
    a.touch_positive.clear()
    for r in a.replies:
        if r.attributed_touch <= 0 or r.ambiguous:
            continue
        a.touch_replies[r.attributed_touch] += 1
        if r.category == "POSITIVE":
            a.touch_positive[r.attributed_touch] += 1


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.2f}%" if d else "-"


def write_outputs(a: Analysis, outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)

    # --- denominator ---
    sent = len(a.outbound)
    delivered = sum(1 for m in a.outbound if m.status in ("delivered", "read"))
    failed = sum(1 for m in a.outbound
                 if m.status in ("failed", "undelivered", "error", "rejected"))
    err_breakdown: dict[str, int] = defaultdict(int)
    for m in a.outbound:
        if m.error_code:
            err_breakdown[m.error_code] += 1

    with open(os.path.join(outdir, "delivery_denominator.csv"), "w",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "count"])
        w.writerow(["sent", sent])
        w.writerow(["delivered", delivered])
        w.writerow(["failed_or_undelivered", failed])
        w.writerow([])
        w.writerow(["twilio_error_code", "count", "meaning"])
        for code, cnt in sorted(err_breakdown.items(),
                                key=lambda kv: -kv[1]):
            w.writerow([code, cnt, TWILIO_ERROR_NOTES.get(code, "")])

    # --- touch decay ---
    with open(os.path.join(outdir, "touch_decay.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["touch", "sends", "delivered", "replies_attributed",
                    "positive_replies", "reply_rate_on_sent",
                    "reply_rate_on_delivered", "positive_rate_on_sent",
                    "positive_rate_on_delivered"])
        for t in sorted(a.touch_sends):
            s = a.touch_sends[t]
            d = a.touch_delivered[t]
            rep = a.touch_replies[t]
            pos = a.touch_positive[t]
            w.writerow([t, s, d, rep, pos, pct(rep, s), pct(rep, d),
                        pct(pos, s), pct(pos, d)])

    # --- replies classified (headline artifact) ---
    with open(os.path.join(outdir, "replies_classified.csv"), "w",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["contact_id", "timestamp_utc", "attributed_touch",
                    "ambiguous", "category", "raw_reply_text"])
        for r in sorted(a.replies, key=lambda x: x.ts):
            w.writerow([
                r.contact_id,
                datetime.fromtimestamp(r.ts, timezone.utc).isoformat(),
                r.attributed_touch, r.ambiguous, r.category,
                r.body.replace("\n", " ").strip(),
            ])

    # --- length / encoding cross-tab ---
    # Positive-reply attribution to a send: a contact's first POSITIVE reply
    # is credited to the outbound message that immediately preceded it.
    pos_by_msg: dict[str, int] = defaultdict(int)
    # Build per-contact outbound list once more for crediting.
    ob_by_contact: dict[str, list[Msg]] = defaultdict(list)
    for m in a.outbound:
        ob_by_contact[m.contact_id].append(m)
    for lst in ob_by_contact.values():
        lst.sort(key=lambda x: x.ts)
    for r in a.replies:
        if r.category != "POSITIVE" or r.attributed_touch <= 0:
            continue
        lst = ob_by_contact.get(r.contact_id, [])
        if r.attributed_touch <= len(lst):
            pos_by_msg[lst[r.attributed_touch - 1].id] += 1

    seg_tab: dict = defaultdict(lambda: {"sends": 0, "pos": 0})
    char_tab: dict = defaultdict(lambda: {"sends": 0, "pos": 0})
    for m in a.outbound:
        info = segment_info(m.body)
        seg = info["segments"]
        seg_tab[seg]["sends"] += 1
        seg_tab[seg]["pos"] += pos_by_msg.get(m.id, 0)
        for lo, hi, label in CHAR_BUCKETS:
            if lo <= info["char_len"] <= hi:
                char_tab[label]["sends"] += 1
                char_tab[label]["pos"] += pos_by_msg.get(m.id, 0)
                break

    with open(os.path.join(outdir, "segment_crosstab.csv"), "w",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["dimension", "bucket", "sends", "positive_replies",
                    "positive_reply_rate"])
        for seg in sorted(seg_tab):
            d = seg_tab[seg]
            w.writerow(["segments", seg, d["sends"], d["pos"],
                        pct(d["pos"], d["sends"])])
        for _, _, label in CHAR_BUCKETS:
            d = char_tab[label]
            w.writerow(["char_count", label, d["sends"], d["pos"],
                        pct(d["pos"], d["sends"])])

    # --- raw outbound (segment detail, for spot-checking) ---
    with open(os.path.join(outdir, "outbound_messages.csv"), "w",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["message_id", "contact_id", "timestamp_utc", "status",
                    "error_code", "encoding", "char_len", "segments", "body"])
        for m in sorted(a.outbound, key=lambda x: x.ts):
            info = segment_info(m.body)
            w.writerow([
                m.id, m.contact_id,
                datetime.fromtimestamp(m.ts, timezone.utc).isoformat(),
                m.status, m.error_code, info["encoding"], info["char_len"],
                info["segments"], m.body.replace("\n", " ").strip(),
            ])


def print_summary(a: Analysis) -> None:
    sent = len(a.outbound)
    delivered = sum(1 for m in a.outbound if m.status in ("delivered", "read"))
    failed = sum(1 for m in a.outbound
                 if m.status in ("failed", "undelivered", "error", "rejected"))
    total_replies = len(a.replies)
    counts = defaultdict(int)
    for r in a.replies:
        counts[r.category] += 1
    ambiguous = sum(1 for r in a.replies if r.ambiguous)
    positive = counts["POSITIVE"]
    optout = counts["OPT-OUT"]

    line = "=" * 66
    print(f"\n{line}\nSMS CADENCE ANALYSIS\n{line}")
    print("\n[2] DENOMINATOR")
    print(f"  outbound sent .......... {sent}")
    print(f"  delivered .............. {delivered}  ({pct(delivered, sent)})")
    print(f"  failed/undelivered ..... {failed}  ({pct(failed, sent)})")
    err = defaultdict(int)
    for m in a.outbound:
        if m.error_code:
            err[m.error_code] += 1
    if err:
        print("  Twilio error codes:")
        for c, n in sorted(err.items(), key=lambda kv: -kv[1]):
            print(f"    {c} ({TWILIO_ERROR_NOTES.get(c,'?')}): {n}")

    print("\n[3] REPLY CLASSIFICATION")
    print(f"  total inbound replies .. {total_replies}")
    for cat in REPLY_CATEGORIES + ("UNCLASSIFIED",):
        if counts.get(cat):
            print(f"    {cat:<13} {counts[cat]}")
    print(f"  ambiguous (excluded from touch decay): {ambiguous}")
    print("\n  >>> HEADLINE  POSITIVE reply rate:")
    print(f"        on SENT ......... {positive}/{sent} = {pct(positive, sent)}")
    print(f"        on DELIVERED .... {positive}/{delivered} = "
          f"{pct(positive, delivered)}")
    print(f"  (raw blended reply rate, all inbound / sent = "
          f"{pct(total_replies, sent)}; opt-outs alone = "
          f"{pct(optout, sent)})")

    print("\n[1] TOUCH-LEVEL DECAY (ambiguous replies excluded)")
    print(f"  {'touch':<6}{'sends':>7}{'deliv':>7}{'replies':>9}"
          f"{'pos':>6}{'reply%/sent':>13}{'pos%/deliv':>12}")
    for t in sorted(a.touch_sends):
        s = a.touch_sends[t]
        d = a.touch_delivered[t]
        rep = a.touch_replies[t]
        pos = a.touch_positive[t]
        print(f"  {t:<6}{s:>7}{d:>7}{rep:>9}{pos:>6}"
              f"{pct(rep, s):>13}{pct(pos, d):>12}")

    print("\n[4] LENGTH / ENCODING")
    seg_tab = defaultdict(lambda: {"sends": 0})
    ucs = 0
    for m in a.outbound:
        info = segment_info(m.body)
        seg_tab[info["segments"]]["sends"] += 1
        if info["encoding"] == "UCS-2":
            ucs += 1
    for seg in sorted(seg_tab):
        print(f"    {seg} segment(s): {seg_tab[seg]['sends']} sends")
    print(f"    UCS-2 (non-GSM char, ~half the chars/segment): {ucs} "
          f"({pct(ucs, sent)} of sends)")
    print(f"\n  Full detail written to CSVs.\n{line}\n")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_date(s: str, end: bool = False) -> int:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if end and len(s) <= 10:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp() * 1000)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help="window start, YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="window end, YYYY-MM-DD")
    ap.add_argument("--location-id", default=os.environ.get("GHL_LOCATION_ID"))
    ap.add_argument("--workflows", default="all",
                    help='comma-separated names, or "all" (default). NOTE: GHL '
                         "messages carry no workflow id, so any value other "
                         'than "all" only filters if a payload happens to '
                         "expose attribution; otherwise all SMS are analyzed.")
    ap.add_argument("--out", default="out", help="output directory")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--no-ai", action="store_true",
                    help="skip Anthropic classifier (keyword opt-out only)")
    args = ap.parse_args()

    token = os.environ.get("GHL_API_KEY")
    if not token:
        sys.stderr.write("ERROR: set GHL_API_KEY in the environment/.env\n")
        return 2
    if not args.location_id:
        sys.stderr.write("ERROR: set GHL_LOCATION_ID or pass --location-id\n")
        return 2

    start_ms = parse_date(args.start)
    end_ms = parse_date(args.end, end=True)
    start_s, end_s = start_ms / 1000.0, end_ms / 1000.0

    ghl = GHL(token, args.location_id)
    sys.stderr.write(f"Pulling conversations {args.start}..{args.end} "
                     f"for location {args.location_id}\n")

    messages: list[Msg] = []
    n_conv = 0
    for conv in ghl.iter_conversations(start_ms):
        n_conv += 1
        if n_conv % 25 == 0:
            sys.stderr.write(f"  {n_conv} conversations, "
                             f"{len(messages)} SMS so far\n")
        cid = conv.get("id")
        for raw in ghl.iter_messages(cid):
            if not is_sms(raw):
                continue
            m = parse_msg(raw)
            if m is None:
                continue
            if not (start_s <= m.ts <= end_s):  # client-side window enforcement
                continue
            messages.append(m)

    sys.stderr.write(f"Collected {len(messages)} SMS across {n_conv} "
                     f"conversations.\n")
    if not messages:
        sys.stderr.write("No SMS in window. Check location id / dates / token.\n")
        return 1

    a = build_analysis(messages)

    # Classification: high-recall keyword opt-out first (compliance surface),
    # then the model for the nuanced remainder.
    for r in a.replies:
        if keyword_optout(r.body):
            r.category = "OPT-OUT"
    api_key = os.environ.get("ANTHROPIC_KEY") or os.environ.get(
        "ANTHROPIC_API_KEY")
    if args.no_ai or not api_key:
        if not args.no_ai:
            sys.stderr.write("No ANTHROPIC_KEY; opt-out keyword pass only.\n")
    else:
        sys.stderr.write("Classifying replies with "
                         f"{args.model}...\n")
        classify_with_ai(a.replies, args.model, api_key)

    score_touches(a)
    write_outputs(a, args.out)
    print_summary(a)
    sys.stderr.write(f"CSVs written to ./{args.out}/\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
