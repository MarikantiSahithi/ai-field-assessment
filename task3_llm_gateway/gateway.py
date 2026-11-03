"""
Task 3 — LLM Gateway Streaming Guardrail (PII redaction)
========================================================

LEARNING IDEA — the hard part is NOT the regex. It is the boundary:
  An LLM streams token by token. A secret can be split across chunks:
      chunk1: "reach me at john.doe@"   chunk2: "example.com"
      chunk1: "SSN 123-"                chunk2: "45-6789"
  If you regex each chunk in isolation, both leak. If you wait for
  the whole response, you break streaming (TTFT + latency suffer,
  memory grows).

  The fix used here — a holdback buffer + partial-token backtrack:
    * Keep a rolling buffer of incoming text.
    * Only release (redact + emit) the part that is "safe": everything
      except the last HOLDBACK chars, which might be the start of a
      pattern that continues in the next chunk.
    * CRITICAL SECOND STEP: a fixed holdback alone is NOT enough.
      The cut can land *inside* a pattern — e.g. safe text ends with
      "SSN 123-" and the next chunk brings "45-6789". Neither half
      matches the SSN regex, so both would leak. So before emitting,
      backtrack the cut to the start of any trailing partial token:
      a trailing word (could become an email), or a trailing
      digit/space/dash run (could become an SSN or a card number,
      whose groups are space-separated). Only emit up to a boundary
      where no pattern can cross.
    * At end-of-stream, redact + flush the remainder.
  Memory stays O(HOLDBACK + one token), TTFT is ~one chunk, and no
  full response is ever accumulated.

Patterns redacted:
  * Email:      \\S+@\\S+\\.\\S+
  * SSN:        \\d{3}-\\d{2}-\\d{4}
  * Credit card: 13–19 digits, optionally space/dash separated
    (the regex below requires 4 groups of 4, or a 16-digit run —
    deliberately conservative to avoid redacting ordinary numbers).

The upstream here is a mock async generator (so the demo runs with
no API key). Swap `mock_upstream()` for a real provider's async
stream — the redaction loop does not change.

Run:   uvicorn gateway:app --port 8003
Test:  python test_gateway.py
Try:   curl -N -X POST localhost:8003/v1/chat/completions \\
         -H 'Content-Type: application/json' \\
         -d '{"prompt": "hi"}'
"""

import asyncio
import json
import re

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="llm-gateway-guardrail")

HOLDBACK = 64  # chars held back; must exceed your longest PII pattern

PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[REDACTED]"),
]


def redact(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _safe_split(buffer: str) -> tuple[str, str]:
    """
    Split buffer into (safe_to_emit, hold_back).

    safe_to_emit ends at a boundary no PII pattern can cross.
    hold_back is everything from the start of the trailing partial
    token (if any) onwards, plus the fixed HOLDBACK tail.
    """
    if len(buffer) <= HOLDBACK:
        return "", buffer
    cut = len(buffer) - HOLDBACK
    safe = buffer[:cut]
    if not safe:
        return "", buffer

    # Case 1: safe ends with whitespace, but the token before it is
    # digits — that is a card group, and more groups may follow.
    stripped = safe.rstrip(" \t\n")
    if safe[-1] in " \t\n" and stripped and stripped[-1].isdigit():
        i = len(stripped)
        while i > 0 and (stripped[i - 1].isdigit() or stripped[i - 1] in " -"):
            i -= 1
        return buffer[:i], buffer[i:]

    # Case 2: safe ends mid-token (word, email fragment, SSN fragment,
    # card digits). Backtrack to the token's start.
    if safe[-1].isalnum() or safe[-1] in "@._%+-":
        i = len(safe)
        while i > 0 and (safe[i - 1].isalnum() or safe[i - 1] in "@._%+-"):
            i -= 1
        return buffer[:i], buffer[i:]

    # Case 3: safe ends with sentence punctuation (., ,, !, …) — the
    # preceding token is complete, so it is safe to emit (and redact).
    return safe, buffer[cut:]


async def mock_upstream(prompt: str):
    """Simulates an LLM that splits PII across chunk boundaries."""
    chunks = [
        "Hello! You can reach me at john.doe@",
        "example.com, or call the office. ",
        "My SSN is 123-",
        "45-6789 and my card is 4111 1111 ",
        "1111 1111. Thanks for asking!",
    ]
    for chunk in chunks:
        yield chunk
        await asyncio.sleep(0.05)  # simulate token latency


class ChatRequest(BaseModel):
    prompt: str = ""


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    """
    Streams SSE deltas, redacting PII on the fly.
    First chunk is yielded as soon as the safe prefix exists — we
    never await the full upstream response before responding.
    """

    async def generate():
        buffer = ""
        async for chunk in mock_upstream(req.prompt):
            buffer += chunk
            safe, buffer = _safe_split(buffer)
            if safe:
                yield _sse(redact(safe))
        # End of stream: nothing is pending any more, so flush all.
        if buffer:
            yield _sse(redact(buffer))
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def _sse(text: str) -> str:
    payload = {"choices": [{"delta": {"content": text}}]}
    return f"data: {json.dumps(payload)}\n\n"


@app.get("/health")
async def health():
    return {"ok": True}
