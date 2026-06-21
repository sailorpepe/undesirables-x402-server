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

    nonce = w3.eth.get_transaction_count(wallet)
    tx = oracle.functions.batchUpdatePricesOnly(ids, prices, lows).build_transaction({
        "chainId": CHAIN_ID, "from": wallet, "nonce": nonce,
        "gas": 5000000, "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(getattr(signed, "raw_transaction", None) or signed.rawTransaction)
    print(f"  TX sent:   {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status == 1:
        total = oracle.functions.totalUpdates().call()
        print(f"  ✅ Confirmed (gas: {receipt.gasUsed}, total: {total})")
    else:
        print(f"  ❌ Failed! TX: {tx_hash.hex()}")
        sys.exit(1)

    print(f"  --- Done ---\n")

if __name__ == "__main__":
    main()
