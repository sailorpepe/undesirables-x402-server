#!/usr/bin/env python3
"""
graded_history_append.py — APPEND-ONLY capture of the graded-slab price series.

PROBLEM (investigated 2026-06-17): graded_prices is a SNAPSHOT — exactly one row
per (product, company, grade), overwritten on every eBay re-fetch. The on-chain
LitVM GradedPriceOracle stores only a Merkle ROOT (no recoverable prices), and the
leaf cache holds keccak hashes. So graded price history has been LOST every night.
A forecast needs a time series; this starts a forward-only one NOW.

This captures one dated row per slab into a SEPARATE append-only table, keyed by
the value's real fetch date (date(fetched_at)) so:
  - the first run salvages the staggered fetch dates already in the snapshot, and
  - re-fetches accrue new dated points (~daily, since enrichment cycles all slabs).
NEVER updates/overwrites; re-runs are idempotent. Does NOT touch graded_prices
(the /api/v1/graded endpoint + merkle builder still read it) — purely additive.

READ-ONLY on the market DB (mode=ro). Writes a separate graded_history.sqlite.
No `random`; deterministic. Do not build a graded forecast yet (phase 2).
"""
import os, sqlite3, argparse
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEF_MARKET = os.path.join(REPO, "..", "undesirables-mcp-server", ".cache", "market_memory.sqlite")
DEF_HISTORY = os.path.join(REPO, "graded_history.sqlite")


def ensure_schema(h):
    h.execute("""CREATE TABLE IF NOT EXISTS graded_price_history (
        date TEXT, product_id INTEGER, card_name TEXT, game_name TEXT,
        grading_company TEXT, grade TEXT, market_price REAL,
        low_price REAL, high_price REAL, num_listings INTEGER,
        source_fetched_at TEXT, captured_at TEXT,
        PRIMARY KEY (date, product_id, grading_company, grade))""")
    h.execute("CREATE INDEX IF NOT EXISTS idx_gph_slab ON graded_price_history(product_id, grading_company, grade, date)")
    h.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-db", default=DEF_MARKET)
    ap.add_argument("--history-db", default=DEF_HISTORY)
    a = ap.parse_args()

    mkt = sqlite3.connect(f"file:{os.path.abspath(a.market_db)}?mode=ro", uri=True)
    hist = sqlite3.connect(a.history_db)
    ensure_schema(hist)
    now = datetime.now().isoformat(timespec="seconds")

    rows = mkt.execute("""
        SELECT date(fetched_at) AS d, product_id, card_name, game_name,
               COALESCE(grading_company, 'PSA') AS company, grade,
               median_price, low_price, high_price, num_listings, fetched_at
        FROM graded_prices
        WHERE median_price IS NOT NULL AND median_price > 0
    """).fetchall()

    written = skipped = 0
    for d, pid, name, game, company, grade, median, low, high, num, fetched in rows:
        cur = hist.execute(
            "INSERT OR IGNORE INTO graded_price_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d, pid, name, game, company, grade, median, low, high, num, fetched, now))
        if cur.rowcount: written += 1
        else: skipped += 1
    hist.commit()

    # report
    tot = hist.execute("SELECT COUNT(*) FROM graded_price_history").fetchone()[0]
    dd = hist.execute("SELECT COUNT(DISTINCT date), MIN(date), MAX(date) FROM graded_price_history").fetchone()
    slabs = hist.execute("SELECT COUNT(*) FROM (SELECT 1 FROM graded_price_history GROUP BY product_id,grading_company,grade)").fetchone()[0]
    print(f"[graded-history] captured this run: {written} new, {skipped} already locked")
    print(f"[graded-history] table total: {tot} rows | {slabs} distinct slabs | "
          f"{dd[0]} dates ({dd[1]} -> {dd[2]})")
    mkt.close(); hist.close()


if __name__ == "__main__":
    main()
