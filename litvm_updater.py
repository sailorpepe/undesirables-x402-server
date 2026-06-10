#!/usr/bin/env python3
"""
LitVM TCGPriceOracle Updater
Reads top 50 most valuable cards from SQLite and pushes prices on-chain
to the TCGPriceOracle contract on LitVM LiteForge testnet.

Chain ID: 4441
RPC: https://liteforge.rpc.caldera.xyz/http
Contract: 0xA79C6b3922949fcaBb518f56f0B6e68Ca7115771
Explorer: https://liteforge.explorer.caldera.xyz

Designed to run every 6 hours via cron.
"""

import os
import sys
import json
import sqlite3
import logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="[LitVM Oracle] %(message)s"
)
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
DB_PATH = Path.home() / "Documents" / "undesirables-mcp-server" / ".cache" / "market_memory.sqlite"
ABI_PATH = SCRIPT_DIR / "TCGPriceOracle_abi.json"
ENV_PATH = SCRIPT_DIR / ".env"

RPC_URL = "https://liteforge.rpc.caldera.xyz/http"
CHAIN_ID = 4441
CONTRACT_ADDR = "0xA79C6b3922949fcaBb518f56f0B6e68Ca7115771"
TOTAL_CARDS = 50
CHUNK_SIZE = 10  # 10 cards per tx to stay within gas limits


def load_env():
    """Load .env file into environment"""
    if ENV_PATH.exists():
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())


def get_top_cards(db_path: Path, limit: int = 50) -> list[dict]:
    """Query SQLite for top N most valuable cards with latest prices"""
    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    query = """
        SELECT
            c.product_id,
            COALESCE(c.name, c.clean_name, 'Unknown') as name,
            COALESCE(c.category_id, 0) as category_id,
            p.market_price,
            COALESCE(p.low_price, 0) as low_price,
            COALESCE(p.mid_price, 0) as mid_price
        FROM cards c
        JOIN price_history p ON c.product_id = p.product_id
        WHERE p.date = (SELECT MAX(date) FROM price_history)
          AND p.market_price > 0
        ORDER BY p.market_price DESC
        LIMIT ?
    """

    rows = conn.execute(query, (limit,)).fetchall()
    conn.close()

    cards = []
    for row in rows:
        cards.append({
            "product_id": row["product_id"],
            "name": row["name"][:64],  # Truncate to 64 chars for full names
            "category_id": min(row["category_id"], 65535),  # uint16 max
            "market_price": int(row["market_price"] * 100),  # dollars → cents
            "low_price": int(row["low_price"] * 100),
            "mid_price": int(row["mid_price"] * 100),
        })

    return cards


