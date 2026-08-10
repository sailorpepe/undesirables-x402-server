#!/usr/bin/env python3
"""
GradedPriceOracle — Base mainnet mirror deploy (2026-08-09, sailorpepe-approved).

WHY: the graded proof tree behind the PAID /api/v1/graded/proof surface lived
ONLY on LiteForge 4441 (testnet) — the same exposure class the Studio flagged
for the price tree on 2026-07-30 (closed 07-31 with the merkle Base mirror).
A LiteForge reset would erase the one contract a paid verification depends on.

SAFETY RAILS (supersets the merkle Base deploy's):
  - refuses if graded_deployment_base.json already exists (no re-deploy)
  - compiles from GradedPriceOracle_v2_verify_input.json — the EXACT standard
    input of the 07-31 audit-patched LiteForge deploy — and refuses unless the
    compiled runtime bytecode is byte-identical to eth_getCode(LiteForge)
  - refuses if graded_merkle_tree_cache.json root != LiteForge merkleRoot()
  - sanity-checks the leaf recipe via computeLeaf() eth_call before deploying
  - PENDING nonce + EIP-1559 headroom (replica-lag + base-fee-tick lessons)
  - renounce-guard check waits for code visibility first (the 07-31 false
    failure was eth_call hitting a replica that hadn't seen the code yet)
  - verifies owner(), merkleRoot(), totalGradedProducts() and a REAL price
    proof via verifyGradedPrice() on Base before declaring success
"""
import json, os, sys, time
from web3 import Web3
from dotenv import load_dotenv
import solcx

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

CHAIN = 8453
LITEFORGE_RPC = "https://liteforge.rpc.caldera.xyz/http"
DEPLOY_JSON = os.path.join(SCRIPT_DIR, "graded_deployment_base.json")
VERIFY_INPUT = os.path.join(SCRIPT_DIR, "GradedPriceOracle_v2_verify_input.json")
CACHE = os.path.join(SCRIPT_DIR, "graded_merkle_tree_cache.json")


