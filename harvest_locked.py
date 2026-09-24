"""Harvest 'predictions are locked' emails via Gmail IMAP -> data/emails/*.html.

This is the headless replacement for the manual 'fetch via the Gmail MCP in a
Claude session' step that left the calibration corpus stale (the MCP uses the
claude.ai connection a cron can't reach). Pure IMAP, no LLM, no claude.ai.

SETUP (one time): create a Gmail App Password (Google Account -> Security ->
2-Step Verification -> App passwords) and save it, 0600, OUTSIDE the repo:
    printf '%s' 'xxxx xxxx xxxx xxxx' > ~/.gmail_app_password
    chmod 600 ~/.gmail_app_password
Required: echo your address to ~/.gmail_address (or set GMAIL_ADDRESS).

Without the credential this exits 0 with a notice, so review.sh still runs on
the existing corpus. Idempotent: never overwrites an email already on disk.

Usage: python harvest_locked.py [--days 7]
"""
from __future__ import annotations

import email
import html
import imaplib
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

EMAIL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "emails")
PW_FILE = Path.home() / ".gmail_app_password"
ADDR_FILE = Path.home() / ".gmail_address"
DEFAULT_ADDR = os.environ.get("GMAIL_ADDRESS", "")
IMAP_HOST = "imap.gmail.com"


def _sanitize(s: str) -> str:
    s = (s.replace("ü", "u").replace("ç", "c").replace("ô", "o").replace("é", "e")
         .replace("í", "i").replace("ñ", "n").replace("ã", "a").replace("á", "a"))
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s


def _matchup(subject: str) -> str | None:
    m = re.search(r"locked:\s*(.+?)\s+vs\.?\s+(.+?)\s*$", subject, re.I)
    if not m:
        return None
    return f"{_sanitize(m.group(1))}_v_{_sanitize(m.group(2))}"


def _html_body(msg) -> str | None:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8",
                                          errors="replace")
        return None
    if msg.get_content_type() == "text/html":
        payload = msg.get_payload(decode=True)
        return payload.decode(errors="replace") if payload else None
    return None


def main(days: int) -> int:
    if not PW_FILE.exists():
        print(f"[harvest] no {PW_FILE} — skipping email harvest (see file header "
              f"for one-time setup). Review runs on the existing corpus.")
        return 0
    addr = ADDR_FILE.read_text().strip() if ADDR_FILE.exists() else DEFAULT_ADDR
    if not addr:
        print(f"[harvest] no {ADDR_FILE} or GMAIL_ADDRESS — skipping email harvest.")
        return 0
    pw = PW_FILE.read_text().strip()
    os.makedirs(EMAIL_DIR, exist_ok=True)
    existing = {p.name for p in Path(EMAIL_DIR).glob("*.html")}

    try:
        M = imaplib.IMAP4_SSL(IMAP_HOST)
        M.login(addr, pw)
        M.select("INBOX")
    except Exception as e:
        print(f"[harvest] IMAP login/select failed: {e} — skipping (corpus unchanged)")
        return 0

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    typ, data = M.search(None, 'FROM', '"noreply@sportspredict.com"',
                         'SUBJECT', '"locked"', 'SINCE', since)
    ids = data[0].split() if data and data[0] else []
    saved = skipped = 0
    for num in ids:
        typ, raw = M.fetch(num, "(RFC822)")
        if typ != "OK" or not raw or not raw[0]:
            continue
        msg = email.message_from_bytes(raw[0][1])
        subject = str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))
        if "locked" not in subject.lower():
            continue
        match = _matchup(subject)
        if not match:
            continue
        date_hdr = msg.get("Date", "")
        try:
            dt = email.utils.parsedate_to_datetime(date_hdr)
            prefix = dt.strftime("%Y-%m-%d")
        except Exception:
            prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fname = f"{prefix}_{match}.html"
        # dedup by matchup (any date prefix) so a re-fetch doesn't duplicate
        if any(match in e for e in existing) or fname in existing:
            skipped += 1
            continue
        body = _html_body(msg)
        if not body or "edge vs the field" not in html.unescape(re.sub(r"<[^>]+>", " ", body)).lower():
            continue
        with open(os.path.join(EMAIL_DIR, fname), "w", encoding="utf-8") as f:
            f.write(body)
        existing.add(fname)
        saved += 1
        print(f"[harvest] saved {fname}")
    M.logout()
    print(f"[harvest] done: {saved} new, {skipped} already had (searched {len(ids)} since {since})")
    return 0


if __name__ == "__main__":
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 7
    raise SystemExit(main(days))
