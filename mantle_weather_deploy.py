#!/usr/bin/env python3
"""
WeatherEdgeOracle — Self-contained deploy + first root push.

Deploys the WeatherEdgeOracle contract to LiteForge Chain 4441,
fetches live NWS weather data from the Shroomy Oracle,
builds a Merkle tree, and pushes the first root on-chain.

Usage:
    cd ~/Documents/undesirables-x402-server
    source venv/bin/activate
    python3 weather_deploy.py
"""
import json, os, sys, time, requests
from datetime import datetime, timezone
from web3 import Web3
from eth_abi import encode as abi_encode
from dotenv import load_dotenv

load_dotenv()

RPC = "https://rpc.sepolia.mantle.xyz"
CHAIN = 5003
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SHROOMY_URL = "http://127.0.0.1:3000/api/weather-edge"


def keccak256(data: bytes) -> bytes:
    return Web3.keccak(data)


def compute_weather_leaf(city_code, timestamp, obs_high, obs_low, fcst_high, fcst_low, edge_count, top_edge):
    """Compute leaf matching the Solidity encoding in WeatherEdgeOracle.sol"""
    inner = abi_encode(
        ["string", "uint256", "int256", "int256", "int256", "int256", "uint256", "uint256"],
        [city_code, timestamp, obs_high, obs_low, fcst_high, fcst_low, edge_count, top_edge]
    )
    return keccak256(keccak256(inner))


def build_merkle_tree(leaves):
    """Build a standard OpenZeppelin-compatible Merkle tree (sorted pairs, double hash)."""
    padded = list(leaves)
    # Pad to next power of 2
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
    """Generate a Merkle proof for a leaf at the given index."""
    proof = []
    for level in range(len(tree) - 1):
        layer = tree[level]
        sib = idx + 1 if idx % 2 == 0 else idx - 1
        proof.append(layer[sib] if sib < len(layer) else b"\x00" * 32)
        idx //= 2
    return proof


def fetch_weather_data():
    """Fetch current weather data from Shroomy Oracle."""
    print(f"  Fetching from {SHROOMY_URL}...")
    resp = requests.get(SHROOMY_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("success"):
        print(f"  ERROR: Weather API returned success=false")
        sys.exit(1)

    cities = data.get("cities", {})
    if not cities:
        print(f"  ERROR: No city data returned")
        sys.exit(1)

    # Parse the ISO timestamp to unix epoch
    ts_str = data.get("timestamp", "")
    ts = int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp())

    entries = []
    for code, city in sorted(cities.items()):
        entries.append({
            "city_code": code,
            "name": city.get("name", code),
            "timestamp": ts,
            "observed_high": int(round(city.get("observedHigh", 0) * 100)),
            "observed_low": int(round(city.get("observedLow", 0) * 100)),
            "forecast_high": int(round(city.get("forecastHigh", 0) * 100)),
            "forecast_low": int(round(city.get("forecastLow", 0) * 100)),
            "edge_count": city.get("edgeCount", 0),
            "top_edge": int(round(city.get("topEdge", 0) * 100)),
        })

    return entries, ts


