#!/usr/bin/env python3
"""
Graded Card Price Enrichment Pipeline
=====================================
Uses eBay Browse API to find PSA/BGS graded listing prices for top TCG cards.
Runs daily at 4 AM via launchd (after the TCGCSV pipeline at 3 AM).

Budget: 4,000 eBay API calls/day → 1,000 cards/day (4 grades each).
Skips sealed products (boxes, cases, displays) — those don't get graded.
Processes cards by raw market price descending (most valuable first).
Graded prices cached for 14 days before refresh.

Data source: eBay Browse API (authorized, free tier, 5,000 calls/day).
"""
import sqlite3
import os
import sys
import base64
import logging
import statistics
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("[ERROR] pip install requests", file=sys.stderr)
    sys.exit(1)

# Load .env from the x402 server directory
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.expanduser("~/Documents/undesirables-x402-server/.env"))
except ImportError:
    pass  # dotenv not available — rely on env vars from shell


# ═══════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════
DB_PATH = os.path.expanduser(
    "~/Documents/undesirables-mcp-server/.cache/market_memory.sqlite"
)
LOG_PATH = os.path.expanduser("~/logs/graded_enrichment.log")

DAILY_BUDGET = int(os.environ.get("ENRICHMENT_BUDGET", 4998))
CALLS_PER_CARD = 6           # PSA 10, 9, 8, 7, 6, 5 — one call each
CARDS_PER_DAY = DAILY_BUDGET // CALLS_PER_CARD
CACHE_DAYS = 14              # Refresh graded prices every 14 days
MIN_RAW_PRICE = 10.0         # Only enrich cards worth $10+ raw
RATE_LIMIT_DELAY = 0.5       # Seconds between eBay calls

EBAY_APP_ID = os.environ.get("EBAY_ENRICHMENT_APP_ID", os.environ.get("EBAY_APP_ID", ""))
EBAY_CLIENT_SECRET = os.environ.get("EBAY_ENRICHMENT_CLIENT_SECRET", os.environ.get("EBAY_CLIENT_SECRET", ""))
EBAY_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

# Liquid tiers only (PSA 8-10). Dropped 5/6/7: ~6 calls/card -> 3 halves eBay
# usage so the run stays under the ~5000/day/key quota and stops getting 429'd.
# Low grades are illiquid anyway; the value/liquidity is in PSA 8-10.
GRADES = ["PSA 10", "PSA 9", "PSA 8"]

# Product type filter: "singles", "sealed", or "all"
ENRICHMENT_MODE = os.environ.get("ENRICHMENT_MODE", "all")
SEALED_KEYWORDS = [
    "Booster Box", "Booster Pack", "Booster Case", "Booster Bundle",
    "Display Case", "Display Master", "Box Case",
    "Elite Trainer", "Premium Collection",
    "Tin Display", "Tin Case", "Tin Set", "Mini Tin",
    "Binder Collection", "Gift Collection",
    "Sleeved Booster", "Pack Display", "Tournament Pack",
    "Commander Deck", "Theme Deck", "Starter Deck", "Deck Box", "Deck Case",
    "Bundle Case", "Blister", "Gift Bundle",
    "Box Set", "Set of", "Briefcase",
]

# Category ID → Game name (mirrors GAME_CATEGORIES in server.py)
CATEGORY_TO_GAME = {
    1: "Magic",
    2: "Yu-Gi-Oh",
    3: "Pokemon",
    62: "Flesh and Blood",
    63: "Digimon",
    68: "One Piece",
    71: "Lorcana",
    79: "Star Wars",
    80: "Dragon Ball",
    81: "Union Arena",
    85: "Pokemon Japan",
    86: "Gundam",
    89: "LoL Riftbound",
}


# ═══════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("graded_enrichment")


# ═══════════════════════════════════════════════════
# eBay OAuth
# ═══════════════════════════════════════════════════
_token = None
_token_exp = 0.0


def get_token() -> str:
    """Get or refresh eBay OAuth2 client-credentials token."""
    global _token, _token_exp
    if _token and time.time() < _token_exp:
        return _token

    if not EBAY_APP_ID or not EBAY_CLIENT_SECRET:
        raise ValueError(
            "Missing EBAY_APP_ID or EBAY_CLIENT_SECRET environment variables. "
            "Set them in .env or the launchd plist."
        )

    creds = base64.b64encode(
        f"{EBAY_APP_ID}:{EBAY_CLIENT_SECRET}".encode()
    ).decode()

    r = requests.post(
        EBAY_OAUTH_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {creds}",
        },
        data="grant_type=client_credentials&scope=https://api.ebay.com/oauth/api_scope",
        timeout=15,
    )

    if r.status_code != 200:
        raise ConnectionError(f"eBay OAuth failed ({r.status_code}): {r.text[:200]}")

    data = r.json()
    _token = data["access_token"]
    _token_exp = time.time() + data.get("expires_in", 7200) - 60
    log.info("eBay OAuth token acquired (expires in %ds)", data.get("expires_in", 0))
    return _token


