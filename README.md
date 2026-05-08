# The Undesirables — x402 Paid API Server

A Coinbase `x402` micropayment-gated API for **The Undesirables Shroomy Oracle** — an autonomous financial intelligence service covering TCG collectibles, NFTs, and fungible crypto tokens.

AI agents discover this server on the [x402 Bazaar](https://docs.cdp.coinbase.com/x402/bazaar), pay per-call with USDC on Base, and receive institutional-grade Monte Carlo forecasting with **full model transparency**.

## ⚡ Overview

Built on the Coinbase Developer Platform's Bazaar Discovery protocol, this server exposes proprietary `fastmcp` tools as REST endpoints. Agents who hit paid endpoints receive a standard HTTP `402 Payment Required` response containing the signed schemas needed to fulfill the price via blockchain.

### API Endpoints

| Endpoint | Price (USDC) | Function |
|----------|-------------|----------|
| `GET /api/v1/grade` | **$0.10** | 3-stage AI card grading: Vision LLM + OpenCV centering + BGS capping |
| `GET /api/v1/grade-or-not` | **$0.10** | Grade-or-Not Decision Engine — "will grading this card make me money?" |
| `GET /api/v1/simulate` | **$0.015** | Monte Carlo price forecasting (Heston, Merton, Kou stochastic models) |
| `GET /api/v1/trending` | **$0.025** | Trending Cards Feed — top movers by sales volume and price velocity |
| `GET /api/v1/arb-grade` | **$0.15** | Raw Card Arbitrage Scanner — finds cards where grading ROI exceeds threshold |
| `GET /api/v1/portfolio-optimize` | **$0.50** | Markowitz mean-variance portfolio optimization with Kou jump-diffusion |
| `GET /api/v1/crypto-oracle` | **$0.05** | NFT floor price oracle — Alchemy + Heston Monte Carlo |
| `GET /api/v1/coin-history` | **$0.05** | CoinGecko OHLC token data + Heston Monte Carlo forecasting |
| `GET /api/v1/arb-cross` | **$1.00** | Cross-Platform Arb Scanner — Polymarket vs Kalshi via Gen3 NLI |
| `GET /api/v1/arb-basket` | **$0.50** | Basket Arb Scanner — guaranteed-profit NO yield aggregator |
| `GET /api/v1/arb-weather` | **$0.25** | Weather Edge Scanner — NWS forecasts vs Kalshi temperature derivatives |
| `GET /api/v1/search` | **Free** | TCGPlayer ID lookup & product metadata |
| `GET /api/v1/market` | **Free** | Price distributions & liquidity metrics |
| `GET /api/v1/accuracy` | **Free** | Public accuracy dashboard — MAE, hit rates, grade distribution |
| `POST /api/v1/accuracy/report` | **Free** | Report your actual PSA/BGS grade vs our prediction |
| `POST /api/v1/batch-triage` | **$0.50** | Batch Card Triage — grade up to 20 cards, ranked by expected profit |
| `POST /api/v1/alerts/subscribe` | **Free** | Subscribe to a price alert — webhook fires when price crosses threshold |
| `GET /api/v1/alerts` | **Free** | List active price alerts |
| `DELETE /api/v1/alerts/{id}` | **Free** | Unsubscribe from a price alert |
| `POST /api/v1/alerts/check` | **Free** | Manually trigger alert check cycle against current prices |

### 🔍 Full Model Transparency

Every paid Oracle response ships with the exact `model_params` used to generate the forecast. Your agent doesn't just get a number — it gets the math:

```json
{
  "model": "heston_stochastic",
  "simulations": 20000,
  "model_params": {
    "drift": 0.15,
    "vol_of_vol": 0.85,
    "mean_reversion": 1.5,
    "long_term_variance": 0.7225
  },
  "forecast_percentiles": {
    "5th": 2.6214,
    "25th": 4.3274,
    "50th": 6.0821,
    "75th": 8.4504,
    "95th": 13.7839
  }
}
```

This allows downstream agents to validate assumptions, compare drift parameters against their own priors, or feed the raw percentiles into portfolio optimization engines.

## 📦 Setup & Installation

**Prerequisites:** Python 3.11+, and a funded Coinbase Developer Platform wallet.

```bash
git clone https://github.com/sailorpepe/undesirables-x402-server.git
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

# Alchemy API Key (Required for /api/v1/crypto-oracle)
ALCHEMY_API_KEY=your_alchemy_key

# CoinGecko API Key (Required for /api/v1/coin-history — free tier)
COINGECKO_API_KEY=your_coingecko_key

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
