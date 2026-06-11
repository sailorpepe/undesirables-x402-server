#!/usr/bin/env python3
"""
Daily Alpha Bot — Posts TCG & prediction market alpha to X (@sailorpepe_eth)
Runs Mon-Sat. Sunday off.

Schedule:
  Mon/Wed/Fri — Weather Edge (NWS vs Kalshi weather derivatives)
  Tue/Thu/Sat — Monte Carlo Forecast (price simulations on popular cards)
  Sun — OFF

Usage:
    python3 daily_alpha.py --dry-run          # Preview without posting
    python3 daily_alpha.py                    # Post today's alpha
    python3 daily_alpha.py --mode arb-weather # Force weather mode
    python3 daily_alpha.py --mode simulate    # Force simulate mode

Data sources (all LOCAL — no x402 payment needed):
    - SQLite: ~/Documents/undesirables-mcp-server/.cache/market_memory.sqlite
    - Shroomy Oracle: http://127.0.0.1:3000 (Next.js on Mac Mini)
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
SHROOMY_URL = "http://127.0.0.1:3000"
ORACLE_URL = "https://oracle.the-undesirables.com"

# Day-of-week rotation (0=Mon, 5=Sat, 6=Sun=OFF)
DAY_SCHEDULE = {
    0: "arb-weather",  # Monday
    1: "simulate",     # Tuesday
    2: "arb-weather",  # Wednesday
    3: "simulate",     # Thursday
    4: "arb-weather",  # Friday
    5: "simulate",     # Saturday
    6: None,           # Sunday — OFF
}


# ---------------------------------------------------------------------------
# Data Fetchers
# ---------------------------------------------------------------------------
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




# ---------------------------------------------------------------------------
# Tweet Formatters — LONG FORMAT with real data breakdowns
# ---------------------------------------------------------------------------


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


FORMATTERS = {
    "arb-weather": format_arb_weather,
    "simulate": format_simulate,
}

FETCHERS = {
    "arb-weather": fetch_arb_weather,
    "simulate": fetch_simulate,
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
        # Fallback: try the other mode
        fallback = "simulate" if mode == "arb-weather" else "arb-weather"
        data = FETCHERS[fallback]()
        mode = fallback
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
