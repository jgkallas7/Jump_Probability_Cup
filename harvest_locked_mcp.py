"""Extract 'predictions are locked' emails fetched via the Gmail MCP -> data/emails/*.html.

Companion to harvest_locked.py (the IMAP path). In a Claude session we fetch the
locked emails with the Gmail MCP get_thread tool; when an email is too large to
return inline the harness auto-saves the full thread to a tool-results JSON file:

    {"id": "...", "messages": [{"subject": "...", "htmlBody": "...", "date": "...", ...}]}

This reads those JSON files and writes each htmlBody to data/emails/ using the SAME
naming / sanitize / dedup rules as harvest_locked.py, so the corpus stays consistent
and a re-run never duplicates. Idempotent: skips any matchup already on disk.

Usage:
  python harvest_locked_mcp.py FILE.json [FILE2.json ...]   # explicit saved threads
  python harvest_locked_mcp.py --dir /path/to/tool-results  # glob get_thread-*.txt

The /harvest-locked slash command drives the Gmail MCP and then calls this.
"""
from __future__ import annotations

import glob
import html
import json
import os
import re
import sys
from datetime import datetime, timezone

# Reuse the canonical naming helpers so MCP-harvested files match IMAP-harvested ones.
from harvest_locked import EMAIL_DIR, _matchup  # noqa: E402


def _extract(path: str, existing: set[str]) -> str | None:
    """Return the filename written, or None if skipped (dup / not a locked email)."""
    try:
        msg = json.load(open(path))["messages"][0]
    except Exception as e:
        print(f"[skip] {os.path.basename(path)}: unreadable ({e})")
        return None
    subject = msg.get("subject", "")
    body = msg.get("htmlBody")
    match = _matchup(subject)
    if not match or not body:
        print(f"[skip] no matchup/body: {subject!r}")
        return None
    # same content guard as harvest_locked.py: must be the field-edge email
    if "edge vs the field" not in html.unescape(re.sub(r"<[^>]+>", " ", body)).lower():
        print(f"[skip] not a locked-edge email: {subject!r}")
        return None
    try:
        prefix = datetime.fromisoformat(msg["date"].replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except Exception:
        prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fname = f"{prefix}_{match}.html"
    if any(match in e for e in existing):  # dedup by matchup, any date prefix
        print(f"[have] {fname}")
        return None
    with open(os.path.join(EMAIL_DIR, fname), "w", encoding="utf-8") as f:
        f.write(body)
    existing.add(fname)
    print(f"[saved] {fname}")
    return fname


def main(argv: list[str]) -> int:
    if "--dir" in argv:
        d = argv[argv.index("--dir") + 1]
        paths = sorted(glob.glob(os.path.join(d, "mcp-claude_ai_Gmail-get_thread-*.txt")))
    else:
        paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print("usage: harvest_locked_mcp.py FILE.json [...]  |  --dir TOOL_RESULTS_DIR")
        return 2
    os.makedirs(EMAIL_DIR, exist_ok=True)
    existing = {p for p in os.listdir(EMAIL_DIR) if p.endswith(".html")}
    saved = sum(1 for p in paths if _extract(p, existing))
    print(f"\n=> {saved} new, {len(paths) - saved} skipped (searched {len(paths)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
