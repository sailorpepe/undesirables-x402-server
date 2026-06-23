#!/usr/bin/env python3
"""
abstract_vibes_watch.py — hourly watcher for a Vibes TCG / Pudgy / Orange Cap (OCG)
smart-contract launch on the ABSTRACT chain (Pudgy's zkSync-stack L2, chainid 2741).

Why this shape: Abstract is zkSync-stack, so you CANNOT derive a new contract address
from a deployer's nonce (its CREATE formula differs from Ethereum). The reliable way
to get actual new-contract addresses is the explorer index — Etherscan V2 multichain
(abscan's native V1 API is deprecated). So we poll `txlist` for known deployer
wallet(s), find contract-creation txns, look up each new contract's name, and flag any
that match Vibes/Pudgy/OCG. State is checkpointed so each run only reports new blocks.

Detection caveat: Vibes is built by Orange Cap Games, which may deploy from its OWN
wallet (not the labeled Pudgy deployer). Add any newly-discovered Vibes/OCG deployer
to WATCH below. Factory/CREATE2 deploys that don't show as a top-level creation tx
from a watched EOA won't be caught — accept this until a real deployer is known.

Needs a FREE Etherscan API key (one key covers all chains incl. Abstract 2741):
  https://etherscan.io/apis  ->  put ETHERSCAN_API_KEY=... in the x402 .env
Stdlib only. Read-only. Logs to ~/logs/abstract_vibes_watch.log; writes a flag file
~/logs/VIBES_CONTRACT_DETECTED.json on a match.
"""
import os, re, sys, json, time, urllib.parse, urllib.request
from datetime import datetime, timezone

CHAIN_ID = 2741
API = "https://api.etherscan.io/v2/api"
STATE = os.path.expanduser("~/logs/abstract_vibes_watch.state.json")
FLAG = os.path.expanduser("~/logs/VIBES_CONTRACT_DETECTED.json")
ENV = os.path.expanduser("~/Documents/undesirables-x402-server/.env")

# Labeled on Abstract as "Pudgy Penguins: Deployer". OCG may differ — extend as found.
WATCH = {
    "Pudgy Penguins Deployer": "0x47a4cf9833b0c0478225e4e35be2e7b7310a3ebe",
}
MATCH = re.compile(r"vibe|pudgy|pengu|orange\s?cap|\bocg\b|birb|fishpocket|huddle|lils", re.I)


def _key():
    k = os.environ.get("ETHERSCAN_API_KEY", "")
    if not k and os.path.exists(ENV):
        for line in open(ENV):
            m = re.match(r"^ETHERSCAN_API_KEY=(.*)$", line.strip())
            if m:
                k = m.group(1).strip().strip('"').strip("'")
    return k


def _get(params, key):
    params = {**params, "chainid": CHAIN_ID, "apikey": key}
    url = API + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "UndesirablesOracle/1.0"}), timeout=25) as r:
        return json.load(r)


def log(msg):
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
    print(line)
    with open(os.path.expanduser("~/logs/abstract_vibes_watch.log"), "a") as f:
        f.write(line + "\n")


def main():
    key = _key()
    if not key:
        log("NO ETHERSCAN_API_KEY — watcher installed but inactive. Get a free key at "
            "https://etherscan.io/apis and add ETHERSCAN_API_KEY=... to the x402 .env. "
            "Covers Abstract (chainid 2741).")
        return
    state = {}
    if os.path.exists(STATE):
        try: state = json.load(open(STATE))
        except Exception: state = {}

    found = []
    for label, addr in WATCH.items():
        addr = addr.lower()
        start = int(state.get(addr, 0))
        try:
            res = _get({"module": "account", "action": "txlist", "address": addr,
                        "startblock": start, "endblock": 99999999, "sort": "asc"}, key)
        except Exception as e:
            log(f"{label}: txlist error: {e}"); continue
        txs = res.get("result") or []
        if not isinstance(txs, list):
            log(f"{label}: unexpected response: {str(res.get('result'))[:80]}"); continue
        max_block = start
        creations = 0
        for tx in txs:
            max_block = max(max_block, int(tx.get("blockNumber", 0)))
            # contract creation = empty 'to' + a contractAddress
            ca = (tx.get("contractAddress") or "").strip()
            if tx.get("to"):           # has a recipient -> not a creation
                continue
            if not ca:
                continue
            creations += 1
            name = ""
            try:
                src = _get({"module": "contract", "action": "getsourcecode", "address": ca}, key)
                r0 = (src.get("result") or [{}])[0]
                name = r0.get("ContractName", "") or ""
            except Exception:
                pass
            matched = bool(MATCH.search(name)) or (not name)  # unverified (no name) is worth flagging too
            tag = "🚨 POSSIBLE VIBES" if MATCH.search(name) else ("⚠️ unnamed/unverified" if not name else "·")
            log(f"{label}: new contract {ca} name={name!r} block={tx.get('blockNumber')} {tag}")
            if MATCH.search(name):
                found.append({"deployer": label, "address": ca, "name": name,
                              "block": tx.get("blockNumber"), "tx": tx.get("hash")})
        state[addr] = max_block
        log(f"{label}: scanned to block {max_block} | {creations} creation tx(s) this window")

    json.dump(state, open(STATE, "w"))
    if found:
        json.dump({"detected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "matches": found}, open(FLAG, "w"), indent=2)
        log(f"🚨🚨 {len(found)} VIBES-LIKE CONTRACT(S) DETECTED — wrote {FLAG}")


if __name__ == "__main__":
    main()
