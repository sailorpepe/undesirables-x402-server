#!/usr/bin/env python3
"""
Tweet Visuals — Programmatic data-driven images for @undesirables_ai tweets.
Each function takes the data dict from daily_alpha.py fetchers and returns a PNG path.

Style: Dark theme, premium fintech aesthetic, branded.
"""

import os
import math
import numpy as np
from pathlib import Path
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from matplotlib import patheffects

# ---------------------------------------------------------------------------
# Brand constants
# ---------------------------------------------------------------------------
BG_COLOR = "#0D1117"        # GitHub dark
PANEL_COLOR = "#161B22"     # Card background
ACCENT_GREEN = "#3FB950"    # Profit / bullish
ACCENT_RED = "#F85149"      # Loss / bearish
ACCENT_GOLD = "#F0C040"     # Highlight
ACCENT_BLUE = "#58A6FF"     # Info
ACCENT_PURPLE = "#BC8CFF"   # Secondary
TEXT_PRIMARY = "#E6EDF3"     # Main text
TEXT_SECONDARY = "#8B949E"   # Muted text
GRID_COLOR = "#21262D"      # Grid lines

# Best available fonts on macOS
FONT_FAMILY = "Helvetica Neue"
FONT_BOLD = "Helvetica Neue"

OUTPUT_DIR = Path(__file__).parent / "tweet_images"
OUTPUT_DIR.mkdir(exist_ok=True)


def _setup_style():
    """Apply dark theme globally."""
    plt.rcParams.update({
        "figure.facecolor": BG_COLOR,
        "axes.facecolor": PANEL_COLOR,
        "axes.edgecolor": GRID_COLOR,
        "axes.labelcolor": TEXT_PRIMARY,
        "text.color": TEXT_PRIMARY,
        "xtick.color": TEXT_SECONDARY,
        "ytick.color": TEXT_SECONDARY,
        "grid.color": GRID_COLOR,
        "grid.alpha": 0.5,
        "font.family": "sans-serif",
        "font.sans-serif": [FONT_FAMILY, "Arial", "DejaVu Sans"],
        "font.size": 13,
    })


def _add_branding(fig, subtitle=""):
    """Add Undesirables branding to figure."""
    fig.text(0.03, 0.97, "🍄 THE UNDESIRABLES", fontsize=14, fontweight="bold",
             color=ACCENT_GOLD, va="top", ha="left",
             path_effects=[patheffects.withStroke(linewidth=2, foreground=BG_COLOR)])
    fig.text(0.97, 0.97, "oracle.the-undesirables.com", fontsize=10,
             color=TEXT_SECONDARY, va="top", ha="right")
    if subtitle:
        fig.text(0.03, 0.93, subtitle, fontsize=10, color=TEXT_SECONDARY, va="top")
    fig.text(0.97, 0.02, datetime.now().strftime("%B %d, %Y"), fontsize=9,
             color=TEXT_SECONDARY, va="bottom", ha="right")


def _save(fig, name):
    """Save figure and return path."""
    path = OUTPUT_DIR / f"{name}.png"
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor(),
                edgecolor="none", pad_inches=0.3)
    plt.close(fig)
    print(f"[✓] Generated: {path}")
    return str(path)


