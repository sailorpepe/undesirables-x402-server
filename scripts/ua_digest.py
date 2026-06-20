#!/usr/bin/env python3
"""
ua_digest.py — read-only digest of ~/logs/oracle_requests.jsonl (the request-logging
middleware's output). Answers ONE question with receipts: which named agents/crawlers
hit the oracle, and did any real (non-test) caller actually consume the FREE forecast
endpoints. Deterministic, stdlib only. Append the output to a daily log via cron.

Usage: python3 ua_digest.py [--hours 24]    (default: last 24h; 0 = all-time)
"""
import json, os, argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

LOG = os.path.expanduser("~/logs/oracle_requests.jsonl")
TEST_IPS = {"127.0.0.1", "::1"}            # localhost self-tests
FORECAST = "/api/v1/forecast"


def is_browser(u):
    u = (u or "").lower()
    return ("mozilla" in u and any(b in u for b in ("chrome", "safari", "firefox", "gecko"))
            and "bot" not in u and "crawler" not in u)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0, help="window in hours (0 = all-time)")
    a = ap.parse_args()
    if not os.path.exists(LOG):
        print("no request log yet:", LOG); return

    cutoff = None
    if a.hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=a.hours)
    recs = []
    for line in open(LOG, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            if cutoff:
                ts = datetime.fromisoformat(r["ts"])
                if ts < cutoff:
                    continue
            recs.append(r)
        except Exception:
            continue

    win = f"last {a.hours:g}h" if a.hours > 0 else "all-time"
    print("=" * 64)
    print(f"ORACLE UA DIGEST — {datetime.now(timezone.utc).isoformat(timespec='seconds')} — window: {win}")
    if not recs:
        print("  (no requests in window)"); print("=" * 64); return
    ips = {r.get("ip") for r in recs}
    print(f"requests: {len(recs)} | unique IPs: {len(ips)} | "
          f"span {recs[0]['ts']} -> {recs[-1]['ts']}")

    ua = Counter(r.get("ua") or "(empty)" for r in recs)
    print("\n-- top user-agents (verbatim) --")
    for u, n in ua.most_common(20):
        print(f"  {n:>5}  {u[:120]}")

    human = sum(n for u, n in ua.items() if is_browser(u))
    empty = ua.get("(empty)", 0)
    print(f"\n-- split --  browser(human): {human} | non-browser(bot/agent): "
          f"{len(recs) - human - empty} | empty-UA: {empty}")

    surfaces = ["/.well-known/x402", "/.well-known/ai-plugin.json", "/.well-known/agent.json",
                "/llms.txt", "/openapi.json"]
    byep = defaultdict(Counter)
    for r in recs:
        p = r.get("path", "")
        key = FORECAST + "/{id}" if p.startswith(FORECAST + "/") else p
        byep[key][r.get("ua") or "(empty)"] += 1
    print("\n-- discovery surfaces (hits <- top UAs) --")
    for key in surfaces:
        c = byep.get(key)
        if c:
            print(f"  {key}: {sum(c.values())}  <-  " +
                  "; ".join(f"{u[:34]}×{n}" for u, n in c.most_common(4)))

    # THE question: real (non-test, non-localhost) consumption of the forecast endpoints
    print("\n-- FORECAST consumption (the hook) --")
    fc = [r for r in recs if r.get("path", "").startswith(FORECAST)]
    real = [r for r in fc if r.get("ip") not in TEST_IPS
            and "curl" not in (r.get("ua") or "").lower()
            and "httpx/x402-agent" not in (r.get("ua") or "")]
    print(f"  total forecast hits: {len(fc)} | non-test (real agent?) hits: {len(real)}")
    for r in real[:25]:
        print(f"    {r['path']:<30} {r['status']}  {(r.get('ua') or '(empty)')[:40]:<40} {r['ip']}")
    if not real:
        print("    none yet — all forecast traffic is localhost/curl/httpx self-tests")
    print("=" * 64)


if __name__ == "__main__":
    main()
