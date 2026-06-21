#!/usr/bin/env python3
"""
MerklePriceOracle — Hourly Root Updater
Rebuilds the Merkle tree from the latest price data and pushes a new root on-chain.
Cost: ~162K gas per update. Runs hourly via cron.
"""
import json, os, sys, sqlite3
from datetime import datetime, timezone
from web3 import Web3
from eth_abi import encode as abi_encode
from dotenv import load_dotenv

load_dotenv()

RPC = "https://rpc.sepolia.mantle.xyz"
CHAIN = 5003
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Auto-detect paths
DB_CANDIDATES = [
    os.path.expanduser("~/Documents/undesirables-mcp-server/.cache/market_memory.sqlite"),
    os.path.expanduser("~/.cache/market_memory.sqlite"),
]
DB_PATH = next((p for p in DB_CANDIDATES if os.path.exists(p)), None)
ABI_PATH = os.path.join(SCRIPT_DIR, "MerklePriceOracle_abi.json")
DEPLOY_PATH = os.path.join(SCRIPT_DIR, "mantle_merkle_deployment.json")
CACHE_PATH = os.path.join(SCRIPT_DIR, "merkle_tree_cache.json")


def keccak256(data: bytes) -> bytes:
    return Web3.keccak(data)


def compute_leaf(pid, cat, name, market, low):
    inner = abi_encode(
        ["uint256", "uint16", "string", "uint256", "uint256"],
        [pid, cat, name, market, low]
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


def get_proof(tree, idx):
    proof = []
    for level in range(len(tree) - 1):
        layer = tree[level]
        sib = idx + 1 if idx % 2 == 0 else idx - 1
        proof.append(layer[sib] if sib < len(layer) else b"\x00" * 32)
        idx //= 2
    return proof


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n  --- Merkle Root Update: {now} ---")

    # Preflight
    if not DB_PATH:
        print("  ERROR: Database not found"); sys.exit(1)
    if not os.path.exists(DEPLOY_PATH):
        print("  ERROR: mantle_merkle_deployment.json not found. Run merkle_deploy.py first."); sys.exit(1)
    if not os.path.exists(ABI_PATH):
        print("  ERROR: ABI not found"); sys.exit(1)

    pk = os.getenv("LITVM_TESTNET_PK", os.getenv("BURNER_PRIVATE_KEY", "")).strip()
    if not pk:
        print("  ERROR: No private key"); sys.exit(1)
    if not pk.startswith("0x"):
        pk = "0x" + pk

    with open(DEPLOY_PATH) as f:
        addr = json.load(f)["contract_address"]
    with open(ABI_PATH) as f:
        abi = json.load(f)

    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 60}))
    acct = w3.eth.account.from_key(pk)
    wallet = acct.address

    # Build tree from latest data
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    latest = c.execute("SELECT MAX(date) FROM price_history").fetchone()[0]
    rows = c.execute("""
        SELECT p.product_id, c.category_id, c.name,
               CAST(p.market_price * 100 AS INTEGER),
               CAST(p.low_price * 100 AS INTEGER)
        FROM price_history p JOIN cards c ON p.product_id = c.product_id
        WHERE p.date = ? AND p.market_price > 0
        ORDER BY p.product_id ASC
    """, (latest,)).fetchall()
    conn.close()

    print(f"  Products: {len(rows):,} | Date: {latest}")

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
    product_index = {}
    for i, (pid, cat, name, market, low) in enumerate(rows):
        leaves.append(compute_leaf(pid, cat, name, market, low))
        product_index[pid] = i

    root, tree = build_merkle_tree(leaves)
    root_hex = "0x" + root.hex()

    if root_hex == old_root:
        print(f"  Root unchanged — skipping on-chain push")
        print(f"  --- Done (no update needed) ---\n")
        return

    print(f"  New root: {root_hex[:18]}...")

    # Push on-chain
    oracle = w3.eth.contract(address=addr, abi=abi)
    nonce = w3.eth.get_transaction_count(wallet)
    tx = oracle.functions.updateMerkleRoot(root, len(rows)).build_transaction({
        "chainId": CHAIN, "from": wallet, "nonce": nonce,
        "gas": 200000, "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, pk)
    h = w3.eth.send_raw_transaction(getattr(signed, "raw_transaction", None) or signed.rawTransaction)
    print(f"  TX: {h.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(h, timeout=120)

    if receipt.status == 1:
        total = oracle.functions.totalRootUpdates().call()
        print(f"  ✅ Root committed (gas: {receipt.gasUsed}, update #{total})")
    else:
        print(f"  ❌ Failed!"); sys.exit(1)

    # Update cache
    cache = {
        "root": root_hex,
        "data_date": latest,
        "total_products": len(rows),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "product_index": {str(k): v for k, v in product_index.items()},
        "leaves": ["0x" + l.hex() for l in leaves],
        "tree": [["0x" + n.hex() for n in level] for level in tree],
    }
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f)

    print(f"  Cache updated ({os.path.getsize(CACHE_PATH) / (1024*1024):.1f} MB)")
    print(f"  --- Done ---\n")


if __name__ == "__main__":
    main()
