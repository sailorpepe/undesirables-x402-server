#!/bin/bash
# ═══════════════════════════════════════════════════
# Graded Enrichment — Run 1 (Old Key, SINGLES ONLY)
# 4:00 AM — KobuMini key, 500 singles, 3,000 calls
# ═══════════════════════════════════════════════════

VENV="/Users/thegreatluna8713/Documents/undesirables-x402-server/venv/bin/python3"
SCRIPT="/Users/thegreatluna8713/bin/graded_enrichment.py"

# Old key (fallback — empty enrichment vars → uses EBAY_APP_ID from .env)
unset EBAY_ENRICHMENT_APP_ID
unset EBAY_ENRICHMENT_CLIENT_SECRET
export ENRICHMENT_BUDGET=3000
export ENRICHMENT_MODE=singles

exec "$VENV" "$SCRIPT"
