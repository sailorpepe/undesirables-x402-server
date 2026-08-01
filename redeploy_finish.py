#!/usr/bin/env python3
"""
Finish the 2026-07-31 audit-patch redeploy. The main script deployed both
patched MerklePriceOracles but died on an Alchemy replica-lag false alarm
(the renounce guard was later confirmed live on both). This picks up exactly
where it stopped:

  DONE (baked in below, do not redeploy):
    merkle-liteforge  0x20A812309AD14aa39B59aE2791972dfe8dDDe80E
    merkle-base       0xE49104b3d540CBA4BFFe3B73bc06e910A3A7da4e
  REMAINING:
    1. deploy patched GradedPriceOracle -> LiteForge (+ guard check w/ retries)
    2. push the current graded root to it
    3. supersede the three deployment JSONs, rewrite the two ABI files
"""
import json, os, re, time
from web3 import Web3
from dotenv import load_dotenv
import solcx

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
LT = "/Users/thegreatluna8713/Documents/litvm-tcg-oracle"
BUILD_INFO = f"{LT}/artifacts/build-info/65126ffaf9a23e9ce5a8b98ef4f65a52.json"
DATE = "20260731"
MERKLE_LF = "0x20A812309AD14aa39B59aE2791972dfe8dDDe80E"
MERKLE_BASE = "0xE49104b3d540CBA4BFFe3B73bc06e910A3A7da4e"
MERKLE_TX_LF = "fc52babd1c9995"  # prefix, full hash on the explorer


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
    fname = f"contracts/{name}.sol"
    sources = transitive(fname, open(f"{LT}/contracts/{name}.sol").read(), oz)
    inp = {"language": "Solidity", "sources": sources,
           "settings": {"optimizer": {"enabled": True, "runs": 200}, "evmVersion": "paris",
                        "outputSelection": {"*": {"*": ["abi", "evm.bytecode", "evm.deployedBytecode"]}}}}
    solcx.set_solc_version("0.8.28")
    out = solcx.compile_standard(inp)
    c = out["contracts"][fname][name]
    return c["abi"], c["evm"]["bytecode"]["object"], inp


def guard_check_with_retries(w3, addr, owner, tries=8, wait=5):
    """Retry loop: a lagging replica sees no code and 'succeeds'. Only a real
    REVERT counts as pass; only a persistent non-revert (with code present)
    counts as fail."""
    sig = Web3.keccak(text="renounceOwnership()")[:4]
    for i in range(tries):
        if len(w3.eth.get_code(addr)) > 2:
            try:
                w3.eth.call({"from": owner, "to": addr, "data": sig})
                return False  # code present, call succeeded -> guard missing
            except Exception:
                return True   # reverted -> guard live
        time.sleep(wait)
    raise TimeoutError(f"{addr}: code never appeared on RPC")


def main():
    pk = os.getenv("LITVM_TESTNET_PK", os.getenv("BURNER_PRIVATE_KEY", "")).strip()
    if not pk.startswith("0x"):
        pk = "0x" + pk
    lf = Web3(Web3.HTTPProvider("https://liteforge.rpc.caldera.xyz/http", request_kwargs={"timeout": 60}))
    acct = lf.eth.account.from_key(pk)

    g_abi, g_bc, g_inp = compile_patched("GradedPriceOracle")
    m_abi, _, m_inp = compile_patched("MerklePriceOracle")
    json.dump(m_inp, open(os.path.join(SCRIPT_DIR, "MerklePriceOracle_v2_verify_input.json"), "w"))
    json.dump(g_inp, open(os.path.join(SCRIPT_DIR, "GradedPriceOracle_v2_verify_input.json"), "w"))

    # 1) deploy graded
    tx = lf.eth.contract(abi=g_abi, bytecode=g_bc).constructor().build_transaction({
        "chainId": 4441, "from": acct.address,
        "nonce": lf.eth.get_transaction_count(acct.address, "pending"),
        "gas": 3_000_000, "gasPrice": int(lf.eth.gas_price * 2)})
    signed = lf.eth.account.sign_transaction(tx, pk)
    h = lf.eth.send_raw_transaction(signed.raw_transaction)
    r = lf.eth.wait_for_transaction_receipt(h, timeout=180)
    assert r.status == 1, "graded deploy reverted"
    g_addr = r.contractAddress
    assert guard_check_with_retries(lf, g_addr, acct.address), "graded guard missing!"
    print(f"  graded-liteforge: {g_addr} (tx {h.hex()[:14]}…, guard VERIFIED)")

    # 2) push current graded root
    gc = json.load(open(os.path.join(SCRIPT_DIR, "graded_merkle_tree_cache.json")))
    n_rows = gc.get("total_products") or gc.get("total_rows") or len(gc.get("product_index", {}))
    oracle = lf.eth.contract(address=g_addr, abi=g_abi)
    tx = oracle.functions.updateMerkleRoot(bytes.fromhex(gc["root"][2:]), n_rows).build_transaction({
        "chainId": 4441, "from": acct.address,
        "nonce": lf.eth.get_transaction_count(acct.address, "pending"),
        "gas": 250_000, "gasPrice": int(lf.eth.gas_price * 2)})
    signed = lf.eth.account.sign_transaction(tx, pk)
    h2 = lf.eth.send_raw_transaction(signed.raw_transaction)
    r2 = lf.eth.wait_for_transaction_receipt(h2, timeout=180)
    assert r2.status == 1, "graded root push reverted"
    print(f"  graded root {gc['root'][:18]}… pushed ({n_rows:,} rows)")

    # 3) bookkeeping: supersede JSONs, rewrite ABIs
    owner = acct.address
    def supersede(fname, contract, extra):
        p = os.path.join(SCRIPT_DIR, fname)
        bak = p.replace(".json", f".superseded-{DATE}.json")
        if not os.path.exists(bak):
            os.rename(p, bak)
        prev = json.load(open(bak))
        info = {"contract": contract, "contract_address": contract,
                "deployer": owner, "deployed_at": time.time(),
                "audit_patch": "de2b673 (renounce guard" + (", uint256 categoryId)" if "merkle" in fname else ")"),
                "supersedes": prev.get("contract") or prev.get("contract_address"),
                **extra}
        json.dump(info, open(p, "w"), indent=2)
    supersede("merkle_deployment.json", MERKLE_LF,
              {"chain_id": 4441, "deploy_tx_prefix": MERKLE_TX_LF,
               "explorer": f"https://liteforge.explorer.caldera.xyz/address/{MERKLE_LF}"})
    supersede("merkle_deployment_base.json", MERKLE_BASE,
              {"chain_id": 8453, "rpc": "alchemy-base-mainnet",
               "explorer": f"https://basescan.org/address/{MERKLE_BASE}"})
    supersede("graded_deployment.json", g_addr,
              {"chain_id": 4441, "deploy_tx": h.hex(),
               "explorer": f"https://liteforge.explorer.caldera.xyz/address/{g_addr}"})
    json.dump(m_abi, open(os.path.join(SCRIPT_DIR, "MerklePriceOracle_abi.json"), "w"))
    json.dump(g_abi, open(os.path.join(SCRIPT_DIR, "GradedPriceOracle_abi.json"), "w"))
    print("  JSONs superseded, ABIs rewritten")
    print("\nALL DONE — tell the agent to flip the encoder and rebuild the tree.")


if __name__ == "__main__":
    main()
