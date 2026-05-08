"""
Agentic.Market (Bazaar) Mass Registration Script

Loops through every paid endpoint on the Oracle Terminal and performs
a full x402 handshake. By completing real settlements, the Coinbase/CDP
Facilitator ingests the `declare_discovery_extension` metadata, which
gets the tools listed on https://agentic.market.

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
from x402.mechanisms.evm.exact.register import register_exact_evm_client
from x402.http.core import parse_payment_required, get_payment_required_from_headers

# All paid endpoints — update this list when adding new routes
ENDPOINTS = [
    "https://oracle.the-undesirables.com/api/v1/simulate",
    "https://oracle.the-undesirables.com/api/v1/grade",
    "https://oracle.the-undesirables.com/api/v1/crypto-oracle",
    "https://oracle.the-undesirables.com/api/v1/coin-history",
    "https://oracle.the-undesirables.com/api/v1/arb-cross",
    "https://oracle.the-undesirables.com/api/v1/arb-basket",
    "https://oracle.the-undesirables.com/api/v1/arb-weather",
    "https://oracle.the-undesirables.com/api/v1/portfolio-optimize",
    "https://oracle.the-undesirables.com/api/v1/grade-or-not",
    "https://oracle.the-undesirables.com/api/v1/arb-grade",
    "https://oracle.the-undesirables.com/api/v1/trending",
    "https://oracle.the-undesirables.com/api/v1/batch-triage",
]


async def register_endpoint(http, client, endpoint):
    print(f"\n🚀 Registering: {endpoint}")
    try:
        response_402 = await http.get(endpoint, headers={"user-agent": "httpx"})

        if response_402.status_code != 402:
            print(f"❌ Expected 402, got {response_402.status_code}. Skipping...")
            return

        headers_dict = dict(response_402.headers)
        pr_headers = list(get_payment_required_from_headers(headers_dict))
        if not pr_headers:
            print("❌ No 'Payment-Required' header found!")
            return

        payment_required = parse_payment_required(pr_headers[0])

        # CRITICAL: Manually attach extensions that the SDK drops.
        # This registers it on Agentic Market.
        payment_payload = await client.create_payment_payload(
            payment_required,
            resource=payment_required.resource,
            extensions=payment_required.extensions,
        )

        payment_signature = await client.authorize_payment(payment_payload)
        sig_b64 = payment_signature.to_base64()

        submit_headers = {"Payment-Signature": sig_b64, "user-agent": "httpx"}
        final_response = await http.get(endpoint, headers=submit_headers)

        print(
            f"✅ Handshake Complete for {endpoint.split('/')[-1]}! "
            f"Status Code: {final_response.status_code}"
        )
    except Exception as e:
        print(f"❌ Error registering {endpoint}: {e}")


async def main():
    pk = os.getenv("BUYER_PRIVATE_KEY")
    if not pk:
        print("❌ Error: BUYER_PRIVATE_KEY not found in .env")
        return

    if not pk.startswith("0x"):
        pk = "0x" + pk

    client = x402Client()
    account = Account.from_key(pk)
    register_exact_evm_client(client, EthAccountSigner(account))

    print("Starting Agentic.Market (Bazaar) Mass Registration...\n")
    async with httpx.AsyncClient(timeout=60.0) as http:
        for ep in ENDPOINTS:
            await register_endpoint(http, client, ep)
            await asyncio.sleep(2)  # rate limit buffer


if __name__ == "__main__":
    asyncio.run(main())