def push_to_chain(cards: list[dict]):
    """Push card prices to TCGPriceOracle contract on LitVM"""
    try:
        from web3 import Web3
    except ImportError:
        logger.error("web3 not installed. Run: pip install web3")
        sys.exit(1)

    # Load private key
    pk = os.environ.get("LITVM_TESTNET_PK", "")
    if not pk:
        logger.error("LITVM_TESTNET_PK not set in .env")
        sys.exit(1)

    # Connect to RPC
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        logger.error(f"Cannot connect to RPC: {RPC_URL}")
        sys.exit(1)

    logger.info(f"Connected to LitVM (chain {CHAIN_ID})")
    logger.info(f"Latest block: {w3.eth.block_number}")

    # Load ABI
    with open(ABI_PATH) as f:
        abi = json.load(f)

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(CONTRACT_ADDR),
        abi=abi
    )

    # Derive account from private key
    account = w3.eth.account.from_key(pk)
    sender = account.address
    logger.info(f"Sender: {sender}")

    # Check balance
    balance = w3.eth.get_balance(sender)
    balance_eth = w3.from_wei(balance, "ether")
    logger.info(f"Balance: {balance_eth} zkLTC")

    if balance == 0:
        logger.error("Wallet has zero balance — need testnet zkLTC for gas")
        sys.exit(1)

    # Verify contract owner
    try:
        owner = contract.functions.owner().call()
        logger.info(f"Contract owner: {owner}")
        if owner.lower() != sender.lower():
            logger.warning(f"Sender {sender} is NOT the owner {owner} — tx may revert")
    except Exception as e:
        logger.warning(f"Could not check owner: {e}")

    # Read current state
    try:
        tracked = contract.functions.getTrackedCount().call()
        total_updates = contract.functions.totalUpdates().call()
        logger.info(f"Currently tracking {tracked} cards, {total_updates} total updates")
    except Exception as e:
        logger.warning(f"Could not read state: {e}")

    # Build PriceInput tuples
    all_inputs = []
    for card in cards:
        all_inputs.append((
            card["product_id"],
            card["market_price"],
            card["low_price"],
            card["mid_price"],
            card["name"],
            card["category_id"],
        ))

    logger.info(f"Pushing {len(all_inputs)} cards in chunks of {CHUNK_SIZE}...")
    logger.info(f"  Most expensive: {cards[0]['name']} (${cards[0]['market_price']/100:.2f})")
    logger.info(f"  Least expensive: {cards[-1]['name']} (${cards[-1]['market_price']/100:.2f})")

    tx_hashes = []
    nonce = w3.eth.get_transaction_count(sender)
    gas_price = w3.eth.gas_price or w3.to_wei(1, "gwei")

    # Send in chunks
    for i in range(0, len(all_inputs), CHUNK_SIZE):
        chunk = all_inputs[i:i + CHUNK_SIZE]
        chunk_num = i // CHUNK_SIZE + 1
        total_chunks = (len(all_inputs) + CHUNK_SIZE - 1) // CHUNK_SIZE

        logger.info(f"  Batch {chunk_num}/{total_chunks}: {len(chunk)} cards")

        try:
            # Estimate gas for this chunk
            gas_est = contract.functions.batchUpdatePrices(chunk).estimate_gas({"from": sender})
            gas_limit = gas_est + 100_000  # buffer

            tx = contract.functions.batchUpdatePrices(chunk).build_transaction({
                "from": sender,
                "nonce": nonce,
                "chainId": CHAIN_ID,
                "gas": gas_limit,
                "gasPrice": gas_price,
            })
        except Exception as e:
            logger.error(f"  Failed to build batch {chunk_num}: {e}")
            continue

        signed = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex = tx_hash.hex()
        tx_hashes.append(tx_hex)

        logger.info(f"  TX: 0x{tx_hex[:16]}... (gas est: {gas_est})")

        # Wait for confirmation before next batch
        try:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            status = "✅" if receipt["status"] == 1 else "❌"
            logger.info(f"  {status} block {receipt['blockNumber']}, gas {receipt['gasUsed']}")
        except Exception as e:
            logger.warning(f"  Receipt timeout: {e}")

        nonce += 1

    # Final state
    try:
        tracked = contract.functions.getTrackedCount().call()
        total_updates = contract.functions.totalUpdates().call()
        logger.info(f"On-chain state: {tracked} cards tracked, {total_updates} total updates")
    except Exception:
        pass

    logger.info(f"Explorer: https://liteforge.explorer.caldera.xyz/address/{CONTRACT_ADDR}")

    return tx_hashes[-1] if tx_hashes else "no_tx"


def main():
    logger.info(f"═══ LitVM Oracle Update — {datetime.now().strftime('%Y-%m-%d %H:%M')} ═══")

    load_env()

    # Get top cards from SQLite
    cards = get_top_cards(DB_PATH, TOTAL_CARDS)
    if not cards:
        logger.error("No cards found in database")
        sys.exit(1)

    logger.info(f"Loaded {len(cards)} cards from {DB_PATH.name}")

    # Push to chain
    tx_hash = push_to_chain(cards)

    logger.info(f"═══ Update complete — tx: 0x{tx_hash} ═══")


if __name__ == "__main__":
    main()
