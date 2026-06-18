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
    
    # We must append query string since /simulate requires parameters:
    endpoint = "https://oracle.the-undesirables.com/api/v1/simulate?card_name=Charizard&current_price=350.00&model=conformal"
    
    print(f"🚀 Triggering MANUAL x402 Handshake...")
    
    # We use naked httpx to bypass the flawed x402HttpxClient wrapper
    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            # Step 1: Initial GET to trigger the 402 challenge
            print("📡 Sending unsigned GET request...")
            # We explicitly pass the httpx user-agent so our server.py recognizes it's an SDK client
            response_402 = await http.get(endpoint, headers={"user-agent": "httpx"})
            
            if response_402.status_code != 402:
                print(f"❌ Expected 402, got {response_402.status_code}. Response: {response_402.text}")
                return
                
            print("✅ Received 402 Challenge from Server.")
            
            # Step 2: Extract the raw Payment-Required Header
            pr_header = response_402.headers.get("payment-required")
            if not pr_header:
                print("❌ No 'Payment-Required' header found in response!")
                return
                
            payment_required = decode_payment_required_header(pr_header)
            print("📦 Successfully parsed schema requirements.")
            
            # Step 3: CRITICAL FIX — Manually attach extensions that the SDK drops
            payment_payload = await client.create_payment_payload(
                payment_required,
                resource=payment_required.resource,  # Copies absolute URL
                extensions=payment_required.extensions  # FORCES Bazaar metadata ingestion!
            )
            
            # Step 4 is done automatically during payment_payload creation!
            print("✍️ Generating crypto signature with EVM wallet...")
            
            # Step 5: Format the Signature header
            sig_b64 = encode_payment_signature_header(payment_payload)
            submit_headers = {"Payment-Signature": sig_b64, "user-agent": "httpx"}
            
            # Step 6: Hit the endpoint one last time with the embedded signature
            print("🚀 Pushing signed payload & schema dictionary directly to Facilitator...")
            final_response = await http.get(endpoint, headers=submit_headers)
            
            print(f"✅ Handshake Complete! Status Code: {final_response.status_code}")
            try:
                print("📦 Final Data Received:", final_response.json())
            except:
                print("📦 Final Raw Response:", final_response.text)
                
            print("🎉 Successfully forced schema extraction bypass. Coinbase MUST index this.")
            
    except Exception as e:
        print(f"❌ Payment Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
