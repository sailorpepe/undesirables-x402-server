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
import os, re, json, math, sqlite3, hashlib, argparse, urllib.request
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
# VOLATILITY-SCALED PUSH (2026-07-29, sailorpepe-approved, FUTURE COHORTS ONLY).
# A flat 1% band pushed 30.5% of the first cohort's scored picks, and because a
# soul only makes 3 picks a week a single push drops it below the 3 rated calls
# a letter needs — 182 of 273 souls lost at least one pick that way.
# The deeper problem is that 1% means different things on different cards: it is
# most of a calm card's typical 30-day move and a rounding error on a jumpy one.
# So a push now requires the move to be small in BOTH senses:
#       push  <=>  |move| < min(PUSH_BAND, PUSH_SIGMA_K * sigma_30d)
# min() is deliberate — the band can only ever NARROW, so no card class gets
# worse. Measured over 25,387 realised 30-day moves: overall push 30.3% -> 23.7%,
# calm 32.5% -> 19.1%, jumpy UNCHANGED at 33.7%. P(all 3 picks clean) 34% -> 44%.
# k=0.20 chosen over 0.10 (which would cut pushes further) because when I pick
# the parameter, the less self-serving of two defensible values is the right one.
#
# APPLIES ONLY WHERE sigma_at_lock IS RECORDED, i.e. locks from 2026-08-03 on.
# Every already-locked cohort keeps the flat rule it was made under. Changing how
# an existing prediction is scored after the fact is how track records become
# worthless, even when the change looks fair.
PUSH_SIGMA_K = 0.20
# STALENESS CAP — SCORING RULE v2 (2026-07-31, sailorpepe-approved). The first
# grade print left 52 due rows across 49 souls silently unscored: illiquid
# sealed products (booster cases, reserved-list lands) stop printing daily
# prices mid-window, and v1 demanded a print at exactly max_date. v2 scores
# against the most recent print ON OR BEFORE maturity when it is at most this
# many days stale; otherwise the row is VOIDED — scored=1, voided=1, a reason
# string, excluded from every rating denominator. A void is a first-class
# outcome, not a skip: a skip with no record is indistinguishable from a bug.
# 7 and not 14 because a 30-day call graded on a >=23-day observation is still
# the question that was asked; graded on a 16-day observation it is not.
# Voiding is final by construction (past dates never gain prints), and safe for
# provenance: the merkle leaf commits (token, week, product, direction,
# lock_hash) — outcomes were never in the tree, so no published root moves.
STALENESS_CAP_DAYS = 7
MIN_RATED = 3             # rated calls before a PROVISIONAL letter prints
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

# ── DUAL-CHAIN (added 2026-07-27) ────────────────────────────────────────────
# LiteForge is a TESTNET. A reset would not cost us a day, it would erase the
# entire prediction track record retroactively -- and a track record is the only
# thing that makes "we called it in advance" mean anything. So every root also
# goes to Base mainnet. Cost: ~$0.004/week.
#
# Weeks 2026-07-01, -07-05 and -07-12 are LiteForge-ONLY and always will be: the
# Base contract structurally refuses roots older than its commit window, and
# copying them across would produce entries whose block timestamps contradict
# the weeks they label -- exactly the manufactured history this design exists to
# prevent. LiteForge remains their historical anchor. That gap is documented,
# not papered over.
#
# WINDOW SAFETY: the Base contract allows a commit up to MAX_COMMIT_LAG_DAYS=10
# after the week it labels, while predictions mature at HORIZON_DAYS=30. Worst
# case a root still lands 20 days BEFORE its outcome resolves, so the window
# cannot admit a post-hoc commitment. If HORIZON_DAYS is ever reduced below ~13
# this coupling breaks and the on-chain window must be tightened with it.
SOUL_ORACLE_BASE_DEPLOYMENT = os.path.join(REPO, "soul_deployment_base.json")


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
    # baseline_rate / skill added 2026-07-28. A hit rate published without its
    # baseline is a misleading number: the 07-31 dry run scored 80.7% in a market
    # where 91.0% of cards ROSE, so an all-"up" strategy beat us by 10 points
    # while "80.7%" reads like skill. CREATE TABLE IF NOT EXISTS will not add a
    # column to a live table, so apply it explicitly.
    _sr_cols = {r[1] for r in db.execute("PRAGMA table_info(soul_ratings)")}
    for _c in ("baseline_rate", "skill"):
        if _c not in _sr_cols:
            db.execute(f"ALTER TABLE soul_ratings ADD COLUMN {_c} REAL")
    db.execute("""CREATE TABLE IF NOT EXISTS merkle_roots (
        as_of TEXT PRIMARY KEY, root TEXT, n_leaves INTEGER, tx_hash TEXT)""")
    # base_tx_hash added 2026-07-27 for the Base mainnet mirror. CREATE TABLE IF
    # NOT EXISTS will not add a column to an existing table, so it is applied
    # explicitly -- otherwise a fresh DB and a live DB would drift apart.
    if "base_tx_hash" not in {r[1] for r in db.execute("PRAGMA table_info(merkle_roots)")}:
        db.execute("ALTER TABLE merkle_roots ADD COLUMN base_tx_hash TEXT")
    # sigma_at_lock: the card's annualised vol AS OF the lock. Its presence is
    # what selects the volatility-scaled push rule, so old rows (NULL) keep the
    # flat band automatically — no date cutoff to get wrong.
    if "sigma_at_lock" not in {r[1] for r in db.execute("PRAGMA table_info(soul_predictions)")}:
        db.execute("ALTER TABLE soul_predictions ADD COLUMN sigma_at_lock REAL")
    # voided / void_reason: scoring rule v2 (see STALENESS_CAP_DAYS). A row the
    # market stopped pricing gets an explicit, queryable outcome instead of
    # sitting scored=0 forever and re-running through the loop every morning.
    _sp_cols = {r[1] for r in db.execute("PRAGMA table_info(soul_predictions)")}
    if "voided" not in _sp_cols:
        db.execute("ALTER TABLE soul_predictions ADD COLUMN voided INTEGER DEFAULT 0")
    if "void_reason" not in _sp_cols:
        db.execute("ALTER TABLE soul_predictions ADD COLUMN void_reason TEXT")
    db.commit()


