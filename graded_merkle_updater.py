#!/usr/bin/env python3
"""
GradedPriceOracle — Hourly Root Updater
Rebuilds the Merkle tree from the latest eBay enriched PSA graded data and pushes a new root on-chain.
"""
import json, os, sys, sqlite3
from datetime import datetime, timezone
from web3 import Web3
from eth_abi import encode as abi_encode
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

RPC = os.getenv("LITVM_RPC_URL", "https://liteforge.rpc.caldera.xyz/http")
CHAIN = 4441

DB_PATH = os.path.expanduser("~/Documents/undesirables-mcp-server/.cache/market_memory.sqlite")
ABI_PATH = os.path.join(SCRIPT_DIR, "GradedPriceOracle_abi.json")
DEPLOY_PATH = os.path.join(SCRIPT_DIR, "graded_deployment.json")
CACHE_PATH = os.path.join(SCRIPT_DIR, "graded_merkle_tree_cache.json")


def keccak256(data: bytes) -> bytes:
    return Web3.keccak(data)


def compute_leaf(pid: int, grade: str, company: str, median_cents: int, num_listings: int) -> bytes:
    inner = abi_encode(
        ["uint256", "string", "string", "uint256", "uint256"],
        [pid, grade, company, median_cents, num_listings]
    )
    return keccak256(keccak256(inner))


def build_merkle_tree(leaves):
    padded = list(leaves)
    while len(padded) & (len(padded) - 1):
        padded.append(b"\x00" * 32)
    if len(padded) < 2:
        padded.extend([b"\x00" * 32] * (2 - len(padded)))

    tree = [padded]
    current = padded

    while len(current) > 1:
        nxt = []
        for i in range(0, len(current), 2):
            left = current[i]
            right = current[i + 1] if i + 1 < len(current) else b"\x00" * 32
            pair = (left + right) if left < right else (right + left)
            nxt.append(keccak256(pair))
        tree.append(nxt)
        current = nxt

    return current[0], tree


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n  --- Graded Merkle Root Update: {now} ---")

    # Preflight
    if not os.path.exists(DB_PATH):
        print(f"  ERROR: Database not found at {DB_PATH}"); sys.exit(1)
    if not os.path.exists(DEPLOY_PATH):
        print("  ERROR: graded_deployment.json not found."); sys.exit(1)
    if not os.path.exists(ABI_PATH):
        print("  ERROR: ABI not found"); sys.exit(1)

    # Allow fallback to general PRIVATE_KEY if LITVM_TESTNET_PK not found
    pk = os.getenv("PRIVATE_KEY", os.getenv("LITVM_TESTNET_PK", "")).strip()
    if not pk:
        print("  ERROR: No private key found in .env"); sys.exit(1)
    if not pk.startswith("0x"):
        pk = "0x" + pk

    with open(DEPLOY_PATH) as f:
        addr = json.load(f)["contract_address"]
    with open(ABI_PATH) as f:
        abi = json.load(f)

    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 60}))
    acct = w3.eth.account.from_key(pk)
    wallet = acct.address

    # Fetch latest graded prices
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    rows = c.execute("""
        SELECT product_id, grade, grading_company, median_price, num_listings
        FROM graded_prices
        WHERE median_price IS NOT NULL AND num_listings > 0
        ORDER BY product_id ASC, grade ASC
    """).fetchall()
    conn.close()

    print(f"  Graded Products: {len(rows):,}")
    if len(rows) == 0:
        print("  No graded prices found in DB. Exiting.")
        return

    # Check if root changed from cached version
    old_root = None
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH) as f:
                old_cache = json.load(f)
                old_root = old_cache.get("root")
        except Exception:
            pass

    leaves = []
    # Data list to store in cache for verification proofs
    # Note: store the raw values so API can compute leaves for users
    data_list = []
    
    for pid, grade, company, median, num in rows:
        company = company or "PSA"  # default to PSA if null
        median_cents = int(median * 100)
        leaf = compute_leaf(pid, grade, company, median_cents, num)
        leaves.append(leaf)
        data_list.append({
            "product_id": pid,
            "grade": grade,
            "company": company,
            "median_cents": median_cents,
            "num_listings": num,
            "leaf": "0x" + leaf.hex()
        })

    root, tree = build_merkle_tree(leaves)
    root_hex = "0x" + root.hex() if not root.hex().startswith("0x") else root.hex()

    if root_hex == old_root:
        print(f"  Root unchanged ({root_hex[:18]}...) — skipping on-chain push")
        print(f"  --- Done (no update needed) ---\n")
        return

    print(f"  New root: {root_hex}")

    # Push on-chain. This account is shared by the other hourly LitVM pushers,
    # so use the PENDING nonce and retry on nonce/replacement conflicts instead
    # of failing the whole run on "nonce too low".
    import time
    oracle = w3.eth.contract(address=addr, abi=abi)
    tx_hash = None
    for attempt in range(4):
        nonce = w3.eth.get_transaction_count(wallet, "pending")   # include in-flight txs
        tx = oracle.functions.updateMerkleRoot(root, len(rows)).build_transaction({
            "chainId": CHAIN, "from": wallet, "nonce": nonce,
            "gas": 250000, "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(tx, private_key=pk)
        # web3 v6+ exposes raw_transaction; older exposes rawTransaction.
        raw_tx = getattr(signed, 'raw_transaction', getattr(signed, 'rawTransaction', None))
        try:
            print(f"  Broadcasting (attempt {attempt+1}, nonce {nonce})...")
            tx_hash = w3.eth.send_raw_transaction(raw_tx)
            break
        except Exception as e:
            msg = str(e).lower()
            if attempt < 3 and ("nonce too low" in msg or "already known" in msg
                                or "replacement transaction underpriced" in msg):
                print(f"  nonce conflict: {e} — backing off, refetching nonce")
                time.sleep(8); continue
            print(f"  ERROR sending tx: {e}")
            sys.exit(1)

    print(f"  Tx Hash: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt.status != 1:
        print("  ERROR: Transaction reverted!")
        sys.exit(1)
    print(f"  Success! Gas used: {receipt.gasUsed:,}")

    # Save cache ONLY after a successful tx
    tree_hex = [["0x" + h.hex() for h in layer] for layer in tree]
    with open(CACHE_PATH, "w") as f:
        json.dump({
            "root": root_hex,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tree": tree_hex,
            "data": data_list
        }, f)
    print("  Cache updated.")

    print(f"  --- Done ---\n")

if __name__ == "__main__":
    main()
