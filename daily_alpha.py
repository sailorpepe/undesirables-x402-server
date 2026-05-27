#!/usr/bin/env python3
"""
Daily Alpha Bot — Posts TCG & prediction market alpha to X (@sailorpepe_eth)
Runs Mon-Sat. Sunday off. Each day rotates through different alpha sources.

Schedule:
  Mon — Prediction Market Arb (Kalshi vs Polymarket)
  Tue — Grading ROI Scanner (which cards are worth grading?)
  Wed — Weather Edge (NWS vs Kalshi weather derivatives)
  Thu — Courtyard Arb (tokenized vs raw card spreads)
  Fri — Monte Carlo Forecast (price simulations on popular cards)
  Sat — Weekly Digest (best signals from the week)
  Sun — OFF

Usage:
    python3 daily_alpha.py --dry-run          # Preview without posting
    python3 daily_alpha.py                    # Post today's alpha
    python3 daily_alpha.py --mode arb-cross   # Force specific mode
    python3 daily_alpha.py --mode arb-grade
    python3 daily_alpha.py --mode arb-weather
    python3 daily_alpha.py --mode courtyard
    python3 daily_alpha.py --mode simulate
    python3 daily_alpha.py --mode digest

Data sources (all LOCAL — no x402 payment needed):
    - SQLite: ~/Documents/undesirables-mcp-server/.cache/market_memory.sqlite
    - Shroomy Oracle: http://127.0.0.1:3000 (Next.js on Mac Mini)
    - Courtyard data: tcg-oracle-tools/data/verified_arbitrage.json
    - X API: tweepy (keys from .env)
"""

import json
import os
import sqlite3
import sys
import random
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (Mac Mini layout)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
DB_PATH = Path.home() / "Documents" / "undesirables-mcp-server" / ".cache" / "market_memory.sqlite"
COURTYARD_DATA = SCRIPT_DIR.parent / "tcg-oracle-tools" / "data" / "verified_arbitrage.json"
SHROOMY_URL = "http://127.0.0.1:3000"
ORACLE_URL = "https://oracle.the-undesirables.com"

# Day-of-week rotation (0=Mon, 5=Sat, 6=Sun=OFF)
DAY_SCHEDULE = {
    0: "arb-cross",    # Monday
    1: "arb-grade",    # Tuesday
    2: "arb-weather",  # Wednesday
    3: "courtyard",    # Thursday
    4: "simulate",     # Friday
    5: "digest",       # Saturday
    6: None,           # Sunday — OFF
}


# ---------------------------------------------------------------------------
# Data Fetchers
# ---------------------------------------------------------------------------
def fetch_arb_cross():
    """Kalshi vs Polymarket cross-platform arbitrage — via Shroomy Oracle."""
    import requests
    try:
        r = requests.get(f"{SHROOMY_URL}/api/arbs?scanType=cross-platform", timeout=30)
        data = r.json()
        # Count real opportunities with meaningful edge
        opps = data.get("opportunities", data.get("arbs", []))
        if isinstance(opps, list):
            real_opps = [o for o in opps if float(o.get("edge_percent", o.get("edge", 0))) > 1.0]
            best = max(real_opps, key=lambda x: float(x.get("edge_percent", x.get("edge", 0))), default=None)
            return {
                "type": "arb-cross",
                "count": len(real_opps),
                "best_edge": round(float(best.get("edge_percent", best.get("edge", 0))), 1) if best else 0,
                "platforms": "Kalshi × Polymarket",
                "raw": data,
            }
    except Exception as e:
        print(f"[!] arb-cross fetch failed: {e}")
    return None


def fetch_arb_basket():
    """Basket arbitrage — guaranteed NO yield on Polymarket/Kalshi."""
    import requests
    try:
        r = requests.get(f"{SHROOMY_URL}/api/arbs?scanType=basket", timeout=30)
        data = r.json()
        arbs = data.get("basketArbs", data.get("arbs", []))
        if isinstance(arbs, list):
            real = [a for a in arbs if a.get("eventTitle")]
            return {
                "type": "arb-basket",
                "count": len(real),
                "events": [a.get("eventTitle", "?")[:50] for a in real[:3]],
                "raw": data,
            }
    except Exception as e:
        print(f"[!] arb-basket fetch failed: {e}")
    return None