def sigma_map(as_of):
    """{product_id: sigma_annual} from the forecast ledger for this board date.

    The 04:30 ledger write precedes the 04:55 lock on the same machine, so the
    row is already there. Read from the LEDGER rather than the board API because
    /api/v1/forecast does not expose sigma_annual and this needs no public
    endpoint change. Missing sigma is not an error — the prediction simply keeps
    the flat push band, which is the correct default.
    """
    try:
        lg = sqlite3.connect(f"file:{os.path.join(REPO,'forecast_ledger.sqlite')}?mode=ro",
                             uri=True)
        m = {pid: sg for pid, sg in lg.execute(
            "SELECT product_id, sigma_annual FROM forecast_ledger "
            "WHERE forecast_date=? AND horizon=30 AND sigma_annual>0", (as_of,))}
        lg.close()
        return m
    except Exception as e:
        print(f"[lock] sigma lookup unavailable ({str(e)[:60]}) — flat push band")
        return {}


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
    sigmas = sigma_map(as_of)
    print(f"[lock] sigma available for {len(sigmas)} of {len(cards)} board cards"
          f"{' — volatility-scaled push band ACTIVE' if sigmas else ' — flat push band'}")
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
                " price_at_lock, matures_on, lock_hash, sigma_at_lock) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (tok, as_of, pid, names.get(pid), d, pu, conf, price, matures, h,
                 sigmas.get(pid)))
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
    due = db.execute("SELECT token_id, as_of, product_id, direction, price_at_lock, "
                     "sigma_at_lock, matures_on FROM soul_predictions "
                     "WHERE scored=0 AND matures_on<=?", (today,)).fetchall()
    scored = voided = 0
    for tok, as_of, pid, d, p0, sig, mat in due:
        # v2 lookup: latest print on or before maturity (was: exactly at
        # max_date, which for an on-time run is the same print — TCGCSV lags a
        # day — but returned nothing for products that stopped printing).
        # date DESC then market_price DESC keeps v1's highest-variant tie-break.
        row = mkt.execute("SELECT date, market_price FROM price_history "
                          "WHERE product_id=? AND date<=? AND market_price>0 "
                          "ORDER BY date DESC, market_price DESC LIMIT 1", (pid, mat)).fetchone()
        stale = (date.fromisoformat(mat) - date.fromisoformat(row[0])).days if row else None
        if not p0 or row is None or stale > STALENESS_CAP_DAYS:
            reason = ("no_lock_price" if not p0 else
                      "no_print_on_or_before_maturity" if row is None else
                      f"last_print_{stale}d_stale_cap_{STALENESS_CAP_DAYS}d")
            db.execute("UPDATE soul_predictions SET scored=1, voided=1, void_reason=? "
                       "WHERE token_id=? AND as_of=? AND product_id=?",
                       (reason, tok, as_of, pid))
            voided += 1
            continue
        move = (float(row[1]) - p0) / p0
        # sigma present -> volatility-scaled band (narrower or equal, never wider).
        # sigma absent  -> the flat rule this prediction was locked under.
        band = PUSH_BAND
        if sig and sig > 0:
            band = min(PUSH_BAND, PUSH_SIGMA_K * sig * math.sqrt(HORIZON_DAYS / 365.0))
        push = 1 if abs(move) < band else 0
        hit = None if push else int((d == "up") == (move > 0))
        db.execute("UPDATE soul_predictions SET scored=1, hit=?, push=?, move_pct=? "
                   "WHERE token_id=? AND as_of=? AND product_id=?",
                   (hit, push, round(move * 100, 2), tok, as_of, pid))
        scored += 1
    # rebuild aggregates
    now = datetime.now().isoformat(timespec="seconds")
    db.execute("DELETE FROM soul_ratings")
    for tok, matured, hits, pushes, brier, rose in db.execute(
            """SELECT token_id, COUNT(*),
                      SUM(CASE WHEN push=0 AND hit=1 THEN 1 ELSE 0 END),
                      SUM(push),
                      AVG(CASE WHEN push=0 THEN (conf - hit)*(conf - hit) END),
                      SUM(CASE WHEN push=0 AND move_pct>0 THEN 1 ELSE 0 END)
               FROM soul_predictions WHERE scored=1 AND COALESCE(voided,0)=0
               GROUP BY token_id"""):
        rated = matured - (pushes or 0)
        hr = (hits or 0) / rated if rated else None
        # BASELINE = what saying "up" on THIS SOUL'S OWN PICKS would have scored.
        # Per-soul, not global, so it controls for card selection — otherwise a
        # soul that simply drew easier cards looks skilled. `skill` is the only
        # honest read of direction-calling ability in a trending market.
        base = (rose or 0) / rated if rated else None
        skill = (hr - base) if (hr is not None and base is not None) else None
        # Studio-approved bands (2026-07-02): A+ needs >=.70 AND matured>=20;
        # 3-9 rated = PROVISIONAL (letter + '*') so the first cohort prints
        # something on day one; <3 stays UNRATED.
        # THRESHOLD = 3, restored 2026-07-29 (sailorpepe's call, and correct).
        # At rated=3 the only attainable rates are 0/33/67/100%, so B, C and D
        # are mathematically unreachable and every soul lands A or F. I briefly
        # raised this to 10, which silently cancelled the 07-31 print he had
        # waited a month for. Raising a threshold was the wrong fix for the same
        # reason hiding the 80.7% hit rate would have been: the answer to a
        # number that needs context is to SHIP the context, not suppress the
        # number. `skill`, `baseline_rate`, `matured` and `rating_note` now ride
        # with every rating, and the '*' suffix marks provisional. A reader can
        # see in one response that an A* means "2 or 3 of 3". That is honest.
        # Do NOT raise this again without a plan for what prints in the gap.
        if rated < MIN_RATED or hr is None:
            rating = "UNRATED"
        else:
            if hr >= .70 and matured >= 20: letter = "A+"
            elif hr >= .60: letter = "A"
            elif hr >= .55: letter = "B"
            elif hr >= .50: letter = "C"
            elif hr >= .45: letter = "D"
            else: letter = "F"
            rating = letter + ("*" if rated < 10 else "")
        db.execute(
            "INSERT INTO soul_ratings (token_id, matured, hits, pushes, hit_rate,"
            " brier, rating, updated_at, baseline_rate, skill)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tok, matured, hits or 0, pushes or 0,
             round(hr, 4) if hr is not None else None,
             round(brier, 4) if brier is not None else None, rating, now,
             round(base, 4) if base is not None else None,
             round(skill, 4) if skill is not None else None))
    db.commit()
    print(f"[score] {scored} matured predictions scored | {voided} voided (rule v2) | "
          f"{len(due)} were due | aggregates rebuilt")
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