# ---------------------------------------------------------------------------
# 1. MONTE CARLO FAN CHART
# ---------------------------------------------------------------------------
def generate_simulate(data):
    """Fan chart showing Monte Carlo simulation paths with percentile bands."""
    _setup_style()

    price = data["current_price"]
    vol = data["volatility"] / 100
    drift = data["drift"] / 100
    name = data["card_name"]
    sims = 2000
    days = 30

    # Generate paths
    np.random.seed(42)
    dt = 1 / 365
    paths = np.zeros((sims, days + 1))
    paths[:, 0] = price
    for t in range(1, days + 1):
        z = np.random.normal(size=sims)
        paths[:, t] = paths[:, t-1] * np.exp((drift - 0.5 * vol**2) * dt + vol * np.sqrt(dt) * z)

    # Percentiles
    p5 = np.percentile(paths, 5, axis=0)
    p25 = np.percentile(paths, 25, axis=0)
    p50 = np.percentile(paths, 50, axis=0)
    p75 = np.percentile(paths, 75, axis=0)
    p95 = np.percentile(paths, 95, axis=0)
    x = np.arange(days + 1)

    fig, ax = plt.subplots(figsize=(12, 6.5))

    # Fan bands
    ax.fill_between(x, p5, p95, alpha=0.15, color=ACCENT_BLUE, label="5th–95th")
    ax.fill_between(x, p25, p75, alpha=0.25, color=ACCENT_BLUE, label="25th–75th")

    # Sample paths (faint)
    for i in range(min(80, sims)):
        ax.plot(x, paths[i], alpha=0.04, color=ACCENT_BLUE, linewidth=0.5)

    # Median
    ax.plot(x, p50, color=ACCENT_GOLD, linewidth=2.5, label="Median", zorder=5)

    # Current price line
    ax.axhline(y=price, color=TEXT_SECONDARY, linewidth=1, linestyle="--", alpha=0.5)
    ax.text(days + 0.5, price, f"${price:.2f}", fontsize=10, color=TEXT_SECONDARY, va="center")

    # End markers
    final_median = p50[-1]
    color = ACCENT_GREEN if final_median > price else ACCENT_RED
    ax.plot(days, final_median, "o", color=color, markersize=8, zorder=6)
    ax.text(days + 0.5, final_median, f"${final_median:.2f}", fontsize=11,
            fontweight="bold", color=color, va="center")

    # Labels
    ax.set_xlabel("Days", fontsize=12)
    ax.set_ylabel("Price ($)", fontsize=12)
    ax.set_title(f"Monte Carlo Forecast: {name}", fontsize=18, fontweight="bold",
                 color=TEXT_PRIMARY, pad=15)

    # Stats box
    upside = data["upside_prob"]
    stats_text = (f"Current: ${price:.2f}\n"
                  f"Median 30d: ${final_median:.2f}\n"
                  f"Upside prob: {upside:.0f}%\n"
                  f"Volatility: {data['volatility']}%\n"
                  f"Sims: {sims:,}")
    props = dict(boxstyle="round,pad=0.6", facecolor=BG_COLOR, edgecolor=GRID_COLOR, alpha=0.9)
    ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, fontsize=10,
            verticalalignment="top", bbox=props, color=TEXT_PRIMARY, family="monospace")

    ax.legend(loc="lower right", fontsize=9, facecolor=PANEL_COLOR, edgecolor=GRID_COLOR)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(0, days + 3)

    _add_branding(fig, "Merton Jump-Diffusion · 2,000 simulations · 30-day horizon")
    return _save(fig, "simulate")


# ---------------------------------------------------------------------------
# 2. GRADING ROI BAR CHART
# ---------------------------------------------------------------------------
def generate_arb_grade(data):
    """Horizontal bar chart showing grading ROI for top cards."""
    _setup_style()

    cards = data.get("top_cards", [])[:8]
    if not cards:
        return None

    fig, ax = plt.subplots(figsize=(12, 6.5))

    names = []
    rois = []
    profits = []
    raw_prices = []
    for card in reversed(cards):  # reverse for bottom-to-top
        name = card.get("card_name", "?")
        if len(name) > 35:
            name = name[:32] + "..."
        names.append(name)
        rois.append(card.get("roi_pct", 0))
        profits.append(card.get("expected_profit", 0))
        raw_prices.append(card.get("raw_price", 0))

    y_pos = np.arange(len(names))
    colors = [ACCENT_GREEN if r > 100 else ACCENT_BLUE if r > 60 else ACCENT_PURPLE for r in rois]

    bars = ax.barh(y_pos, rois, color=colors, height=0.65, edgecolor="none", alpha=0.85)

    # Value labels
    for i, (bar, roi, profit, raw) in enumerate(zip(bars, rois, profits, raw_prices)):
        ax.text(bar.get_width() + 2, bar.get_y() + bar.get_height()/2,
                f"{roi:.0f}% ROI  (+${profit:.0f})",
                fontsize=10, fontweight="bold", color=colors[i], va="center")
        ax.text(2, bar.get_y() + bar.get_height()/2,
                f"${raw:.0f} raw",
                fontsize=9, color=BG_COLOR, va="center", fontweight="bold")

    ax.set_yticks(y_pos)
    ax.set_yticklabels(names, fontsize=11)
    ax.set_xlabel("Return on Investment (%)", fontsize=12)
    ax.set_title("🃏 Grading ROI Scanner — Which Cards Beat the Fee?", fontsize=17,
                 fontweight="bold", color=TEXT_PRIMARY, pad=15)

    # Cost note
    ax.text(0.98, 0.02, "PSA grading cost: $25 (economy + shipping)",
            transform=ax.transAxes, fontsize=9, color=TEXT_SECONDARY,
            ha="right", va="bottom")

    ax.grid(True, axis="x", alpha=0.3)
    ax.set_xlim(0, max(rois) * 1.35)

    _add_branding(fig, f"{data['count']} cards with positive ROI · Price range: {data.get('price_range', 'N/A')}")
    return _save(fig, "arb_grade")


