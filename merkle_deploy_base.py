#!/usr/bin/env python3
"""
MerklePriceOracle — Base mainnet mirror deploy (2026-07-31).

WHY: the 286K-product proof tree behind /api/v1/merkle/proof lived ONLY on
LiteForge 4441 (testnet) — a chain reset would erase the one contract the
public verification story depends on (Studio flag, 2026-07-30). The daily
price PANEL was already Base-mirrored via the tcg_price stream; the PROOF
TREE was not. This deploys the identical bytecode to Base 8453 and pushes
the CURRENT root so Base == LiteForge == the API from block one.

SAFETY RAILS:
  - refuses to run if merkle_tree_cache.json root != LiteForge merkleRoot()
    (never mirrors a root the API can't prove into)
  - refuses if a Base deployment JSON already exists (no accidental re-deploy)
  - EIP-1559 fees with 2x headroom (the exact-gasPrice trap stalled the soul
    grader on LiteForge — "max fee per gas less than block base fee")
  - verifies owner(), merkleRoot() and a REAL proof from the cache on Base
    before declaring success

Reuses: MerklePriceOracle_abi.json + MerklePriceOracle_bytecode.txt — the
exact artifacts that produced the LiteForge deploy (byte-identical behavior).
"""
import json, os, sys, time
from web3 import Web3
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

CHAIN = 8453
LITEFORGE_RPC = "https://liteforge.rpc.caldera.xyz/http"
LITEFORGE_ADDR = "0x96B124f50156589274ADF8F674509374752170Cd"
DEPLOY_JSON = os.path.join(SCRIPT_DIR, "merkle_deployment_base.json")


