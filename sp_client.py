"""SportsPredict contest API client (documented at /probabilitycup/api).

Auth: bearer key sp_live_<...>, shown only at creation. Resolution order:
SP_API_KEY env var, then ~/.sp_api_key file (ext4, never OneDrive/repo).

Hard facts from the docs:
  - probabilities are INTEGERS 1-99 inclusive
  - one prediction per market per user; PATCH revises until market close;
    the latest value at close is what gets scored
  - batch endpoint takes 1-50, each validated independently
  - 60 req/min per IP across REST + MCP
"""

from __future__ import annotations

import os
from pathlib import Path

import requests

from config import SP_API_BASE

KEY_FILE = Path.home() / ".sp_api_key"


def _api_key() -> str:
    key = os.environ.get("SP_API_KEY", "")
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    if not key:
        raise RuntimeError(
            "SportsPredict key missing: set SP_API_KEY or write ~/.sp_api_key")
    return key


class SPClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {_api_key()}"

    def _req(self, method: str, path: str, **kw):
        r = self.s.request(method, f"{SP_API_BASE}{path}", timeout=30, **kw)
        r.raise_for_status()
        return r.json()

    # ---- discovery ----
    def events(self, limit: int = 20) -> list:
        return self._req("GET", "/events", params={"limit": limit})

    def lobbies(self, event_id: str) -> list:
        return self._req("GET", "/lobbies", params={"event_id": event_id})

    def join_lobby(self, lobby_id: str) -> dict:
        return self._req("POST", f"/lobbies/{lobby_id}/join")

    def matches(self, event_id: str, lobby_id: str) -> list:
        return self._req("GET", "/matches",
                         params={"event_id": event_id, "lobby_id": lobby_id})

    def markets(self, lobby_id: str, match_id: str | None = None) -> list:
        params = {"lobby_id": lobby_id}
        if match_id:
            params["match_id"] = match_id
        return self._req("GET", "/markets", params=params)

    # ---- predictions ----
    def submit(self, market_id: str, lobby_id: str, probability: int) -> dict:
        if not (1 <= probability <= 99 and isinstance(probability, int)):
            raise ValueError(f"probability must be int 1-99, got {probability!r}")
        return self._req("POST", "/predictions", json={
            "market_id": market_id, "lobby_id": lobby_id,
            "probability": probability})

    def revise(self, prediction_id: str, probability: int) -> dict:
        if not (1 <= probability <= 99 and isinstance(probability, int)):
            raise ValueError(f"probability must be int 1-99, got {probability!r}")
        return self._req("PATCH", f"/predictions/{prediction_id}",
                         json={"probability": probability})

    def submit_batch(self, predictions: list[dict]) -> dict:
        if not (1 <= len(predictions) <= 50):
            raise ValueError("batch is 1-50 predictions")
        return self._req("POST", "/predictions/batch",
                         json={"predictions": predictions})

    def my_predictions(self, lobby_id: str) -> list:
        """NB: probability reads back as 0-1 decimal, not the 1-99 integer."""
        return self._req("GET", "/predictions", params={"lobby_id": lobby_id})

    def results(self, lobby_id: str) -> list:
        return self._req("GET", "/results", params={"lobby_id": lobby_id})

    # ---- key management (bot rename; creation is UI-first) ----
    def keys(self) -> list:
        return self._req("GET", "/keys")

    def rename_key(self, key_id: str, label: str) -> dict:
        return self._req("PATCH", f"/keys/{key_id}", json={"label": label})


if __name__ == "__main__":
    c = SPClient()
    evs = c.events()
    print(f"{len(evs)} events visible")
    for e in evs:
        print(f"  {e.get('id')}  {e.get('type')}  {e.get('status')}  {e.get('title')}")