def fetch_arb_weather():
    """NWS vs Kalshi weather derivative edge scanner."""
    import requests
    try:
        r = requests.get(f"{SHROOMY_URL}/api/weather-edge", timeout=30)
        data = r.json()
        edges = data.get("edges", data.get("opportunities", []))
        if isinstance(edges, list):
            real = [e for e in edges if abs(float(e.get("edge_percent", e.get("edge", 0)))) > 2.0]
            best = max(real, key=lambda x: abs(float(x.get("edge_percent", x.get("edge", 0)))), default=None)
            return {
                "type": "arb-weather",
                "count": len(real),
                "best_edge": round(abs(float(best.get("edge_percent", best.get("edge", 0)))), 1) if best else 0,
                "best_type": best.get("type", best.get("market", "weather")) if best else "?",
                "raw": data,
            }
    except Exception as e:
        print(f"[!] arb-weather fetch failed: {e}")
    return None


def fetch_arb_grade():
    """Scan SQLite for cards where grading ROI beats $35 total cost."""
    if not DB_PATH.exists():
        print(f"[!] DB not found: {DB_PATH}")
        return None

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        query = """
        SELECT 
            COALESCE(c.name, c.clean_name) as card_name,
            ph.market_price as raw_price,
            CASE 
                WHEN ph.market_price > 200 THEN 8.5
                WHEN ph.market_price > 80 THEN 8.0
                WHEN ph.market_price > 30 THEN 7.5
                ELSE 7.0
            END as est_grade,
            ph.market_price * CASE 
                WHEN ph.market_price > 200 THEN 2.5
                WHEN ph.market_price > 80 THEN 2.0
                WHEN ph.market_price > 30 THEN 1.8
                ELSE 1.5
            END as est_graded_value,
            (ph.market_price * CASE 
                WHEN ph.market_price > 200 THEN 2.5
                WHEN ph.market_price > 80 THEN 2.0
                WHEN ph.market_price > 30 THEN 1.8
                ELSE 1.5
            END - ph.market_price - 35) as expected_profit,
            ROUND(((ph.market_price * CASE 
                WHEN ph.market_price > 200 THEN 2.5
                WHEN ph.market_price > 80 THEN 2.0
                WHEN ph.market_price > 30 THEN 1.8
                ELSE 1.5
            END - ph.market_price - 35) / (ph.market_price + 35)) * 100, 0) as roi_pct
        FROM price_history ph
        LEFT JOIN cards c ON ph.product_id = c.product_id
        WHERE ph.date = (SELECT MAX(date) FROM price_history)
          AND ph.market_price BETWEEN 15 AND 500
          AND (ph.market_price * CASE 
                WHEN ph.market_price > 200 THEN 2.5
                WHEN ph.market_price > 80 THEN 2.0
                WHEN ph.market_price > 30 THEN 1.8
                ELSE 1.5
            END - ph.market_price - 35) > 0
        ORDER BY roi_pct DESC
        LIMIT 20
        """
        rows = conn.execute(query).fetchall()
        opps = [dict(r) for r in rows]
        # Filter to only meaningful ROI
        good = [o for o in opps if o["roi_pct"] and o["roi_pct"] > 30]
        best = good[0] if good else None
        return {
            "type": "arb-grade",
            "count": len(good),
            "best_roi": round(best["roi_pct"]) if best else 0,
            "best_profit": round(best["expected_profit"], 2) if best else 0,
            "price_range": f"${min(o['raw_price'] for o in good):.0f}-${max(o['raw_price'] for o in good):.0f}" if good else "N/A",
            "raw": good[:5],  # Keep top 5 for reference
        }
    except Exception as e:
        print(f"[!] arb-grade query failed: {e}")
        return None
    finally:
        conn.close()


def fetch_courtyard():
    """Courtyard vs TCGPlayer — tokenized card arbitrage."""
    if not COURTYARD_DATA.exists():
        print(f"[!] Courtyard data not found: {COURTYARD_DATA}")
        return None

    try:
        with open(COURTYARD_DATA) as f:
            data = json.load(f)

        # Filter for real BUY signals (listing > raw price = overpriced on Courtyard)
        # Also look for underpriced (listing < estimated graded value)
        buys = []
        sells = []
        for card in data:
            listing = card.get("listing_usd", 0)
            raw = card.get("raw_price", 0)
            graded_est = card.get("estimated_graded_value", 0)
            spread_pct = card.get("spread_pct", 0)

            if listing and raw and listing > 0 and raw > 0:
                if spread_pct > 50:  # Overpriced on Courtyard by >50%
                    sells.append(card)
                elif spread_pct < -20:  # Underpriced on Courtyard by >20%
                    buys.append(card)

        total_savings = sum(abs(c.get("spread", 0)) for c in buys)

        return {
            "type": "courtyard",
            "buy_signals": len(buys),
            "sell_signals": len(sells),
            "total_cards": len(data),
            "total_savings": round(total_savings, 2),
            "best_buy": buys[0] if buys else None,
        }
    except Exception as e:
        print(f"[!] Courtyard data parse failed: {e}")
        return None


