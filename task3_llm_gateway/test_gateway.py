"""
Task 3 test — starts the gateway, streams the response, and asserts:
  * no raw email / SSN / card digits appear anywhere in the stream
  * [REDACTED] appears (3 times: email, SSN, card)
  * chunks arrive incrementally (we count SSE events, not one blob)

Run from this folder:  python test_gateway.py
"""

import re
import subprocess
import sys
import time

import httpx

FORBIDDEN = ["john.doe", "example.com", "123-45-6789", "4111"]
# trust_env=False: local traffic must bypass any egress proxy.
client = httpx.Client(trust_env=False, timeout=10)


def wait_ok(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if client.get(url).status_code == 200:
                return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("gateway did not come up")


def main() -> None:
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "gateway:app", "--port", "8003"])
    try:
        wait_ok("http://127.0.0.1:8003/health")
        full, events = "", 0
        with client.stream("POST", "http://127.0.0.1:8003/v1/chat/completions",
                           json={"prompt": "hi"}) as resp:
            for line in resp.iter_lines():
                if line.startswith("data: ") and "[DONE]" not in line:
                    events += 1
                    full += httpx.Response(200, content=line[6:]).json()[
                        "choices"][0]["delta"]["content"]
        print(f"streamed in {events} SSE events (not one blob)")
        print("reassembled:", full)
        leaked = [p for p in FORBIDDEN if p in full]
        print("leaked PII:", leaked or "none")
        print("[REDACTED] count:", full.count("[REDACTED]"), "(expected 3)")
        assert not leaked and full.count("[REDACTED]") == 3 and events > 1
        print("PASS")
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
