#!/usr/bin/env python3
"""
bazaar_keepalive.py — guarded self-settlement so we never fall out of the
CDP Bazaar index (30-day recency filter; playbook #1, market research
2026-07-14 — docs kept in the private soul-engine repo).

WHY: the Bazaar drops any resource with no settled payment in 30 days. We are
pre-organic-revenue, so OUR OWN settlements are what keep the shelf stocked.
Found 2026-07-14: the authoritative clock is the Bazaar's own
quality.lastCalledAt (our local memory of "we paid recently" was 11 days off).

WHAT IT DOES (cron, daily):
  1. Ask the Bazaar merchant-discovery API for our resources' lastCalledAt.
  2. If the OLDEST one is >= TRIGGER_DAYS (21) → run the full paid sweep
     (scripts/x402_smoke.py sweep, ~$3.37 USDC) exactly once.
  3. ntfy a receipt (or a failure alert). Silence = nothing was due.

SPEND GUARDS (rule 13: never automate spending without a run-once guard):
  - Lockfile prevents concurrent runs.
  - State file records the last sweep date; a sweep NEVER fires if one ran in
    the last MIN_GAP_DAYS (14), even if the Bazaar data looks stale/garbled.
  - If the Bazaar API is unreachable/unparseable we DO NOTHING (fail-safe:
    no data -> no spend) — the stack healthcheck alarm remains the backstop.
  - One sweep per invocation, cron'd once a day: worst case ~$3.37/day is
    impossible; steady state is ~$3.67 every ~21 days (~$5/month; +verdict $0.30 added 2026-08-26).
"""
import fcntl
import json
import os
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timezone

X = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(X, "bazaar_keepalive_state.json")
LOCK = "/tmp/bazaar_keepalive.lock"
LOG = os.path.expanduser("~/logs/bazaar_keepalive.log")
PAY_TO = "0x642e8a7C289381f24f0395e0539f0bA41c74Cc1B"
DISCOVERY = f"https://api.cdp.coinbase.com/platform/v2/x402/discovery/merchant?payTo={PAY_TO}"
TRIGGER_DAYS = 21   # sweep when the stalest resource hits this age
MIN_GAP_DAYS = 14   # hard floor between sweeps, regardless of anything else


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def env(k):
    for line in open(os.path.join(X, ".env")):
        if line.startswith(k + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get(k)


def ntfy(title, body, priority="default", tags="package"):
    topic = env("NTFY_TOPIC")
    if not topic:
        return
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"https://ntfy.sh/{topic}", data=body.encode(),
            headers={"Title": title, "Priority": priority, "Tags": tags}), timeout=15)
    except Exception as e:
        log(f"(ntfy failed: {e})")


def oldest_settle_age():
    """Days since the STALEST of our indexed resources was last paid,
    straight from the Bazaar. None on any failure (fail-safe: no spend)."""
    try:
        req = urllib.request.Request(DISCOVERY, headers={"User-Agent": "undesirables-keepalive/1.0"})
        d = json.load(urllib.request.urlopen(req, timeout=30))
        items = d.get("items") or d.get("resources") or []
        # ONLY listings the oracle still SELLS (2026-09-12). Retired (410) and
        # suspended (200, no charge) routes stay in the Bazaar index until it
        # drops them at 30d, and they can never be re-settled — measuring the
        # stalest of THOSE would trigger a sweep every day from ~day 21 forever.
        try:
            _r = urllib.request.Request("https://oracle.the-undesirables.com/",
                                        headers={"User-Agent": "undesirables-keepalive/1.0"})
            _paid = {e["path"] for e in json.load(urllib.request.urlopen(_r, timeout=30))["endpoints"]["paid"]}
        except Exception as _e:
            log(f"oracle manifest unreadable ({str(_e)[:60]}) — refusing to spend on no data")
            return None, 0
        from urllib.parse import urlparse as _up
        live = [i for i in items if _up(i.get("resource") or "").path in _paid]
        stamps = []
        for i in live:
            ts = (i.get("quality") or {}).get("lastCalledAt")
            if ts:
                stamps.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
        if len(live) != len(items):
            log(f"Bazaar: {len(items) - len(live)} indexed listing(s) are retired/suspended — not counted")
        if not stamps:
            return None, 0
        oldest = min(stamps)
        age = (datetime.now(timezone.utc) - oldest).days
        return age, len(live)
    except Exception as e:
        log(f"Bazaar discovery unreadable ({str(e)[:80]}) — refusing to spend on no data")
        return None, 0


def main():
    # concurrency guard
    lf = open(LOCK, "w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another keepalive run holds the lock — exiting")
        return

    # hard spend-floor guard
    state = {}
    try:
        state = json.load(open(STATE))
    except Exception:
        pass
    last_sweep = state.get("last_sweep")
    if last_sweep:
        gap = (date.today() - date.fromisoformat(last_sweep)).days
        if gap < MIN_GAP_DAYS:
            log(f"last sweep {last_sweep} ({gap}d ago) < hard floor {MIN_GAP_DAYS}d — not spending")
            return

    age, n = oldest_settle_age()
    if age is None:
        return
    log(f"Bazaar: {n} resources indexed; stalest lastCalledAt = {age}d ago (trigger {TRIGGER_DAYS}d)")
    if age < TRIGGER_DAYS:
        return

    # due — run the sweep ONCE (x402_smoke never retries paid failures)
    log("sweep due — running x402_smoke sweep (~$3.67)")
    py = os.path.join(X, "venv", "bin", "python")
    r = subprocess.run([py, os.path.join(X, "scripts", "x402_smoke.py"), "sweep"],
                       capture_output=True, text=True, timeout=900)
    tail = (r.stdout or "")[-800:]
    ok = r.returncode == 0
    # record the ATTEMPT date either way — a partial sweep still settled real
    # money; the hard floor must apply to attempts, not successes.
    state["last_sweep"] = date.today().isoformat()
    state["last_result"] = "ok" if ok else f"exit {r.returncode}"
    json.dump(state, open(STATE, "w"))
    log(f"sweep finished: {'OK' if ok else 'FAILED rc=' + str(r.returncode)}")
    # PERSIST THE PER-ENDPOINT RESULT (2026-07-30). Previously `tail` went only
    # to the ntfy push, so when batch-triage silently failed to refresh in the
    # 07-26 sweep there was no local record of WHY — the sweep exited 0 because
    # one endpoint failing is not a script failure. A paid sweep whose outcome
    # cannot be reconstructed a week later is money spent for no evidence.
    for line in (r.stdout or "").strip().splitlines()[-25:]:
        log(f"  sweep| {line}")
    if r.stderr:
        for line in r.stderr.strip().splitlines()[-8:]:
            log(f"  sweep-err| {line}")
    if ok:
        ntfy("Bazaar keepalive: sweep settled",
             f"Self-settlement sweep ran (stalest listing was {age}d old).\n"
             f"~$3.37 USDC spent to stay in the Bazaar index.\n\n{tail}",
             tags="package,moneybag")
    else:
        ntfy("Bazaar keepalive: SWEEP FAILED",
             f"Sweep exited rc={r.returncode} — listings age out at 30d "
             f"(currently {age}d). Investigate soon.\n\n{tail}",
             priority="high", tags="rotating_light")


if __name__ == "__main__":
    main()
