#!/usr/bin/env python3
"""
Redeploy the audit-patched MerklePriceOracle (Base + LiteForge) and
GradedPriceOracle (LiteForge) — 2026-07-31, sailorpepe-approved.

WHY: the June-4 audit commit (de2b673, "12 findings") patched both contracts
AFTER their deploys, so the on-chain code lacked: (a) the renounceOwnership
lockout guard (a renounce on a write-once oracle permanently bricks root
updates), and (b) merkle's categoryId uint16 -> uint256 widening, which
changes the LEAF ENCODING. (b) is why this script must be followed by the
merkle_root_updater.py encoder change + a tree rebuild — old-encoding proofs
do not verify against the new contract, by design.

Sources: litvm-tcg-oracle HEAD (== de2b673 for merkle/graded). Compiled here
with solc 0.8.28 / optimizer 200 / evmVersion=paris (LiteForge predates PUSH0;
one artifact serves both chains, same pattern as the original deploys).

Old deployment JSONs are preserved as *.superseded-20260731.json; the old
contracts stay on-chain with their full root history.
"""
import json, os, re, sys, time
from web3 import Web3
from dotenv import load_dotenv
import solcx

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
LT = "/Users/thegreatluna8713/Documents/litvm-tcg-oracle"
BUILD_INFO = f"{LT}/artifacts/build-info/65126ffaf9a23e9ce5a8b98ef4f65a52.json"
DATE = "20260731"


def transitive(entry_name, entry_src, oz_pool):
    pool = dict(oz_pool)
    pool[entry_name] = {"content": entry_src}
    need, seen = [entry_name], {}
    while need:
        n = need.pop()
        if n in seen or n not in pool:
            continue
        seen[n] = pool[n]
        for m in re.findall(r'import\s+(?:\{[^}]*\}\s+from\s+)?"([^"]+)"', pool[n]["content"]):
            if m.startswith("."):
                # resolve relative to the importing file (OZ imports its
                # siblings as ./X.sol and ../utils/X.sol)
                base = n.rsplit("/", 1)[0] if "/" in n else ""
                parts = (base + "/" + m).split("/")
                out = []
                for p in parts:
                    if p == "..":
                        out.pop()
                    elif p not in (".", ""):
                        out.append(p)
                m = "/".join(out)
            need.append(m)
    return seen


def compile_patched(name):
    bi = json.load(open(BUILD_INFO))
    oz = {k: v for k, v in bi["input"]["sources"].items() if k.startswith("@openzeppelin")}
    src = open(f"{LT}/contracts/{name}.sol").read()
    fname = f"contracts/{name}.sol"
    sources = transitive(fname, src, oz)
    inp = {"language": "Solidity", "sources": sources,
           "settings": {"optimizer": {"enabled": True, "runs": 200}, "evmVersion": "paris",
                        "outputSelection": {"*": {"*": ["abi", "evm.bytecode", "evm.deployedBytecode"]}}}}
    solcx.set_solc_version("0.8.28")
    out = solcx.compile_standard(inp)
    c = out["contracts"][fname][name]
    return c["abi"], c["evm"]["bytecode"]["object"], inp


def fees_1559(w3):
    base = w3.eth.get_block("latest")["baseFeePerGas"]
    tip = max(w3.eth.max_priority_fee, 1_000_000)
    return {"maxFeePerGas": base * 3 + tip, "maxPriorityFeePerGas": tip}


def deploy(w3, chain_id, abi, bytecode, pk, legacy=False):
    acct = w3.eth.account.from_key(pk)
    tx = w3.eth.contract(abi=abi, bytecode=bytecode).constructor().build_transaction({
        "chainId": chain_id, "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
        "gas": 3_000_000,
        **({"gasPrice": int(w3.eth.gas_price * 2)} if legacy else fees_1559(w3)),
    })
    signed = w3.eth.account.sign_transaction(tx, pk)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    r = w3.eth.wait_for_transaction_receipt(h, timeout=180)
    assert r.status == 1, f"deploy reverted: {h.hex()}"
    return r.contractAddress, h.hex(), r.gasUsed


def assert_renounce_blocked(w3, addr, abi, owner):
    """The point of the patch: renounceOwnership must revert even for the owner."""
    c = w3.eth.contract(address=addr, abi=abi)
    try:
        w3.eth.call({"from": owner, "to": addr,
                     "data": c.functions.renounceOwnership()._encode_transaction_data()})
        raise AssertionError(f"{addr}: renounceOwnership did NOT revert — wrong bytecode?")
    except AssertionError:
        raise
    except Exception:
        pass  # revert = the guard works


