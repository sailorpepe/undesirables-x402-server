#!/usr/bin/env python3
"""
TCGPriceOracleV2 — Deploy + Register Script
Deploys the V2 oracle contract to LitVM LiteForge and registers 50 products.
"""
import json
import os
import sys
import time
import sqlite3
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
ABI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "TCGPriceOracleV2_abi.json")
BYTECODE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "TCGPriceOracleV2_bytecode.txt")

def validate():
    errors = []
    key = os.getenv("LITVM_TESTNET_PK", "")
    if not key or key == "your_private_key_here":
        errors.append("LITVM_TESTNET_PK not set in .env")
    if not os.path.exists(ABI_PATH):
        errors.append(f"ABI file not found: {ABI_PATH}")
    if not os.path.exists(BYTECODE_PATH):
        errors.append(f"Bytecode file not found: {BYTECODE_PATH}")
    if not os.path.exists(DB_PATH):
        errors.append(f"Database not found: {DB_PATH}")
    if errors:
        print("\n  PREFLIGHT FAILED:")
        for e in errors:
            print(f"    ✗ {e}")
        sys.exit(1)

def get_top_50_products(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    latest_date = cursor.execute("SELECT MAX(date) FROM price_history").fetchone()[0]
    rows = cursor.execute("""
        SELECT p.product_id, c.category_id, c.name,
               CAST(p.market_price * 100 AS INTEGER),
               CAST(p.low_price * 100 AS INTEGER)
        FROM price_history p
        JOIN cards c ON p.product_id = c.product_id
        WHERE p.date = ? AND p.market_price > 0
        ORDER BY p.market_price DESC
        LIMIT 50
    """, (latest_date,)).fetchall()
    conn.close()
    print(f"  Loaded {len(rows)} products from {latest_date}")
    return rows

def deploy():
    validate()

    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 60}))
    if False:  # RPC works despite is_connected quirk
        print("  ERROR: Cannot connect to LiteForge RPC")
        sys.exit(1)

    private_key = os.getenv("LITVM_TESTNET_PK").strip()
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    account = w3.eth.account.from_key(private_key)
    wallet = account.address
    balance = w3.eth.get_balance(wallet)

    print("\n" + "=" * 60)
    print("  TCGPriceOracleV2 — Deployment")
    print("=" * 60)
    print(f"  Wallet:   {wallet}")
    print(f"  Balance:  {w3.from_wei(balance, 'ether')} zkLTC")

    if balance == 0:
        print("  ERROR: No zkLTC balance")
        sys.exit(1)

    with open(ABI_PATH) as f:
        abi = json.load(f)
    with open(BYTECODE_PATH) as f:
        bytecode = f.read().strip()

    # Deploy
    print("\n  [1/3] Deploying contract...")
    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    nonce = w3.eth.get_transaction_count(wallet)

    tx = contract.constructor().build_transaction({
        "chainId": CHAIN_ID, "from": wallet, "nonce": nonce,
        "gas": 5000000, "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    print(f"         TX: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status != 1:
        print("  ERROR: Deployment failed!")
        sys.exit(1)

    contract_address = receipt.contractAddress
    print(f"  ✅ Deployed: {contract_address}")

    # Register products
    print("\n  [2/3] Registering products...")
    oracle = w3.eth.contract(address=contract_address, abi=abi)
    products = get_top_50_products(DB_PATH)

    BATCH_SIZE = 25
    nonce = w3.eth.get_transaction_count(wallet)

    for i in range(0, len(products), BATCH_SIZE):
        batch = products[i:i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1
        total_batches = (len(products) + BATCH_SIZE - 1) // BATCH_SIZE

        ids = [p[0] for p in batch]
        cats = [p[1] for p in batch]
        names = [p[2] for p in batch]
        prices = [p[3] for p in batch]
        lows = [p[4] for p in batch]

        print(f"         Batch {batch_num}/{total_batches} ({len(batch)} products)...")

        tx = oracle.functions.batchRegister(ids, cats, names, prices, lows).build_transaction({
            "chainId": CHAIN_ID, "from": wallet, "nonce": nonce,
            "gas": 8000000, "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        if receipt.status != 1:
            print(f"  ERROR: Batch {batch_num} failed!")
            sys.exit(1)

        nonce += 1
        print(f"         ✅ Batch {batch_num} confirmed (gas: {receipt.gasUsed})")

    # Verify
    print("\n  [3/3] Verifying...")
    count = oracle.functions.productCount().call()
    updates = oracle.functions.totalUpdates().call()
    owner = oracle.functions.owner().call()

    print(f"         Products: {count}")
    print(f"         Updates:  {updates}")
    print(f"         Owner:    {owner}")

    result = {
        "contract_address": contract_address,
        "chain_id": CHAIN_ID,
        "deployer": wallet,
        "product_count": count,
        "total_updates": updates,
        "deployed_at": int(time.time()),
        "explorer": f"https://liteforge.explorer.caldera.xyz/address/{contract_address}",
    }

    result_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "v2_deployment.json")
    with open(result_path, "w") as f:
        json.dump(result, f, indent=2)

    print("\n" + "=" * 60)
    print("  DEPLOYMENT COMPLETE")
    print("=" * 60)
    print(f"  Contract: {contract_address}")
    print(f"  Explorer: {result['explorer']}")
    print(f"  Saved:    {result_path}")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    deploy()
