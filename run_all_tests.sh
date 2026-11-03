#!/usr/bin/env bash
# Runs every task's test, in order. Run from this folder.
set -e
PY=./.venv/bin/python
echo "=== Task 1: MCP server ==="
(cd task1_mcp_server && $PY test_client.py 2>/dev/null)
echo "=== Task 2: MCP security gateway ==="
(cd task2_mcp_gateway && $PY test_gateway.py 2>/dev/null)
echo "=== Task 3: Streaming PII guardrail ==="
(cd task3_llm_gateway && $PY test_gateway.py 2>/dev/null)
echo "=== Task 4: Rate-limit + fallback router ==="
(cd task4_router && rm -f router.db && $PY test_router.py 2>/dev/null)
echo "ALL PASS"
