#!/usr/bin/env python3
"""
graded_ultrarare_refresh.py — refresh ULTRA-RARE graded cards (raw > $2000) that
generic eBay search can't match.

Problem: a search for 'Charizard Star Delta Species "PSA 10" graded' returns
generic ~$165 Charizard listings, so the PSA10/9>=raw sanity gate rejects them and
the ultra-rares never refresh. Fix: EXACT-ish matching — keep only listings whose
TITLE actually contains the card's distinguishing tokens AND the grade AND whose
price sits in a sane band around the card's known graded value. Then median those.

Companion to graded_bluechip_refresh.py (which handles raw <= $2000). Reuses its
eBay client. Writes graded_prices (+30d expiry) -> feeds graded_history_append.
READ where it can; numpy not needed; no `random`. --dry-run to inspect the universe.
"""
import os, sys, time, re, math, sqlite3, statistics, argparse, logging
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import graded_bluechip_refresh as BC          # reuse get_token, _key, DB_PATH

DB_PATH = BC.DB_PATH
EBAY_BROWSE_URL = BC.EBAY_BROWSE_URL
GRADES = ["PSA 10", "PSA 9", "PSA 8"]
RATE_LIMIT_DELAY = 0.5
EXPIRY_DAYS = 30
STOP = {"the", "of", "a", "ex", "gx", "v", "vmax", "card", "holo", "full", "art"}

logging.basicConfig(level=logging.INFO, format="[ultrarare] %(message)s")
log = logging.getLogger(__name__)

UPSERT_SQL = """
INSERT INTO graded_prices
    (product_id, card_name, game_name, grade, grading_company,
     median_price, low_price, high_price, num_listings,
     raw_market_price, ebay_search_query, fetched_at, expires_at)
VALUES (?, ?, ?, ?, 'PSA', ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now', '+%d days'))
ON CONFLICT(product_id, grade) DO UPDATE SET
    median_price=excluded.median_price, low_price=excluded.low_price,
    high_price=excluded.high_price, num_listings=excluded.num_listings,
    raw_market_price=excluded.raw_market_price, ebay_search_query=excluded.ebay_search_query,
    fetched_at=datetime('now'), expires_at=datetime('now', '+%d days')
""" % (EXPIRY_DAYS, EXPIRY_DAYS)


def name_tokens(name):
    toks = [t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in STOP and len(t) >= 2]
    return toks


def search_titled(token, query, limit=40):
    """Return [(title, price)] for a (broad) query; '429' on rate limit, None on error.
    The query is intentionally broad (top distinctive tokens + grade) — the full
    name returns 0 for ultra-rares; precision comes from exact_match() on titles."""
    q = query
    try:
        r = requests.get(EBAY_BROWSE_URL,
                         headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY-US"},
                         params={"q": q, "limit": limit,
                                 "filter": "buyingOptions:{FIXED_PRICE},price:[5..],priceCurrency:USD",
                                 "category_ids": "183454"},
                         timeout=15)
    except requests.RequestException:
        return None
    if r.status_code == 429:
        return "429"
    if r.status_code != 200:
        return None
    out = []
    for it in r.json().get("itemSummaries", []):
        try:
            p = float(it.get("price", {}).get("value", 0))
            t = it.get("title", "") or ""
            if p > 0 and t:
                out.append((t, p))
        except (ValueError, TypeError):
            continue
    return out


def exact_match(title, toks, grade, price, ref):
    """Keep a listing only if its title carries the card's distinguishing tokens +
    the grade, and its price is in a sane band around the known graded value."""
    tl = title.lower()
    if grade.lower().replace(" ", "") not in tl.replace(" ", ""):     # grade present (e.g. 'psa10')
        return False
    hit = sum(1 for t in toks if t in tl)
    if hit < max(1, math.ceil(0.7 * len(toks))):                      # >=70% of distinguishing tokens
        return False
    if ref and not (0.25 * ref <= price <= 4.0 * ref):                # sane price band
        return False
    return True


def select_universe(conn, top_n, raw_min):
    rows = conn.execute("""
        SELECT product_id, MAX(card_name), MAX(game_name), MAX(raw_market_price) raw,
               SUM(COALESCE(num_listings,0)) listings
        FROM graded_prices WHERE median_price IS NOT NULL AND median_price > 0
        GROUP BY product_id HAVING raw > ?
        ORDER BY listings DESC LIMIT ?""", [raw_min, top_n]).fetchall()
    # current per-grade medians for the price band
    out = []
    for pid, name, game, raw, listings in rows:
        meds = dict(conn.execute(
            "SELECT grade, median_price FROM graded_prices WHERE product_id=?", [pid]).fetchall())
        out.append((pid, name, game, raw, meds))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=150)
    ap.add_argument("--raw-min", type=float, default=2000.0)
    ap.add_argument("--max-calls", type=int, default=1500)
    ap.add_argument("--key", choices=["old", "new"], default="old")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    conn = sqlite3.connect(os.path.abspath(DB_PATH))
    uni = select_universe(conn, a.top_n, a.raw_min)
    log.info(f"ultra-rare universe: {len(uni)} cards (raw>${a.raw_min:.0f}) x {len(GRADES)} grades "
             f"(cap {a.max_calls}, key={a.key}, dry_run={a.dry_run})")
    if a.dry_run:
        for pid, name, game, raw, meds in uni[:8]:
            log.info(f"  pid {pid} {str(name)[:34]:36} raw=${raw} tokens={name_tokens(str(name))}")
        conn.close(); return

    app, sec = BC._key(a.key)
    token = BC.get_token(app, sec)
    calls = upserts = rl = 0
    for pid, name, game, raw, meds in uni:
        toks = name_tokens(str(name))
        for grade in GRADES:
            if calls >= a.max_calls:
                break
            query = " ".join(toks[:3]) + " " + grade        # broad; precision via exact_match
            res = search_titled(token, query); calls += 1
            if res == "429":
                rl += 1; time.sleep(2.0); continue
            if not res:
                time.sleep(RATE_LIMIT_DELAY); continue
            ref = meds.get(grade) or (raw * 3 if raw else None)
            matched = [p for (t, p) in res if exact_match(t, toks, grade, p, ref)]
            if len(matched) >= 1:
                med = round(statistics.median(matched), 2)
                if grade in ("PSA 10", "PSA 9") and raw and med < raw:    # sanity
                    time.sleep(RATE_LIMIT_DELAY); continue
                conn.execute(UPSERT_SQL, (pid, name, game, grade, med, round(min(matched), 2),
                                          round(max(matched), 2), len(matched), raw,
                                          f'{name} "{grade}" graded [exact]'))
                upserts += 1
                log.info(f"  {str(name)[:30]:32} {grade}: ${med} ({len(matched)}/{len(res)} matched)")
            time.sleep(RATE_LIMIT_DELAY)
        conn.commit()
    conn.close()
    log.info(f"DONE: {calls} calls | {upserts} ultra-rare grade-rows refreshed | {rl} rate-limited "
             f"| {len(uni)} cards targeted")


if __name__ == "__main__":
    main()