def commit_base(as_of, root, n):
    """Mirror one weekly root to Base mainnet. NEVER raises.

    Deliberately isolated from the LiteForge path and called only after it, so
    that a Base/RPC problem can never interfere with the primary commit. The
    Base contract is write-once and enforces its own commit window, so this is
    safe to re-run any number of times.
    """
    try:
        from web3 import Web3
        if not os.path.exists(SOUL_ORACLE_BASE_DEPLOYMENT):
            print("[base] no deployment record — skipped")
            return None
        dep = json.load(open(SOUL_ORACLE_BASE_DEPLOYMENT))
        key = os.getenv("ALCHEMY_API_KEY", "").strip()
        if not key:
            print("[base] ALCHEMY_API_KEY missing — skipped")
            return None
        pk = os.getenv("LITVM_TESTNET_PK", "").strip()
        w3 = Web3(Web3.HTTPProvider(f"https://base-mainnet.g.alchemy.com/v2/{key}",
                                    request_kwargs={"timeout": 60}))
        c = w3.eth.contract(address=Web3.to_checksum_address(dep["contract"]),
                            abi=dep["abi"])
        week_id = int(as_of.replace("-", ""))
        if c.functions.commitments(week_id).call()[0] != b"\x00" * 32:
            print(f"[base] week {week_id} already committed")
            return "(already on base)"

        lag = c.functions.currentEpochDay().call() - \
            c.functions.weekIdToEpochDay(week_id).call()
        if lag > c.functions.MAX_COMMIT_LAG_DAYS().call():
            print(f"[base] week {week_id} is {lag}d old — outside the commit "
                  f"window; it stays LiteForge-only by design")
            return None

        acct = w3.eth.account.from_key(pk)
        fn = c.functions.commitRoot(week_id, bytes.fromhex(root), int(n))
        tx = fn.build_transaction({
            "chainId": 8453, "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
            "gas": int(fn.estimate_gas({"from": acct.address}) * 1.3),
            "gasPrice": int(w3.eth.gas_price * 2)})
        signed = w3.eth.account.sign_transaction(tx, pk)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        txh = w3.eth.send_raw_transaction(raw).hex()
        rc = w3.eth.wait_for_transaction_receipt(txh, timeout=240)
        if rc.status != 1:
            print(f"[base] week {week_id} REVERTED tx {txh}")
            return None
        print(f"[base] week {week_id} committed tx 0x{txh.lstrip('0x')} "
              f"(lag {lag}d, matures in {HORIZON_DAYS - lag}d)")
        return txh
    except Exception as e:
        print(f"[base] commit failed (non-fatal): {str(e)[:120]}")
        return None


