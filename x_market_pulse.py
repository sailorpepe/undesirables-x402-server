#!/usr/bin/env python3
"""
X Market Pulse Bot — Posts automated TCG market updates to @undesirable_ai
Reads courtyard_live.json and posts top arbitrage opportunities to X.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import tweepy
except ImportError:
    print("[!] tweepy not installed. Run: pip3 install tweepy")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Auth — loaded from environment variables
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("X_API_KEY")
API_SECRET = os.environ.get("X_API_SECRET")
ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN")
ACCESS_SECRET = os.environ.get("X_ACCESS_SECRET")

if not all([API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_SECRET]):
    print("[!] Missing X API credentials. Set X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
DATA_FILE = Path.home() / "courtyard_live.json"


def load_cards():
    """Load courtyard data from the scraper's JSON file."""
    if not DATA_FILE.exists():
        print(f"[!] Data file not found: {DATA_FILE}")
        sys.exit(1)

    with open(DATA_FILE) as f:
        data = json.load(f)

    cards = data.get("cards", [])
    scanned_at = data.get("scanned_at", "unknown")

    # Compute deltas
    valid = []
    for c in cards:
        if c.get("list") and c.get("fmv") and c["fmv"] > 0:
            delta = ((c["list"] - c["fmv"]) / c["fmv"]) * 100
            savings = c["fmv"] - c["list"]
            valid.append({**c, "delta": delta, "savings": savings})

    valid.sort(key=lambda x: x["delta"])
    return valid, scanned_at, len(cards)


def format_tweet(valid, scanned_at, total_cards):
    """Format the market pulse tweet."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%b %d")

    underpriced = [c for c in valid if c["delta"] < -5]
    overpriced = [c for c in valid if c["delta"] > 5]
    total_savings = sum(c["savings"] for c in underpriced)

    # Top 3 deals
    top_deals = underpriced[:3]

    lines = [f"📊 Courtyard Market Pulse — {date_str}\n"]

    if top_deals:
        lines.append("🔥 Top Deals Found:")
        for c in top_deals:
            name = c.get("name", "Unknown")
            # Truncate long names
            if len(name) > 45:
                name = name[:42] + "..."
            grade = c.get("grade", "")
            discount = abs(c["delta"])
            lines.append(f"  • {name} — {discount:.0f}% below FMV")

    lines.append(f"\n💎 {len(underpriced)} underpriced / {len(overpriced)} overpriced")
    lines.append(f"💰 ${total_savings:.0f} total savings across {total_cards} listings")
    lines.append(f"\n🔍 Live scanner: the-undesirables.com/courtyard_arb.html")
    lines.append(f"\n#Pokemon #TCG #TradingCards #PSA #SportsCards")

    tweet = "\n".join(lines)

    # X has a 280 char limit — trim if needed
    if len(tweet) > 280:
        # Drop hashtags first
        lines = lines[:-1]
        tweet = "\n".join(lines)

    if len(tweet) > 280:
        # Drop to just 2 deals
        top_deals = underpriced[:2]
        lines = [f"📊 Courtyard Market Pulse — {date_str}\n"]
        lines.append("🔥 Top Deals:")
        for c in top_deals:
            name = c.get("name", "Unknown")
            if len(name) > 40:
                name = name[:37] + "..."
            lines.append(f"• {name} — {abs(c['delta']):.0f}% below FMV")
        lines.append(f"\n💎 {len(underpriced)} underpriced · ${total_savings:.0f} savings")
        lines.append(f"🔍 the-undesirables.com/courtyard_arb.html")
        tweet = "\n".join(lines)

    return tweet[:280]


def post_tweet(text):
    """Post a tweet to X using API v2."""
    client = tweepy.Client(
        consumer_key=API_KEY,
        consumer_secret=API_SECRET,
        access_token=ACCESS_TOKEN,
        access_token_secret=ACCESS_SECRET,
    )

    response = client.create_tweet(text=text)
    tweet_id = response.data["id"]
    print(f"[✓] Posted! https://x.com/undesirable_ai/status/{tweet_id}")
    return tweet_id


def main():
    print(f"[*] Loading data from {DATA_FILE}...")
    valid, scanned_at, total = load_cards()
    print(f"[*] {len(valid)} valid cards, scanned at {scanned_at}")

    tweet = format_tweet(valid, scanned_at, total)
    print(f"\n--- TWEET ({len(tweet)} chars) ---")
    print(tweet)
    print("--- END ---\n")

    if "--dry-run" in sys.argv:
        print("[*] Dry run — not posting.")
        return

    post_tweet(tweet)


if __name__ == "__main__":
    main()
