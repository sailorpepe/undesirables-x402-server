import asyncio
import os
import httpx
from eth_account import Account
from dotenv import load_dotenv

load_dotenv()
from x402 import x402Client
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client
from x402.http.x402_http_client import x402HTTPClient

# Grade is the last unindexed endpoint
ENDPOINTS = [
    {
        "url": "https://oracle.the-undesirables.com/api/v1/grade",
        "params": {"image_url": "https://upload.wikimedia.org/wikipedia/en/1/1a/Pok%C3%A9mon_Charizard_art.png", "game": "Pokemon"}
    },
]

async def register_endpoint(http, http_client, ep):
    url = ep["url"]
    params = ep["params"]
    name = url.split("/")[-1]
    print(f"\n🚀 Registering: {name} (with valid params)")
    try:
        resp = await http.get(url, params=params, headers={"user-agent": "httpx"})

        if resp.status_code != 402:
            print(f"❌ Expected 402, got {resp.status_code}")
            return

        headers_dict = dict(resp.headers)
        payment_headers, payload = await http_client.handle_402_response(
            headers_dict, resp.content
        )

        final = await http.get(url, params=params, headers={**payment_headers, "user-agent": "httpx"})
        print(f"✅ {name} → Status: {final.status_code}")
        if final.status_code == 200:
            print(f"   💰 Settlement triggered! Facilitator will index.")
        else:
            print(f"   ⚠️  Body: {final.text[:300]}")

    except Exception as e:
        print(f"❌ {name} Error: {e}")

async def main():
    pk = os.getenv("BUYER_PRIVATE_KEY")
    if not pk:
        print("❌ BUYER_PRIVATE_KEY not set"); return
    if not pk.startswith("0x"):
        pk = "0x" + pk

    client = x402Client()
    register_exact_evm_client(client, EthAccountSigner(Account.from_key(pk)))
    http_client = x402HTTPClient(client)

    print("🔥 Final Registration — grade endpoint\n")
    async with httpx.AsyncClient(timeout=120.0) as http:
        for ep in ENDPOINTS:
            await register_endpoint(http, http_client, ep)

if __name__ == "__main__":
    asyncio.run(main())
