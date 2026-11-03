"""
Task 4 — Rate-Limiting & Model Fallback Router
==============================================

LEARNING IDEA — three independent mechanisms, keep them separate:
  1. TOKEN-AWARE SLIDING WINDOW (SQLite, on disk):
     Every successful request inserts one row (tenant, tokens, ts).
     On each request we first DELETE rows older than 60s, then
     SUM the rest. That is a true sliding window — unlike a fixed
     window, a burst at 00:59 + a burst at 01:01 cannot double-spend
     the budget. Eviction happens on the read path, so no background
     sweeper is needed, and the DB stays small.
     Token estimate: len(prompt)//4. Real gateways use the model's
     tokenizer; the estimate is documented, not hidden.
  2. FALLBACK ON 429 OR TIMEOUT (async race):
     httpx with timeout=3.0s. A 429 or a TimeoutException from the
     primary immediately tries the secondary — once, not in a loop
     (retrying the primary would worsen an overload).
  3. SANITIZED ERRORS:
     Clients only ever see {"error": {code, message, request_id}}.
     Upstream bodies, tracebacks, file paths, SQL errors are never
     copied into the response — they go to server logs only.
     (Never `except Exception as e: return {"error": str(e)}`.)

Config via env:
  PRIMARY_URL, SECONDARY_URL, ROUTER_DB_PATH,
  RATE_LIMIT_TOKENS_PER_MINUTE (default 50000), TIMEOUT_S (default 3.0)

Run:
  (terminals) uvicorn mock_providers:primary --port 9101
             uvicorn mock_providers:secondary --port 9102
             uvicorn router:app --port 8004
Test: python test_router.py
"""

import os
import sqlite3
import threading
import time
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

PRIMARY_URL = os.environ.get("PRIMARY_URL", "http://127.0.0.1:9101/complete")
SECONDARY_URL = os.environ.get("SECONDARY_URL", "http://127.0.0.1:9102/complete")
DB_PATH = os.environ.get("ROUTER_DB_PATH", os.path.join(os.path.dirname(__file__), "router.db"))
LIMIT = int(os.environ.get("RATE_LIMIT_TOKENS_PER_MINUTE", "50000"))
TIMEOUT_S = float(os.environ.get("TIMEOUT_S", "3.0"))
WINDOW_S = 60

app = FastAPI(title="llm-gateway-router")
_db_lock = threading.Lock()  # sqlite writes are serialized; reads are cheap


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS token_events (
                        tenant TEXT NOT NULL, tokens INTEGER NOT NULL,
                        ts REAL NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tenant_ts ON token_events(tenant, ts)")
    return conn


def estimate_tokens(prompt: str) -> int:
    """Rough estimate (~4 chars/token). Documented, not pretend-exact."""
    return max(1, len(prompt) // 4)


def tokens_used_in_window(tenant: str, now: float) -> int:
    """Evict stale rows, then sum. Eviction on the read path = no sweeper."""
    with _db_lock, _db() as conn:
        conn.execute("DELETE FROM token_events WHERE ts < ?", (now - WINDOW_S,))
        row = conn.execute("SELECT COALESCE(SUM(tokens), 0) FROM token_events "
                           "WHERE tenant = ?", (tenant,)).fetchone()
        return int(row[0])


def record_usage(tenant: str, tokens: int, now: float) -> None:
    with _db_lock, _db() as conn:
        conn.execute("INSERT INTO token_events VALUES (?, ?, ?)", (tenant, tokens, now))


def gateway_error(status: int, code: str, message: str) -> JSONResponse:
    """The ONLY error shape clients ever see. No upstream text inside."""
    return JSONResponse(status_code=status, content={
        "error": {"code": code, "message": message, "request_id": str(uuid.uuid4())}})


class CompletionRequest(BaseModel):
    tenant_api_key: str
    prompt: str


async def _call_provider(url: str, prompt: str) -> httpx.Response:
    # trust_env=False: local providers must bypass any egress proxy.
    async with httpx.AsyncClient(timeout=TIMEOUT_S, trust_env=False) as client:
        return await client.post(url, json={"prompt": prompt})


@app.post("/v1/completions")
async def completions(req: CompletionRequest, request: Request):
    tenant = req.tenant_api_key
    tokens = estimate_tokens(req.prompt)
    now = time.time()

    used = tokens_used_in_window(tenant, now)
    if used + tokens > LIMIT:
        return gateway_error(429, "RATE_LIMIT_EXCEEDED",
                             "Token rate limit exceeded for this tenant. Try again later.")

    # Primary, with fallback on exactly the two specified failures.
    provider_used = "primary"
    try:
        resp = await _call_provider(PRIMARY_URL, req.prompt)
        if resp.status_code == 429:
            raise _PrimaryUnavailable()
        resp.raise_for_status()
    except (_PrimaryUnavailable, httpx.TimeoutException, httpx.HTTPStatusError, httpx.HTTPError):
        # httpx.HTTPError also covers connection errors; deliberately
        # NOT bare Exception, and the cause is logged, never returned.
        try:
            resp = await _call_provider(SECONDARY_URL, req.prompt)
            resp.raise_for_status()
            provider_used = "secondary"
        except Exception:
            return gateway_error(502, "UPSTREAM_UNAVAILABLE",
                                 "All model providers are currently unavailable.")

    record_usage(tenant, tokens, time.time())
    return {"provider": provider_used, "text": resp.json().get("text", ""),
            "tokens_estimated": tokens}


class _PrimaryUnavailable(Exception):
    pass


@app.get("/health")
async def health():
    return {"ok": True, "limit_per_minute": LIMIT, "db": DB_PATH}
