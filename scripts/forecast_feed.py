#!/usr/bin/env python3
"""
forecast_feed.py — nightly free artifact powering the public "Card Forecast"
dashboard. Snapshots the PUBLISHED universe (publish_flag=1, ~top 200 by
liquidity) from forecast_ledger.sqlite at its latest forecast_date and writes
forecast_feed.json: real prices, the stored conformal forecast, and the
Safe-Hold / Momentum letter grades.

NO paid API calls and NO model recompute — it reuses the ledger's stored fields
and the EXACT shipped grade functions (scripts/card_grades.py). Read-only on
every DB (mode=ro). Deterministic; stdlib only (no random/scipy/sklearn).

drift_spike note: older ledger rows were written before forecast_ledger.py used
named INSERT columns, so on tables where the column was ALTER-appended the 0/1
flag physically landed in `offsets_fit_date` while `drift_spike` got the
created_at timestamp. We recover the true flag from whichever column actually
holds a 0/1, so the feed is correct across both the legacy and fixed writers.
"""
import os, sys, json, sqlite3, argparse
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from card_grades import safe_hold_grade, momentum_grade

DEF_LEDGER = os.path.join(REPO, "forecast_ledger.sqlite")
DEF_MARKET = os.path.join(REPO, "..", "undesirables-mcp-server", ".cache", "market_memory.sqlite")
DEF_OUT = os.path.join(REPO, "forecast_feed.json")

# category_id -> friendly game label (TCGplayer category ids; see tcg_game_stats.csv)
GAME = {
    1: "Magic", 2: "Yu-Gi-Oh!", 3: "Pokémon", 85: "Pokémon (JP)",
    62: "Flesh and Blood", 63: "Digimon", 68: "One Piece",
    79: "Star Wars Unlimited", 80: "Dragon Ball Super", 81: "Union Arena",
    71: "Lorcana", 86: "Gundam", 89: "Riftbound", 87: "Universus", 9001: "Vibes TCG",
}


def game_label(category_id):
    return GAME.get(category_id, f"Category {category_id}" if category_id is not None else "Unknown")


def recover_spike(drift_spike_val, offsets_fit_val):
    """The true 0/1 flag is in whichever column physically holds a 0/1 — robust
    to the legacy ALTER-append column mis-order (see module docstring)."""
    for v in (drift_spike_val, offsets_fit_val):
        if v in (0, 1, "0", "1"):
            return int(v)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=DEF_LEDGER)
    ap.add_argument("--market", default=DEF_MARKET)
    ap.add_argument("--out", default=DEF_OUT)
    a = ap.parse_args()

    led = sqlite3.connect(f"file:{os.path.abspath(a.ledger)}?mode=ro", uri=True)
    mkt = sqlite3.connect(f"file:{os.path.abspath(a.market)}?mode=ro", uri=True)

    as_of = led.execute("SELECT MAX(forecast_date) FROM forecast_ledger").fetchone()[0]
    catmap = dict(mkt.execute("SELECT product_id, category_id FROM cards").fetchall())

    rows = led.execute(
        """SELECT u.rank, l.product_id, l.card_name, l.current_price, l.point,
                  l.band_90_high, l.var95_pct, l.var99_pct, l.regime, l.prob_up,
                  l.drift_spike, l.offsets_fit_date
           FROM forecast_ledger l
           JOIN forecast_universe u
             ON l.forecast_date=u.forecast_date AND l.product_id=u.product_id
                AND l.sub_type=u.sub_type
           WHERE l.forecast_date=? AND l.horizon=30 AND u.publish_flag=1
           ORDER BY u.rank ASC""",
        [as_of]).fetchall()

    cards, drop_pool, storms = [], [], []
    for (rank, pid, name, price, point, b90h, v95, v99, regime, prob_up,
         ds_col, off_col) in rows:
        if not price or price <= 0:
            continue
        spike = recover_spike(ds_col, off_col)
        move = round((point / price - 1) * 100, 2)
        band = round((b90h - point) / price * 100, 2)
        safe = safe_hold_grade(v95 if v95 is not None else 0.0,
                               v99 if v99 is not None else 0.0)
        mom = "NA" if spike else momentum_grade(move, prob_up if prob_up is not None else 0.5)
        game = game_label(catmap.get(pid))
        cards.append({
            "name": name,
            "set": game,                 # no set-label source in the DB yet; game is the label
            "game": game,
            "product_id": pid,
            "price": round(float(price), 2),
            "regime": regime,
            "move": move,
            "band": band,
            "safe": safe,
            "mom": mom,
            "graded": None,              # raw singles; graded tier is phase 2
            "img": None,                 # dashboard builds the TCGplayer URL from product_id
        })
        if spike:
            storms.append({"name": name, "reason": "drift spike — 30d forecast unreliable", "emoji": "🌪️"})
        elif prob_up is not None:
            drop_pool.append((prob_up, name))

    # storms = drift_spike cards first, then fill with worst chance-of-drop (lowest prob_up); 2–5 total
    drop_pool.sort(key=lambda x: x[0])
    for prob_up, name in drop_pool:
        if len(storms) >= 5:
            break
        drop = round((1 - prob_up) * 100)
        if drop < 50:                    # only surface a genuine downside bias
            break
        storms.append({"name": name, "reason": f"{drop}% chance of a 30-day drop", "emoji": "📉"})
    storms = storms[:5]

    feed = {
        "as_of": as_of,
        "horizon_default": 30,
        "cards": cards,
        "storms": storms,
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)
    led.close(); mkt.close()
    print(f"✓ wrote {a.out} — as_of {as_of} · {len(cards)} cards · {len(storms)} storms")


if __name__ == "__main__":
    main()
