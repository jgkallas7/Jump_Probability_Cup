"""Submission + revision against SportsPredict.

submit: latest unsubmitted forecast per open question -> batch POST
        (<=50/call, per-entry failures reported, 409 -> recover via PATCH)
revise: re-run forecast engine, PATCH any submitted question whose fresh
        integer moved >= REVISE_THRESHOLD_PTS from what is on the books.
        Latest value at market close is what gets scored.

Usage:
  python submit.py submit [--hours 36] [--dry-run]
  python submit.py revise [--hours 3]  [--dry-run]
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import requests

import db
import forecast
from sp_client import SPClient

REVISE_THRESHOLD_PTS = 2  # don't churn PATCHes on sub-2-point moves


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lobby(conn) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key='sp_lobby_id'").fetchone()
    if not row:
        raise SystemExit("no sp_lobby_id in meta — run ingest_questions.py first")
    return row["value"]


def _migrate(conn) -> None:
    try:
        conn.execute("ALTER TABLE forecasts ADD COLUMN sp_prediction_id TEXT")
    except Exception:
        pass
    conn.commit()


def _pending(conn, hours: float):
    """Latest forecast row per question that has never been submitted.

    Horizon is an ISO string with 'T' built in Python — deadlines are stored
    ISO-T and sqlite's datetime() emits a space, which breaks string compares
    exactly at the day boundary."""
    from datetime import timedelta
    horizon = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    return conn.execute("""
        WITH latest AS (
          SELECT f.*, ROW_NUMBER() OVER (PARTITION BY qid ORDER BY ts DESC) rn
          FROM forecasts f)
        SELECT l.id fid, l.qid, l.final_prob, q.text, q.deadline
        FROM latest l JOIN questions q ON q.qid = l.qid
        WHERE l.rn = 1 AND l.submitted_at IS NULL AND q.status = 'open'
          AND NOT EXISTS (SELECT 1 FROM forecasts f2
                          WHERE f2.qid = l.qid AND f2.submitted_at IS NOT NULL)
          AND q.deadline <= ?
        ORDER BY q.deadline""", (horizon,)).fetchall()


def _to_int(p: float) -> int:
    return min(99, max(1, int(round(p * 100))))


def cmd_submit(conn, hours: float, dry: bool) -> None:
    c = SPClient()
    lobby_id = _lobby(conn)
    rows = _pending(conn, hours)
    if not rows:
        print("nothing to submit")
        return
    print(f"{len(rows)} predictions to submit{' (DRY RUN)' if dry else ''}")
    for i in range(0, len(rows), 50):
        chunk = rows[i:i + 50]
        batch = [{"market_id": r["qid"], "lobby_id": lobby_id,
                  "probability": _to_int(r["final_prob"])} for r in chunk]
        if dry:
            for r, b in zip(chunk, batch):
                print(f"  {b['probability']:3d}  {r['text']}")
            continue
        resp = c.submit_batch(batch)
        ok = {x["market_id"]: x for x in resp.get("results", []) if x.get("success")}
        fail = [x for x in resp.get("results", []) if not x.get("success")]
        ts = _now()
        for r in chunk:
            hit = ok.get(r["qid"])
            if hit:
                conn.execute(
                    "UPDATE forecasts SET submitted_at=?, submitted_prob=?, "
                    "sp_prediction_id=? WHERE id=?",
                    (ts, _to_int(r["final_prob"]) / 100,
                     (hit.get("trade") or {}).get("id"), r["fid"]))
        conn.commit()
        print(f"  batch {i//50 + 1}: {resp.get('succeeded')}/{resp.get('total')} ok")
        for x in fail:
            print(f"    FAILED {x.get('market_id')}: {x.get('error')}")


def cmd_revise(conn, hours: float, dry: bool) -> None:
    c = SPClient()
    sheet, _ = forecast.run(conn, hours)   # fresh forecasts for the window
    fresh = {row["qid"]: row for row in sheet}
    submitted = conn.execute("""
        WITH last_sub AS (
          SELECT f.*, ROW_NUMBER() OVER (PARTITION BY qid ORDER BY submitted_at DESC) rn
          FROM forecasts f WHERE submitted_at IS NOT NULL)
        SELECT s.qid, s.submitted_prob, s.sp_prediction_id, q.text
        FROM last_sub s JOIN questions q ON q.qid = s.qid
        WHERE s.rn = 1 AND q.status = 'open'""").fetchall()

    n_patched = 0
    ts = _now()
    for r in submitted:
        f = fresh.get(r["qid"])
        if not f or not r["sp_prediction_id"]:
            continue
        new_int = f["submit_int"]
        old_int = int(round((r["submitted_prob"] or 0) * 100))
        if abs(new_int - old_int) < REVISE_THRESHOLD_PTS:
            continue
        print(f"  {old_int:3d} -> {new_int:3d}  {r['text']}")
        if dry:
            continue
        try:
            c.revise(r["sp_prediction_id"], new_int)
        except requests.HTTPError as e:
            print(f"    PATCH failed: {e}")
            continue
        conn.execute(
            """UPDATE forecasts SET submitted_at=?, submitted_prob=?,
               sp_prediction_id=? WHERE qid=? AND ts=(
                 SELECT MAX(ts) FROM forecasts WHERE qid=?)""",
            (ts, new_int / 100, r["sp_prediction_id"], r["qid"], r["qid"]))
        conn.commit()
        n_patched += 1
    print(f"revised {n_patched} predictions{' (DRY RUN)' if dry else ''}")


if __name__ == "__main__":
    conn = db.init()
    _migrate(conn)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "submit"
    dry = "--dry-run" in sys.argv
    hours = float(sys.argv[sys.argv.index("--hours") + 1]) \
        if "--hours" in sys.argv else (36 if cmd == "submit" else 3)
    if cmd == "submit":
        cmd_submit(conn, hours, dry)
    elif cmd == "revise":
        cmd_revise(conn, hours, dry)
    else:
        raise SystemExit(f"unknown command: {cmd}")