def fetch_simulate():
    """Monte Carlo simulation on a popular card from the DB."""
    if not DB_PATH.exists():
        return None

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        # Pick a recognizable card with enough price history and decent value
        query = """
        SELECT c.name, c.product_id, 
               ph.market_price as current_price,
               s.drift, s.volatility
        FROM cards c
        JOIN price_history ph ON c.product_id = ph.product_id
        LEFT JOIN shroomy_stats s ON c.product_id = s.product_id
        WHERE ph.date = (SELECT MAX(date) FROM price_history)
          AND ph.market_price BETWEEN 20 AND 1000
          AND s.volatility IS NOT NULL
          AND s.volatility > 0.1
          AND s.volatility < 2.0
          AND c.name IS NOT NULL
          AND LENGTH(c.name) > 5
          AND (c.name LIKE '%Charizard%' OR c.name LIKE '%Pikachu%' 
               OR c.name LIKE '%Lugia%' OR c.name LIKE '%Mewtwo%'
               OR c.name LIKE '%Black Lotus%' OR c.name LIKE '%Mox%'
               OR c.name LIKE '%Dark Magician%' OR c.name LIKE '%Blue-Eyes%'
               OR c.name LIKE '%Umbreon%' OR c.name LIKE '%Gengar%'
               OR c.name LIKE '%Eevee%' OR c.name LIKE '%Rayquaza%'
               OR c.name LIKE '%Jace%' OR c.name LIKE '%Liliana%')
        ORDER BY RANDOM()
        LIMIT 1
        """
        row = conn.execute(query).fetchone()
        if not row:
            # Fallback: any card with good data
            query = """
            SELECT c.name, c.product_id, ph.market_price as current_price,
                   s.drift, s.volatility
            FROM cards c
            JOIN price_history ph ON c.product_id = ph.product_id
            LEFT JOIN shroomy_stats s ON c.product_id = s.product_id
            WHERE ph.date = (SELECT MAX(date) FROM price_history)
              AND ph.market_price BETWEEN 30 AND 500
              AND s.volatility BETWEEN 0.1 AND 1.5
              AND c.name IS NOT NULL
            ORDER BY RANDOM() LIMIT 1
            """
            row = conn.execute(query).fetchone()

        if not row:
            return None

        card = dict(row)
        price = card["current_price"]
        vol = card["volatility"]
        drift = card["drift"]

        # Simple GBM Monte Carlo (30 days, 1000 sims)
        import numpy as np
        dt = 1 / 365
        days = 30
        sims = 1000
        np.random.seed(int(datetime.now().timestamp()) % 2**31)

        paths = np.zeros(sims)
        for i in range(sims):
            p = price
            for _ in range(days):
                p *= np.exp((drift - 0.5 * vol**2) * dt + vol * np.sqrt(dt) * np.random.normal())
            paths[i] = p

        return {
            "type": "simulate",
            "card_name": card["name"],
            "current_price": round(price, 2),
            "p5": round(np.percentile(paths, 5), 2),
            "p25": round(np.percentile(paths, 25), 2),
            "p50": round(np.percentile(paths, 50), 2),
            "p75": round(np.percentile(paths, 75), 2),
            "p95": round(np.percentile(paths, 95), 2),
            "volatility": round(vol * 100, 1),
            "upside_prob": round((np.sum(paths > price) / sims) * 100, 0),
        }
    except Exception as e:
        print(f"[!] Simulate failed: {e}")
        return None
    finally:
        conn.close()


def fetch_digest():
    """Saturday digest — combine signals from multiple sources."""
    results = []

    grade = fetch_arb_grade()
    if grade and grade["count"] > 0:
        results.append(f"🃏 {grade['count']} cards worth grading (best ROI: {grade['best_roi']}%)")

    court = fetch_courtyard()
    if court and court["buy_signals"] > 0:
        results.append(f"💎 {court['buy_signals']} Courtyard BUY signals")

    basket = fetch_arb_basket()
    if basket and basket["count"] > 0:
        results.append(f"🧺 {basket['count']} basket arb opportunities")

    sim = fetch_simulate()
    if sim:
        results.append(f"📈 {sim['card_name']}: {sim['upside_prob']:.0f}% chance of gain in 30d")

    return {
        "type": "digest",
        "signals": results,
        "grade_data": grade,
        "court_data": court,
        "sim_data": sim,
    }


