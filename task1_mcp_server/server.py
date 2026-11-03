"""
Task 1 — Custom MCP Server (stdio, strict validation)
====================================================

LEARNING IDEA — why this file looks the way it does:
  * MCP over stdio is just JSON-RPC 2.0 lines on stdin/stdout.
    The client (Claude / an agent) reads *every* stdout line as JSON-RPC.
    One stray print() / console.log() corrupts the whole session.
    => ALL human logs go to stderr. Never print() in this file.
  * Validation happens BEFORE any business logic, in two layers:
    (1) the tool's JSON Schema (generated from Pydantic) is checked by
    the SDK, (2) the handler re-validates with Pydantic (defence in
    depth, and it catches the reason-whitespace edge case the schema
    alone cannot). A bad shape is conceptually JSON-RPC -32602
    (Invalid params).
    IMPORTANT SDK NUANCE (worth knowing for the interview): the
    official Python SDK surfaces tool-level failures as
    CallToolResult(isError=True), NOT as a wire-level JSON-RPC error
    object — that is the MCP-spec behaviour for tools/call. Wire-level
    JSON-RPC errors (-32601 Method not found, -32700 Parse error)
    still occur for protocol-level problems. So an evaluator checking
    "was it rejected?" should check isError=True, not just error.code.
    Our handler still raises McpError(-32602) for validation so the
    intent/code is explicit in the source.
  * We use the low-level `mcp.server.Server` (not FastMCP) on purpose:
    list_tools / call_tool handlers make the schema + validation
    flow visible instead of hidden behind decorators.

Run:
    python server.py          # speaks MCP on stdin/stdout, logs on stderr
Test:
    python test_client.py     # drives it through a real MCP client session
"""

import json
import logging
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS, ErrorData, TextContent, Tool
from pydantic import BaseModel, Field, ValidationError, field_validator

# --- stderr-only logging -------------------------------------------------
# logging defaults to stderr, but make it explicit so a future edit
# cannot accidentally redirect it to stdout.
logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("customer-mcp")

# --- Fake data store (replace with a real DB in production) --------------
CUSTOMERS: dict[str, dict[str, Any]] = {
    "CUST-12345": {"customer_id": "CUST-12345", "name": "Asha Rao",
                   "email": "asha@example.com", "tier": "gold"},
    "CUST-00001": {"customer_id": "CUST-00001", "name": "Ben Carter",
                   "email": "ben@example.com", "tier": "standard"},
}


# --- Strict input schemas ------------------------------------------------
# CUST-XXXXX means literally: "CUST-" + exactly 5 digits.
# ^...$ anchors matter: without them "XXCUST-12345YY" would pass.
class CustomerId(BaseModel):
    customer_id: str = Field(pattern=r"^CUST-\d{5}$")


class RefundRequest(BaseModel):
    customer_id: str = Field(pattern=r"^CUST-\d{5}$")
    amount: float = Field(gt=0, description="Positive refund amount, e.g. 25.50")
    reason: str = Field(min_length=10)

    @field_validator("reason")
    @classmethod
    def reason_must_be_meaningful(cls, v: str) -> str:
        # min_length=10 alone allows "          " (10 spaces).
        # strip() closes that edge case.
        if not v.strip():
            raise ValueError("reason must contain non-whitespace text")
        return v


def _validation_error(exc: ValidationError) -> McpError:
    """Turn Pydantic errors into a standard -32602 JSON-RPC error."""
    details = "; ".join(
        f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()
    )
    return McpError(ErrorData(code=INVALID_PARAMS,
                              message=f"Invalid params: {details}",
                              data={"validation_errors": exc.errors()}))


# --- MCP server ----------------------------------------------------------
server = Server("customer-service")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Advertise the two tools + their JSON Schemas to the client."""
    return [
        Tool(
            name="get_customer_record",
            description="Fetch a customer record by ID (format CUST-XXXXX).",
            inputSchema=CustomerId.model_json_schema(),
        ),
        Tool(
            name="trigger_refund",
            description="Issue a refund. Amount must be > 0, reason >= 10 chars.",
            inputSchema=RefundRequest.model_json_schema(),
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    log.info("call_tool name=%s arguments=%s", name, arguments)
    arguments = arguments or {}

    if name == "get_customer_record":
        try:
            req = CustomerId.model_validate(arguments)
        except ValidationError as exc:
            raise _validation_error(exc)
        record = CUSTOMERS.get(req.customer_id)
        if record is None:
            # Not a validation failure, so not -32602. Use a server-side
            # MCP error code and a message that leaks no internal detail.
            raise McpError(ErrorData(code=-32000,
                                     message=f"Customer not found: {req.customer_id}"))
        return [TextContent(type="text", text=json.dumps(record))]

    if name == "trigger_refund":
        try:
            req = RefundRequest.model_validate(arguments)
        except ValidationError as exc:
            raise _validation_error(exc)
        if req.customer_id not in CUSTOMERS:
            raise McpError(ErrorData(code=-32000,
                                     message=f"Customer not found: {req.customer_id}"))
        # In production this would call a payments service, idempotently.
        result = {"status": "refunded", "customer_id": req.customer_id,
                  "amount": req.amount, "reason": req.reason}
        return [TextContent(type="text", text=json.dumps(result))]

    raise McpError(ErrorData(code=-32601, message=f"Unknown tool: {name}"))


async def main() -> None:
    # stdio_server wires stdin -> server, server -> stdout (JSON-RPC only).
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream,
                         server.create_initialization_options())


if __name__ == "__main__":
    import anyio

    log.info("customer-service MCP server starting on stdio (logs -> stderr)")
    anyio.run(main)
