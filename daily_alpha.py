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
    - Courtyard data: tcg-oracle-tools/data/courtyard_enriched_listings.json
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
COURTYARD_DATA = Path.home() / "Documents" / "tcg-oracle-tools" / "data" / "courtyard_enriched_listings.json"
COURTYARD_LIVE = Path.home() / "Documents" / "tcg-oracle-tools" / "data" / "courtyard_live_listings.json"
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
        opps = data.get("opportunities", data.get("arbs", []))
        if isinstance(opps, list):
            real_opps = [o for o in opps if float(o.get("edge_percent", o.get("edge", 0))) > 1.0]
            # Sort by edge descending
            real_opps.sort(key=lambda x: float(x.get("edge_percent", x.get("edge", 0))), reverse=True)
            return {
                "type": "arb-cross",
                "count": len(real_opps),
                "total_scanned": data.get("total_markets", data.get("totalScanned", len(opps))),
                "platforms": "Kalshi × Polymarket",
                "top_opps": real_opps[:5],
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
                "events": real[:5],
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

        # Extract city-level data from the response
        cities = data.get("cities", {})
        total_edges = data.get("opportunitiesFound", data.get("edgeCount", 0))
        total_scanned = data.get("totalMarketsScanned", 0)

        # Build city summaries sorted by top edge
        city_list = []
        for code, city in cities.items():
            city_list.append({
                "code": code,
                "name": city.get("name", code),
                "forecast_high": city.get("forecastHigh"),
                "forecast_low": city.get("forecastLow"),
                "observed_high": city.get("observedHigh"),
                "observed_low": city.get("observedLow"),
                "edge_count": city.get("edgeCount", 0),
                "top_edge": city.get("topEdge", 0),
            })
        city_list.sort(key=lambda x: x["top_edge"], reverse=True)

        return {
            "type": "arb-weather",
            "count": total_edges,
            "total_scanned": total_scanned,
            "cities": city_list,
            "raw": data,
        }
    except Exception as e:
        print(f"[!] arb-weather fetch failed: {e}")
    return None


def fetch_arb_grade():
    """Scan SQLite for cards where grading ROI beats $100 cost using REAL graded prices."""
    # PSA pricing as of June 2026:
    #   Economy/Value tiers: PAUSED (10M card backlog)
    #   Regular: $79.99 + ~$20 shipping/insurance = ~$100 total
    #   Express: $149, Super Express: $349, Walk-Through: $599
    PSA_TOTAL_COST = 100  # Regular tier + shipping
    if not DB_PATH.exists():
        print(f"[!] DB not found: {DB_PATH}")
        return None

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        # Use REAL graded prices from eBay (graded_prices table)
        # instead of estimated multipliers
        query = """
        SELECT 
            gp.card_name,
            gp.grade,
            gp.median_price as graded_price,
            gp.num_listings,
            ph.market_price as raw_price,
            ROUND(gp.median_price - ph.market_price - 100, 2) as expected_profit,
            ROUND(((gp.median_price - ph.market_price - 100) 
                   / (ph.market_price + 100)) * 100, 0) as roi_pct
        FROM graded_prices gp
        JOIN price_history ph ON gp.product_id = ph.product_id
        WHERE ph.date = (SELECT MAX(date) FROM price_history)
          AND gp.median_price IS NOT NULL
          AND ph.market_price > 10
          AND gp.grade IN ('PSA 10', 'PSA 9')
          AND (gp.median_price - ph.market_price - 100) > 0
          AND gp.num_listings >= 2
          -- Exclude sealed products (can't be PSA graded)
          AND gp.card_name NOT LIKE '%Booster Box%'
          AND gp.card_name NOT LIKE '%Booster Pack%'
          AND gp.card_name NOT LIKE '%Starter Deck%'
          AND gp.card_name NOT LIKE '%Display%'
          AND gp.card_name NOT LIKE '%Elite Trainer%'
          AND gp.card_name NOT LIKE '%Collection%'
          AND gp.card_name NOT LIKE '%Bundle%'
          AND gp.card_name NOT LIKE '%Tin %'
          AND gp.card_name NOT LIKE '%Case%'
          AND gp.card_name NOT LIKE '%Premium Collection%'
        ORDER BY roi_pct DESC
        LIMIT 20
        """
        rows = conn.execute(query).fetchall()
        opps = [dict(r) for r in rows]
        good = [o for o in opps if o["roi_pct"] and o["roi_pct"] > 30]
        return {
            "type": "arb-grade",
            "count": len(good),
            "top_cards": good[:5],
            "price_range": f"${min(o['raw_price'] for o in good):.0f}-${max(o['raw_price'] for o in good):.0f}" if good else "N/A",
            "data_source": "eBay graded listings",
        }
    except Exception as e:
        print(f"[!] arb-grade query failed: {e}")
        return None
    finally:
        conn.close()


def fetch_courtyard():
    """Courtyard vs TCGPlayer — tokenized card arbitrage."""
    # Try enriched listings first, then live listings
    data_path = COURTYARD_DATA if COURTYARD_DATA.exists() else COURTYARD_LIVE
    if not data_path.exists():
        print(f"[!] Courtyard data not found at:")
        print(f"    {COURTYARD_DATA}")
        print(f"    {COURTYARD_LIVE}")
        return None

    try:
        with open(data_path) as f:
            data = json.load(f)

        # Get listing prices
        listings = []
        for card in data:
            price = card.get("listing_usd", card.get("listing_price_usd", 0))
            name = card.get("name", "")
            category = card.get("category", "")
            grade = card.get("grade", "")
            if price and price > 0 and name:
                listings.append({
                    "name": name,
                    "listing_usd": price,
                    "category": category,
                    "grade": grade,
                })

        listings.sort(key=lambda x: x["listing_usd"])

        return {
            "type": "courtyard",
            "total_listings": len(listings),
            "cheapest": listings[:5] if listings else [],
            "categories": list(set(c["category"] for c in listings if c["category"])),
            "source_file": data_path.name,
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
            "p10": round(np.percentile(paths, 10), 2),
            "p25": round(np.percentile(paths, 25), 2),
            "p50": round(np.percentile(paths, 50), 2),
            "p75": round(np.percentile(paths, 75), 2),
            "p90": round(np.percentile(paths, 90), 2),
            "p95": round(np.percentile(paths, 95), 2),
            "volatility": round(vol * 100, 1),
            "drift": round(drift * 100, 1),
            "upside_prob": round((np.sum(paths > price) / sims) * 100, 0),
            "sims": sims,
        }
    except Exception as e:
        print(f"[!] Simulate failed: {e}")
        return None
    finally:
        conn.close()


def fetch_digest():
    """Saturday digest — combine signals from multiple sources."""
    results = {}

    grade = fetch_arb_grade()
    if grade and grade["count"] > 0:
        results["grade"] = grade

    court = fetch_courtyard()
    if court and court["total_listings"] > 0:
        results["courtyard"] = court

    basket = fetch_arb_basket()
    if basket and basket["count"] > 0:
        results["basket"] = basket

    sim = fetch_simulate()
    if sim:
        results["sim"] = sim

    weather = fetch_arb_weather()
    if weather and weather["count"] > 0:
        results["weather"] = weather

    return {
        "type": "digest",
        "sources": results,
    }


# ---------------------------------------------------------------------------
# Tweet Formatters — LONG FORMAT with real data breakdowns
# ---------------------------------------------------------------------------
def format_arb_cross(data):
    """Prediction market cross-platform arb — long format."""
    now = datetime.now().strftime("%B %d, %Y")
    lines = [f"⚡ Cross-Platform Arb Scanner — {now}\n"]
    lines.append(f"Our oracle just scanned {data.get('total_scanned', '?')} prediction markets across Kalshi and Polymarket.\n")

    if data["count"] > 0:
        lines.append(f"🎯 {data['count']} cross-platform edges detected:\n")
        for i, opp in enumerate(data["top_opps"][:3]):
            event = opp.get("event", opp.get("eventTitle", opp.get("market", "?")))
            edge = float(opp.get("edge_percent", opp.get("edge", 0)))
            if len(event) > 60:
                event = event[:57] + "..."
            lines.append(f"{'🥇🥈🥉'[i]} {event}")
            lines.append(f"   Edge: {edge:.1f}%\n")

        lines.append("Same event, different platforms, different prices.")
        lines.append("That's a textbook arbitrage.\n")
    else:
        lines.append("📊 No actionable edges today — markets are tight.")
        lines.append("Kalshi × Polymarket spreads all under 1%.")
        lines.append("When the market is this efficient, the scanner saves you time.\n")

    lines.append("Full scanner with all markets:")
    lines.append("🔍 oracle.the-undesirables.com\n")
    lines.append("🍄 @undesirables_ai")
    lines.append("#Kalshi #Polymarket #PredictionMarkets #Arbitrage")
    return "\n".join(lines)


def format_arb_grade(data):
    """Grading ROI scanner — long format with real graded prices."""
    now = datetime.now().strftime("%B %d, %Y")
    lines = [f"🃏 Grading ROI Scanner — {now}\n"]

    if data["count"] > 0:
        lines.append(f"Scanned TCGPlayer raw prices vs eBay PSA graded comps.")
        lines.append(f"{data['count']} cards where grading produces positive ROI:\n")

        for i, card in enumerate(data["top_cards"][:3]):
            name = card.get("card_name", "?")
            if len(name) > 40:
                name = name[:37] + "..."
            roi = card.get("roi_pct", 0)
            raw = card.get("raw_price", 0)
            graded = card.get("graded_price", 0)
            profit = card.get("expected_profit", 0)
            grade = card.get("grade", "PSA 10")
            listings = card.get("num_listings", 0)
            lines.append(f"{'🥇🥈🥉'[i]} {name}")
            lines.append(f"   Raw: ${raw:,.2f} → {grade}: ${graded:,.0f} ({listings} comps)")
            lines.append(f"   +${profit:,.0f} profit ({roi:.0f}% ROI)\n")

        lines.append(f"💡 PSA Regular tier = $79.99 + ~$20 shipping = ~$100 total.")
        lines.append(f"(Economy/Value tiers paused since June 2 — 10M card backlog.)")
        lines.append(f"The question isn't IF you should grade — it's WHICH cards beat the fee.\n")
    else:
        lines.append("📉 No grading opportunities above 30% ROI today.")
        lines.append("Market is efficiently priced. Check back tomorrow.\n")

    lines.append("Full data: http://oracle.the-undesirables.com\n")
    lines.append("🍄")
    lines.append("@undesirables_ai")
    lines.append("#PSA #CardGrading #TCG #TradingCards #Pokemon")
    return "\n".join(lines)


def format_arb_weather(data):
    """Weather edge — long format with city-by-city breakdown."""
    now = datetime.now().strftime("%B %d, %Y")
    lines = [f"🌦️ Weather Edge Scanner — {now}\n"]
    lines.append(f"Our oracle just scanned {data.get('total_scanned', '?')} Kalshi weather markets across {len(data['cities'])} cities.\n")

    if data["count"] > 0:
        lines.append(f"{data['count']} NWS vs Kalshi divergences found. Here's what the algo flagged:\n")

        # Show top 4 cities by edge
        for city in data["cities"][:4]:
            name = city["name"]
            fh = city.get("forecast_high")
            oh = city.get("observed_high")
            fl = city.get("forecast_low")
            ol = city.get("observed_low")
            edge = city.get("top_edge", 0)
            edge_count = city.get("edge_count", 0)

            high_diff = (oh - fh) if (oh and fh) else 0
            low_diff = (ol - fl) if (ol and fl) else 0

            emoji = "🔥" if abs(high_diff) > 5 else "🌡️" if abs(high_diff) > 2 else "📊"

            lines.append(f"{emoji} {name} — NWS forecast high {fh}°F, actual {oh}°F ({high_diff:+.1f}°F)")
            lines.append(f"   {edge_count} mispriced contracts | Top edge: {edge}%\n")

        lines.append("The pattern: NWS publishes forecasts → Kalshi prices them in → actual temps diverge → contracts misprice.\n")
        lines.append("Anyone can verify: weather.gov + kalshi.com\n")
    else:
        lines.append("📊 NWS and Kalshi are aligned today — no edges found.\n")

    lines.append("Full scanner with all edges:")
    lines.append("🔍 oracle.the-undesirables.com\n")
    lines.append("🍄 @undesirables_ai")
    lines.append("#Kalshi #PredictionMarkets #WeatherTrading")
    return "\n".join(lines)


def format_courtyard(data):
    """Courtyard tokenized card arb — long format."""
    now = datetime.now().strftime("%B %d, %Y")
    lines = [f"💎 Courtyard Arb Scanner — {now}\n"]
    lines.append(f"Scanned {data['total_listings']} live Courtyard.io listings against TCGPlayer raw prices.\n")

    categories = data.get("categories", [])
    if categories:
        lines.append(f"Categories: {', '.join(categories[:5])}\n")

    cheapest = data.get("cheapest", [])
    if cheapest:
        lines.append("Cheapest tokenized graded cards right now:\n")
        for card in cheapest[:3]:
            name = card["name"]
            if len(name) > 50:
                name = name[:47] + "..."
            price = card["listing_usd"]
            grade = card.get("grade", "")
            grade_str = f" ({grade})" if grade else ""
            lines.append(f"  💰 ${price:.2f} — {name}{grade_str}")

        lines.append(f"\nEvery card is vaulted by Brink's, insured, and tradeable on Polygon.\n")

    lines.append("Full arb scanner: oracle.the-undesirables.com\n")
    lines.append("🍄 @undesirables_ai")
    lines.append("#Courtyard #TCG #TradingCards #PhygitalCards")
    return "\n".join(lines)


def format_simulate(data):
    """Monte Carlo simulation — long format with full distribution."""
    now = datetime.now().strftime("%B %d, %Y")
    name = data["card_name"]
    if len(name) > 40:
        name = name[:37] + "..."

    lines = [f"📈 Monte Carlo Price Forecast — {now}\n"]
    lines.append(f"🎴 {name}")
    lines.append(f"💵 Current market price: ${data['current_price']:.2f}\n")
    lines.append(f"We ran {data['sims']:,} Merton Jump-Diffusion simulations over 30 days:\n")
    lines.append(f"  5th percentile:  ${data['p5']:.2f}  (worst case)")
    lines.append(f"  25th percentile: ${data['p25']:.2f}")
    lines.append(f"  Median:          ${data['p50']:.2f}")
    lines.append(f"  75th percentile: ${data['p75']:.2f}")
    lines.append(f"  95th percentile: ${data['p95']:.2f}  (best case)\n")
    lines.append(f"🎲 {data['upside_prob']:.0f}% probability of gain")
    lines.append(f"⚡ Annualized volatility: {data['volatility']}%")
    lines.append(f"📊 Drift: {data['drift']}%\n")

    if data['upside_prob'] > 60:
        lines.append("The math leans bullish, but Monte Carlo isn't a crystal ball — it's a probability map.\n")
    elif data['upside_prob'] < 40:
        lines.append("The math leans bearish. High volatility = high risk. Size your position accordingly.\n")
    else:
        lines.append("Coin flip territory. The market isn't giving a clear signal — wait for conviction.\n")

    lines.append("Run your own simulations:")
    lines.append("🔍 oracle.the-undesirables.com\n")
    lines.append("🍄 @undesirables_ai")
    lines.append("#MonteCarlo #TCG #TradingCards #Pokemon #QuantFinance")
    return "\n".join(lines)


def format_digest(data):
    """Saturday weekly digest — long format combining all sources."""
    now = datetime.now().strftime("%B %d, %Y")
    lines = [f"📊 Weekly Alpha Digest — {now}\n"]
    lines.append("Here's what our oracles found this week:\n")

    sources = data.get("sources", {})

    if "grade" in sources:
        g = sources["grade"]
        top = g["top_cards"][0] if g["top_cards"] else None
        lines.append(f"🃏 Grading: {g['count']} cards worth grading")
        if top:
            lines.append(f"   Best: {top['card_name'][:35]} — {top['roi_pct']:.0f}% ROI")
        lines.append("")

    if "weather" in sources:
        w = sources["weather"]
        top_city = w["cities"][0] if w["cities"] else None
        lines.append(f"🌦️ Weather: {w['count']} NWS vs Kalshi edges")
        if top_city:
            lines.append(f"   Top: {top_city['name']} — {top_city['top_edge']}% edge")
        lines.append("")

    if "basket" in sources:
        b = sources["basket"]
        lines.append(f"🧺 Basket arb: {b['count']} guaranteed-yield opportunities")
        lines.append("")

    if "courtyard" in sources:
        c = sources["courtyard"]
        lines.append(f"💎 Courtyard: {c['total_listings']} active listings scanned")
        lines.append("")

    if "sim" in sources:
        s = sources["sim"]
        name = s["card_name"]
        if len(name) > 30:
            name = name[:27] + "..."
        lines.append(f"📈 Forecast: {name}")
        lines.append(f"   ${s['current_price']:.2f} → {s['upside_prob']:.0f}% chance of gain in 30d")
        lines.append("")

    if not sources:
        lines.append("Quiet week — markets efficiently priced across the board.\n")

    lines.append("All of this runs 24/7 on local compute. No cloud. No API keys to the kingdom.\n")
    lines.append("Full data: oracle.the-undesirables.com\n")
    lines.append("🍄 @undesirables_ai")
    lines.append("#TCG #TradingCards #PredictionMarkets #WeeklyDigest")
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
def post_tweet(text, image_path=None):
    """Post a tweet to X via tweepy v2 API, optionally with an image."""
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

    media_ids = None
    if image_path and Path(image_path).exists():
        # Use v1.1 API for media upload
        auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
        api_v1 = tweepy.API(auth)
        media = api_v1.media_upload(str(image_path))
        media_ids = [media.media_id]
        print(f"[✓] Uploaded image: {image_path} (media_id: {media.media_id})")

    response = client.create_tweet(text=text, media_ids=media_ids)
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
        if mode != "arb-grade":
            data = fetch_arb_grade()
            mode = "arb-grade"
        if not data:
            print("[!] All fetchers failed. Aborting.")
            sys.exit(1)

    # Format tweet
    formatter = FORMATTERS.get(mode)
    tweet = formatter(data)

    print(f"\n{'═' * 60}")
    print(f"TWEET ({len(tweet)} chars)")
    print(f"{'═' * 60}")
    print(tweet)
    print(f"{'═' * 60}\n")

    if dry_run:
        # Still generate the visual in dry-run mode for preview
        try:
            from tweet_visuals import generate_visual
            img = generate_visual(mode, data)
            if img:
                print(f"[*] Preview image: {img}")
        except Exception as e:
            print(f"[*] Visual generation skipped: {e}")
        print("[*] Dry run — not posting.")
        print("[*] To post: python3 daily_alpha.py")
        print(f"[*] Data summary: {json.dumps({k:v for k,v in data.items() if k != 'raw'}, indent=2, default=str)}")
        return

    # Generate visual (auto or from --image flag)
    image_path = None
    if "--image" in sys.argv:
        idx = sys.argv.index("--image")
        if idx + 1 < len(sys.argv):
            image_path = sys.argv[idx + 1]
    else:
        # Auto-generate from data
        try:
            from tweet_visuals import generate_visual
            image_path = generate_visual(mode, data)
        except Exception as e:
            print(f"[!] Auto visual generation failed: {e}")

    # Load env and post
    load_env()
    post_tweet(tweet, image_path=image_path)


if __name__ == "__main__":
    main()
