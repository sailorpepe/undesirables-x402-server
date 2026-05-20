"""
Agentic.Market (Bazaar) Mass Registration Script

Loops through every paid endpoint on the Oracle Terminal and performs
a full x402 handshake. By completing real settlements, the Coinbase/CDP
Facilitator ingests the `declare_discovery_extension` metadata, which
gets the tools listed on https://agentic.market.

x402 SDK v2.10+ — uses ExactEvmScheme with automatic 402 handling.

Usage:
  1. Set BUYER_PRIVATE_KEY in your .env (a funded Base wallet)
  2. Run: python3 register_agentic.py
"""

import asyncio
import os
import httpx
from eth_account import Account
from dotenv import load_dotenv

load_dotenv()

from x402 import x402Client
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact import ExactEvmScheme

# All paid endpoints that return 402 — verified May 20, 2026
ENDPOINTS = [
    {
        "url": "https://oracle.the-undesirables.com/api/v1/simulate",
        "params": {"card_name": "Charizard", "current_price": 350, "days": 30},
    },
    {
        "url": "https://oracle.the-undesirables.com/api/v1/grade",
        "params": {"card_name": "Charizard Base Set"},
    },
    {
        "url": "https://oracle.the-undesirables.com/api/v1/crypto-oracle",
        "params": {"query": "What is ETH price prediction?"},
    },
    {
        "url": "https://oracle.the-undesirables.com/api/v1/coin-history",
        "params": {"coin": "ethereum", "days": 7},
    },
    {
        "url": "https://oracle.the-undesirables.com/api/v1/arb-cross",
        "params": {"query": "cross-market arbitrage opportunities"},
    },
    {
        "url": "https://oracle.the-undesirables.com/api/v1/arb-basket",
        "params": {"query": "basket arbitrage analysis"},
    },
    {
        "url": "https://oracle.the-undesirables.com/api/v1/arb-weather",
        "params": {"query": "weather derivatives arbitrage"},
    },
]


async def register_endpoint(client, http, endpoint):
    url = endpoint["url"]
    name = url.split("/")[-1]
    params = endpoint.get("params", {})

    print(f"\n🚀 Registering: {name}")
    try:
        # Step 1: Hit endpoint to get 402 challenge
        response_402 = await http.get(url, params=params)

        if response_402.status_code != 402:
            print(f"   ⚠️  Expected 402, got {response_402.status_code}. Skipping.")
            return False

        # Step 2: Extract payment-required header
        pr_header = response_402.headers.get("payment-required")
        if not pr_header:
            print("   ❌ No 'Payment-Required' header found.")
            return False

        # Step 3: Let the v2.10 client handle the 402 automatically
        # The client.get() method intercepts 402, signs, and retries
        paid_response = await client.get(url, params=params, session=http)

        if paid_response.status_code == 200:
            print(f"   ✅ Settlement complete! Status: {paid_response.status_code}")
            return True
        else:
            print(f"   ⚠️  Post-payment status: {paid_response.status_code}")
            return False

    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


async def main():
    pk = os.getenv("BUYER_PRIVATE_KEY")
    if not pk:
        print("❌ Error: BUYER_PRIVATE_KEY not found in .env")
        return

    if not pk.startswith("0x"):
        pk = "0x" + pk

    # v2.10 client setup
    account = Account.from_key(pk)
    signer = EthAccountSigner(account)
    client = x402Client()
    client.register("eip155:*", ExactEvmScheme(signer=signer))

    print(f"Wallet: {account.address}")
    print(f"Endpoints: {len(ENDPOINTS)}")
    print("Starting Agentic.Market registration...\n")

    success = 0
    failed = 0

    async with httpx.AsyncClient(timeout=60.0) as http:
        for ep in ENDPOINTS:
            result = await register_endpoint(client, http, ep)
            if result:
                success += 1
            else:
                failed += 1
            await asyncio.sleep(2)  # rate limit buffer

    print(f"\n{'═' * 50}")
    print(f"  Results: {success} registered, {failed} failed")
    print(f"  Total endpoints: {len(ENDPOINTS)}")
    print(f"{'═' * 50}")


if __name__ == "__main__":
    asyncio.run(main())
