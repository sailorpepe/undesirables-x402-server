#!/bin/bash
# ═══════════════════════════════════════════════════
# Graded Enrichment — Run 2 (New Key, SEALED PRODUCTS)
# 5:00 AM — TheUndes-Litecoin key, 833 sealed, 5,000 calls
# ═══════════════════════════════════════════════════

VENV="/Users/thegreatluna8713/Documents/undesirables-x402-server/venv/bin/python3"
SCRIPT="/Users/thegreatluna8713/bin/graded_enrichment.py"

# New dedicated key (loaded from .env via dotenv)
export ENRICHMENT_BUDGET=4998
export ENRICHMENT_MODE=singles

exec "$VENV" "$SCRIPT"