def main():
    print("=" * 60)
    print("  GradedPriceOracle -> Base mainnet mirror")
    print("=" * 60)

    if os.path.exists(DEPLOY_JSON):
        print(f"  ERROR: {DEPLOY_JSON} already exists — refusing to re-deploy.")
        sys.exit(1)

    lf_dep = json.load(open(os.path.join(SCRIPT_DIR, "graded_deployment.json")))
    lf_addr = lf_dep["contract"]

    # ── Rail 1: recompile the exact 07-31 input; runtime must match LiteForge ──
    print("\n  [1/5] Compiling the exact audit-patched input (0.8.28/paris/opt200)…")
    inp = json.load(open(VERIFY_INPUT))
    solcx.set_solc_version("0.8.28")
    out = solcx.compile_standard(inp)
    c = out["contracts"]["contracts/GradedPriceOracle.sol"]["GradedPriceOracle"]
    abi, bytecode = c["abi"], c["evm"]["bytecode"]["object"]
    runtime = "0x" + c["evm"]["deployedBytecode"]["object"]

    lf = Web3(Web3.HTTPProvider(LITEFORGE_RPC, request_kwargs={"timeout": 60}))
    on_lf = lf.eth.get_code(Web3.to_checksum_address(lf_addr)).hex()
    if not on_lf.startswith("0x"):
        on_lf = "0x" + on_lf
    if on_lf.lower() != runtime.lower():
        print(f"  ERROR: compiled runtime != LiteForge code ({len(runtime)//2-1}B vs "
              f"{len(on_lf)//2-1}B) — would deploy DIFFERENT code to Base. Aborting.")
        sys.exit(1)
    print(f"  Bytecode gate: compiled runtime == LiteForge {lf_addr[:10]}… "
          f"({len(on_lf)//2 - 1:,} bytes) ✅")

    # ── Rail 2: the root we mirror must be the root the API proves into ──
    cache = json.load(open(CACHE))
    root_hex = cache["root"]
    lf_oracle = lf.eth.contract(address=Web3.to_checksum_address(lf_addr), abi=abi)
    lf_root = lf_oracle.functions.merkleRoot().call().hex()
    lf_root = lf_root if lf_root.startswith("0x") else "0x" + lf_root
    if lf_root.lower() != root_hex.lower():
        print(f"  ERROR: cache root {root_hex} != LiteForge {lf_root} — drifted, aborting.")
        sys.exit(1)
    n = lf_oracle.functions.totalGradedProducts().call()
    print(f"  Parity gate: cache == LiteForge == {root_hex[:18]}… (n={n:,}) ✅")

    # ── Rail 3: leaf-recipe sanity via the contract's own computeLeaf ──
    e = cache["data"][0]
    want = lf_oracle.functions.computeLeaf(
        e["product_id"], e["grade"], e["company"],
        e["median_cents"], e["num_listings"]).call().hex()
    want = want if want.startswith("0x") else "0x" + want
    if want.lower() != e["leaf"].lower():
        print(f"  ERROR: computeLeaf {want} != cached leaf {e['leaf']} — recipe drift, aborting.")
        sys.exit(1)
    print(f"  Leaf gate: computeLeaf(pid {e['product_id']}, {e['grade']}) == cache ✅")

    pk = os.getenv("LITVM_TESTNET_PK", os.getenv("BURNER_PRIVATE_KEY", "")).strip()
    if not pk:
        print("  ERROR: no key in .env"); sys.exit(1)
    if not pk.startswith("0x"):
        pk = "0x" + pk

    rpc = f"https://base-mainnet.g.alchemy.com/v2/{os.getenv('ALCHEMY_API_KEY')}"
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 60}))
    assert w3.eth.chain_id == CHAIN, f"wrong chain: {w3.eth.chain_id}"
    acct = w3.eth.account.from_key(pk)
    print(f"\n  Wallet:  {acct.address}")
    print(f"  Balance: {w3.from_wei(w3.eth.get_balance(acct.address), 'ether')} ETH")

    def fees():
        base = w3.eth.get_block("latest")["baseFeePerGas"]
        tip = max(w3.eth.max_priority_fee, 10_000)
        return {"maxFeePerGas": base * 3 + tip, "maxPriorityFeePerGas": tip}

    # ── Deploy ──
    print("\n  [2/5] Deploying…")
    tx = w3.eth.contract(abi=abi, bytecode=bytecode).constructor().build_transaction({
        "chainId": CHAIN, "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
        "gas": 3_000_000, **fees(),
    })
    signed = w3.eth.account.sign_transaction(tx, pk)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=180)
    if rcpt.status != 1:
        print("  ERROR: deploy reverted"); sys.exit(1)
    addr = rcpt.contractAddress
    print(f"  Deployed: {addr}  (tx {h.hex()}, gas {rcpt.gasUsed:,})")

    # wait for code visibility before ANY eth_call (07-31 replica-lag lesson)
    for i in range(12):
        if w3.eth.get_code(addr) not in (b"", b"\x00"):
            break
        time.sleep(5)
    else:
        print("  ERROR: code never became visible on the RPC"); sys.exit(1)

    # ── Renounce guard (the point of the audit patch) ──
    print("\n  [3/5] Renounce guard…")
    oracle = w3.eth.contract(address=addr, abi=abi)
    try:
        w3.eth.call({"from": acct.address, "to": addr,
                     "data": oracle.functions.renounceOwnership()._encode_transaction_data()})
        print("  ERROR: renounceOwnership did NOT revert — wrong bytecode?"); sys.exit(1)
    except Exception:
        print("  renounceOwnership reverts for the owner ✅")

    # ── First root: the CURRENT one, so Base == LiteForge from block one ──
    print("\n  [4/5] Committing current root…")
    tx = oracle.functions.updateMerkleRoot(
        bytes.fromhex(root_hex[2:]), n).build_transaction({
            "chainId": CHAIN, "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
            "gas": 250_000, **fees(),
        })
    signed = w3.eth.account.sign_transaction(tx, pk)
    h2 = w3.eth.send_raw_transaction(signed.raw_transaction)
    rcpt2 = w3.eth.wait_for_transaction_receipt(h2, timeout=180)
    if rcpt2.status != 1:
        print("  ERROR: root push reverted"); sys.exit(1)
    print(f"  Root committed (tx {h2.hex()}, gas {rcpt2.gasUsed:,})")

    # ── Verify everything on Base before claiming success ──
    print("\n  [5/5] Verifying on Base…")
    # A confirmed receipt does NOT mean every Alchemy replica has the state yet
    # — the 08-09 run's root push confirmed, then read back 0x000… from a
    # lagging replica and false-failed here. Poll until the root is non-zero.
    got_root = "0x" + "00" * 32
    for _ in range(12):
        got_root = oracle.functions.merkleRoot().call().hex()
        got_root = got_root if got_root.startswith("0x") else "0x" + got_root
        if int(got_root, 16) != 0:
            break
        time.sleep(10)
    owner = oracle.functions.owner().call()
    total = oracle.functions.totalGradedProducts().call()
    assert got_root.lower() == root_hex.lower(), f"root mismatch: {got_root}"
    assert owner == acct.address, f"owner mismatch: {owner}"
    assert total == n, f"totalGradedProducts mismatch: {total}"

    # a REAL proof from the cache, verified against the Base contract
    mid = cache["data"][len(cache["data"]) // 2]
    level0 = [x.lower() for x in cache["tree"][0]]
    idx = level0.index(mid["leaf"].lower())
    proof, i = [], idx
    for level in cache["tree"][:-1]:
        sib = i + 1 if i % 2 == 0 else i - 1
        proof.append(bytes.fromhex(level[sib][2:]) if sib < len(level) else b"\x00" * 32)
        i //= 2
    ok = oracle.functions.verifyGradedPrice(
        mid["product_id"], mid["grade"], mid["company"],
        mid["median_cents"], mid["num_listings"], proof).call()
    print(f"  merkleRoot: {got_root[:18]}… == cache ✅   owner ✅   n={total:,} ✅")
    print(f"  live proof (pid {mid['product_id']}, {mid['grade']}): "
          f"{'✅ VERIFIED' if ok else '❌ FAILED'}")
    if not ok:
        sys.exit(1)

    json.dump({
        "contract": addr,
        "contract_address": addr,
        "chain_id": CHAIN,
        "rpc": "alchemy-base-mainnet",
        "deployed_at": time.time(),
        "deploy_tx": h.hex(),
        "first_root_tx": h2.hex(),
        "deployer": acct.address,
        "first_root": root_hex,
        "total_graded_products": n,
        "audit_patch": lf_dep.get("audit_patch", "de2b673 (renounce guard)"),
        "liteforge_counterpart": lf_addr,
        "note": "Base mirror of the graded proof tree. Deployed because the tree "
                "behind the PAID /api/v1/graded/proof surface was LiteForge-only "
                "(same exposure class as the 07-30 merkle flag). "
                "graded_merkle_updater.py mirrors each new root, non-blocking.",
        "explorer": f"https://basescan.org/address/{addr}",
    }, open(DEPLOY_JSON, "w"), indent=2)
    print(f"\n  Wrote {DEPLOY_JSON}")
    print(f"  Explorer: https://basescan.org/address/{addr}")
    spent = (rcpt.gasUsed * rcpt.effectiveGasPrice + rcpt2.gasUsed * rcpt2.effectiveGasPrice) / 1e18
    print(f"  Total gas spent: {spent:.8f} ETH")


if __name__ == "__main__":
    main()
