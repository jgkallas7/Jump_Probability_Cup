"""Kalshi World Cup market reader — LIVE prediction-market prices for the exact
question types the contest asks (corners, totals, score-or-assist, halves...).

Kalshi lists ~99 FIFA WC series (KXWC*). The summary yes_bid/last fields are
empty until a trade prints, but the ORDERBOOK carries live two-sided quotes —
the mid is a real-money crowd probability. Corners/totals quote tight (~3c);
player props wide. Because Kalshi is itself a crowd prediction market, its mid
is the closest LIVE proxy we have for the SP field (which only arrives post-
close in the locked email).

Read-only and UNAUTHENTICATED: Kalshi's /markets and /markets/{t}/orderbook are
public, so no API key and NO trading credential are needed (or touched). Never
trades. This is deliberate — keeping the trading credential off the bot's path removes
it as an exfiltration target for the self-improvement agent.

  python kalshi_wc.py "France" "Senegal" 2026-06-16
"""
from __future__ import annotations

import base64
import sys
import time
from datetime import datetime

import requests

BASE = "https://api.elections.kalshi.com"
PREFIX = "/trade-api/v2"

# FIFA trigrams Kalshi uses in tickers (KXWCTOTAL-26JUN16FRASEN-3). Built from
# ingest_questions.FIFA_CODES so the two stay in sync.
def _trigrams() -> dict[str, str]:
    from ingest_questions import FIFA_CODES
    inv = {v: k for k, v in FIFA_CODES.items()}
    # Odds API / SP name aliases -> the canonical FIFA_CODES value
    inv.setdefault("South Korea", "KOR")
    inv.setdefault("Turkey", "TUR")
    inv.setdefault("USA", "USA")
    inv.setdefault("Czech Republic", "CZE")
    return inv


class KalshiRO:
    """Read-only Kalshi client — UNAUTHENTICATED. Kalshi's /markets list and
    /markets/{ticker}/orderbook are public (verified 2026-06-16), so we need no
    API key and crucially NO trading credential on the host. This removes the
    security-review HIGH: there is no Kalshi secret for any process to read or
    exfiltrate. GET only; never trades."""

    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0"
        self._mk_cache: dict[str, list] = {}   # series_ticker -> markets list

    def _get(self, path: str, params=None) -> dict:
        full = PREFIX + path
        for attempt in range(4):
            r = self.s.get(BASE + full, params=params, timeout=15)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1)); continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()

    def markets(self, series: str, status: str = "open") -> list:
        if series in self._mk_cache:           # series list is match-independent
            return self._mk_cache[series]
        out, cursor = [], ""
        while True:
            p = {"series_ticker": series, "status": status, "limit": 200}
            if cursor:
                p["cursor"] = cursor
            d = self._get("/markets", p)
            out += d.get("markets", [])
            cursor = d.get("cursor", "")
            if not cursor:
                break
        self._mk_cache[series] = out
        return out

    def mid(self, ticker: str) -> float | None:
        """Orderbook mid as a probability in [0,1], or None if no book.

        Handles V3 (orderbook_fp: yes_dollars/no_dollars, prices as $ strings)
        and V2 (orderbook: yes/no, prices in cents). yes_ask = 1 - best_no_bid
        (selling YES == buying NO on the other side)."""
        d = self._get(f"/markets/{ticker}/orderbook")
        if "orderbook_fp" in d:
            book = d["orderbook_fp"]
            yes = [(float(p), float(q)) for p, q in (book.get("yes_dollars") or [])]
            no = [(float(p), float(q)) for p, q in (book.get("no_dollars") or [])]
            yes_bid = max((p for p, _ in yes), default=None)
            no_bid = max((p for p, _ in no), default=None)
            yes_ask = (1.0 - no_bid) if no_bid is not None else None
        else:
            ob = d.get("orderbook", d)
            yes = ob.get("yes") or []
            no = ob.get("no") or []
            yes_bid = (max(l[0] for l in yes) / 100.0) if yes else None
            no_bid = (max(l[0] for l in no) / 100.0) if no else None
            yes_ask = (1.0 - no_bid) if no_bid is not None else None
        if yes_bid is not None and yes_ask is not None:
            return (yes_bid + yes_ask) / 2
        return yes_bid if yes_bid is not None else yes_ask


def match_code(home: str, away: str, date: str) -> list[str]:
    """Candidate Kalshi match codes, e.g. '26JUN16FRASEN'. Kalshi dates the
    ticker by US-local day, so a late-UTC kickoff (02:00Z) lands on the prior
    US date — span date-1/date/date+1 and both team orders to be safe."""
    from datetime import timedelta
    tri = _trigrams()
    h, a = tri.get(home), tri.get(away)
    if not h or not a:
        return []
    base = datetime.fromisoformat(date.replace("Z", ""))
    codes = []
    for delta in (-1, 0, 1):
        d = (base + timedelta(days=delta)).strftime("%y%b%d").upper()
        codes += [f"{d}{h}{a}", f"{d}{a}{h}"]
    return codes


def corners_total(client: KalshiRO, home, away, date) -> dict[int, float]:
    """{N: P(N+ total corners)} from KXWCCORNERS orderbook mids."""
    codes = match_code(home, away, date)
    mks = client.markets("KXWCCORNERS")
    out = {}
    for m in mks:
        tk = m["ticker"]
        if any(c and c in tk for c in codes):
            n = tk.rsplit("-", 1)[-1]
            if n.isdigit():
                p = client.mid(tk)
                if p is not None:
                    out[int(n)] = round(p, 4)
    return dict(sorted(out.items()))