# ---------------------------------------------------------------------------
# Tweet Formatters — TEASERS ONLY, no paid data leaked
# ---------------------------------------------------------------------------
def format_arb_cross(data):
    """Prediction market cross-platform arb teaser."""
    now = datetime.now(timezone.utc).strftime("%b %d")
    lines = [f"⚡ Prediction Market Arb Alert — {now}\n"]

    if data["count"] > 0:
        lines.append(f"🎯 {data['count']} cross-platform edges found")
        lines.append(f"📊 Best edge: {data['best_edge']}% guaranteed yield")
        lines.append(f"🔀 {data['platforms']}")
    else:
        lines.append("📊 No actionable edges today — markets are tight")
        lines.append("🔀 Kalshi × Polymarket spreads under 1%")

    lines.append(f"\n🔍 Full scanner: oracle.the-undesirables.com")
    lines.append(f"\n🍄 @undesirable_ai")
    return "\n".join(lines)


def format_arb_grade(data):
    """Grading ROI scanner teaser."""
    now = datetime.now(timezone.utc).strftime("%b %d")
    lines = [f"🃏 Grading ROI Alert — {now}\n"]

    if data["count"] > 0:
        lines.append(f"💰 {data['count']} cards worth grading right now")
        lines.append(f"📈 Best ROI: {data['best_roi']}% ({data['price_range']} raw)")
        lines.append(f"💵 Top expected profit: ${data['best_profit']:.0f}")
        lines.append(f"\nPSA economy = $20. Which cards beat the fee?")
    else:
        lines.append("📉 No grading opportunities above 30% ROI today")
        lines.append("Market is efficiently priced — check back tomorrow")

    lines.append(f"\n🔍 oracle.the-undesirables.com")
    lines.append(f"\n🍄 @undesirable_ai")
    return "\n".join(lines)


def format_arb_weather(data):
    """Weather edge teaser."""
    now = datetime.now(timezone.utc).strftime("%b %d")
    lines = [f"🌦️ Weather Edge Alert — {now}\n"]

    if data["count"] > 0:
        lines.append(f"🎯 {data['count']} NWS vs Kalshi divergences found")
        lines.append(f"📊 Biggest edge: {data['best_edge']}%")
        lines.append(f"🌡️ NWS says one thing, Kalshi prices another")
    else:
        lines.append("📊 NWS and Kalshi are aligned — no edges today")

    lines.append(f"\n🔍 oracle.the-undesirables.com")
    lines.append(f"\n🍄 @undesirable_ai")
    return "\n".join(lines)


def format_courtyard(data):
    """Courtyard tokenized card arb teaser."""
    now = datetime.now(timezone.utc).strftime("%b %d")
    lines = [f"💎 Courtyard Arb Scanner — {now}\n"]

    if data["buy_signals"] > 0:
        lines.append(f"🟢 {data['buy_signals']} BUY signals (underpriced tokenized cards)")
    if data["sell_signals"] > 0:
        lines.append(f"🔴 {data['sell_signals']} overpriced listings detected")

    lines.append(f"📊 Scanned {data['total_cards']} tokenized cards vs TCGPlayer")

    if data["total_savings"] > 0:
        lines.append(f"💰 ${data['total_savings']:.0f} total spread across all signals")

    lines.append(f"\n🔍 oracle.the-undesirables.com")
    lines.append(f"\n🍄 @undesirable_ai")
    return "\n".join(lines)


def format_simulate(data):
    """Monte Carlo simulation teaser."""
    now = datetime.now(timezone.utc).strftime("%b %d")
    name = data["card_name"]
    if len(name) > 35:
        name = name[:32] + "..."

    lines = [f"📈 Monte Carlo Forecast — {now}\n"]
    lines.append(f"🎴 {name}")
    lines.append(f"💵 Current: ${data['current_price']:.2f}")
    lines.append(f"📊 30-day range (5th-95th):")
    lines.append(f"   ${data['p5']:.2f} — ${data['p95']:.2f}")
    lines.append(f"🎲 {data['upside_prob']:.0f}% probability of gain")
    lines.append(f"⚡ Volatility: {data['volatility']}%")
    lines.append(f"\n🔍 oracle.the-undesirables.com")
    lines.append(f"\n🍄 @undesirable_ai")
    return "\n".join(lines)


