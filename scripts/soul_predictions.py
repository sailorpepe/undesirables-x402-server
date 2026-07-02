#!/usr/bin/env python3
"""
soul_predictions.py — "FICO for souls": deterministic, personality-driven card
predictions for the MINTED Undesirables (tokens 1-273), scored by the oracle at
maturity into a PUBLIC per-soul track record.

Personalities stay holder-gated; only the RATING is public. Every lock row gets
a sha256 lock_hash; the week's hashes fold into a Merkle root committed BEFORE
any prediction can be judged — third parties can recompute picks() from the
public forecast board + this file (the policy is Studio-verified and must stay
EXACTLY this math; note fits() checks regime in ("jumpy","normal") verbatim even
though the board only emits calm/medium/jumpy — spec fidelity > tidiness).

Modes:
  --lock   weekly (Mon 4:55am, after the 4:30 ledger): picks() for tokens 1-273
           against this morning's /api/v1/forecast board -> soul_predictions
           (~819 rows/week) + weekly Merkle root of lock_hashes.
  --score  daily (5:10am): mature unscored rows -> hit/push via current market
           price; rebuild soul_ratings aggregates (hit_rate, brier, rating).

Rating: matured<10 UNRATED; hit_rate >=.60 A, >=.55 B, >=.50 C, >=.45 D, else F.
DB: soul_predictions.sqlite (own file, gitignored — no contention with anything).
GPU-free by design (P2 soul-example generation owns the GPU).
"""
import os, re, json, sqlite3, hashlib, argparse, urllib.request
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DB = os.path.join(REPO, "soul_predictions.sqlite")
MARKET = os.path.expanduser("~/Documents/undesirables-mcp-server/.cache/market_memory.sqlite")
PROFILES = os.path.expanduser("~/Documents/soul_examples/soul_profiles_for_examples.json")
BOARD_URL = "http://127.0.0.1:8402/api/v1/forecast"
MINTED_MAX = 273
K = 3
PUSH_BAND = 0.01          # |move| < 1% = push (excluded from hit-rate)
HORIZON_DAYS = 30

# ── THE POLICY (Studio-verified prototype — EXACT math, do not "improve") ──
CONTRARIAN = {"The Contrarian", "The Phantom", "The Mystic"}


def picks(token_id, profile, board_cards, as_of, k=K):
    risk = profile["scores"]["risk"]
    contrarian = profile["archetype"] in CONTRARIAN

    def fits(c):
        if c.get("drift_spike"):
            return False
        if risk >= 60:
            return c.get("regime") in ("jumpy", "normal") and abs(c.get("move_pct", 0)) >= 3
        if risk <= 30:
            return (c.get("safe_hold") or "").startswith("A")
        return True

    pool = [c for c in board_cards if fits(c)] or board_cards
    pool = sorted(pool, key=lambda c: int(hashlib.sha256(
        f"{token_id}:{as_of}:{c['product_id']}".encode()).hexdigest()[:8], 16))[:k]
    out = []
    for c in pool:
        pu = c.get("prob_up", 0.5)
        if contrarian and pu >= 0.7:
            d = "down"
        elif contrarian and pu <= 0.3:
            d = "up"
        else:
            d = "up" if pu >= 0.5 else "down"
        conf = round(min(0.95, abs(pu - 0.5) * 1.2 + risk / 400 + 0.5), 2)
        out.append((c["product_id"], d, pu, conf, c["price"]))
    return out
# ── end policy ──


def lock_hash(token_id, as_of, product_id, direction, pu, conf, price):
    canon = f"{token_id}|{as_of}|{product_id}|{direction}|{pu}|{conf}|{price}"
    return hashlib.sha256(canon.encode()).hexdigest()


def merkle_root(leaves):
    """LEGACY (week 1 / 2026-07-01 only): plain sha256, sorted hex leaves,
    duplicate-last-odd. That root is already committed (calldata + contract) —
    kept only so third parties can recompute week 1."""
    layer = sorted(leaves)
    if not layer:
        return None
    layer = [bytes.fromhex(x) for x in layer]
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        layer = [hashlib.sha256(layer[i] + layer[i + 1]).digest() for i in range(0, len(layer), 2)]
    return layer[0].hex()


# ── Week 2+ convention: SoulPredictionOracle (0x5503D08D…) verifyPrediction
# expects the family standard — leaf = keccak(keccak(abi.encode(
# uint256 tokenId, uint256 weekId, uint256 productId, string direction,
# bytes32 lockHash))), zero-padded power-of-two tree, sorted-pair keccak nodes
# (identical to graded_merkle_updater.build_merkle_tree / OZ MerkleProof). ──
SOUL_ORACLE = "0x5503D08D7D167eE23AcE818bff1a00eF77A76dBF"


def oz_leaf(w3, token_id, week_id, product_id, direction, lock_hash_hex):
    from eth_abi import encode as abi_encode
    inner = abi_encode(["uint256", "uint256", "uint256", "string", "bytes32"],
                       [token_id, week_id, product_id, direction, bytes.fromhex(lock_hash_hex)])
    return w3.keccak(w3.keccak(inner))


