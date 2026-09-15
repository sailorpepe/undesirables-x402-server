#!/usr/bin/env python3
"""contract_smoke.py — the oracle's public CONTRACT, asserted (2026-09-15).

WHY: on 09-12 /recommend answered 500 for three minutes (a missing dict key) and
on 09-15 /api/v1/jp/summary answered 500 on its first request (a bad ORDER BY).
Both were caught only because a human hit the route after the restart. This is
that human, every morning, with no memory lapses: retired routes are 410,
suspended routes are 200 + not_charged, paid routes are 402 with a payment
envelope, free routes are 200 with the shape agents rely on.

Exit 1 on any mismatch; prints one line per failure. Cheap: ~25 requests.
Run:  ./venv/bin/python scripts/contract_smoke.py [--base http://127.0.0.1:8402]
"""
import argparse, json, sys, urllib.request, urllib.error

def get(base, path, timeout=60):
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, headers={"User-Agent": "contract-smoke/1.0"}), timeout=timeout) as r:
            body = r.read()
            return r.status, body, r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers
    except Exception as e:
        return 0, str(e).encode(), {}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default="http://127.0.0.1:8402")
    base = ap.parse_args().base
    fails = []
    def expect(cond, msg):
        if not cond: fails.append(msg)

    st, body, _ = get(base, "/")
    expect(st == 200, f"/ -> {st}")
    root = json.loads(body) if st == 200 else {}
    ep = root.get("endpoints", {})
    expect(len(ep.get("paid", [])) >= 1 and len(ep.get("free", [])) >= 1, "root endpoints lists empty")
    expect("suspended" in ep, "root lacks endpoints.suspended")
    expect(isinstance(root.get("panels"), dict) and "usd" in root["panels"] and "japanese" in root["panels"], "root lacks panels")
    expect(isinstance(root.get("total_minted"), int), "root lacks total_minted")

    # retired -> 410, never charged
    for p in ("/api/v1/arb-cross", "/api/v1/arb-basket", "/api/v1/arb-weather", "/api/v1/casper/price", "/api/v1/phygital/stats"):
        st, _, _ = get(base, p); expect(st == 410, f"{p} -> {st} (want 410)")

    # suspended -> 200 + status suspended + not_charged, for every path the root lists
    for e in ep.get("suspended", []):
        p = e["path"]; st, body, _ = get(base, p)
        try: d = json.loads(body)
        except Exception: d = {}
        expect(st == 200 and d.get("status") == "suspended" and d.get("not_charged") is True, f"{p} -> {st} {d.get('status')} (want 200 suspended not_charged)")

    # paid -> 402 (bare GET) for every GET route the root lists as paid
    for e in ep.get("paid", []):
        if e.get("method", "GET").upper() != "GET" or e["path"].endswith("/upload"): continue
        st, _, _ = get(base, e["path"]); expect(st == 402, f"{e['path']} -> {st} (want 402)")

    # free routes with the shapes agents rely on
    checks = {
        "/api/v1/search?query=charizard": lambda d: "usd_panel" in d,
        "/api/v1/price?product_id=477": lambda d: "usd_panel" in d and d["usd_panel"].get("as_of"),
        "/api/v1/forecast/477": lambda d: "usd_panel" in d,
        "/api/v1/merkle/proof?product_id=477": lambda d: "usd_panel" in d,
        "/api/v1/sports/board": lambda d: d.get("status") in ("ok", "live") or "movers" in d or "leagues" in d,
        "/api/v1/souls/leaderboard": lambda d: "board" in d and d.get("minted"),
        "/api/v1/soul-rating/1": lambda d: "recent_results" in d and "open_predictions" in d,
        "/api/v1/loan-terms/universe": lambda d: d.get("method_version", "").startswith("v2") and d.get("total", 0) > 100,
        "/api/v1/jp/summary": lambda d: d.get("as_of") and d["totals"]["cards"] > 300000 and d["onchain"]["root"] and "card_id" not in json.dumps(d.get("bands")) ,
        "/api/v1/recommend?goal=lend+against+a+slab": lambda d: (d.get("top_recommendation") or {}).get("workflow_id") == "lending_terms",
        "/api/v1/recommend?goal=whats+trending": lambda d: (d.get("top_recommendation") or {}).get("has_suspended_steps") is True,
        "/api/v1/graded?product_id=477": lambda d: d.get("status") == "ok",
        "/api/v1/graded-bluechips": lambda d: "price_basis" in d and "premium_caveat" in d,
        "/api/v1/census/summary": lambda d: d.get("status") == "ok",
        "/api/v1/accuracy": lambda d: (d.get("tcg_conformal_coverage") or {}).get("panel_frozen") is not None or "usd_panel" in d,
        "/api/v1/collection": lambda d: d.get("total_minted"),
        "/api/v1/collection/wallet/0x0000000000000000000000000000000000000000": None,   # 400 expected
        "/api/v1/wallet/portfolio?address=0x0000000000000000000000000000000000000000": None,  # 400 expected
    }
    for p, shape in checks.items():
        st, body, _ = get(base, p)
        if shape is None:
            expect(st == 400, f"{p} -> {st} (want 400)"); continue
        expect(st == 200, f"{p} -> {st}")
        if st == 200:
            try: d = json.loads(body)
            except Exception: fails.append(f"{p} non-JSON"); continue
            try: expect(bool(shape(d)), f"{p} shape mismatch")
            except Exception as e: fails.append(f"{p} shape check raised {type(e).__name__}: {e}")

    # loan-terms v2: raw quote must be basis_frozen (via the free preview it is 404/not_in_universe; use the manifest route with a paid probe -> 402 already covered)
    st, body, _ = get(base, "/lending"); expect(st == 200, f"/lending -> {st}")
    st, body, _ = get(base, "/card/477"); expect(st == 200 and b"USD panel FROZEN" in body, "/card/477 lacks the frozen banner")
    st, body, _ = get(base, "/llms.txt"); expect(st == 200 and b"SUSPENDED" in body, "/llms.txt lacks the suspended marker")
    st, body, _ = get(base, "/openapi.json")
    if st == 200:
        paths = json.loads(body).get("paths", {})
        for p in ("/api/v1/market", "/api/v1/simulate"):
            expect("Suspended" in (paths.get(p, {}).get("get", {}).get("tags") or []), f"openapi {p} not tagged Suspended")

    if fails:
        print("CONTRACT SMOKE: %d failure(s)" % len(fails))
        for f in fails: print("  ✗ " + f)
        sys.exit(1)
    print("CONTRACT SMOKE: all checks passed")

if __name__ == "__main__":
    main()
