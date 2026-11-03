"""
Task 2 test — starts mock + gateway as subprocesses, then proves:
  * viewer can tools/list and call a normal tool (forwarded)
  * viewer calling admin_reset_key gets -32001 AND mock call count
    does NOT increase (the security property that matters)
  * admin calling admin_reset_key succeeds (forwarded)

NOTE: all httpx clients here use trust_env=False so local
127.0.0.1 traffic bypasses any egress proxy set in the shell env.

Run from this folder:  python test_gateway.py
"""

import subprocess
import sys
import time

import httpx

BASE_GW = "http://127.0.0.1:8002/mcp"
BASE_MOCK = "http://127.0.0.1:9001"
client = httpx.Client(trust_env=False, timeout=5)


def wait_ok(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if client.get(url).status_code == 200:
                return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"server did not come up: {url}")


def rpc(method: str, params: dict | None = None, token: str | None = None, rid: int = 1):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(BASE_GW, json={"jsonrpc": "2.0", "id": rid, "method": method,
                                     "params": params or {}}, headers=headers).json()


def main() -> None:
    mock = subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_server:app", "--port", "9001"])
    gw = subprocess.Popen([sys.executable, "-m", "uvicorn", "gateway:app", "--port", "8002"])
    try:
        wait_ok(f"{BASE_MOCK}/health")
        wait_ok("http://127.0.0.1:8002/health")

        print("viewer tools/list:", rpc("tools/list", token="viewer-token-456")["result"]["tools"][0]["name"], "...")
        print("viewer normal call:", rpc("tools/call", {"name": "get_customer"}, "viewer-token-456")["result"])

        before = client.get(f"{BASE_MOCK}/__calls").json()["count"]
        blocked = rpc("tools/call", {"name": "admin_reset_key"}, "viewer-token-456")
        after = client.get(f"{BASE_MOCK}/__calls").json()["count"]
        print("viewer admin call:", blocked)
        print(f"mock calls before={before} after={after} -> "
              f"{'NOT FORWARDED (correct)' if before == after else 'FORWARDED (BUG!)'}")

        print("admin admin call:", rpc("tools/call", {"name": "admin_reset_key"}, "admin-token-123")["result"])
        print("no-token admin call:", rpc("tools/call", {"name": "admin_reset_key"})["error"])
        assert before == after and blocked["error"]["code"] == -32001
        print("PASS")
    finally:
        mock.terminate()
        gw.terminate()


if __name__ == "__main__":
    main()
