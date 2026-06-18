#!/usr/bin/env python3
"""
graded_bluechip_refresh.py — FOCUSED daily refresh of the graded blue-chips, so a
forecastable graded TIME SERIES actually accrues.

WHY: the broad graded_enrichment.py caches each slab for 14 days and over-calls
eBay (≈7 grade-searches/card → past the ~5000/day per-key quota → HTTP 429). So
graded prices barely refresh (~once / 2 weeks) and a time series can't form. This
job refreshes only the top-N graded blue-chips, daily, on the LIQUID tiers
(PSA 10/9/8), within quota — feeding graded_history_append a fresh daily point.

DESIGN (additive, low-risk):
  - Universe: top-N existing graded slabs by dollar-liquidity (sum of
    median_price*num_listings), the cards we'd publish/forecast.
  - Refresh PSA 10/9/8 only (3 calls/card, not 6) via the SAME eBay client +
    sanity checks as graded_enrichment.py.
  - Hard call cap << 5000/key so we never trip 429. Uses the OLD key (EBAY_APP_ID),
    which has headroom after run1.
  - Upserts graded_prices with a LONG expiry (+30d) so the broad enrichment leaves
    these to us (no double-fetch); we refresh them daily by our own list.
  - graded_history_append.py (later) captures the fresh daily fetched_at.

Run BEFORE the broad enrichment (e.g. 3:30 AM, after the 3 AM DB import).
numpy not needed; no `random`. --dry-run selects the universe without eBay/writes.
"""
import os, sys, time, base64, sqlite3, statistics, argparse, logging
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DB_PATH = os.path.join(REPO, "..", "undesirables-mcp-server", ".cache", "market_memory.sqlite")
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO, ".env"))
except Exception:
    pass

# OLD key by default (run1's key, has headroom); override with --key new.
def _key(which):
    if which == "new":
        return (os.environ.get("EBAY_ENRICHMENT_APP_ID", ""), os.environ.get("EBAY_ENRICHMENT_CLIENT_SECRET", ""))
    return (os.environ.get("EBAY_APP_ID", ""), os.environ.get("EBAY_CLIENT_SECRET", ""))

EBAY_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
GRADES = ["PSA 10", "PSA 9", "PSA 8"]        # liquid tiers only
RATE_LIMIT_DELAY = 0.5
EXPIRY_DAYS = 30

logging.basicConfig(level=logging.INFO, format="[graded-bluechip] %(message)s")
log = logging.getLogger(__name__)
_tok = {"v": None, "exp": 0.0}

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


def get_token(app_id, secret):
    if _tok["v"] and time.time() < _tok["exp"]:
        return _tok["v"]
    if not app_id or not secret:
        raise ValueError("Missing eBay key (EBAY_APP_ID/SECRET in .env)")
    creds = base64.b64encode(f"{app_id}:{secret}".encode()).decode()
    r = requests.post(EBAY_OAUTH_URL,
                      headers={"Content-Type": "application/x-www-form-urlencoded",
                               "Authorization": f"Basic {creds}"},
                      data="grant_type=client_credentials&scope=https://api.ebay.com/oauth/api_scope",
                      timeout=15)
    if r.status_code != 200:
        raise ConnectionError(f"eBay OAuth failed ({r.status_code}): {r.text[:160]}")
    d = r.json(); _tok["v"] = d["access_token"]; _tok["exp"] = time.time() + d.get("expires_in", 7200) - 60
    return _tok["v"]


def search_graded(token, card_name, grade, limit=20):
    """Same query/parse as graded_enrichment.py. Returns prices or raises on 429."""
    q = f'{card_name} "{grade}" graded'
    try:
        r = requests.get(EBAY_BROWSE_URL,
                         headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY-US"},
                         params={"q": q, "limit": limit,
                                 "filter": "buyingOptions:{FIXED_PRICE},price:[5..],priceCurrency:USD",
                                 "category_ids": "183454"},
                         timeout=15)
    except requests.RequestException as e:
        log.warning("  request error '%s': %s", q, e); return None
    if r.status_code == 429:
        return "429"
    if r.status_code != 200:
        log.warning("  search failed '%s': HTTP %d", q, r.status_code); return None
    return [float(i.get("price", {}).get("value", 0)) for i in r.json().get("itemSummaries", [])
            if float(i.get("price", {}).get("value", 0)) > 0]


