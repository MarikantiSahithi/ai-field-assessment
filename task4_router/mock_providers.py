"""
Task 4 — mock primary / secondary LLM providers (for local testing).

Each exposes:
  POST /complete  {"prompt": ...} -> {"text": "..."}
  POST /__set_mode {"mode": "ok"|"429"|"slow"}
    ok   -> 200 immediately
    429  -> 429 Too Many Requests (triggers router fallback)
    slow -> sleeps 5s, longer than the router's 3s timeout (triggers fallback)

Run both:
  uvicorn mock_providers:primary --port 9101
  uvicorn mock_providers:secondary --port 9102
"""

import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


def make_app(name: str) -> FastAPI:
    app = FastAPI(title=f"mock-{name}")
    state = {"mode": "ok"}

    class Req(BaseModel):
        prompt: str = ""

    @app.post("/complete")
    async def complete(req: Req):
        if state["mode"] == "429":
            return JSONResponse({"error": "rate limited upstream"}, status_code=429)
        if state["mode"] == "slow":
            await asyncio.sleep(5)
        return {"text": f"[{name}] answer to: {req.prompt[:60]}"}

    @app.post("/__set_mode")
    async def set_mode(request: Request):
        state["mode"] = (await request.json()).get("mode", "ok")
        return {"mode": state["mode"]}

    @app.get("/health")
    async def health():
        return {"ok": True, "provider": name, "mode": state["mode"]}

    return app


primary = make_app("primary")
secondary = make_app("secondary")