# ═══════════════════════════════════════════════════
# eBay Browse API Search
# ═══════════════════════════════════════════════════
def search_graded(card_name: str, grade: str, limit: int = 20) -> list[float]:
    """
    Search eBay Browse API for graded card listings.
    Returns a list of asking prices (USD).
    """
    token = get_token()
    # Quote the grade for exact phrase match — prevents "PSA 10 potential" junk
    query = f'{card_name} "{grade}" graded'

    try:
        r = requests.get(
            EBAY_BROWSE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY-US",
            },
            params={
                "q": query,
                "limit": limit,
                "filter": "buyingOptions:{FIXED_PRICE},price:[5..],priceCurrency:USD",
                "category_ids": "183454",  # Trading Card Singles
            },
            timeout=15,
        )
    except requests.RequestException as e:
        log.warning("  eBay request error for '%s': %s", query, e)
        return []

    if r.status_code != 200:
        log.warning("  eBay search failed for '%s': HTTP %d", query, r.status_code)
        return []

    items = r.json().get("itemSummaries", [])
    prices = []
    for item in items:
        try:
            price = float(item.get("price", {}).get("value", 0))
            if price > 0:
                prices.append(price)
        except (ValueError, TypeError):
            continue

    return prices


# ═══════════════════════════════════════════════════
# Database Setup
# ═══════════════════════════════════════════════════
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS graded_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    card_name TEXT NOT NULL,
    game_name TEXT,
    grade TEXT NOT NULL,
    grading_company TEXT,
    median_price REAL,
    low_price REAL,
    high_price REAL,
    num_listings INTEGER,
    raw_market_price REAL,
    ebay_search_query TEXT,
    source TEXT DEFAULT 'ebay_browse_api',
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME,
    UNIQUE(product_id, grade)
);
CREATE INDEX IF NOT EXISTS idx_graded_product ON graded_prices(product_id);
CREATE INDEX IF NOT EXISTS idx_graded_game ON graded_prices(game_name);
CREATE INDEX IF NOT EXISTS idx_graded_expires ON graded_prices(expires_at);
CREATE INDEX IF NOT EXISTS idx_graded_price ON graded_prices(median_price DESC);
"""

UPSERT_SQL = """
INSERT INTO graded_prices
    (product_id, card_name, game_name, grade, grading_company,
     median_price, low_price, high_price, num_listings,
     raw_market_price, ebay_search_query, fetched_at, expires_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now', '+14 days'))
ON CONFLICT(product_id, grade) DO UPDATE SET
    median_price = excluded.median_price,
    low_price = excluded.low_price,
    high_price = excluded.high_price,
    num_listings = excluded.num_listings,
    raw_market_price = excluded.raw_market_price,
    ebay_search_query = excluded.ebay_search_query,
    fetched_at = datetime('now'),
    expires_at = datetime('now', '+14 days')
"""


# ═══════════════════════════════════════════════════
# Main Pipeline
# ═══════════════════════════════════════════════════
def run():
    log.info("=" * 60)
    log.info(
        "Graded Enrichment Pipeline — %s",
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    log.info("Budget: %d calls, %d cards/day", DAILY_BUDGET, CARDS_PER_DAY)

    if not os.path.exists(DB_PATH):
        log.error("Database not found: %s", DB_PATH)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    # Create table + indexes
    conn.executescript(SCHEMA_SQL)

    # Get the latest price date
    max_date = conn.execute(
        "SELECT MAX(date) FROM price_history"
    ).fetchone()[0]
    if not max_date:
        log.error("No price data in database")
        conn.close()
        sys.exit(1)

    log.info("Latest price date: %s", max_date)

    # Select cards that need enrichment:
    #   - raw price >= $10 on the latest date
    #   - NOT already enriched with a valid (non-expired) entry
    # Ordered by raw price DESC (most valuable first)
    # Build sealed filter based on ENRICHMENT_MODE
    sealed_filter = ""
    if ENRICHMENT_MODE == "singles":
        conditions = " AND ".join(
            f"c.clean_name NOT LIKE '%{kw}%'" for kw in SEALED_KEYWORDS
        )
        sealed_filter = f"AND ({conditions})"
        log.info("Mode: SINGLES only (excluding sealed products)")
    elif ENRICHMENT_MODE == "sealed":
        conditions = " OR ".join(
            f"c.clean_name LIKE '%{kw}%'" for kw in SEALED_KEYWORDS
        )
        sealed_filter = f"AND ({conditions})"
        log.info("Mode: SEALED products only")
    else:
        log.info("Mode: ALL products")

    cards = conn.execute(
        f"""
        SELECT DISTINCT c.product_id, c.clean_name, c.category_id, ph.market_price
        FROM cards c
        JOIN price_history ph ON c.product_id = ph.product_id
            AND ph.date = ?
        WHERE ph.market_price >= ?
          AND c.product_id NOT IN (
              SELECT DISTINCT product_id FROM graded_prices
              WHERE expires_at > datetime('now')
          )
          {sealed_filter}
        ORDER BY ph.market_price DESC
        LIMIT ?
        """,
        (max_date, MIN_RAW_PRICE, CARDS_PER_DAY),
    ).fetchall()

    log.info("Found %d cards to enrich", len(cards))

    if not cards:
        log.info("Nothing to enrich — all cards are up to date")
        conn.close()
        return

    calls_made = 0
    cards_enriched = 0

    for product_id, card_name, category_id, raw_price in cards:
        if calls_made >= DAILY_BUDGET:
            log.info("Daily budget exhausted at %d calls", calls_made)
            break

        game_name = CATEGORY_TO_GAME.get(category_id, "Other")
        log.info(
            "  Enriching: %s ($%.2f, %s)",
            card_name[:50],
            raw_price,
            game_name,
        )

        for grade in GRADES:
            if calls_made >= DAILY_BUDGET:
                break

            prices = search_graded(card_name, grade)
            calls_made += 1

            if prices:
                median_p = round(statistics.median(prices), 2)
                low_p = round(min(prices), 2)
                high_p = round(max(prices), 2)

                # === Price sanity checks ===
                # PSA 10/9 should NEVER be cheaper than raw
                if grade in ("PSA 10", "PSA 9") and median_p < raw_price:
                    log.warning(
                        "    ⚠️ SKIP %s %s: $%.2f < raw $%.2f — bad listing",
                        card_name[:40], grade, median_p, raw_price,
                    )
                    time.sleep(RATE_LIMIT_DELAY)
                    continue

                # Any grade below 10% of raw is junk data
                if median_p < raw_price * 0.1:
                    log.warning(
                        "    ⚠️ SKIP %s %s: $%.2f < 10%% of raw $%.2f — junk",
                        card_name[:40], grade, median_p, raw_price,
                    )
                    time.sleep(RATE_LIMIT_DELAY)
                    continue

                conn.execute(
                    UPSERT_SQL,
                    (
                        product_id,
                        card_name,
                        game_name,
                        grade,
                        "PSA",
                        median_p,
                        low_p,
                        high_p,
                        len(prices),
                        raw_price,
                        f'{card_name} "{grade}" graded',
                    ),
                )
                log.info(
                    "    %s: $%.2f ($%.2f–$%.2f, %d listings)",
                    grade,
                    median_p,
                    low_p,
                    high_p,
                    len(prices),
                )
            else:
                log.info("    %s: no listings found", grade)

            time.sleep(RATE_LIMIT_DELAY)

        # === Grade hierarchy validation ===
        _validate_grade_hierarchy(conn, product_id, card_name)

        conn.commit()
        cards_enriched += 1

    # Summary
    total_graded = conn.execute("SELECT COUNT(*) FROM graded_prices").fetchone()[0]
    total_with_data = conn.execute(
        "SELECT COUNT(DISTINCT product_id) FROM graded_prices "
        "WHERE median_price IS NOT NULL"
    ).fetchone()[0]

    log.info("")
    log.info("=== ENRICHMENT COMPLETE ===")
    log.info("  Cards enriched today: %d", cards_enriched)
    log.info("  eBay API calls used:  %d", calls_made)
    log.info("  Total graded entries: %d", total_graded)
    log.info("  Unique cards w/ data: %d", total_with_data)
    log.info("  Remaining budget:     %d", DAILY_BUDGET - calls_made)
    log.info("=" * 60)

    conn.close()


def _validate_grade_hierarchy(conn, product_id: int, card_name: str):
    """Ensure PSA 10 >= PSA 9 >= PSA 8 ... Delete inversions."""
    rows = conn.execute(
        """
        SELECT grade, median_price, num_listings
        FROM graded_prices
        WHERE product_id = ? AND median_price IS NOT NULL
          AND expires_at > datetime('now')
        ORDER BY CAST(REPLACE(grade, 'PSA ', '') AS INTEGER) DESC
        """,
        [product_id],
    ).fetchall()

    if len(rows) < 2:
        return

    for i in range(len(rows) - 1):
        higher_grade, higher_price, higher_listings = rows[i]
        lower_grade, lower_price, lower_listings = rows[i + 1]

        if lower_price > higher_price * 1.5:
            bad_grade = higher_grade if (higher_listings or 0) < (lower_listings or 0) else lower_grade
            log.warning(
                "    ⚠️ HIERARCHY: %s %s ($%.0f) > %s ($%.0f) — removing %s",
                card_name[:35], lower_grade, lower_price,
                higher_grade, higher_price, bad_grade,
            )
            conn.execute(
                "DELETE FROM graded_prices WHERE product_id = ? AND grade = ?",
                [product_id, bad_grade],
            )


if __name__ == "__main__":
    run()
