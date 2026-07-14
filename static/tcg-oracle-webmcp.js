/**
 * TCG Oracle — WebMCP Integration
 * 
 * Registers on-chain trading card price tools via the WebMCP standard,
 * making them discoverable and callable by any AI agent browsing this page.
 * 
 * WebMCP Spec: https://developer.chrome.com/docs/ai/webmcp
 * Oracle API:  https://oracle.the-undesirables.com
 * 
 * @author  SailorPepe — The Undesirables LLC
 * @license BUSL-1.1
 * @version 1.0.0
 */

(function () {
  "use strict";

  const API_BASE = "https://oracle.the-undesirables.com";

  // ── Feature Detection ──────────────────────────────────────────────
  if (typeof navigator === "undefined" || !navigator.modelContext) {
    console.log(
      "[TCG Oracle WebMCP] navigator.modelContext not available. " +
      "Enable via chrome://flags/#enable-webmcp-testing (Chrome 146+)."
    );
    return;
  }

  const mc = navigator.modelContext;

  // ── Helper: fetch JSON from the oracle API ─────────────────────────
  async function oracleFetch(path) {
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { "Accept": "application/json" },
    });
    if (!res.ok) {
      throw new Error(`Oracle API ${res.status}: ${res.statusText}`);
    }
    return res.json();
  }

  // ── Tool 1: Search Cards ───────────────────────────────────────────
  mc.addTool({
    name: "tcg_search",
    description:
      "Search 432,000+ trading cards across Pokémon, Magic: The Gathering, " +
      "Yu-Gi-Oh!, Lorcana, and 9 other categories. Returns product IDs, " +
      "names, categories, and current market prices.",
    schema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "Card name to search for (e.g. 'Charizard Base Set', 'Black Lotus')",
        },
        limit: {
          type: "number",
          description: "Max results to return (default 10, max 50)",
        },
      },
      required: ["query"],
    },
    handler: async ({ query, limit }) => {
      const n = Math.min(limit || 10, 50);
      return oracleFetch(`/api/v1/search?query=${encodeURIComponent(query)}&limit=${n}`);
    },
  });

  // ── Tool 2: Get Price + History ────────────────────────────────────
  mc.addTool({
    name: "tcg_price",
    description:
      "Get the current market price and historical price data for a " +
      "specific trading card by product ID. Returns market, low, mid, " +
      "and high prices with up to 60 days of daily history.",
    schema: {
      type: "object",
      properties: {
        product_id: {
          type: "number",
          description: "TCG product ID (obtain from tcg_search)",
        },
        days: {
          type: "number",
          description: "Days of price history to return (default 30, max 60)",
        },
      },
      required: ["product_id"],
    },
    handler: async ({ product_id, days }) => {
      const d = Math.min(days || 30, 60);
      return oracleFetch(`/api/v1/price?product_id=${product_id}&days=${d}`);
    },
  });

  // ── Tool 3: Graded Card Premiums ───────────────────────────────────
  mc.addTool({
    name: "tcg_graded_premiums",
    description:
      "Get graded card prices (PSA 10, PSA 9, BGS 9.5, CGC 9.5) for a " +
      "trading card. Shows the premium multiplier versus raw market price. " +
      "Data sourced from eBay sold listings.",
    schema: {
      type: "object",
      properties: {
        product_id: {
          type: "number",
          description: "TCG product ID (obtain from tcg_search)",
        },
      },
      required: ["product_id"],
    },
    handler: async ({ product_id }) => {
      return oracleFetch(`/api/v1/graded?product_id=${product_id}`);
    },
  });

  // ── Tool 4: List Categories ────────────────────────────────────────
  mc.addTool({
    name: "tcg_categories",
    description:
      "List all 13 supported trading card game categories with product " +
      "counts. Includes Pokémon, Magic, Yu-Gi-Oh!, Lorcana, Flesh and " +
      "Blood, MetaZoo, Cardfight Vanguard, and more.",
    schema: {
      type: "object",
      properties: {},
    },
    handler: async () => {
      return oracleFetch("/api/v1/categories");
    },
  });

  // ── Tool 5: On-Chain Merkle Proof ──────────────────────────────────
  mc.addTool({
    name: "tcg_merkle_proof",
    description:
      "Get a cryptographic Merkle proof for a card's price, verifiable " +
      "on-chain against the LitVM LiteForge Merkle Oracle contract " +
      "(0xc159550e9e751d6E75A0A06Bb04cfA2f59aD636B). Covers 276,000+ " +
      "actively priced products.",
    schema: {
      type: "object",
      properties: {
        product_id: {
          type: "number",
          description: "TCG product ID to generate proof for",
        },
      },
      required: ["product_id"],
    },
    handler: async ({ product_id }) => {
      return oracleFetch(`/api/v1/merkle/proof?product_id=${product_id}`);
    },
  });

  // ── Tool 6: Oracle Stats ───────────────────────────────────────────
  mc.addTool({
    name: "tcg_oracle_stats",
    description:
      "Get live statistics about the TCG Oracle: total products indexed, " +
      "price history rows, on-chain contract addresses, data freshness, " +
      "and supported categories.",
    schema: {
      type: "object",
      properties: {},
    },
    handler: async () => {
      return oracleFetch("/api/v1/stats");
    },
  });

  // ── Tool 7: Top Movers ─────────────────────────────────────────────
  mc.addTool({
    name: "tcg_top_movers",
    description:
      "Get the top gaining and losing trading cards by price change " +
      "percentage over the last 7 days. Useful for identifying trending " +
      "cards and market shifts.",
    schema: {
      type: "object",
      properties: {
        direction: {
          type: "string",
          enum: ["gainers", "losers"],
          description: "Show top gainers or top losers",
        },
        limit: {
          type: "number",
          description: "Number of results (default 10, max 25)",
        },
      },
      required: ["direction"],
    },
    handler: async ({ direction, limit }) => {
      const n = Math.min(limit || 10, 25);
      return oracleFetch(`/api/v1/movers?direction=${direction}&limit=${n}`);
    },
  });

  // ── Tool 8: Undesirables Collection — mint status ──────────────────
  mc.addTool({
    name: "undesirables_mint_status",
    description:
      "Live mint status for The Undesirables (UNDSR) NFT collection — " +
      "4,444-supply ERC-721A on Ethereum mainnet. Returns supply minted/" +
      "remaining, public mint price in ETH, wallet limits, and how to mint.",
    schema: { type: "object", properties: {} },
    handler: async () => oracleFetch("/api/v1/collection"),
  });

  // ── Tool 9: Undesirables — prepare unsigned mint transaction ───────
  mc.addTool({
    name: "undesirables_prepare_mint",
    description:
      "Build an UNSIGNED Ethereum transaction to mint The Undesirables NFT. " +
      "Returns {to, data, value, chainId} which the USER must sign with " +
      "their own wallet — this oracle never holds keys and cannot mint on " +
      "anyone's behalf. Validates wallet limit and supply on-chain first.",
    schema: {
      type: "object",
      properties: {
        quantity: {
          type: "number",
          description: "How many to mint (public wallet limit applies, currently 2)",
        },
        to: {
          type: "string",
          description: "Optional recipient address; omit to mint to the transaction sender",
        },
      },
      required: ["quantity"],
    },
    handler: async ({ quantity, to }) => {
      const q = `quantity=${encodeURIComponent(quantity)}` + (to ? `&to=${encodeURIComponent(to)}` : "");
      return oracleFetch(`/api/v1/collection/prepare-mint?${q}`);
    },
  });

  console.log(
    "[TCG Oracle WebMCP] ✅ 9 tools registered — " +
    "432K products + UNDSR minting available to AI agents via navigator.modelContext"
  );
})();
