#!/usr/bin/env python3
"""
register_bazaar_missing.py — idempotent Coinbase x402 Bazaar registrar.

WHY: the CDP Bazaar merchant index only lists a resource after a payment
handshake that carries the resource EXTENSIONS metadata (the forced
`create_payment_payload(..., extensions=...)` path). A plain settled payment
(e.g. the smoke test's standard SDK client, which drops extensions) moves USDC
but does NOT register the resource for discovery. Endpoints only appear once
they've been through this handshake — market + batch-triage never were
(batch-triage was unpayable until 2026-07-01), so agents browsing the Bazaar
can't find/buy them despite them returning valid 402s.

IDEMPOTENT: queries the live merchant discovery first and SKIPS any endpoint
already indexed — so re-running never double-spends (this is the run-once
guard). Spends real USDC ONLY for genuinely-missing endpoints.
  market ($0.025, GET) + batch-triage ($0.50, POST) = $0.525 max.
"""
import asyncio, os, sys, json, urllib.request
import httpx
from eth_account import Account
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
from x402 import x402Client
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client
from x402.http import decode_payment_required_header, encode_payment_signature_header

BASE = "https://oracle.the-undesirables.com"
PAYTO = "0x642e8a7C289381f24f0395e0539f0bA41c74Cc1B"
DISCOVERY = f"https://api.cdp.coinbase.com/platform/v2/x402/discovery/merchant?payTo={PAYTO}"
IMG = "https://product-images.tcgplayer.com/fit-in/437x437/84198.jpg"

# The endpoints the Studio flagged as missing. Idempotency means it's safe to
# list already-indexed ones too, but we scope to exactly the gap.
TARGETS = [
    {"name": "Daily Market Snapshot", "path": "/api/v1/market", "method": "GET", "body": None},
    # batch-triage: register the GET variant — CDP does not index POST-only
    # resources (issue #2112), and the GET form is functionally identical.
    {"name": "Batch Card Triage", "path": "/api/v1/batch-triage", "method": "GET",
     "query": f"image_urls={IMG}&game=Pokemon", "body": None},
]


def indexed_paths():
    req = urllib.request.Request(DISCOVERY, headers={"user-agent": "undesirables-registrar"})
    d = json.load(urllib.request.urlopen(req, timeout=25))
    out = set()
    for r in d.get("resources", []):
        res = r.get("resource", "")
        if "the-undesirables.com" in res:
            out.add("/" + res.split("the-undesirables.com/", 1)[-1].split("?")[0].lstrip("/"))
    return out, len(d.get("resources", []))


async def register(http, client, t):
    url = BASE + t["path"] + (("?" + t["query"]) if t.get("query") else "")
    kw = {"headers": {"user-agent": "httpx"}}
    if t["method"] == "POST":
        kw["json"] = t["body"]
        kw["headers"]["content-type"] = "application/json"
    # Step 1: unsigned request → 402 challenge
    r402 = await (http.post(url, **kw) if t["method"] == "POST" else http.get(url, **kw))
    if r402.status_code != 402:
        print(f"   ❌ expected 402, got {r402.status_code}: {r402.text[:120]}")
        return False
    pr = r402.headers.get("payment-required")
    if not pr:
        print("   ❌ no payment-required header"); return False
    payment_required = decode_payment_required_header(pr)
    # Step 2: FORCED extensions — this is what makes Coinbase index the resource
    payload = await client.create_payment_payload(
        payment_required, resource=payment_required.resource,
        extensions=payment_required.extensions)
    sig = encode_payment_signature_header(payload)
    sub = dict(kw)
    sub["headers"] = {**kw["headers"], "Payment-Signature": sig}
    # Step 3: signed retry (settles + carries extensions to the facilitator)
    final = await (http.post(url, **sub) if t["method"] == "POST" else http.get(url, **sub))
    ok = final.status_code == 200
    print(f"   {'✅' if ok else '❌'} settled handshake → HTTP {final.status_code}")
    return ok


async def main():
    pk = os.getenv("BUYER_PRIVATE_KEY")
    if not pk:
        sys.exit("❌ BUYER_PRIVATE_KEY missing")
    if not pk.startswith("0x"):
        pk = "0x" + pk
    client = x402Client()
    register_exact_evm_client(client, EthAccountSigner(Account.from_key(pk)))

    have, count_before = indexed_paths()
    print(f"📖 Bazaar currently indexes {count_before} resources.")
    # optional path filter: `python register_bazaar_missing.py /api/v1/market`
    # registers ONLY the given path(s) — used to avoid re-charging an endpoint
    # whose handshake already settled this session but hasn't propagated yet.
    only = [a for a in sys.argv[1:] if a.startswith("/")]
    pool = [t for t in TARGETS if not only or t["path"] in only]
    todo = [t for t in pool if t["path"] not in have]
    if not todo:
        print("✅ nothing to do — all targets already indexed."); return
    print(f"🎯 missing: {[t['path'] for t in todo]}")

    async with httpx.AsyncClient(timeout=230.0) as http:
        for t in todo:
            print(f"\n📡 registering {t['path']} ({t['method']})…")
            await register(http, client, t)

    have_after, count_after = indexed_paths()
    print(f"\n📊 Bazaar now indexes {count_after} resources (was {count_before}).")
    for t in TARGETS:
        print(f"   {'✅' if t['path'] in have_after else '⏳'} {t['path']}"
              + ("" if t["path"] in have_after else "  (may take a few min to propagate)"))


if __name__ == "__main__":
    asyncio.run(main())
