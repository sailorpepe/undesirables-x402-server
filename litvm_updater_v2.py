#!/usr/bin/env python3
"""
TCGPriceOracleV2 — Hourly Price Updater
Sends 1 batch transaction instead of V1's 50 individual calls.
"""
import json
import os
import sys
import sqlite3
from datetime import datetime, timezone
from web3 import Web3
from pathlib import Path

# Load .env manually
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

RPC_URL = "https://liteforge.rpc.caldera.xyz/http"
CHAIN_ID = 4441
DB_PATH = os.path.expanduser("~/Documents/undesirables-mcp-server/.cache/market_memory.sqlite")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ABI_PATH = os.path.join(SCRIPT_DIR, "TCGPriceOracleV2_abi.json")
DEPLOY_PATH = os.path.join(SCRIPT_DIR, "v2_deployment.json")
CACHE_PATH = os.path.join(SCRIPT_DIR, "v2_last_push_cache.json")

def load_contract_address():
    if not os.path.exists(DEPLOY_PATH):
        print("  ERROR: v2_deployment.json not found. Run deploy_v2.py first.")
        sys.exit(1)
    with open(DEPLOY_PATH) as f:
        return json.load(f)["contract_address"]

def get_top_50_prices(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    latest_date = cursor.execute("SELECT MAX(date) FROM price_history").fetchone()[0]
    rows = cursor.execute("""
        SELECT p.product_id,
               CAST(p.market_price * 100 AS INTEGER),
               CAST(p.low_price * 100 AS INTEGER)
        FROM price_history p
        JOIN cards c ON p.product_id = c.product_id
        WHERE p.date = ? AND p.market_price > 0
        ORDER BY p.market_price DESC
        LIMIT 50
    """, (latest_date,)).fetchall()
    conn.close()
    return rows, latest_date

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n  --- V2 Oracle Update: {now} ---")

    private_key = os.getenv("LITVM_TESTNET_PK", "").strip()
    if not private_key:
        print("  ERROR: LITVM_TESTNET_PK not set")
        sys.exit(1)
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 60}))
    if not w3.is_connected():
        print("  ERROR: Cannot connect to LiteForge RPC")
        sys.exit(1)

    account = w3.eth.account.from_key(private_key)
    wallet = account.address
    contract_address = load_contract_address()

    with open(ABI_PATH) as f:
        abi = json.load(f)

    oracle = w3.eth.contract(address=contract_address, abi=abi)
    products, data_date = get_top_50_prices(DB_PATH)

    if not products:
        print("  ERROR: No products found")
        sys.exit(1)

    ids = [p[0] for p in products]
    prices = [p[1] for p in products]
    lows = [p[2] for p in products]

    print(f"  Products:  {len(products)}")
    print(f"  Data date: {data_date}")
    print(f"  Contract:  {contract_address}")

    # SKIP-IF-UNCHANGED (2026-08-10, sailorpepe-approved). The source data
    # (TCGCSV via the nightly pipeline) changes ONCE per day, so 23 of this
    # job's 24 hourly pushes wrote byte-identical prices on-chain — measured
    # at 89% of ALL LiteForge gas burn (0.055 of 0.0617/day). Same pattern as
    # merkle_root_updater's "Root unchanged — skipping": push only when the
    # payload differs from what the chain already has. A TWAP of a constant
    # is the constant; the redundant writes added zero information.
    # Cache is written ONLY after a confirmed tx (house ordering rule), so a
    # failed push retries next hour rather than being skipped forever.
    import hashlib
    payload_hash = hashlib.sha256(
        json.dumps([data_date, ids, prices, lows]).encode()).hexdigest()
    if os.path.exists(CACHE_PATH):
        try:
            if json.load(open(CACHE_PATH)).get("payload_hash") == payload_hash:
                print(f"  Prices unchanged since last push — skipping on-chain push")
                print(f"  --- Done (no update needed) ---\n")
                return
        except (json.JSONDecodeError, OSError):
            pass  # unreadable cache = push (fail open, never wedge)

    # 3x gas buffer + retry. Bare w3.eth.gas_price is fetched BEFORE signing, so a
    # LiteForge base-fee tick between fetch and send kills the push outright:
    #   "max fee per gas less than block base fee: maxFee 10112000, base 10133000"
    # — off by 0.2%, and it paged David at 07:00 on 2026-07-22. Same failure class
    # that stranded a weekly soul root on 2026-07-19 (fixed there the same way).
    # The push self-heals next hour, but a page for a transient blip is the real
    # cost: alert fatigue is how a genuine outage gets ignored.
    # ⚠️ DO NOT "OPTIMIZE" THIS LADDER DOWN TO 1x. Tried and reverted 2026-08-10.
    # I proposed starting at 1x to cut cost, sailorpepe approved it, and MEASUREMENT
    # KILLED IT on both counts:
    #   1. 1x CANNOT LAND. `eth_gasPrice` on LiteForge returns EXACTLY the current
    #      base fee (measured 6 samples: ratio 0.997-1.001), so a 1x tx is priced
    #      at the base fee at build time and any tick rejects it with "max fee per
    #      gas less than block base fee". The live 1x attempt failed immediately.
    #   2. THE MULTIPLIER IS FREE. This chain refunds the excess: a tx with
    #      gasPrice SET to 6.8060 gwei (3x) paid effectiveGasPrice 2.2745 gwei ==
    #      the block base fee exactly. So 3x costs the same as 1x and just buys
    #      headroom. Verified on tx 86df7f7b… (gasUsed 1,105,472, cost 0.002514,
    #      identical to the base-fee-only cost).
    # Starting at 1x therefore adds a GUARANTEED failed attempt every hour for
    # zero saving — and a "failed" line in the log every hour trips the nightly
    # partial-failure detector, which is the alert fatigue this file's comment
    # above already warns about.
    # SEPARATE LESSON worth keeping (it cost four days): on 2026-08-07 this job
    # started failing with `insufficient funds` because the balance check requires
    # gas_limit(5,000,000) x gasPrice x mult UPFRONT even though only gasUsed x
    # baseFee is charged — at 3x that reserved 0.0385 against a 0.0202 balance.
    # Escalating gas is the right cure for a base-fee miss and exactly the WRONG
    # one for insufficient funds: a higher multiplier RAISES the reservation and
    # fails harder. Fix for that is tokens (or a smaller gas limit), never a ladder.
    last_err = None
    for mult in (3, 6, 12):
        try:
            tx = oracle.functions.batchUpdatePricesOnly(ids, prices, lows).build_transaction({
                "chainId": CHAIN_ID, "from": wallet,
                "nonce": w3.eth.get_transaction_count(wallet),
                # 1.8M limit (2026-08-10, was 5M): measured usage is 1.0-1.25M,
                # so 1.8M keeps ~45% headroom while cutting the UPFRONT balance
                # reservation (gas_limit x gasPrice x mult) ~3x — that
                # reservation, not actual cost, is what caused the 08-07..08-10
                # insufficient-funds failures at low balance. Unused gas is
                # never charged; the limit only gates the balance check.
                "gas": 1800000, "gasPrice": int(w3.eth.gas_price * mult),
            })
            signed = w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = w3.eth.send_raw_transaction(getattr(signed, "raw_transaction", None) or signed.rawTransaction)
            print(f"  TX sent:   {tx_hash.hex()} ({mult}x gas)")
            break
        except Exception as e:
            last_err = e
            print(f"  gas attempt {mult}x failed: {str(e)[:110]}")
    else:
        raise RuntimeError(f"LitVM price push failed after 3 gas attempts: {last_err}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status == 1:
        total = oracle.functions.totalUpdates().call()
        print(f"  ✅ Confirmed (gas: {receipt.gasUsed}, total: {total})")
        # cache AFTER confirmation only — a failed push must retry next hour
        with open(CACHE_PATH, "w") as f:
            json.dump({"payload_hash": payload_hash, "data_date": data_date,
                       "tx": tx_hash.hex(),
                       "pushed_at": datetime.now(timezone.utc).isoformat()}, f)
    else:
        print(f"  ❌ Failed! TX: {tx_hash.hex()}")
        sys.exit(1)

    print(f"  --- Done ---\n")

if __name__ == "__main__":
    main()
