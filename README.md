# AI Field Developer / Solutions Engineer — Assessment (Python)

All 4 tasks, runnable, tested end-to-end. Each folder is self-contained
and its header comment explains the *idea* behind the code, not just
what the code does — read those before you submit, so you can defend
every choice in a follow-up interview.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# or run the whole suite:
bash run_all_tests.sh
```

## Task 1 — MCP server (`task1_mcp_server/`)

```bash
python server.py              # stdio transport
python test_client.py         # real MCP client session
```

Tools: `get_customer_record` (customer_id `CUST-XXXXX`), `trigger_refund`
(positive amount, reason ≥ 10 chars).

**What to say about it:**
- stdout is JSON-RPC only; every log goes to `stderr` (`logging`
  configured with `stream=sys.stderr`, no `print()` anywhere).
- Validation is two-layer: JSON Schema from Pydantic + Pydantic
  re-validation in the handler (catches the "10 spaces" reason edge case).
- SDK nuance: the official Python SDK surfaces tool failures as
  `CallToolResult(isError=True)`, not a wire-level `-32602` object.
  The handler still raises `McpError(-32602)` so the intent is explicit;
  wire-level `-32601` etc. still occur for protocol errors.

## Task 2 — MCP security gateway (`task2_mcp_gateway/`)

```bash
uvicorn mock_server:app --port 9001
uvicorn gateway:app --port 8002
python test_gateway.py
```

Demo tokens: `admin-token-123` → admin, `viewer-token-456` → viewer
(in production: verify a signed JWT, don't trust a raw token string).

**The security property that matters:** an unauthorized `admin_*`
`tools/call` returns `-32001` *without ever contacting* the downstream
server. The test proves this with the mock's `/__calls` counter.
`tools/list` is always forwarded (read-only discovery).

## Task 3 — Streaming PII guardrail (`task3_llm_gateway/`)

```bash
uvicorn gateway:app --port 8003
python test_gateway.py
curl -N -X POST localhost:8003/v1/chat/completions \
  -H 'Content-Type: application/json' -d '{"prompt": "hi"}'
```

Redacts emails, SSNs, and card numbers with `[REDACTED]`, streaming
SSE deltas as they arrive — no full response is ever accumulated.

**The trap this task is testing (and the bug we fixed while building):**
a fixed holdback buffer alone is *not* enough. If the cut lands inside
a pattern (`"SSN 123-"` / `"45-6789"`), neither half matches the regex
and the SSN leaks. The gateway therefore backtracks the cut to the
start of any trailing partial token (word, email fragment, or
digit/space/dash run for cards) before emitting. See `_safe_split()`.

## Task 4 — Rate-limit + fallback router (`task4_router/`)

```bash
uvicorn mock_providers:primary --port 9101
uvicorn mock_providers:secondary --port 9102
uvicorn router:app --port 8004
python test_router.py   # uses a 100-token test limit via env
```

- **Sliding window in SQLite (`router.db`):** each success inserts
  `(tenant, tokens, ts)`; each request first deletes rows older than
  60s, then sums the rest. Default limit 50,000 tokens/min/tenant.
- **Fallback:** primary 429 or a 3.0s timeout → secondary, once.
- **Sanitized errors:** clients only see
  `{"error": {"code", "message", "request_id"}}` — never upstream
  bodies, tracebacks, or SQL errors.
- Token counting is `len(prompt)//4` — a documented estimate, not a
  real tokenizer. Say that out loud if asked; pretending otherwise
  is the mistake.

## Honest limitations (say these before they find them)

- Task 2 demo tokens are a stand-in for real JWT verification.
- Task 3's upstream is a mock generator; swap `mock_upstream()` for a
  real provider stream — the redaction loop is unchanged.
- Task 4's providers are mocks with a `/__set_mode` test hook.
- No auth on the Task 3/4 demo endpoints beyond the tenant key in Task 4.

## Project layout

```
task1_mcp_server/   server.py, test_client.py
task2_mcp_gateway/  gateway.py, mock_server.py, test_gateway.py
task3_llm_gateway/  gateway.py, test_gateway.py
task4_router/       router.py, mock_providers.py, test_router.py
```