# ---------------------------------------------------------------------------
# 3. WEEKLY DIGEST — Multi-panel infographic
# ---------------------------------------------------------------------------
def generate_digest(data):
    """Multi-panel summary combining the week's signals."""
    _setup_style()

    sources = data.get("sources", {})
    n_panels = len(sources)
    if n_panels == 0:
        return None

    fig = plt.figure(figsize=(12, 7))
    fig.patch.set_facecolor(BG_COLOR)

    # Title
    fig.text(0.5, 0.96, "📊 WEEKLY ALPHA DIGEST", fontsize=22, fontweight="bold",
             color=TEXT_PRIMARY, ha="center", va="top")
    fig.text(0.5, 0.915, datetime.now().strftime("%B %d, %Y"),
             fontsize=12, color=TEXT_SECONDARY, ha="center", va="top")

    # Layout panels in a grid
    panel_data = []

    if "grade" in sources:
        g = sources["grade"]
        cards = g.get("top_cards", [])[:5]
        panel_data.append({
            "icon": "🃏",
            "title": "GRADING ROI",
            "metric": f"{g['count']} cards",
            "metric_color": ACCENT_GREEN,
            "details": [f"{c['card_name'][:28]}: {c['roi_pct']:.0f}%" for c in cards[:3]],
            "footer": f"Best ROI: {cards[0]['roi_pct']:.0f}%" if cards else "",
        })

    if "sim" in sources:
        s = sources["sim"]
        name = s["card_name"]
        if len(name) > 25:
            name = name[:22] + "..."
        color = ACCENT_GREEN if s["upside_prob"] > 50 else ACCENT_RED
        panel_data.append({
            "icon": "📈",
            "title": "PRICE FORECAST",
            "metric": name,
            "metric_color": ACCENT_GOLD,
            "details": [
                f"Current: ${s['current_price']:.2f}",
                f"Median 30d: ${s['p50']:.2f}",
                f"Upside: {s['upside_prob']:.0f}%",
            ],
            "footer": f"Vol: {s['volatility']}% · Drift: {s['drift']}%",
            "highlight_color": color,
        })

    if "weather" in sources:
        w = sources["weather"]
        cities = w.get("cities", [])[:3]
        panel_data.append({
            "icon": "🌦️",
            "title": "WEATHER EDGE",
            "metric": f"{w['count']} edges",
            "metric_color": ACCENT_BLUE,
            "details": [f"{c['name']}: {c['top_edge']}% edge" for c in cities],
            "footer": f"Scanned: {w.get('total_scanned', '?')} markets",
        })

    if "basket" in sources:
        b = sources["basket"]
        panel_data.append({
            "icon": "🧺",
            "title": "BASKET ARB",
            "metric": f"{b['count']} opportunities",
            "metric_color": ACCENT_GREEN,
            "details": [],
            "footer": "Guaranteed yield via NO basket",
        })

    if "courtyard" in sources:
        c = sources["courtyard"]
        cheapest = c.get("cheapest", [])[:3]
        panel_data.append({
            "icon": "💎",
            "title": "COURTYARD",
            "metric": f"{c['total_listings']} listings",
            "metric_color": ACCENT_PURPLE,
            "details": [f"${x['listing_usd']:.2f} — {x['name'][:25]}" for x in cheapest],
            "footer": "Tokenized graded cards on Polygon",
        })

    # Draw panels
    n = len(panel_data)
    if n <= 2:
        cols, rows = n, 1
    elif n <= 4:
        cols, rows = 2, 2
    else:
        cols, rows = 3, 2

    gs = GridSpec(rows, cols, figure=fig, top=0.87, bottom=0.08, left=0.04, right=0.96,
                  hspace=0.35, wspace=0.15)

    for idx, panel in enumerate(panel_data):
        row = idx // cols
        col = idx % cols
        ax = fig.add_subplot(gs[row, col])
        ax.set_xlim(0, 10)
        ax.set_ylim(0, 10)
        ax.axis("off")

        # Panel background
        rect = mpatches.FancyBboxPatch((0, 0), 10, 10, boxstyle="round,pad=0.3",
                                        facecolor=PANEL_COLOR, edgecolor=GRID_COLOR,
                                        linewidth=1.5)
        ax.add_patch(rect)

        # Icon + Title
        ax.text(0.5, 9.2, panel["icon"], fontsize=20, ha="left", va="top")
        ax.text(1.5, 9.3, panel["title"], fontsize=13, fontweight="bold",
                color=TEXT_PRIMARY, ha="left", va="top")

        # Main metric
        ax.text(0.5, 7.5, panel["metric"], fontsize=15, fontweight="bold",
                color=panel.get("metric_color", ACCENT_GOLD), ha="left", va="top")

        # Detail lines
        for i, detail in enumerate(panel.get("details", [])[:4]):
            ax.text(0.5, 6.0 - i * 1.3, detail, fontsize=10,
                    color=TEXT_SECONDARY, ha="left", va="top")

        # Footer
        if panel.get("footer"):
            ax.text(0.5, 0.6, panel["footer"], fontsize=9,
                    color=TEXT_SECONDARY, ha="left", va="bottom", style="italic")

    _add_branding(fig, "All signals generated on local compute · No cloud · No API keys")
    return _save(fig, "digest")


