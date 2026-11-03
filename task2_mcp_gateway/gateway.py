"""
Task 2 — MCP Security Gateway Proxy
===================================

LEARNING IDEA — the gateway is a policy checkpoint, not a tool server:
  1. AUTHENTICATE:  Bearer token -> role. (Demo tokens below; in
     production verify a signed JWT / call your IdP — never trust a
     raw string a client invents.)
  2. AUTHORIZE BY METHOD + TOOL NAME, *before* forwarding:
       tools/list              -> always forward (read-only discovery)
       tools/call + admin_*    -> only role == "admin"
       tools/call + anything else -> forward
     The key test: an unauthorized admin_* call must NEVER reach the
     downstream server. Returning an error after forwarding would be
     too late — the side effect already happened.
  3. PROXY TRANSPARENTLY otherwise: same JSON-RPC id, same result,
     same error — the client should not be able to tell a gateway
     is in the middle, except for the -32001 it gets when blocked.

Error code: -32001 is the task's specified code for
"Unauthorized Tool Call" (server-error range -32000..-32099, so it
does not collide with standard -326xx codes).

Demo tokens (replace with real auth):
  admin-token-123  -> admin
  viewer-token-456 -> viewer

Run:
  (terminal 1) uvicorn mock_server:app --port 9001
  (terminal 2) uvicorn gateway:app --port 8002
Test: python test_gateway.py
"""

import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

DOWNSTREAM_URL = os.environ.get("DOWNSTREAM_URL", "http://127.0.0.1:9001/mcp")
TOKENS = {"admin-token-123": "admin", "viewer-token-456": "viewer"}
UNAUTHORIZED_TOOL_CALL = -32001

app = FastAPI(title="mcp-security-gateway")


def _role_from_header(request: Request) -> str | None:
    """'Bearer <token>' -> role, or None. Case-insensitive scheme."""
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return TOKENS.get(token.strip())


def _jsonrpc_error(req_id, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id,
                         "error": {"code": code, "message": message}})


@app.post("/mcp")
async def proxy(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _jsonrpc_error(None, -32700, "Parse error: body is not valid JSON")
    if not isinstance(body, dict) or "method" not in body:
        return _jsonrpc_error(body.get("id") if isinstance(body, dict) else None,
                              -32600, "Invalid Request: missing method")

    method, params, req_id = body["method"], body.get("params") or {}, body.get("id")

    # --- authorization checkpoint (BEFORE any forwarding) ---------------
    if method == "tools/call":
        tool_name = params.get("name", "")
        if isinstance(tool_name, str) and tool_name.startswith("admin_"):
            role = _role_from_header(request)
            if role != "admin":
                # Intercept here. Downstream is never contacted.
                return _jsonrpc_error(
                    req_id, UNAUTHORIZED_TOOL_CALL,
                    f"Unauthorized Tool Call: '{tool_name}' requires admin role")

    # --- transparent forward -------------------------------------------
    # trust_env=False: local downstream traffic must NOT go through
    # the machine's egress proxy (httpx otherwise honours HTTP_PROXY
    # and can mis-route 127.0.0.1). Same fix is in the test clients.
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
        upstream = await client.post(DOWNSTREAM_URL, json=body)
    # Return the upstream JSON verbatim (preserves id/result/error).
    return JSONResponse(content=upstream.json(), status_code=upstream.status_code)


@app.get("/health")
async def health():
    return {"ok": True, "downstream": DOWNSTREAM_URL}