def main():
    pk = os.getenv("LITVM_TESTNET_PK", os.getenv("BURNER_PRIVATE_KEY", "")).strip()
    if not pk.startswith("0x"):
        pk = "0x" + pk
    owner = Web3().eth.account.from_key(pk).address

    lf = Web3(Web3.HTTPProvider("https://liteforge.rpc.caldera.xyz/http", request_kwargs={"timeout": 60}))
    ba = Web3(Web3.HTTPProvider(
        f"https://base-mainnet.g.alchemy.com/v2/{os.getenv('ALCHEMY_API_KEY')}",
        request_kwargs={"timeout": 60}))

    print("compiling patched sources (0.8.28 / paris / opt 200)…")
    m_abi, m_bc, m_inp = compile_patched("MerklePriceOracle")
    g_abi, g_bc, g_inp = compile_patched("GradedPriceOracle")
    json.dump(m_inp, open(os.path.join(SCRIPT_DIR, "MerklePriceOracle_v2_verify_input.json"), "w"))
    json.dump(g_inp, open(os.path.join(SCRIPT_DIR, "GradedPriceOracle_v2_verify_input.json"), "w"))

    results = {}
    for label, w3, chain, abi, bc, legacy in [
            ("merkle-liteforge", lf, 4441, m_abi, m_bc, True),
            ("merkle-base", ba, 8453, m_abi, m_bc, False),
            ("graded-liteforge", lf, 4441, g_abi, g_bc, True)]:
        addr, txh, gas = deploy(w3, chain, abi, bc, pk, legacy)
        assert_renounce_blocked(w3, addr, abi, owner)
        got_owner = w3.eth.contract(address=addr, abi=abi).functions.owner().call()
        assert got_owner == owner, f"owner mismatch {got_owner}"
        results[label] = {"contract": addr, "tx": txh, "gas": gas}
        print(f"  {label}: {addr} (tx {txh[:14]}…, gas {gas:,}, renounce-guard VERIFIED)")

    # graded: push the current cache root (encoding unchanged for graded)
    gc = json.load(open(os.path.join(SCRIPT_DIR, "graded_merkle_tree_cache.json")))
    g_addr = results["graded-liteforge"]["contract"]
    oracle = lf.eth.contract(address=g_addr, abi=g_abi)
    acct = lf.eth.account.from_key(pk)
    tx = oracle.functions.updateMerkleRoot(bytes.fromhex(gc["root"][2:]), gc.get("total_products") or gc.get("total_rows") or len(gc.get("product_index", {}))).build_transaction({
        "chainId": 4441, "from": acct.address,
        "nonce": lf.eth.get_transaction_count(acct.address, "pending"),
        "gas": 250_000, "gasPrice": int(lf.eth.gas_price * 2)})
    signed = lf.eth.account.sign_transaction(tx, pk)
    h = lf.eth.send_raw_transaction(signed.raw_transaction)
    r = lf.eth.wait_for_transaction_receipt(h, timeout=180)
    assert r.status == 1
    print(f"  graded root pushed: {gc['root'][:18]}… (tx {h.hex()[:14]}…)")

    # supersede the deployment JSONs (backups first), rewrite ABIs
    for old, new_label, extra in [
            ("merkle_deployment.json", "merkle-liteforge", {"chain_id": 4441, "explorer": "https://liteforge.explorer.caldera.xyz/address/"}),
            ("merkle_deployment_base.json", "merkle-base", {"chain_id": 8453, "rpc": "alchemy-base-mainnet", "explorer": "https://basescan.org/address/"}),
            ("graded_deployment.json", "graded-liteforge", {"chain_id": 4441, "explorer": "https://liteforge.explorer.caldera.xyz/address/"})]:
        p = os.path.join(SCRIPT_DIR, old)
        os.rename(p, p.replace(".json", f".superseded-{DATE}.json"))
        prev = json.load(open(p.replace(".json", f".superseded-{DATE}.json")))
        info = {"contract": results[new_label]["contract"],
                "contract_address": results[new_label]["contract"],   # both key styles read in the wild
                "deploy_tx": results[new_label]["tx"],
                "deployer": owner, "deployed_at": time.time(),
                "audit_patch": "de2b673 (renounce guard" + (", uint256 categoryId)" if "merkle" in new_label else ")"),
                "supersedes": prev.get("contract") or prev.get("contract_address"),
                **extra}
        info["explorer"] += results[new_label]["contract"]
        json.dump(info, open(p, "w"), indent=2)
    json.dump(m_abi, open(os.path.join(SCRIPT_DIR, "MerklePriceOracle_abi.json"), "w"))
    json.dump(g_abi, open(os.path.join(SCRIPT_DIR, "GradedPriceOracle_abi.json"), "w"))
    print("deployment JSONs superseded + ABIs rewritten")
    print("\nNEXT (required): switch merkle_root_updater.py leaf encoding to uint256, "
          "run it (pushes the rebuilt tree to BOTH new merkle contracts), restart x402.")


if __name__ == "__main__":
    main()