def main():
    print("\n" + "=" * 60)
    print("  WeatherEdgeOracle — Deploy + First Root")
    print("=" * 60)

    # ── Preflight checks ──
    abi_path = os.path.join(SCRIPT_DIR, "WeatherEdgeOracle_abi.json")
    bc_path = os.path.join(SCRIPT_DIR, "WeatherEdgeOracle_bytecode.txt")

    for path, name in [(abi_path, "ABI"), (bc_path, "Bytecode")]:
        if not os.path.exists(path):
            print(f"  ERROR: {name} not found: {path}")
            sys.exit(1)

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
    print(f"\n  [1/4] Deploying WeatherEdgeOracle...")
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

    # ── Step 2: Fetch weather data ──
    print(f"\n  [2/4] Fetching live weather data...")
    entries, snapshot_ts = fetch_weather_data()
    print(f"  Cities: {len(entries)}")
    for e in entries:
        print(f"    {e['city_code']} ({e['name']}): {e['observed_high']/100:.0f}°F high, {e['observed_low']/100:.0f}°F low")

    # ── Step 3: Build Merkle tree ──
    print(f"\n  [3/4] Building Merkle tree...")
    leaves = []
    city_index = {}
    for i, e in enumerate(entries):
        leaf = compute_weather_leaf(
            e["city_code"], e["timestamp"],
            e["observed_high"], e["observed_low"],
            e["forecast_high"], e["forecast_low"],
            e["edge_count"], e["top_edge"]
        )
        leaves.append(leaf)
        city_index[e["city_code"]] = i

    root, tree = build_merkle_tree(leaves)
    root_hex = "0x" + root.hex()
    print(f"  Merkle root: {root_hex}")
    print(f"  Tree depth:  {len(tree)} levels")

    # ── Step 4: Push root on-chain ──
    print(f"\n  [4/4] Pushing root on-chain...")
    oracle = w3.eth.contract(address=addr, abi=abi)
    time.sleep(2)
    nonce = w3.eth.get_transaction_count(wallet, 'pending')
    tx = oracle.functions.updateMerkleRoot(root, len(entries)).build_transaction({
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

    # ── Verify with a proof ──
    print(f"\n  Waiting 5 seconds for Mantle RPC to sync...")
    time.sleep(5)
    test_idx = 0  # Test with first city (alphabetically)
    test_entry = entries[test_idx]
    test_proof = get_proof(tree, test_idx)
    verified = oracle.functions.verifyWeather(
        test_entry["city_code"], test_entry["timestamp"],
        test_entry["observed_high"], test_entry["observed_low"],
        test_entry["forecast_high"], test_entry["forecast_low"],
        test_entry["edge_count"], test_entry["top_edge"],
        test_proof
    ).call()

    # ── Save deployment info ──
    deploy_info = {
        "contract_address": addr,
        "contract_name": "WeatherEdgeOracle",
        "chain_id": CHAIN,
        "deployer": wallet,
        "first_root": root_hex,
        "total_entries": len(entries),
        "cities": [e["city_code"] for e in entries],
        "deployed_at": int(time.time()),
        "explorer": f"https://explorer.sepolia.mantle.xyz/address/{addr}",
    }
    deploy_path = os.path.join(SCRIPT_DIR, "mantle_weather_deployment.json")
    with open(deploy_path, "w") as f:
        json.dump(deploy_info, f, indent=2)

    # Save tree cache for proof generation
    cache = {
        "root": root_hex,
        "snapshot_timestamp": snapshot_ts,
        "total_entries": len(entries),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "city_index": city_index,
        "entries": entries,
        "leaves": ["0x" + l.hex() for l in leaves],
        "tree": [["0x" + n.hex() for n in level] for level in tree],
    }
    cache_path = os.path.join(SCRIPT_DIR, "weather_merkle_cache.json")
    with open(cache_path, "w") as f:
        json.dump(cache, f, indent=2)

    print(f"\n" + "=" * 60)
    print(f"  DEPLOYMENT COMPLETE")
    print(f"=" * 60)
    print(f"  Contract:    {addr}")
    print(f"  Explorer:    {deploy_info['explorer']}")
    print(f"  Cities:      {len(entries)} ({', '.join(e['city_code'] for e in entries)})")
    print(f"  Root:        {root_hex[:18]}...")
    print(f"  Proof test:  {'✅ VERIFIED' if verified else '❌ FAILED'}")
    print(f"               ({test_entry['city_code']} — {test_entry['name']})")
    print(f"=" * 60)
    print(f"\n  Next: Schedule daily cron job:")
    print(f"  0 6 * * * cd {SCRIPT_DIR} && source venv/bin/activate && python3 weather_merkle_updater.py >> ~/logs/weather_merkle.log 2>&1")
    print()


if __name__ == "__main__":
    main()
