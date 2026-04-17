# The Undesirables — x402 Paid API Server

A Coinbase `x402` micropayment-gated API proxy for **The Undesirables TCG Oracle**. 

This server acts as a monetization gateway, allowing AI agents to semantically discover and autonomously pay for trading card market intelligence using USDC on the Base network.

## ⚡ Overview

Built on the Coinbase Developer Platform's [Bazaar Discovery protocol](https://docs.cdp.coinbase.com/x402/bazaar), this repository exposes several of our proprietary `fastmcp` tools as REST endpoints. Agents who hit paid endpoints receive a standard HTTP `402 Payment Required` response containing the signed schemas needed to fulfill the price via blockchain.

### API Endpoints

| Endpoint | Price (USDC) | Function |
|----------|-------------|----------|
| `GET /api/v1/grade` | **$0.10** | Multimodal vision grading (predicts PSA / Beckett scores) |
| `GET /api/v1/crypto-oracle` | **$0.05** | Shroomy Web3 Oracle (NFT floor pricing + Monte Carlo simulation) |
| `GET /api/v1/coin-history` | **$0.05** | Historical Token Simulator (CoinGecko historical prices + Monte Carlo) |
| `GET /api/v1/simulate` | **$0.015** | Monte Carlo forecasting (Heston, Merton, Kou stochastic models) |
| `GET /api/v1/search` | **Free** | TCGPlayer ID lookup & product metadata mapping |
| `GET /api/v1/market` | **Free** | Price distributions & liquidity metrics |

## 📦 Setup & Installation

**Prerequisites:** Python 3.11+, and a funded Coinbase Developer Platform wallet.

```bash
git clone https://gitlab.com/meme-merchants/undesirables-x402-server.git
cd undesirables-x402-server

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Environment Configuration
Create a `.env` file in the root directory:

```env
# Your Base receive address
PAYMENT_ADDRESS=0xYOUR_MERCHANT_WALLET

# x402 Mainnet Configuration
FACILITATOR_URL=https://api.cdp.coinbase.com/platform/v2/x402
NETWORK=eip155:8453
USDC_ADDRESS=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913

# CDP API Keys (Required for Base Mainnet Discovery & Settlement)
CDP_API_KEY_ID=your_cdp_key_id
CDP_API_KEY_PRIVATE_KEY=your_cdp_private_key

# Server Config
HOST=0.0.0.0
PORT=8402
```

## 🚀 Running the Server

```bash
python server.py
```

The server automatically registers its JSON schemas with the Coinbase CDP Facilitator upon startup. Once a client successfully triggers the verify-and-settle cycle over the network, your Cloudflare or public IP will be permanently indexed in the global x402 Bazaar.

## 🛡️ License

This project is licensed under the **Business Source License 1.1** (BSL-1.1). You are free to view, learn from, fork, and use this code for non-commercial and academic purposes. You may not use this code to host a competing commercial TCG pricing/grading oracle. See the `LICENSE` file for full terms. 

*After 4 years, this code will automatically convert to the Apache 2.0 license.*