def oz_merkle_root(w3, leaves):
    padded = list(leaves)
    while len(padded) & (len(padded) - 1):
        padded.append(b"\x00" * 32)
    if len(padded) < 2:
        padded.extend([b"\x00" * 32] * (2 - len(padded)))
    current = padded
    while len(current) > 1:
        nxt = []
        for i in range(0, len(current), 2):
            left, right = current[i], current[i + 1]
            nxt.append(w3.keccak((left + right) if left < right else (right + left)))
        current = nxt
    return current[0].hex().replace("0x", "")


def ensure_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS soul_predictions (
        token_id INTEGER, as_of TEXT, product_id INTEGER, name TEXT,
        direction TEXT, prob_up_at_lock REAL, conf REAL, price_at_lock REAL,
        matures_on TEXT, scored INTEGER DEFAULT 0, hit INTEGER, push INTEGER DEFAULT 0,
        move_pct REAL, lock_hash TEXT,
        PRIMARY KEY (token_id, as_of, product_id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS soul_ratings (
        token_id INTEGER PRIMARY KEY, matured INTEGER, hits INTEGER, pushes INTEGER,
        hit_rate REAL, brier REAL, rating TEXT, updated_at TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS merkle_roots (
        as_of TEXT PRIMARY KEY, root TEXT, n_leaves INTEGER, tx_hash TEXT)""")
    db.commit()


def do_lock():
    board = json.load(urllib.request.urlopen(urllib.request.Request(
        BOARD_URL, headers={"User-Agent": "UndesirablesOracle/souls"}), timeout=30))
    as_of = board["as_of"]
    cards = board["cards"]
    names = {c["product_id"]: c.get("name") for c in cards}
    profiles = json.load(open(PROFILES))
    db = sqlite3.connect(DB, timeout=30)
    ensure_schema(db)
    matures = (date.fromisoformat(as_of) + timedelta(days=HORIZON_DAYS)).isoformat()
    n = 0
    leaves = []
    for tok in range(1, MINTED_MAX + 1):
        prof = profiles.get(str(tok))
        if not prof:
            continue
        for pid, d, pu, conf, price in picks(tok, prof, cards, as_of):
            h = lock_hash(tok, as_of, pid, d, pu, conf, price)
            cur = db.execute(
                "INSERT OR IGNORE INTO soul_predictions "
                "(token_id, as_of, product_id, name, direction, prob_up_at_lock, conf, "
                " price_at_lock, matures_on, lock_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (tok, as_of, pid, names.get(pid), d, pu, conf, price, matures, h))
            if cur.rowcount:
                n += 1
                leaves.append(h)
    # week-2+ roots use the contract's OZ convention (verifyPrediction-compatible)
    from web3 import Web3
    w3h = Web3()
    week_id = int(as_of.replace("-", ""))
    rows_all = db.execute("SELECT token_id, product_id, direction, lock_hash "
                          "FROM soul_predictions WHERE as_of=?", (as_of,)).fetchall()
    root = oz_merkle_root(w3h, [oz_leaf(w3h, t, week_id, p, d, h) for t, p, d, h in rows_all])
    db.execute("INSERT OR REPLACE INTO merkle_roots (as_of, root, n_leaves, tx_hash) "
               "VALUES (?,?,?, (SELECT tx_hash FROM merkle_roots WHERE as_of=?))",
               (as_of, root,
                db.execute("SELECT COUNT(*) FROM soul_predictions WHERE as_of=?", (as_of,)).fetchone()[0],
                as_of))
    db.commit()
    print(f"[lock] as_of {as_of}: {n} new predictions ({MINTED_MAX} souls x {K}) | "
          f"weekly merkle root {root[:16]}…" if root else "[lock] nothing locked")
    db.close()


def do_score():
    db = sqlite3.connect(DB, timeout=30)
    ensure_schema(db)
    today = date.today().isoformat()
    mkt = sqlite3.connect(f"file:{MARKET}?mode=ro", uri=True)
    max_date = mkt.execute("SELECT MAX(date) FROM price_history WHERE product_id < 9500000").fetchone()[0]
    due = db.execute("SELECT token_id, as_of, product_id, direction, price_at_lock "
                     "FROM soul_predictions WHERE scored=0 AND matures_on<=?", (today,)).fetchall()
    scored = 0
    for tok, as_of, pid, d, p0 in due:
        row = mkt.execute("SELECT market_price FROM price_history WHERE product_id=? AND date=? "
                          "AND market_price>0 ORDER BY market_price DESC LIMIT 1", (pid, max_date)).fetchone()
        if not row or not p0:
            continue
        move = (float(row[0]) - p0) / p0
        push = 1 if abs(move) < PUSH_BAND else 0
        hit = None if push else int((d == "up") == (move > 0))
        db.execute("UPDATE soul_predictions SET scored=1, hit=?, push=?, move_pct=? "
                   "WHERE token_id=? AND as_of=? AND product_id=?",
                   (hit, push, round(move * 100, 2), tok, as_of, pid))
        scored += 1
    # rebuild aggregates
    now = datetime.now().isoformat(timespec="seconds")
    db.execute("DELETE FROM soul_ratings")
    for tok, matured, hits, pushes, brier in db.execute(
            """SELECT token_id, COUNT(*),
                      SUM(CASE WHEN push=0 AND hit=1 THEN 1 ELSE 0 END),
                      SUM(push),
                      AVG(CASE WHEN push=0 THEN (conf - hit)*(conf - hit) END)
               FROM soul_predictions WHERE scored=1 GROUP BY token_id"""):
        rated = matured - (pushes or 0)
        hr = (hits or 0) / rated if rated else None
        # Studio-approved bands (2026-07-02): A+ needs >=.70 AND matured>=20;
        # 3-9 rated = PROVISIONAL (letter + '*') so the first cohort prints
        # something on day one; <3 stays UNRATED.
        if rated < 3 or hr is None:
            rating = "UNRATED"
        else:
            if hr >= .70 and matured >= 20: letter = "A+"
            elif hr >= .60: letter = "A"
            elif hr >= .55: letter = "B"
            elif hr >= .50: letter = "C"
            elif hr >= .45: letter = "D"
            else: letter = "F"
            rating = letter + ("*" if rated < 10 else "")
        db.execute("INSERT INTO soul_ratings VALUES (?,?,?,?,?,?,?,?)",
                   (tok, matured, hits or 0, pushes or 0,
                    round(hr, 4) if hr is not None else None,
                    round(brier, 4) if brier is not None else None, rating, now))
    db.commit()
    print(f"[score] {scored} matured predictions scored | {len(due)} were due | aggregates rebuilt")
    mkt.close(); db.close()


_ORACLE_ABI = [
    {"name": "commitRoot", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "_weekId", "type": "uint256"}, {"name": "_root", "type": "bytes32"},
                {"name": "_n", "type": "uint32"}], "outputs": []},
    {"name": "commitments", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "", "type": "uint256"}],
     "outputs": [{"name": "root", "type": "bytes32"}, {"name": "nPredictions", "type": "uint32"},
                 {"name": "timestamp", "type": "uint64"}]},
]


def commit_onchain():
    """Commit the latest weekly root to the SoulPredictionOracle contract on LitVM
    (immutable per week — no overwrite path). Week 1 (2026-07-01) was v1
    calldata-committed (tx 2270231…c50) then recommitted on the contract
    (tx 0xbfdf2fc9…f355c); weeks 2+ land here directly with OZ-convention roots
    so verifyPrediction() works per-prediction."""
    from web3 import Web3
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO, ".env"))
    pk = os.getenv("LITVM_TESTNET_PK", "").strip()
    if not pk:
        print("[commit] LITVM_TESTNET_PK not set — skipped"); return
    db = sqlite3.connect(DB, timeout=30)
    row = db.execute("SELECT as_of, root, n_leaves FROM merkle_roots WHERE tx_hash IS NULL "
                     "ORDER BY as_of DESC LIMIT 1").fetchone()
    if not row:
        print("[commit] no uncommitted root"); db.close(); return
    as_of, root, n = row
    week_id = int(as_of.replace("-", ""))
    w3 = Web3(Web3.HTTPProvider("https://liteforge.rpc.caldera.xyz/http", request_kwargs={"timeout": 60}))
    acct = w3.eth.account.from_key(pk)
    oracle = w3.eth.contract(address=Web3.to_checksum_address(SOUL_ORACLE), abi=_ORACLE_ABI)
    if oracle.functions.commitments(week_id).call()[0] != b"\x00" * 32:
        print(f"[commit] week {week_id} already committed on-contract — marking done")
        db.execute("UPDATE merkle_roots SET tx_hash='(pre-committed on contract)' WHERE as_of=?", (as_of,))
        db.commit(); db.close(); return
    tx = oracle.functions.commitRoot(week_id, bytes.fromhex(root), int(n)).build_transaction({
        "chainId": w3.eth.chain_id, "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
        "gas": 150000, "gasPrice": w3.eth.gas_price})
    signed = w3.eth.account.sign_transaction(tx, pk)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    txh = w3.eth.send_raw_transaction(raw).hex()
    rc = w3.eth.wait_for_transaction_receipt(txh, timeout=120)
    db.execute("UPDATE merkle_roots SET tx_hash=? WHERE as_of=?", (txh, as_of))
    db.commit(); db.close()
    print(f"[commit] week {week_id} root {root[:16]}… -> SoulPredictionOracle tx {txh} (status {rc.status})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    if a.lock:
        do_lock()
    if a.commit or a.lock:      # lock implies commit (commitment must precede judgment)
        commit_onchain()
    if a.score:
        do_score()
    if not (a.lock or a.score or a.commit):
        print("use --lock / --score / --commit")
