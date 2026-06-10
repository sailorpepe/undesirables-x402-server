#!/bin/bash
echo "🛡️ Booting x402 Server with Secure Enterprise Proxy..."

# Inject Enterprise Master Keys exclusively into this subshell
export EBAY_CLIENT_ID="YOUR_MASTER_CLIENT_ID"
export EBAY_CLIENT_SECRET="YOUR_MASTER_CLIENT_SECRET"
export CDP_API_KEY_ID="YOUR_CDP_KEY_ID"
export CDP_API_KEY_PRIVATE_KEY="YOUR_CDP_PRIVATE_KEY"

# Force Python into unbuffered mode and execute
/Users/thegreatluna8713/Documents/undesirables-x402-server/venv/bin/python3 -u server.py
