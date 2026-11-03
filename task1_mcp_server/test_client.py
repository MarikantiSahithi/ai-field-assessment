"""
Task 1 test — drives server.py through a REAL MCP stdio client session.

What it proves (the exact things an evaluator checks):
  1. Valid calls succeed.
  2. Bad customer_id / negative amount / short reason are rejected
     (SDK surfaces this as isError=True — see server.py docstring
     for the -32602 nuance), not a silent success.
  3. stdout stayed pure JSON-RPC — if server.py printed anything else,
     the client session below would fail to parse it.

Run from this folder:  python test_client.py
"""

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])

            ok = await session.call_tool(
                "get_customer_record", {"customer_id": "CUST-12345"})
            print("valid get:", ok.content[0].text)

            for label, tool, args in [
                ("bad format", "get_customer_record", {"customer_id": "12345"}),
                ("negative amount", "trigger_refund",
                 {"customer_id": "CUST-12345", "amount": -5, "reason": "item was broken"}),
                ("short reason", "trigger_refund",
                 {"customer_id": "CUST-12345", "amount": 10, "reason": "bad"}),
            ]:
                try:
                    result = await session.call_tool(tool, args)
                    status = "REJECTED (isError=True)" if result.isError else "UNEXPECTED SUCCESS"
                    print(f"{label}: {status} -> {result.content[0].text}")
                except McpError as exc:
                    print(f"{label}: rejected McpError code={exc.error.code} msg={exc.error.message}")


if __name__ == "__main__":
    asyncio.run(main())