def format_digest(data):
    """Saturday weekly digest."""
    now = datetime.now(timezone.utc).strftime("%b %d")
    lines = [f"📊 Weekly Alpha Digest — {now}\n"]

    if data["signals"]:
        for signal in data["signals"][:4]:
            lines.append(signal)
    else:
        lines.append("Quiet week — markets efficiently priced")

    lines.append(f"\n🔍 Full data: oracle.the-undesirables.com")
    lines.append(f"\n🍄 @undesirable_ai | #TCG #TradingCards #PredictionMarkets")
    return "\n".join(lines)


FORMATTERS = {
    "arb-cross": format_arb_cross,
    "arb-grade": format_arb_grade,
    "arb-weather": format_arb_weather,
    "courtyard": format_courtyard,
    "simulate": format_simulate,
    "digest": format_digest,
}

FETCHERS = {
    "arb-cross": fetch_arb_cross,
    "arb-grade": fetch_arb_grade,
    "arb-weather": fetch_arb_weather,
    "courtyard": fetch_courtyard,
    "simulate": fetch_simulate,
    "digest": fetch_digest,
}


# ---------------------------------------------------------------------------
# Tweet Posting
# ---------------------------------------------------------------------------
def post_tweet(text):
    """Post a tweet to X via tweepy v2 API."""
    try:
        import tweepy
    except ImportError:
        print("[!] tweepy not installed. Run: pip3 install tweepy")
        sys.exit(1)

    api_key = os.environ.get("X_API_KEY")
    api_secret = os.environ.get("X_API_SECRET")
    access_token = os.environ.get("X_ACCESS_TOKEN")
    access_secret = os.environ.get("X_ACCESS_SECRET")

    if not all([api_key, api_secret, access_token, access_secret]):
        print("[!] Missing X API credentials. Set X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET")
        sys.exit(1)

    client = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
    )

    response = client.create_tweet(text=text)
    tweet_id = response.data["id"]
    print(f"[✓] Posted! https://x.com/sailorpepe_eth/status/{tweet_id}")
    return tweet_id


def load_env():
    """Load .env file if credentials not in environment."""
    env_file = SCRIPT_DIR / ".env"
    if env_file.exists() and not os.environ.get("X_API_KEY"):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    dry_run = "--dry-run" in sys.argv
    force_mode = None

    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        if idx + 1 < len(sys.argv):
            force_mode = sys.argv[idx + 1]

    # Determine today's mode
    today = datetime.now().weekday()  # 0=Mon, 6=Sun

    if force_mode:
        mode = force_mode
    else:
        mode = DAY_SCHEDULE.get(today)

    if mode is None:
        print("[*] Sunday — day off. No alpha today. 🌿")
        return

    print(f"[*] Day: {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][today]}")
    print(f"[*] Mode: {mode}")
    print(f"[*] Dry run: {dry_run}")

    # Fetch data
    fetcher = FETCHERS.get(mode)
    if not fetcher:
        print(f"[!] Unknown mode: {mode}")
        sys.exit(1)

    print(f"[*] Fetching {mode} data...")
    data = fetcher()

    if not data:
        print(f"[!] No data returned for {mode}. Trying fallback...")
        # Fallback: try arb-grade (always works with SQLite)
        if mode != "arb-grade":
            data = fetch_arb_grade()
            mode = "arb-grade"
        if not data:
            print("[!] All fetchers failed. Aborting.")
            sys.exit(1)

    # Format tweet
    formatter = FORMATTERS.get(mode)
    tweet = formatter(data)

    # Enforce 280 char limit
    if len(tweet) > 280:
        # Remove the @undesirable_ai line
        lines = tweet.split("\n")
        lines = [l for l in lines if "@undesirable_ai" not in l or "oracle" in l]
        tweet = "\n".join(lines)

    if len(tweet) > 280:
        tweet = tweet[:277] + "..."

    print(f"\n{'═' * 50}")
    print(f"TWEET ({len(tweet)}/280 chars)")
    print(f"{'═' * 50}")
    print(tweet)
    print(f"{'═' * 50}\n")

    if dry_run:
        print("[*] Dry run — not posting.")
        print("[*] To post: python3 daily_alpha.py")
        print(f"[*] Data summary: {json.dumps({k:v for k,v in data.items() if k != 'raw'}, indent=2, default=str)}")
        return

    # Load env and post
    load_env()
    post_tweet(tweet)


if __name__ == "__main__":
    main()