def totals(client: KalshiRO, home, away, date) -> dict[int, float]:
    codes = match_code(home, away, date)
    out = {}
    for m in client.markets("KXWCTOTAL"):
        tk = m["ticker"]
        if any(c and c in tk for c in codes):
            n = tk.rsplit("-", 1)[-1]
            if n.isdigit():
                p = client.mid(tk)
                if p is not None:
                    out[int(n)] = round(p, 4)
    return dict(sorted(out.items()))


def totals_half(client: KalshiRO, home, away, date, half: str) -> dict[int, float]:
    """{N: P(N+ goals in <half>)} from KXWC1HTOTAL / KXWC2HTOTAL mids.

    half in {'h1','h2'}. Ticker '...-N' subtitles as 'Over (N-0.5) goals', i.e.
    the mid IS P(N or more) — same ladder convention as the full-match totals()."""
    series = "KXWC1HTOTAL" if half == "h1" else "KXWC2HTOTAL"
    codes = match_code(home, away, date)
    out = {}
    for m in client.markets(series):
        tk = m["ticker"]
        if any(c and c in tk for c in codes):
            n = tk.rsplit("-", 1)[-1]
            if n.isdigit():
                p = client.mid(tk)
                if p is not None:
                    out[int(n)] = round(p, 4)
    return dict(sorted(out.items()))


def _norm_name(s: str) -> str:
    """Player-name match key: NFKD-fold accents to ASCII *then* strip non-letters.
    Without the fold, 'Gyökeres' -> 'gykeres' (umlaut dropped) but Kalshi's
    'Gyokeres' -> 'gyokeres', so accented stars silently miss their anchor."""
    import re as _re
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return _re.sub(r"[^a-z]", "", s.lower())


def score_or_assist(client: KalshiRO, home, away, date) -> dict[str, float]:
    """{normalized player name: P(score or assist)} from KXWCSOA mids."""
    codes = match_code(home, away, date)
    out = {}
    for m in client.markets("KXWCSOA"):
        if any(c and c in m["ticker"] for c in codes):
            name = (m.get("yes_sub_title") or m.get("title") or "").split(":")[0]
            key = _norm_name(name)
            if key:
                p = client.mid(m["ticker"])
                if p is not None:
                    out[key] = round(p, 4)
    return out


def match_book(client: KalshiRO, home, away, date, extras: bool = False) -> dict:
    """All usable Kalshi ladders for a match, fetched once. Robust: any series
    that errors just yields {} so a partial outage never sinks the others.

    extras=True also fetches the 1H/2H goal-total ladders (KXWC1HTOTAL/2HTOTAL).
    Off by default so existing callers (derive alpha path) are unchanged; the
    forecast book path passes it from the WC_KALSHI_HTOTAL flag."""
    series = [("corners", corners_total), ("totals", totals),
              ("soa", score_or_assist)]
    if extras:
        series += [("h1tot", lambda c, h, a, d: totals_half(c, h, a, d, "h1")),
                   ("h2tot", lambda c, h, a, d: totals_half(c, h, a, d, "h2"))]
    book = {}
    for name, fn in series:
        try:
            book[name] = fn(client, home, away, date)
        except Exception:
            book[name] = {}
    return book


def price_question(text, book) -> tuple[float, str] | None:
    """Price a contest question from a pre-fetched Kalshi `match_book`.
    Returns (prob, reason) or None. Kalshi mid = live crowd probability."""
    import re as _re
    t = text.strip()
    m = _re.search(r"(\d+) or more total corner kicks", t)
    if m and book.get("corners"):
        p = book["corners"].get(int(m.group(1)))
        return (p, f"kalshi corners P(>={m.group(1)})") if p is not None else None
    m = _re.search(r"match have (\d+) or (more|fewer) total goals", t)
    if m and book.get("totals"):
        n = int(m.group(1))
        if m.group(2) == "more":
            p = book["totals"].get(n)
        else:                                  # 'N or fewer' = 1 - P(N+1 or more)
            up = book["totals"].get(n + 1)
            p = (1 - up) if up is not None else None
        return (p, f"kalshi totals {m.group(1)} or {m.group(2)}") if p is not None else None
    # half goal-totals: 'Will the first/second half have N or (more|fewer) total
    # goals?' -> KXWC1HTOTAL/2HTOTAL ladder (present only when match_book(extras)).
    m = _re.search(r"(first|second) half have (\d+) or (more|fewer) total goals", t, _re.I)
    if m:
        key = "h1tot" if m.group(1).lower() == "first" else "h2tot"
        ladder = book.get(key) or {}
        n = int(m.group(2))
        if m.group(3).lower() == "more":
            p = ladder.get(n)
        else:
            up = ladder.get(n + 1)
            p = (1 - up) if up is not None else None
        if p is not None:
            half = "1H" if key == "h1tot" else "2H"
            return p, f"kalshi {half} total {n} or {m.group(3).lower()}"
    m = _re.search(r"Will (.+?) score or assist", t)
    if m and book.get("soa"):
        key = _norm_name(m.group(1))
        # exact or token-subset match against Kalshi player keys (accent-folded)
        for kk, p in book["soa"].items():
            if kk == key or (len(key) > 5 and (key in kk or kk in key)):
                return p, "kalshi score-or-assist"
    return None


if __name__ == "__main__":
    home, away, date = sys.argv[1], sys.argv[2], sys.argv[3]
    c = KalshiRO()
    print(f"=== Kalshi WC mids: {home} vs {away} ({date}) ===")
    print("match codes:", match_code(home, away, date))
    print("\ntotal corners P(N+):", corners_total(c, home, away, date))
    print("total goals   P(N+):", totals(c, home, away, date))
