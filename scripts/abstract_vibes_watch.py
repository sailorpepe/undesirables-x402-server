#!/usr/bin/env python3
"""
abstract_vibes_watch.py — hourly tripwire for a Vibes TCG / Pudgy / Orange Cap (OCG)
smart-contract launch on the ABSTRACT chain (Pudgy's zkSync-stack L2, chainid 2741).

Comprehensive approach (catches ANY deployer, not just a known wallet): on zkSync
every contract is created by the ContractDeployer SYSTEM contract
(0x0000000000000000000000000000000000008006), which emits
  ContractDeployed(address indexed deployer, bytes32 indexed bytecodeHash, address indexed contractAddress)
We poll that one event via eth_getLogs over new blocks each hour, then for each new
contract read name()/symbol() and flag anything matching Vibes/Pudgy/OCG (or deployed
by a watched wallet). Key-free, pure RPC. Deployment volume on Abstract is low
(~tens/hour), so the name/symbol probing is cheap.

What it can tell you: a new contract's ADDRESS, NAME/SYMBOL, DEPLOYER, and whether it
looks token/NFT-shaped. It canNOT explain business logic — when it flags something,
a human (me) investigates the verified source / announcement to say what it actually
is. Expect rare hits + occasional false positives (e.g. the PENGU bridge).

State checkpoint in ~/logs/abstract_vibes_watch.state.json; flag file
~/logs/VIBES_CONTRACT_DETECTED.json on a match. Run with the x402 venv python (web3).
"""
import os, json, time
from datetime import datetime, timezone
from web3 import Web3
from eth_abi import decode as abi_decode

RPC = "https://api.mainnet.abs.xyz"
SYS_DEPLOYER = "0x0000000000000000000000000000000000008006"
STATE = os.path.expanduser("~/logs/abstract_vibes_watch.state.json")
FLAG = os.path.expanduser("~/logs/VIBES_CONTRACT_DETECTED.json")
LOG = os.path.expanduser("~/logs/abstract_vibes_watch.log")

BASELINE_BLOCKS = 50_000        # first run: look back this far; afterwards incremental
CHUNK = 9_000                   # eth_getLogs block window per call
import re
MATCH = re.compile(r"vibe|pudgy|pengu|orange\s?cap|\bocg\b|birb|fishpocket|huddle|\blils?\b", re.I)
# wallets worth flagging regardless of contract name (extend as OCG/Vibes deployers surface)
WATCH_DEPLOYERS = {"0x47a4cf9833b0c0478225e4e35be2e7b7310a3ebe"}   # labeled "Pudgy Penguins: Deployer"

SEL_NAME = "0x06fdde03"
SEL_SYMBOL = "0x95d89b41"


def log(msg):
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def read_str(w3, addr, selector):
    try:
        data = bytes(w3.eth.call({"to": addr, "data": selector}))
        if not data:
            return ""
        try:
            return abi_decode(["string"], data)[0]
        except Exception:
            return data.rstrip(b"\x00").decode("utf-8", "ignore")
    except Exception:
        return ""


def main():
    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        log("RPC unreachable: " + RPC); return
    topic0 = w3.keccak(text="ContractDeployed(address,bytes32,address)")
    sys_addr = Web3.to_checksum_address(SYS_DEPLOYER)
    latest = w3.eth.block_number

    state = {}
    if os.path.exists(STATE):
        try: state = json.load(open(STATE))
        except Exception: state = {}
    start = int(state.get("last_block", 0)) + 1
    if start <= 1:
        start = max(0, latest - BASELINE_BLOCKS)        # first run baseline
        log(f"first run: baselining from block {start} (last {latest-start} blocks)")

    if start > latest:
        log(f"no new blocks (at {latest})"); return

    new = {}     # contractAddr -> deployer
    b = start
    while b <= latest:
        to = min(b + CHUNK, latest)
        for attempt in range(3):
            try:
                logs = w3.eth.get_logs({"address": sys_addr, "topics": [topic0], "fromBlock": b, "toBlock": to})
                break
            except Exception as e:
                if attempt == 2:
                    log(f"get_logs {b}-{to} failed: {e}"); logs = []
                else:
                    time.sleep(3)
        for lg in logs:
            t = lg["topics"]
            if len(t) >= 4:
                ca = Web3.to_checksum_address("0x" + t[3].hex()[-40:])
                dep = "0x" + t[1].hex()[-40:]
                new[ca] = dep.lower()
        b = to + 1

    named = matches = 0
    found = []
    for ca, dep in new.items():
        name = read_str(w3, ca, SEL_NAME)
        symbol = read_str(w3, ca, SEL_SYMBOL)
        watched = dep in WATCH_DEPLOYERS
        hit = bool(MATCH.search(name) or MATCH.search(symbol)) or watched
        if name or symbol:
            named += 1
        if hit:
            matches += 1
            tag = "🚨 VIBES-LIKE" if MATCH.search(name or "") or MATCH.search(symbol or "") else "⚠️ watched-deployer"
            log(f"{tag}: {ca} name={name!r} symbol={symbol!r} deployer={dep}")
            found.append({"address": ca, "name": name, "symbol": symbol, "deployer": dep})

    state["last_block"] = latest
    json.dump(state, open(STATE, "w"))
    log(f"scanned {start}-{latest}: {len(new)} new contracts, {named} named, {matches} flagged")
    if found:
        prev = []
        if os.path.exists(FLAG):
            try: prev = json.load(open(FLAG)).get("matches", [])
            except Exception: prev = []
        seen = {m["address"] for m in prev}
        fresh = [f for f in found if f["address"] not in seen]
        if fresh:
            json.dump({"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "matches": prev + fresh}, open(FLAG, "w"), indent=2)
            log(f"🚨🚨 {len(fresh)} NEW flagged contract(s) -> {FLAG} (investigate before acting)")


if __name__ == "__main__":
    main()
