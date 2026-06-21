#!/usr/bin/env python3
"""
WeatherEdgeOracle — Hourly Merkle root updater.

Fetches live NWS weather data from the Shroomy Oracle (localhost:3000),
builds a Merkle tree of 10-city observations, and pushes the root on-chain
if it has changed since the last update.

Runs hourly via cron or launchd.

Usage:
    cd ~/Documents/undesirables-x402-server
    source venv/bin/activate
    python3 weather_merkle_updater.py
"""
import json, os, sys, time, requests, logging
from datetime import datetime, timezone
from web3 import Web3
from eth_abi import encode as abi_encode
from dotenv import load_dotenv

load_dotenv()

# ── Config ──────────────────────────────────────────────────
RPC = "https://rpc.sepolia.mantle.xyz"
CHAIN = 5003
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SHROOMY_URL = "http://127.0.0.1:3000/api/weather-edge"

# Load deployment info
DEPLOY_PATH = os.path.join(SCRIPT_DIR, "mantle_weather_deployment.json")
CACHE_PATH = os.path.join(SCRIPT_DIR, "weather_merkle_cache.json")
ABI_PATH = os.path.join(SCRIPT_DIR, "WeatherEdgeOracle_abi.json")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("weather-merkle")


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


def fetch_weather_data():
    """Fetch current weather data from Shroomy Oracle."""
    resp = requests.get(SHROOMY_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("success"):
        raise RuntimeError("Weather API returned success=false")

    cities = data.get("cities", {})
    if not cities:
        raise RuntimeError("No city data returned")

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

    return entries, ts, data


def main():
    log.info("=" * 50)
    log.info("WeatherEdgeOracle — Hourly Merkle Update")
    log.info("=" * 50)

    # ── Load deployment config ──
    if not os.path.exists(DEPLOY_PATH):
        log.error(f"Deployment file not found: {DEPLOY_PATH}")
        log.error("Run weather_deploy.py first")
        sys.exit(1)

    with open(DEPLOY_PATH) as f:
        deploy = json.load(f)
    contract_addr = deploy["contract_address"]
    log.info(f"Contract: {contract_addr}")

    if not os.path.exists(ABI_PATH):
        log.error(f"ABI not found: {ABI_PATH}")
        sys.exit(1)

    with open(ABI_PATH) as f:
        abi = json.load(f)

    pk = os.getenv("LITVM_TESTNET_PK", os.getenv("BURNER_PRIVATE_KEY", "")).strip()
    if not pk:
        log.error("No private key in .env")
        sys.exit(1)
    if not pk.startswith("0x"):
        pk = "0x" + pk

    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 60}))
    if not w3.is_connected():
        log.error("RPC not reachable")
        sys.exit(1)

    acct = w3.eth.account.from_key(pk)
    wallet = acct.address

    # ── Fetch weather data ──
    try:
        entries, snapshot_ts, raw_data = fetch_weather_data()
    except Exception as e:
        log.error(f"Failed to fetch weather data: {e}")
        log.error("Is Shroomy Oracle running on port 3000?")
        sys.exit(1)

    log.info(f"Fetched {len(entries)} cities:")
    for e in entries:
        log.info(f"  {e['city_code']:3s} ({e['name']:15s}): Hi {e['observed_high']/100:.0f}°F  Lo {e['observed_low']/100:.0f}°F  Edges: {e['edge_count']}")

    # ── Build Merkle tree ──
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
    log.info(f"Merkle root: {root_hex[:18]}...")

    # ── Check if root changed ──
    cached_root = None
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH) as f:
                cached = json.load(f)
            cached_root = cached.get("root")
        except Exception:
            pass

    if cached_root == root_hex:
        log.info("Root unchanged — skipping on-chain push")
        log.info("Weather data hasn't changed since last update")
        return

    # ── Push root on-chain ──
    log.info("Root changed — pushing on-chain...")
    oracle = w3.eth.contract(address=contract_addr, abi=abi)
    nonce = w3.eth.get_transaction_count(wallet)
    tx = oracle.functions.updateMerkleRoot(root, len(entries)).build_transaction({
        "chainId": CHAIN, "from": wallet, "nonce": nonce,
        "gas": 200000, "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, pk)
    h = w3.eth.send_raw_transaction(getattr(signed, "raw_transaction", None) or signed.rawTransaction)
    log.info(f"TX: {h.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(h, timeout=120)

    if receipt.status != 1:
        log.error("Root push FAILED!")
        sys.exit(1)

    total = oracle.functions.totalRootUpdates().call()
    log.info(f"✅ Root committed (gas: {receipt.gasUsed}, update #{total})")

    # ── Save cache ──
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
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)
    log.info(f"Cache saved: {CACHE_PATH}")
    log.info("Done.")


if __name__ == "__main__":
    main()