def _commit_liteforge():
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
    # 2026-07-20: the Jul-19 commit died with "max fee per gas less than block
    # base fee" — bare w3.eth.gas_price (fetched pre-send) was ticked past by a
    # transient LitVM base-fee rise. Buffer 3x + retry with escalating gas so a
    # gas blip can never leave a weekly root uncommitted (load-bearing: roots
    # must land BEFORE outcomes). Idempotent: the commitments() guard above and
    # the on-chain immutability mean a re-run of an already-mined week is a noop.
    last_err = None
    for attempt, mult in enumerate((3, 6, 12), 1):
        try:
            tx = oracle.functions.commitRoot(week_id, bytes.fromhex(root), int(n)).build_transaction({
                "chainId": w3.eth.chain_id, "from": acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
                "gas": 150000, "gasPrice": int(w3.eth.gas_price * mult)})
            signed = w3.eth.account.sign_transaction(tx, pk)
            raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
            txh = w3.eth.send_raw_transaction(raw).hex()
            rc = w3.eth.wait_for_transaction_receipt(txh, timeout=120)
            db.execute("UPDATE merkle_roots SET tx_hash=? WHERE as_of=?", (txh, as_of))
            db.commit(); db.close()
            print(f"[commit] week {week_id} root {root[:16]}… -> SoulPredictionOracle tx {txh} (status {rc.status}, {mult}x gas)")
            return
        except Exception as e:
            last_err = e
            print(f"[commit] attempt {attempt} ({mult}x gas) failed: {str(e)[:120]}")
    db.close()
    raise RuntimeError(f"[commit] week {week_id} root NOT committed after 3 attempts: {last_err}")


def commit_onchain():
    """Commit the latest weekly root to BOTH chains.

    LiteForge runs first and unchanged -- it is the path that has carried every
    week so far and must not be destabilised days before a grade print. Base
    runs afterwards and swallows its own errors, so the mainnet mirror can never
    take down the primary commit.
    """
    _commit_liteforge()
    db = sqlite3.connect(DB, timeout=30)
    try:
        row = db.execute(
            "SELECT as_of, root, n_leaves FROM merkle_roots "
            "WHERE base_tx_hash IS NULL ORDER BY as_of DESC LIMIT 1").fetchone()
        if not row:
            print("[base] nothing pending")
            return
        as_of, root, n = row
        txh = commit_base(as_of, root, n)
        if txh:
            db.execute("UPDATE merkle_roots SET base_tx_hash=? WHERE as_of=?",
                       (txh, as_of))
            db.commit()
    finally:
        db.close()


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
