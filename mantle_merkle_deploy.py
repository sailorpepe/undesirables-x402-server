#!/usr/bin/env python3
"""
MerklePriceOracle — Self-contained deploy + first root push.
AirDrop this file + MerklePriceOracle_abi.json + MerklePriceOracle_bytecode.txt
to the Mac Mini, then run:

    cd ~/Documents/undesirables-x402-server
    source venv/bin/activate
    python3 merkle_deploy.py
"""
import json, os, sys, sqlite3, time
from datetime import datetime, timezone
from web3 import Web3
from eth_abi import encode as abi_encode
from dotenv import load_dotenv

load_dotenv()

RPC = "https://rpc.sepolia.mantle.xyz"
CHAIN = 5003
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Paths — auto-detect Mac Mini layout
DB_CANDIDATES = [
    os.path.expanduser("~/Documents/undesirables-mcp-server/.cache/market_memory.sqlite"),
    os.path.expanduser("~/.cache/market_memory.sqlite"),
]
DB_PATH = next((p for p in DB_CANDIDATES if os.path.exists(p)), None)


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
    print("\n" + "=" * 60)
    print("  MerklePriceOracle — Deploy + First Root")
    print("=" * 60)

    # ── Preflight checks ──
    abi_path = os.path.join(SCRIPT_DIR, "MerklePriceOracle_abi.json")
    bc_path = os.path.join(SCRIPT_DIR, "MerklePriceOracle_bytecode.txt")

    for path, name in [(abi_path, "ABI"), (bc_path, "Bytecode")]:
        if not os.path.exists(path):
            print(f"  ERROR: {name} not found: {path}")
            sys.exit(1)

    if not DB_PATH:
        print(f"  ERROR: Database not found. Checked: {DB_CANDIDATES}")
        sys.exit(1)
    print(f"  Database: {DB_PATH}")

    pk = os.getenv("LITVM_TESTNET_PK", os.getenv("BURNER_PRIVATE_KEY", "")).strip()
    if not pk:
        print("  ERROR: No private key in .env")
        sys.exit(1)
    if not pk.startswith("0x"):
        pk = "0x" + pk

    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 60}))
    if not w3.is_connected():
        print("  ERROR: RPC not reachable")
        sys.exit(1)

    acct = w3.eth.account.from_key(pk)
    wallet = acct.address
    bal = w3.from_wei(w3.eth.get_balance(wallet), "ether")
    print(f"  Wallet:  {wallet}")
    print(f"  Balance: {bal} MNT")
    if bal == 0:
        print("  ERROR: No MNT for gas")
        sys.exit(1)

    with open(abi_path) as f:
        abi = json.load(f)
    with open(bc_path) as f:
        bytecode = f.read().strip()

    # ── Step 1: Deploy ──
    print(f"\n  [1/3] Deploying MerklePriceOracle...")
    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    nonce = w3.eth.get_transaction_count(wallet)
    tx = contract.constructor().build_transaction({
        "chainId": CHAIN, "from": wallet, "nonce": nonce,
        "gas": 3000000, "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, pk)
    h = w3.eth.send_raw_transaction(signed.rawTransaction)
    print(f"  TX: {h.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(h, timeout=120)
    if receipt.status != 1:
        print("  ERROR: Deploy failed!")
        sys.exit(1)
    addr = receipt.contractAddress
    print(f"  ✅ Deployed: {addr}")

    # ── Step 2: Build Merkle tree from ALL products ──
    print(f"\n  [2/3] Building Merkle tree from full database...")
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

    print(f"  Products: {len(rows):,}")
    print(f"  Data date: {latest}")

    leaves = []
    product_index = {}
    for i, (pid, cat, name, market, low) in enumerate(rows):
        leaves.append(compute_leaf(pid, cat, name, market, low))
        product_index[pid] = i

    root, tree = build_merkle_tree(leaves)
    root_hex = "0x" + root.hex()
    print(f"  Merkle root: {root_hex}")
    print(f"  Tree depth: {len(tree)} levels")

    # ── Step 3: Push root on-chain ──
    print(f"\n  [3/3] Pushing root on-chain...")
    oracle = w3.eth.contract(address=addr, abi=abi)
    nonce = w3.eth.get_transaction_count(wallet)
    tx = oracle.functions.updateMerkleRoot(root, len(rows)).build_transaction({
        "chainId": CHAIN, "from": wallet, "nonce": nonce,
        "gas": 200000, "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, pk)
    h = w3.eth.send_raw_transaction(signed.rawTransaction)
    print(f"  TX: {h.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(h, timeout=120)
    if receipt.status != 1:
        print("  ERROR: Root push failed!")
        sys.exit(1)
    total_updates = oracle.functions.totalRootUpdates().call()
    print(f"  ✅ Root committed (gas: {receipt.gasUsed}, update #{total_updates})")

    # ── Save deployment + tree cache ──
    deploy_info = {
        "contract_address": addr,
        "chain_id": CHAIN,
        "deployer": wallet,
        "first_root": root_hex,
        "total_products": len(rows),
        "deployed_at": int(time.time()),
        "explorer": f"https://explorer.sepolia.mantle.xyz/address/{addr}",
    }
    deploy_path = os.path.join(SCRIPT_DIR, "mantle_merkle_deployment.json")
    with open(deploy_path, "w") as f:
        json.dump(deploy_info, f, indent=2)

    # Save tree cache for proof API
    cache = {
        "root": root_hex,
        "data_date": latest,
        "total_products": len(rows),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "product_index": {str(k): v for k, v in product_index.items()},
        "leaves": ["0x" + l.hex() for l in leaves],
        "tree": [["0x" + n.hex() for n in level] for level in tree],
    }
    cache_path = os.path.join(SCRIPT_DIR, "merkle_tree_cache.json")
    with open(cache_path, "w") as f:
        json.dump(cache, f)
    cache_mb = os.path.getsize(cache_path) / (1024 * 1024)

    # Verify with a random proof
    test_idx = len(rows) // 2
    test_product = rows[test_idx]
    test_proof = get_proof(tree, test_idx)
    proof_bytes = [p for p in test_proof]
    verified = oracle.functions.verifyPrice(
        test_product[0], test_product[1], test_product[2],
        test_product[3], test_product[4], proof_bytes
    ).call()

    print(f"\n" + "=" * 60)
    print(f"  DEPLOYMENT COMPLETE")
    print(f"=" * 60)
    print(f"  Contract:   {addr}")
    print(f"  Explorer:   {deploy_info['explorer']}")
    print(f"  Products:   {len(rows):,}")
    print(f"  Root:       {root_hex[:18]}...")
    print(f"  Tree cache: {cache_mb:.1f} MB")
    print(f"  Proof test: {'✅ VERIFIED' if verified else '❌ FAILED'}")
    print(f"              ({test_product[2][:50]}...)")
    print(f"=" * 60)
    print(f"\n  Next: Add daily cron job:")
    print(f"  0 4 * * * cd {SCRIPT_DIR} && source venv/bin/activate && python3 merkle_root_updater.py >> merkle.log 2>&1")
    print()


if __name__ == "__main__":
    main()
