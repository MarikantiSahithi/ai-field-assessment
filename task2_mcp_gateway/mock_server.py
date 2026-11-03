"""
Task 2 — downstream MOCK MCP server (what the gateway protects).

A tiny JSON-RPC server over HTTP. It is deliberately dumb: it trusts
whoever calls it. All auth lives in the gateway, not here — that is
the whole point of a gateway.

Endpoints:
  POST /mcp        JSON-RPC: tools/list, tools/call
  GET  /__calls    test-only counter: proves the gateway did / did not
                   forward a call. Not part of MCP.

Run:  uvicorn mock_server:app --port 9001
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="mock-mcp-server")
CALLS: list[dict] = []  # in-memory, test-only audit trail


@app.post("/mcp")
async def mcp(request: Request):
    body = await request.json()
    method, params, req_id = body.get("method"), body.get("params") or {}, body.get("id")

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": [
            {"name": "get_customer", "description": "Read-only lookup"},
            {"name": "admin_reset_key", "description": "ADMIN ONLY: rotates a key"},
            {"name": "admin_delete_user", "description": "ADMIN ONLY"},
        ]}}

    if method == "tools/call":
        CALLS.append(params)
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text",
                         "text": f"mock executed {params.get('name')}"}]}}

    return JSONResponse({"jsonrpc": "2.0", "id": req_id,
                         "error": {"code": -32601, "message": f"Method not found: {method}"}})


@app.get("/__calls")
async def calls():
    return {"count": len(CALLS), "calls": CALLS}


@app.get("/health")
async def health():
    return {"ok": True}
