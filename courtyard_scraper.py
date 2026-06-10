#!/usr/bin/env python3
"""
Courtyard.io Marketplace Scraper v3
Intercepts the actual API/network requests that Courtyard makes
to fetch card data, rather than trying to parse the DOM.
Writes verified data to data/courtyard_live.json
Designed to run every 4 hours via cron.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "courtyard_live.json"


def scrape_courtyard():
    """Scrape all marketplace listings from Courtyard.io via API interception"""
    captured_responses = []
    cards = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
            ]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)
        page = context.new_page()

        # ─── Intercept ALL network responses to capture API data ───
        def handle_response(response):
            url = response.url
            # Capture Algolia search responses (Courtyard uses Algolia for search)
            if 'algolia' in url.lower() or 'search' in url.lower():
                try:
                    body = response.json()
                    captured_responses.append({'url': url, 'data': body, 'type': 'algolia'})
                    print(f"[SCRAPER] Captured Algolia response: {url[:80]}...")
                except:
                    pass
            # Capture any API endpoint that returns card data
            elif '/api/' in url or '/marketplace' in url:
                if response.headers.get('content-type', '').startswith('application/json'):
                    try:
                        body = response.json()
                        captured_responses.append({'url': url, 'data': body, 'type': 'api'})
                        print(f"[SCRAPER] Captured API response: {url[:80]}...")
                    except:
                        pass
            # Capture RSC flight data
            elif 'rsc' in url.lower() or '_next/data' in url:
                try:
                    body = response.text()
                    if '$' in body and len(body) > 500:
                        captured_responses.append({'url': url, 'data': body, 'type': 'rsc'})
                        print(f"[SCRAPER] Captured RSC response: {url[:80]}... ({len(body)} bytes)")
                except:
                    pass

        page.on("response", handle_response)

        print("[SCRAPER] Loading courtyard.io/marketplace...")
        try:
            page.goto("https://courtyard.io/marketplace", wait_until="networkidle", timeout=45000)
        except Exception as e:
            print(f"[SCRAPER] Initial load timed out, continuing anyway: {e}")
        time.sleep(5)

        # Scroll to trigger lazy loads and additional API calls
        for i in range(10):
            page.evaluate("window.scrollBy(0, 600)")
            time.sleep(1)

        # Also try clicking "next page" buttons to get more data
        time.sleep(2)

        # ─── Fallback: scrape the fully-hydrated DOM with a wait ───
        print("[SCRAPER] Waiting for React hydration...")
        time.sleep(3)

        # Try to grab card data from the rendered DOM after full hydration
        dom_data = page.evaluate("""
        () => {
            const results = [];

            // After hydration, grab ALL anchor tags that link to assets
            const links = document.querySelectorAll('a[href*="/asset/"]');
            
            links.forEach(link => {
                const text = link.innerText || '';
                const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                const href = link.getAttribute('href') || '';
                
                // Find all dollar amounts in this card's text
                const priceMatches = text.match(/\\$\\s*[\\d,]+(?:\\.\\d{2})?/g) || [];
                const percentMatches = text.match(/-?\\d+%/g) || [];

                let name = '';
                let prices = [];
                let discount = null;

                // Extract prices
                for (const pm of priceMatches) {
                    const val = parseFloat(pm.replace(/[$,\\s]/g, ''));
                    if (!isNaN(val)) prices.push(val);
                }

                // Extract discount
                for (const pct of percentMatches) {
                    const val = parseInt(pct);
                    if (!isNaN(val)) discount = val;
                }

                // Name: find the line that looks like a card name
                for (const line of lines) {
                    if (line.length > 8
                        && !line.startsWith('$')
                        && !line.startsWith('FMV')
                        && !line.match(/^-?\\d+%$/)
                        && !line.match(/^\\d+$/)
                        && !line.match(/^Recently/)
                        && !line.match(/^Filters/)
                    ) {
                        name = line;
                        break;
                    }
                }

                if (name && prices.length > 0) {
                    results.push({
                        name: name,
                        prices: prices,
                        discount: discount,
                        href: href,
                        raw_text: text.substring(0, 300),
                        raw_lines: lines
                    });
                }
            });

            return {
                cards: results,
                count: results.length,
                page_title: document.title,
                all_links_count: document.querySelectorAll('a[href*="/asset/"]').length
            };
        }
        """)

        print(f"[SCRAPER] Page: {dom_data.get('page_title', '?')}")
        print(f"[SCRAPER] Asset links found: {dom_data.get('all_links_count', 0)}")
        print(f"[SCRAPER] Cards with prices: {dom_data.get('count', 0)}")
        print(f"[SCRAPER] Network captures: {len(captured_responses)}")

        # ─── Process DOM-extracted cards ───
        if dom_data.get("count", 0) > 0:
            for card in dom_data["cards"]:
                name = card["name"]
                prices = card.get("prices", [])
                
                # The first price is usually the list price, second is FMV
                list_price = prices[0] if len(prices) >= 1 else None
                fmv = prices[1] if len(prices) >= 2 else None

                if list_price is None:
                    continue

                # Calculate delta
                if list_price is not None and fmv is not None and fmv > 0:
                    delta = round((list_price - fmv) / fmv * 100, 1)
                    savings = round(fmv - list_price, 2)
                else:
                    delta = 0
                    savings = 0

                # Extract grade
                grade = "N/A"
                for pattern in ["PSA 10", "PSA 9", "PSA 8", "PSA 7", "PSA 6", "PSA 5",
                                "CGC 10", "CGC 9.5", "CGC 9", "CGC 8.5", "CGC 8", "CGC 7.5",
                                "BGS 10", "BGS 9.5", "BGS 9", "BGS 8"]:
                    if pattern in name:
                        grade = pattern
                        break

                # Detect category
                cat = "Other"
                nl = name.lower()
                if any(k in nl for k in ["pokémon", "pokemon", "pikachu", "charizard", "holo", "vmax", "ex ", "gx ", "vstar"]):
                    cat = "Pokemon"
                elif any(k in nl for k in ["panini", "topps", "bowman", "fleer", "score ", "donruss", "upper deck", "prizm", "select", "optic"]):
                    cat = "Sports"
                elif any(k in nl for k in ["magic", "mtg", "booster"]):
                    cat = "MTG"
                elif any(k in nl for k in ["yu-gi-oh", "yugioh"]):
                    cat = "YuGiOh"

                cards.append({
                    "name": name,
                    "grade": grade,
                    "list": list_price,
                    "fmv": fmv,
                    "delta_pct": delta,
                    "savings": savings,
                    "cat": cat,
                    "signal": "BUY" if delta < -5 else "OVERPRICED" if delta > 5 else "FAIR",
                    "courtyard_badge": card.get("discount"),
                    "url": f"https://courtyard.io{card.get('href', '')}",
                })

        # ─── Process captured API/Algolia responses ───
        if len(cards) == 0 and len(captured_responses) > 0:
            print("[SCRAPER] DOM extraction got 0 cards. Parsing network captures...")
            for cap in captured_responses:
                if cap['type'] == 'algolia' and isinstance(cap['data'], dict):
                    # Algolia returns results in .results[0].hits or .hits
                    hits = []
                    if 'results' in cap['data']:
                        for result_block in cap['data']['results']:
                            hits.extend(result_block.get('hits', []))
                    elif 'hits' in cap['data']:
                        hits = cap['data']['hits']

                    # Debug: dump all keys from first hit so we can find FMV field
                    if hits:
                        print(f"[SCRAPER] Algolia hit keys: {list(hits[0].keys())}")
                        # Also dump the first hit for full inspection
                        debug_hit_path = OUTPUT_DIR / "courtyard_algolia_sample.json"
                        with open(debug_hit_path, "w") as df:
                            json.dump(hits[0], df, indent=2)
                        print(f"[SCRAPER] Saved sample hit to {debug_hit_path}")

                    for hit in hits:
                        # Use exact Algolia schema fields
                        name = hit.get('title', '')
                        image = hit.get('imageUrl', '')

                        # Price: nested object {currency, amountUsd, amountNative}
                        raw_price = hit.get('price', {})
                        list_price = raw_price.get('amountUsd') if isinstance(raw_price, dict) else raw_price

                        # FMV: estimatedValueUsd (flat number)
                        fmv = hit.get('estimatedValueUsd')
                        fmv_confidence = hit.get('estimatedValueConfidence', 'unknown')

                        # Deal score: Courtyard's own calculated discount %
                        deal_score = hit.get('dealScore')

                        # Grade: extract full label from title parentheses
                        # Titles end with e.g. "(PSA 10 GEM MINT)" or "(CGC 9 MINT)"
                        import re
                        grade_match = re.search(r'\(((?:PSA|CGC|BGS|SGC)\s+[\d.]+[^)]*)\)', name)
                        if grade_match:
                            grade = grade_match.group(1).strip()
                        else:
                            grade = "Ungraded"

                        # Store cert serial separately if available
                        cert = hit.get('certification', {})
                        cert_serial = cert.get('number', '') if isinstance(cert, dict) else ''

                        # Category from metadata or title
                        meta = hit.get('metadata', {})
                        meta_cat = meta.get('Category', '') if isinstance(meta, dict) else ''

                        if meta_cat:
                            cat_lower = meta_cat.lower()
                            if 'pokemon' in cat_lower or 'pokémon' in cat_lower:
                                cat = "Pokemon"
                            elif 'magic' in cat_lower:
                                cat = "MTG"
                            elif 'yu-gi-oh' in cat_lower or 'yugioh' in cat_lower:
                                cat = "YuGiOh"
                            elif 'one piece' in cat_lower:
                                cat = "One Piece"
                            elif any(k in cat_lower for k in ['baseball', 'basketball', 'football', 'soccer', 'hockey', 'sports']):
                                cat = "Sports"
                            else:
                                cat = meta_cat  # Use Courtyard's own category
                        else:
                            cat = "Other"
                            nl = name.lower()
                            if any(k in nl for k in ["pokémon", "pokemon", "pikachu", "charizard"]):
                                cat = "Pokemon"
                            elif any(k in nl for k in ["panini", "topps", "bowman", "prizm"]):
                                cat = "Sports"
                            elif any(k in nl for k in ["magic", "mtg"]):
                                cat = "MTG"

                        if name and list_price is not None:
                            # Calculate delta from FMV
                            if fmv and fmv > 0:
                                delta = round((list_price - fmv) / fmv * 100, 1)
                                savings = round(fmv - list_price, 2)
                            else:
                                delta = 0
                                savings = 0

                            cards.append({
                                "name": name,
                                "grade": grade,
                                "list": list_price,
                                "fmv": fmv,
                                "delta_pct": delta,
                                "savings": savings,
                                "deal_score": deal_score,
                                "fmv_confidence": fmv_confidence,
                                "cat": cat,
                                "collection": hit.get('collection', ''),
                                "set": hit.get('set', ''),
                                "year": hit.get('year', 0),
                                "image": image,
                                "chain": hit.get('chain', ''),
                                "signal": "BUY" if delta < -5 else "OVERPRICED" if delta > 5 else "FAIR",
                            })
                    print(f"[SCRAPER] Extracted {len(cards)} cards from Algolia network capture")

        # ─── Save debug data if still empty ───
        if len(cards) == 0:
            debug = {
                "captured_responses_count": len(captured_responses),
                "dom_data": dom_data,
                "captured_urls": [c['url'] for c in captured_responses],
                "sample_captures": []
            }
            for cap in captured_responses[:3]:
                sample = str(cap.get('data', ''))[:2000]
                debug["sample_captures"].append({"url": cap['url'], "type": cap['type'], "data_preview": sample})

            debug_path = OUTPUT_DIR / "courtyard_debug.json"
            with open(debug_path, "w") as f:
                json.dump(debug, f, indent=2)
            print(f"[SCRAPER] Saved full debug data to {debug_path}")

        browser.close()

    # Sort by delta (most underpriced first)
    cards.sort(key=lambda c: c["delta_pct"])

    # Compute stats
    underpriced = [c for c in cards if c["delta_pct"] < -5]
    overpriced = [c for c in cards if c["delta_pct"] > 5]
    total_savings = sum(c["savings"] for c in underpriced)

    result = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "source": "courtyard.io/marketplace",
        "total_listings": len(cards),
        "underpriced": len(underpriced),
        "overpriced": len(overpriced),
        "total_savings_usd": round(total_savings, 2),
        "cards": cards,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print(f"[SCRAPER] ✅ Saved {len(cards)} verified listings to {OUTPUT_FILE}")
    print(f"[SCRAPER]    {len(underpriced)} underpriced | {len(overpriced)} overpriced | ${total_savings:.0f} total savings")

    return result


if __name__ == "__main__":
    scrape_courtyard()
