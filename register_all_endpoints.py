import asyncio
import os
import httpx
from eth_account import Account
from dotenv import load_dotenv

load_dotenv()

from x402 import x402Client
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client
from x402.http import decode_payment_required_header, encode_payment_signature_header

async def register_endpoint(http, client, endpoint_url, name, cost):
    print(f"\n==================================================")
    print(f"🚀 Registering {name} ({cost})")
    print(f"📡 Target: {endpoint_url}")
    
    try:
        # Step 1: Initial GET to trigger the 402 challenge
        response_402 = await http.get(endpoint_url, headers={"user-agent": "httpx"})
        
        if response_402.status_code != 402:
            print(f"❌ Expected 402, got {response_402.status_code}. Skipping...")
            return False
            
        # Step 2: Extract the raw Payment-Required Header
        pr_header = response_402.headers.get("payment-required")
        if not pr_header:
            print("❌ No 'Payment-Required' header found. Skipping...")
            return False
            
        payment_required = decode_payment_required_header(pr_header)
        
        # Step 3: Attach extensions natively via create_payment_payload!
        payment_payload = await client.create_payment_payload(
            payment_required,
            resource=payment_required.resource,  
            extensions=payment_required.extensions
        )
        
        # Step 4: Sign and Format
        sig_b64 = encode_payment_signature_header(payment_payload)
        submit_headers = {"Payment-Signature": sig_b64, "user-agent": "httpx"}
        
        # Step 5: Final Handshake Push
        final_response = await http.get(endpoint_url, headers=submit_headers)
        
        if final_response.status_code == 200:
            print(f"✅ SUCCESSFULLY INDEXED! Handshake Complete.")
            return True
        else:
            print(f"❌ FAILED to index! HTTP {final_response.status_code}: {final_response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Payment Error: {str(e)}")
        return False

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
    
    # Define all paid endpoints we want on the Bazaar directory
    endpoints = [
        {"name": "AI Card Grading", "cost": "$0.10", "url": "https://oracle.the-undesirables.com/api/v1/grade?image_url=https://example.com/card.png"},
        {"name": "Crypto Oracle", "cost": "$0.05", "url": "https://oracle.the-undesirables.com/api/v1/crypto-oracle?contract_address=0xBd3531dA5CF5857e7CfAA92426877b022e612cf8"},
        {"name": "Coin History Emulator", "cost": "$0.05", "url": "https://oracle.the-undesirables.com/api/v1/coin-history?coin_id=pepe"},
        {"name": "Cross-Platform Arbitrage", "cost": "$1.00", "url": "https://oracle.the-undesirables.com/api/v1/arb-cross"},
        {"name": "Risk-Free Basket Arbitrage", "cost": "$0.50", "url": "https://oracle.the-undesirables.com/api/v1/arb-basket"},
        {"name": "Weather Derivative Arbitrage", "cost": "$0.25", "url": "https://oracle.the-undesirables.com/api/v1/arb-weather"}
    ]
    
    print("==================================================")
    print("🍄 STARTING SHROOMY BAZAAR CATALOG PUBLISHER 🍄")
    print("==================================================")
    
    async with httpx.AsyncClient(timeout=60.0) as http:
        for idx, ep in enumerate(endpoints):
            await register_endpoint(http, client, ep["url"], ep["name"], ep["cost"])
            
    print(f"\n🎉 ALL MENU ITEMS HAVE BEEN PROCESSED!")

if __name__ == "__main__":
    asyncio.run(main())