def select_universe(conn, top_n, raw_max):
    # Cap raw price: generic eBay graded search can't match ultra-rares (a
    # "Charizard Star" search returns ~$165 generic Charizard listings, far below
    # its $4000 raw, so the sanity gate rightly rejects them). Focus the daily
    # refresh on the value band where eBay matches reliably -> a refreshable,
    # forecastable universe. (Ultra-rares need exact-item matching: future work.)
    # Rank by total graded LISTINGS (matchability/liquidity): cards eBay actually
    # has many listings of refresh reliably day-to-day. Dollar-liquidity front-
    # loaded sparse rares that never match. Tie-break by dollar-liquidity.
    return conn.execute("""
        SELECT product_id, MAX(card_name), MAX(game_name), MAX(raw_market_price) raw,
               SUM(COALESCE(num_listings,0)) listings,
               SUM(COALESCE(median_price,0)*COALESCE(num_listings,0)) dolvol
        FROM graded_prices
        WHERE median_price IS NOT NULL AND median_price > 0
        GROUP BY product_id
        HAVING raw >= 10 AND raw <= ?
        ORDER BY listings DESC, dolvol DESC
        LIMIT ?""", [raw_max, top_n]).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=200)
    ap.add_argument("--max-calls", type=int, default=2500)   # hard cap << 5000/key
    ap.add_argument("--raw-max", type=float, default=2000.0)  # exclude un-matchable ultra-rares
    ap.add_argument("--key", choices=["old", "new"], default="old")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    conn = sqlite3.connect(os.path.abspath(DB_PATH))
    universe = select_universe(conn, a.top_n, a.raw_max)
    planned = len(universe) * len(GRADES)
    log.info(f"universe: {len(universe)} blue-chips x {len(GRADES)} grades = {planned} planned calls "
             f"(cap {a.max_calls}, key={a.key}, dry_run={a.dry_run})")
    if a.dry_run:
        for pid, name, game, raw, listings, dolvol in universe[:8]:
            log.info(f"  pid {pid} {str(name)[:32]:34} raw=${raw} listings={listings}")
        log.info("dry-run: no eBay calls, no writes."); conn.close(); return

    app_id, secret = _key(a.key)
    token = get_token(app_id, secret)
    calls = updated = rate_limited = 0
    for pid, name, game, raw, listings, dolvol in universe:
        if calls >= a.max_calls:
            log.info("call cap reached; stopping"); break
        for grade in GRADES:
            if calls >= a.max_calls:
                break
            res = search_graded(token, name, grade); calls += 1
            if res == "429":
                rate_limited += 1; log.warning("  429 — backing off"); time.sleep(2.0); continue
            if res:
                med = round(statistics.median(res), 2); lo = round(min(res), 2); hi = round(max(res), 2)
                if grade in ("PSA 10", "PSA 9") and med < raw:      # sanity: graded >= raw
                    time.sleep(RATE_LIMIT_DELAY); continue
                if raw and med < raw * 0.1:                          # junk
                    time.sleep(RATE_LIMIT_DELAY); continue
                conn.execute(UPSERT_SQL, (pid, name, game, grade, med, lo, hi, len(res), raw,
                                          f'{name} "{grade}" graded'))
                updated += 1
            time.sleep(RATE_LIMIT_DELAY)
        conn.commit()
    conn.close()
    log.info(f"DONE: {calls} eBay calls | {updated} grade-rows upserted | {rate_limited} rate-limited "
             f"| {len(universe)} blue-chips targeted")


if __name__ == "__main__":
    main()