def main():
    print("=" * 60)
    print("  MerklePriceOracle -> Base mainnet mirror")
    print("=" * 60)

    if os.path.exists(DEPLOY_JSON):
        print(f"  ERROR: {DEPLOY_JSON} already exists — refusing to re-deploy.")
        sys.exit(1)

    abi = json.load(open(os.path.join(SCRIPT_DIR, "MerklePriceOracle_abi.json")))
    bytecode = open(os.path.join(SCRIPT_DIR, "MerklePriceOracle_bytecode.txt")).read().strip()
    cache = json.load(open(os.path.join(SCRIPT_DIR, "merkle_tree_cache.json")))
    root_hex, n, data_date = cache["root"], cache["total_products"], cache["data_date"]

    # ── Rail 1: the root we mirror must be the root the API proves into ──
    lf = Web3(Web3.HTTPProvider(LITEFORGE_RPC, request_kwargs={"timeout": 60}))
    lf_root = "0x" + lf.eth.contract(address=LITEFORGE_ADDR, abi=abi)\
                       .functions.merkleRoot().call().hex().replace("0x", "")
    if lf_root.lower() != root_hex.lower():
        print(f"  ERROR: cache root {root_hex} != LiteForge {lf_root} — trees drifted, aborting.")
        sys.exit(1)
    print(f"  Parity gate: cache == LiteForge == {root_hex[:18]}… ({n:,} products, {data_date})")

    pk = os.getenv("LITVM_TESTNET_PK", os.getenv("BURNER_PRIVATE_KEY", "")).strip()
    if not pk:
        print("  ERROR: no key in .env"); sys.exit(1)
    if not pk.startswith("0x"):
        pk = "0x" + pk

    rpc = f"https://base-mainnet.g.alchemy.com/v2/{os.getenv('ALCHEMY_API_KEY')}"
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 60}))
    assert w3.eth.chain_id == CHAIN, f"wrong chain: {w3.eth.chain_id}"
    acct = w3.eth.account.from_key(pk)
    bal = w3.eth.get_balance(acct.address)
    print(f"  Wallet:  {acct.address}")
    print(f"  Balance: {w3.from_wei(bal, 'ether')} ETH")

    def fees():
        base = w3.eth.get_block("latest")["baseFeePerGas"]
        tip = max(w3.eth.max_priority_fee, 10_000)          # floor: 0.00001 gwei
        return {"maxFeePerGas": base * 2 + tip, "maxPriorityFeePerGas": tip}

    # ── Deploy ──
    print("\n  [1/3] Deploying…")
    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = contract.constructor().build_transaction({
        "chainId": CHAIN, "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "gas": 3_000_000, **fees(),
    })
    signed = w3.eth.account.sign_transaction(tx, pk)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=180)
    if rcpt.status != 1:
        print("  ERROR: deploy reverted"); sys.exit(1)
    addr = rcpt.contractAddress
    print(f"  Deployed: {addr}  (tx {h.hex()}, gas {rcpt.gasUsed:,})")

    # ── First root: the CURRENT one, so Base == LiteForge from block one ──
    print("\n  [2/3] Committing current root…")
    oracle = w3.eth.contract(address=addr, abi=abi)
    tx = oracle.functions.updateMerkleRoot(bytes.fromhex(root_hex[2:]), n).build_transaction({
        "chainId": CHAIN, "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "gas": 200_000, **fees(),
    })
    signed = w3.eth.account.sign_transaction(tx, pk)
    h2 = w3.eth.send_raw_transaction(signed.raw_transaction)
    rcpt2 = w3.eth.wait_for_transaction_receipt(h2, timeout=180)
    if rcpt2.status != 1:
        print("  ERROR: root push reverted"); sys.exit(1)
    print(f"  Root committed (tx {h2.hex()}, gas {rcpt2.gasUsed:,})")

    # ── Rail 2: verify everything on-chain before claiming success ──
    print("\n  [3/3] Verifying…")
    on_base = "0x" + oracle.functions.merkleRoot().call().hex().replace("0x", "")
    owner = oracle.functions.owner().call()
    total = oracle.functions.totalProducts().call()
    assert on_base.lower() == root_hex.lower(), f"root mismatch on Base: {on_base}"
    assert owner == acct.address, f"owner mismatch: {owner}"
    assert total == n, f"totalProducts mismatch: {total}"
    # real proof from the cache, verified against the Base contract
    pids = list(cache["product_index"].keys())
    mid = pids[len(pids) // 2]
    idx = cache["product_index"][mid]
    proof, i = [], idx
    for level in cache["tree"][:-1]:
        sib = i + 1 if i % 2 == 0 else i - 1
        proof.append(level[sib] if sib < len(level) else "0x" + "00" * 32)
        i //= 2
    import sqlite3
    db = sqlite3.connect(os.path.expanduser(
        "~/Documents/undesirables-mcp-server/.cache/market_memory.sqlite"))
    row = db.execute(
        "SELECT p.product_id, c.category_id, c.name, "
        "CAST(p.market_price*100 AS INTEGER), CAST(p.low_price*100 AS INTEGER) "
        "FROM price_history p JOIN cards c ON p.product_id=c.product_id "
        "WHERE p.product_id=? AND p.date=? AND p.market_price>0 "
        "ORDER BY p.market_price DESC LIMIT 1", (int(mid), data_date)).fetchone()
    db.close()
    ok = oracle.functions.verifyPrice(row[0], row[1], row[2], row[3], row[4], proof).call()
    print(f"  merkleRoot on Base: {on_base[:18]}…  == cache ✅")
    print(f"  owner: {owner} ✅   totalProducts: {total:,} ✅")
    print(f"  live proof ({row[2][:40]}…): {'✅ VERIFIED' if ok else '❌ FAILED'}")
    if not ok:
        sys.exit(1)

    json.dump({
        "contract": addr,
        "chain_id": CHAIN,
        "rpc": "alchemy-base-mainnet",
        "deployed_at": time.time(),
        "deploy_tx": h.hex(),
        "first_root_tx": h2.hex(),
        "deployer": acct.address,
        "first_root": root_hex,
        "total_products": n,
        "data_date": data_date,
        "liteforge_counterpart": LITEFORGE_ADDR,
        "note": "Base mirror of the public proof tree. Deployed because the tree "
                "the verification story depends on was LiteForge-only (Studio "
                "flag 2026-07-30). merkle_root_updater.py mirrors each new root.",
        "explorer": f"https://basescan.org/address/{addr}",
    }, open(DEPLOY_JSON, "w"), indent=2)
    print(f"\n  Wrote {DEPLOY_JSON}")
    print(f"  Explorer: https://basescan.org/address/{addr}")
    spent = (rcpt.gasUsed * rcpt.effectiveGasPrice + rcpt2.gasUsed * rcpt2.effectiveGasPrice) / 1e18
    print(f"  Total gas spent: {spent:.8f} ETH")


if __name__ == "__main__":
    main()
