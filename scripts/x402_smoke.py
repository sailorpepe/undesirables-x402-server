#!/usr/bin/env python3
"""
x402_smoke.py — real-payment smoke test: 402 -> signed EIP-3009 USDC payment -> 200.

For each endpoint: (a) unpaid GET/POST must 402 with a valid payment-required
header; (b) pay via the x402 client (CDP facilitator settles; buyer needs USDC
only) and the retry must 200 with a real payload. One attempt per endpoint —
NEVER retry a paid failure (each retry costs money). Prints a pass/fail table,
per-call settlement info (Payment-Response header if present), and body snippets.

Usage:
  ./venv/bin/python scripts/x402_smoke.py step1          # simulate + market (~$0.04)
  ./venv/bin/python scripts/x402_smoke.py sweep          # all EVM-paid endpoints (~$3.37)
"""
import os, sys, json, base64, asyncio
import httpx
from dotenv import load_dotenv
from eth_account import Account
from x402 import x402Client
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client
from x402.http import decode_payment_required_header, encode_payment_signature_header

BASE = "https://oracle.the-undesirables.com"
IMG = "https://product-images.tcgplayer.com/fit-in/437x437/84198.jpg"   # real card image
PUDGY = "0xBd3531dA5CF5857e7CfAA92426877b022e612cf8"                     # real ERC-721 (mainnet)

STEP1 = [
    ("simulate",  "$0.015", "GET",  f"{BASE}/api/v1/simulate?card_name=Charizard&current_price=300&days=30", None),
    ("market",    "$0.025", "GET",  f"{BASE}/api/v1/market?game=Pokemon", None),
]
SWEEP_EXTRA = [
    ("trending",           "$0.025", "GET",  f"{BASE}/api/v1/trending?limit=10", None),
    ("coin-history",       "$0.05",  "GET",  f"{BASE}/api/v1/coin-history?coin_id=pepe", None),
    ("crypto-oracle",      "$0.05",  "GET",  f"{BASE}/api/v1/crypto-oracle?contract_address={PUDGY}", None),
    ("grade",              "$0.10",  "GET",  f"{BASE}/api/v1/grade?image_url={IMG}&game=Pokemon", None),
    ("grade-or-not",       "$0.10",  "GET",  f"{BASE}/api/v1/grade-or-not?card_name=Base%20Set%20Charizard%20Holo", None),
    ("phygital/arbitrage", "$0.10",  "GET",  f"{BASE}/api/v1/phygital/arbitrage", None),
    ("arb-grade",          "$0.15",  "GET",  f"{BASE}/api/v1/arb-grade", None),
    ("arb-weather",        "$0.25",  "GET",  f"{BASE}/api/v1/arb-weather", None),
    ("arb-basket",         "$0.50",  "GET",  f"{BASE}/api/v1/arb-basket", None),
    ("portfolio-optimize", "$0.50",  "GET",  f"{BASE}/api/v1/portfolio-optimize?cards=Charizard,Pikachu,Blastoise&budget=1000", None),
    ("batch-triage",       "$0.50",  "POST", f"{BASE}/api/v1/batch-triage", {"image_urls": IMG}),
    ("arb-cross",          "$1.00",  "GET",  f"{BASE}/api/v1/arb-cross?min_edge=3.0", None),
]


async def hit(http, method, url, body, headers):
    if method == "POST":
        return await http.post(url, json=body, headers=headers)
    return await http.get(url, headers=headers)


async def smoke(name, cost, method, url, body, client, http):
    row = {"name": name, "cost": cost, "unpaid": None, "paid": None, "settle": "", "note": ""}
    try:
        r402 = await hit(http, method, url, body, {"user-agent": "httpx"})
        row["unpaid"] = r402.status_code
        if r402.status_code != 402:
            row["note"] = f"expected 402, got {r402.status_code} — NOT gated?!"
            return row
        pr_header = r402.headers.get("payment-required")
        if not pr_header:
            row["note"] = "402 but no payment-required header"
            return row
        payment_required = decode_payment_required_header(pr_header)
        payload = await client.create_payment_payload(payment_required)
        sig_b64 = encode_payment_signature_header(payload)
        r200 = await hit(http, method, url, body, {"Payment-Signature": sig_b64, "user-agent": "httpx"})
        row["paid"] = r200.status_code
        raw = r200.headers.get("payment-response", "")
        if raw:
            try:
                s = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)))
                row["settle"] = f"success={s.get('success')} tx={s.get('transaction') or s.get('txHash') or s.get('transactionHash')}"
            except Exception:
                row["settle"] = raw[:80]
        text = r200.text[:180].replace("\n", " ")
        row["note"] = ("OK: " + text) if r200.status_code == 200 else ("PAID-FAIL body: " + text)
    except Exception as e:
        row["note"] = f"{type(e).__name__}: {str(e)[:120]}"
    return row


async def main():
    load_dotenv()
    pk = os.getenv("BUYER_PRIVATE_KEY")
    assert pk, "BUYER_PRIVATE_KEY missing"
    client = x402Client()
    register_exact_evm_client(client, EthAccountSigner(Account.from_key(pk)))
    which = sys.argv[1] if len(sys.argv) > 1 else "step1"
    targets = STEP1 if which == "step1" else STEP1 + SWEEP_EXTRA
    results = []
    async with httpx.AsyncClient(timeout=180.0) as http:
        for t in targets:
            row = await smoke(*t, client, http)
            status = "✅" if (row["unpaid"] == 402 and row["paid"] == 200) else "❌"
            print(f"{status} {row['name']:<20} {row['cost']:>7}  unpaid={row['unpaid']}  paid={row['paid']}")
            print(f"     {row['note'][:170]}")
            if row["settle"]:
                print(f"     settle-hdr: {row['settle']}")
            results.append(row)
    ok = sum(1 for r in results if r["unpaid"] == 402 and r["paid"] == 200)
    paid_fail = [r for r in results if r["unpaid"] == 402 and r["paid"] not in (200, None)]
    print(f"\n{ok}/{len(results)} passed. PAID-BUT-FAILED (worst class): {[r['name'] for r in paid_fail] or 'none'}")


if __name__ == "__main__":
    asyncio.run(main())