# ---------------------------------------------------------------------------
# 4. PREDICTION MARKET ARB
# ---------------------------------------------------------------------------
def generate_arb_cross(data):
    """Side-by-side bar chart showing cross-platform prediction market edges."""
    _setup_style()

    opps = data.get("top_opps", [])[:6]
    if not opps:
        return None

    fig, ax = plt.subplots(figsize=(12, 6.5))

    names = []
    edges = []
    for opp in reversed(opps):
        event = opp.get("event", opp.get("eventTitle", opp.get("market", "?")))
        if len(event) > 45:
            event = event[:42] + "..."
        names.append(event)
        edges.append(float(opp.get("edge_percent", opp.get("edge", 0))))

    y_pos = np.arange(len(names))
    colors = [ACCENT_GREEN if e > 5 else ACCENT_BLUE if e > 2 else ACCENT_PURPLE for e in edges]

    bars = ax.barh(y_pos, edges, color=colors, height=0.6, alpha=0.85)

    for bar, edge, color in zip(bars, edges, colors):
        ax.text(bar.get_width() + 0.2, bar.get_y() + bar.get_height()/2,
                f"{edge:.1f}%", fontsize=11, fontweight="bold", color=color, va="center")

    ax.set_yticks(y_pos)
    ax.set_yticklabels(names, fontsize=10)
    ax.set_xlabel("Edge (%)", fontsize=12)
    ax.set_title("⚡ Cross-Platform Arb Scanner — Kalshi × Polymarket",
                 fontsize=17, fontweight="bold", pad=15)
    ax.grid(True, axis="x", alpha=0.3)

    _add_branding(fig, f"{data['count']} edges found · {data.get('total_scanned', '?')} markets scanned")
    return _save(fig, "arb_cross")


# ---------------------------------------------------------------------------
# 5. WEATHER EDGE
# ---------------------------------------------------------------------------
def generate_arb_weather(data):
    """City-by-city NWS vs Kalshi temperature comparison."""
    _setup_style()

    cities = data.get("cities", [])[:6]
    if not cities:
        return None

    fig, ax = plt.subplots(figsize=(12, 6.5))

    names = [c["name"] for c in reversed(cities)]
    forecast_highs = [c.get("forecast_high", 0) or 0 for c in reversed(cities)]
    observed_highs = [c.get("observed_high", 0) or 0 for c in reversed(cities)]
    edges = [c.get("top_edge", 0) for c in reversed(cities)]

    y = np.arange(len(names))
    width = 0.35

    ax.barh(y - width/2, forecast_highs, width, label="NWS Forecast", color=ACCENT_BLUE, alpha=0.8)
    ax.barh(y + width/2, observed_highs, width, label="Actual Temp", color=ACCENT_GOLD, alpha=0.8)

    for i, (fh, oh, edge) in enumerate(zip(forecast_highs, observed_highs, edges)):
        diff = oh - fh
        color = ACCENT_RED if abs(diff) > 5 else ACCENT_GREEN
        ax.text(max(fh, oh) + 1, i, f"{diff:+.0f}°F  |  {edge}% edge",
                fontsize=10, color=color, va="center", fontweight="bold")

    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=11)
    ax.set_xlabel("Temperature (°F)", fontsize=12)
    ax.set_title("🌦️ Weather Edge — NWS Forecast vs Actual",
                 fontsize=17, fontweight="bold", pad=15)
    ax.legend(loc="lower right", fontsize=10, facecolor=PANEL_COLOR, edgecolor=GRID_COLOR)
    ax.grid(True, axis="x", alpha=0.3)

    _add_branding(fig, f"{data['count']} mispriced contracts · {data.get('total_scanned', '?')} markets scanned")
    return _save(fig, "arb_weather")


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
GENERATORS = {
    "simulate": generate_simulate,
    "arb-grade": generate_arb_grade,
    "arb-cross": generate_arb_cross,
    "arb-weather": generate_arb_weather,
    "digest": generate_digest,
}


def generate_visual(mode, data):
    """Generate a visual for the given mode and data. Returns image path or None."""
    gen = GENERATORS.get(mode)
    if not gen:
        print(f"[*] No visual generator for mode: {mode}")
        return None
    try:
        return gen(data)
    except Exception as e:
        print(f"[!] Visual generation failed for {mode}: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    # Test with sample data
    test_sim = {
        "card_name": "Charizard Base Set",
        "current_price": 450.0,
        "volatility": 25.0,
        "drift": 5.0,
        "upside_prob": 62,
        "p5": 320, "p10": 350, "p25": 400, "p50": 460, "p75": 520, "p90": 580, "p95": 620,
        "sims": 2000,
    }
    path = generate_simulate(test_sim)
    print(f"Test image: {path}")
