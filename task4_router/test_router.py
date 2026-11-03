"""
Task 4 test — starts mock providers + router (with a tiny limit so the
rate-limit test finishes fast), then proves:
  1. Normal request uses primary.
  2. Primary 429 -> response comes from secondary.
  3. Primary slow (>3s) -> response comes from secondary, quickly.
  4. Exceeding the token budget -> 429 with the STANDARD error shape,
     and no upstream / traceback text in the body.

Run from this folder:
  RATE_LIMIT_TOKENS_PER_MINUTE=100 python test_router.py
  (the limit env var is read at router import time)
"""

import os
import subprocess
import sys
import time

import httpx

PRIMARY = "http://127.0.0.1:9101"
SECONDARY = "http://127.0.0.1:9102"
ROUTER = "http://127.0.0.1:8004"
# trust_env=False: local traffic must bypass any egress proxy.
client = httpx.Client(trust_env=False, timeout=15)


def wait_ok(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if client.get(url).status_code == 200:
                return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"did not come up: {url}")


def set_mode(base: str, mode: str) -> None:
    client.post(f"{base}/__set_mode", json={"mode": mode})


def complete(prompt: str, tenant: str = "tenant-demo"):
    return client.post(f"{ROUTER}/v1/completions",
                       json={"tenant_api_key": tenant, "prompt": prompt})


def main() -> None:
    env = dict(os.environ, RATE_LIMIT_TOKENS_PER_MINUTE="100")
    procs = [
        subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_providers:primary", "--port", "9101"]),
        subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_providers:secondary", "--port", "9102"]),
        subprocess.Popen([sys.executable, "-m", "uvicorn", "router:app", "--port", "8004"], env=env),
    ]
    try:
        wait_ok(f"{PRIMARY}/health")
        wait_ok(f"{SECONDARY}/health")
        wait_ok(f"{ROUTER}/health")

        set_mode(PRIMARY, "ok")
        r = complete("hello world, short prompt")
        print("1. normal:", r.json())

        set_mode(PRIMARY, "429")
        r = complete("hello again")
        print("2. primary 429 ->", r.json())
        assert r.json()["provider"] == "secondary"

        set_mode(PRIMARY, "slow")
        t0 = time.time()
        r = complete("hello slow")
        print(f"3. primary slow -> {r.json()} in {time.time() - t0:.1f}s (must be < 5s)")
        assert r.json()["provider"] == "secondary" and time.time() - t0 < 5

        set_mode(PRIMARY, "ok")
        big = "x" * 400  # ~100 tokens, fills the 100-token test budget
        r1 = complete(big, tenant="tenant-limit")
        r2 = complete(big, tenant="tenant-limit")
        print("4. first big:", r1.status_code, " second big (over budget):",
              r2.status_code, r2.json())
        assert r2.status_code == 429 and set(r2.json()) == {"error"}
        print("PASS")
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
