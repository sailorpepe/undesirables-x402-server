"""
collection.py — The Undesirables (UNDSR) NFT collection layer.

Makes the collection AGENT-LEGIBLE: live mint status, wallet eligibility, and
an unsigned-transaction builder so any agent (or human tool) can mint without
us ever holding a key. Strategy per docs/research/x402_market_landscape
_2026-07-14.md: be discoverable/actionable to agents BEFORE the funded-buyer
wave arrives — read tools free, mint via prepare-and-sign only.

Contract: Scatter.art Archetype (ERC-721A) minimal proxy on ETHEREUM MAINNET.
Public mint list = auth key 0x0, empty merkle proof, zero affiliate, empty
signature. Scatter docs: contracts mint ONLY via mint(), never bare ETH sends.

SECURITY INVARIANT: this module never signs, never loads a private key, and
never broadcasts. prepare_mint_tx returns calldata the CALLER signs with their
own wallet. Keep it that way.
"""
import json
import os
import time
import urllib.request

CONTRACT = "0xA893648A701C03B14bF2FB767B72b2C55ed5c17A"
CHAIN_ID = 1  # Ethereum mainnet
RPC_URL = os.getenv("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")
SCATTER_PAGE = "https://www.scatter.art/collection/the-undesirables"
PUBLIC_KEY = "0x" + "00" * 32  # public invite list key
_UA = {"Content-Type": "application/json", "User-Agent": "undesirables-oracle/1.0"}

# Minimal Archetype ABI — reads + the payable mint we encode for callers.
_ABI = [
    {"inputs": [], "name": "name", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "totalSupply", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "config", "outputs": [
        {"type": "string", "name": "baseUri"}, {"type": "address", "name": "affiliateSigner"},
        {"type": "uint32", "name": "maxSupply"}, {"type": "uint32", "name": "maxBatchSize"},
        {"type": "uint16", "name": "affiliateFee"}, {"type": "uint16", "name": "affiliateDiscount"},
        {"type": "uint16", "name": "defaultRoyalty"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"type": "bytes32"}], "name": "invites", "outputs": [
        {"type": "uint128", "name": "price"}, {"type": "uint128", "name": "reservePrice"},
        {"type": "uint128", "name": "delta"}, {"type": "uint32", "name": "start"},
        {"type": "uint32", "name": "end"}, {"type": "uint32", "name": "limit"},
        {"type": "uint32", "name": "maxSupply"}, {"type": "uint32", "name": "interval"},
        {"type": "uint32", "name": "unitSize"}, {"type": "address", "name": "tokenAddress"},
        {"type": "bool", "name": "isBlacklist"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"type": "bytes32", "name": "key"}, {"type": "uint256", "name": "quantity"},
                {"type": "bool", "name": "affiliateUsed"}], "name": "computePrice",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"type": "bytes32", "name": "key"}], "name": "listSupply",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"type": "address", "name": "minter"}, {"type": "bytes32", "name": "key"}],
     "name": "minted", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"type": "address", "name": "owner"}], "name": "balanceOf",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"type": "address", "name": "owner"}], "name": "tokensOfOwner",
     "outputs": [{"type": "uint256[]"}], "stateMutability": "view", "type": "function"},
    {"inputs": [
        {"components": [{"type": "bytes32", "name": "key"}, {"type": "bytes32[]", "name": "proof"}],
         "name": "auth", "type": "tuple"},
        {"type": "uint256", "name": "quantity"}, {"type": "address", "name": "affiliate"},
        {"type": "bytes", "name": "signature"}], "name": "mint",
     "outputs": [], "stateMutability": "payable", "type": "function"},
    {"inputs": [
        {"components": [{"type": "bytes32", "name": "key"}, {"type": "bytes32[]", "name": "proof"}],
         "name": "auth", "type": "tuple"},
        {"type": "uint256", "name": "quantity"}, {"type": "address", "name": "to"},
        {"type": "address", "name": "affiliate"}, {"type": "bytes", "name": "signature"}],
     "name": "mintTo", "outputs": [], "stateMutability": "payable", "type": "function"},
]

_w3 = None
_contract = None


def _get_contract():
    """Lazy web3 init so importing this module never requires network."""
    global _w3, _contract
    if _contract is None:
        from web3 import Web3
        _w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 15, "headers": _UA}))
        _contract = _w3.eth.contract(address=Web3.to_checksum_address(CONTRACT), abi=_ABI)
    return _contract


# ── tiny TTL cache: mint state changes rarely; don't hammer the public RPC ──
_cache = {}


def _cached(key, ttl, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


def _zero32():
    return b"\x00" * 32


def mint_status():
    """Live collection + public-mint state. Cached 60s."""
    def _fetch():
        c = _get_contract()
        cfg = c.functions.config().call()
        inv = c.functions.invites(_zero32()).call()
        total = c.functions.totalSupply().call()
        list_minted = c.functions.listSupply(_zero32()).call()
        price_wei = c.functions.computePrice(_zero32(), 1, False).call()
        now = int(time.time())
        # end of 2106 == effectively open-ended
        open_ended = inv[4] > 4_000_000_000
        active = inv[3] <= now and (open_ended or now <= inv[4]) and total < cfg[2]
        return {
            "collection": "The Undesirables",
            "symbol": "UNDSR",
            "chain": "ethereum-mainnet",
            "chain_id": CHAIN_ID,
            "contract": CONTRACT,
            "standard": "ERC-721A (Scatter.art Archetype)",
            "total_minted": total,
            "max_supply": cfg[2],
            "remaining": cfg[2] - total,
            "public_mint": {
                "active": active,
                "price_eth": float(price_wei) / 1e18,
                "price_wei": str(price_wei),
                "currency": "ETH",
                "wallet_limit": inv[5],
                "list_max_supply": inv[6],
                "list_minted": list_minted,
                "list_remaining": max(0, inv[6] - list_minted),
                "starts": inv[3],
                "ends": None if open_ended else inv[4],
                "max_batch_size": cfg[3],
            },
            "royalty_bps": cfg[6],
            "mint_page": SCATTER_PAGE,
            "how_to_mint": (
                "Call GET /api/v1/collection/prepare-mint?quantity=N&to=0x... to get an "
                "unsigned transaction, sign it with your own wallet, and broadcast to "
                "Ethereum mainnet. Or mint via the Scatter page."
            ),
        }
    return _cached("mint_status", 60, _fetch)


def wallet_status(address):
    """Eligibility + holdings for one wallet. Cached 30s per address."""
    from web3 import Web3
    addr = Web3.to_checksum_address(address)

    def _fetch():
        c = _get_contract()
        inv = c.functions.invites(_zero32()).call()
        already = c.functions.minted(addr, _zero32()).call()
        balance = c.functions.balanceOf(addr).call()
        tokens = []
        if 0 < balance <= 50:  # tokensOfOwner is O(supply); skip for whales
            tokens = c.functions.tokensOfOwner(addr).call()
        can_mint = max(0, inv[5] - already)
        return {
            "address": addr,
            "holds": balance,
            "token_ids": tokens,
            "minted_on_public_list": already,
            "public_wallet_limit": inv[5],
            "can_still_mint": can_mint,
        }
    return _cached(f"wallet:{addr}", 30, _fetch)


def prepare_mint_tx(quantity, to=None):
    """Build the UNSIGNED public-mint transaction. The caller signs & sends
    with their own wallet — we never touch keys. Uses mint() when to is the
    sender's own choice (i.e. omitted), mintTo() when minting to another
    address."""
    from web3 import Web3
    c = _get_contract()

    status = mint_status()
    pm = status["public_mint"]
    quantity = int(quantity)
    if quantity < 1:
        raise ValueError("quantity must be >= 1")
    if quantity > pm["max_batch_size"]:
        raise ValueError(f"quantity exceeds max batch size {pm['max_batch_size']}")
    if quantity > pm["wallet_limit"]:
        raise ValueError(f"public list wallet limit is {pm['wallet_limit']}")
    if quantity > pm["list_remaining"]:
        raise ValueError(f"only {pm['list_remaining']} left on the public list")
    if not pm["active"]:
        raise ValueError("public mint is not currently active")

    value_wei = c.functions.computePrice(_zero32(), quantity, False).call()
    auth = (_zero32(), [])  # public list: zero key, empty proof
    zero_addr = "0x0000000000000000000000000000000000000000"

    if to:
        to_addr = Web3.to_checksum_address(to)
        data = c.encode_abi("mintTo", args=[auth, quantity, to_addr, zero_addr, b""])
    else:
        data = c.encode_abi("mint", args=[auth, quantity, zero_addr, b""])

    return {
        "unsigned_transaction": {
            "to": CONTRACT,
            "data": data,
            "value": str(value_wei),
            "chainId": CHAIN_ID,
        },
        "value_eth": float(value_wei) / 1e18,
        "quantity": quantity,
        "mint_recipient": to or "transaction sender",
        "instructions": (
            "Sign this transaction with your own Ethereum mainnet wallet and broadcast "
            "it. Set gas params (maxFeePerGas/maxPriorityFeePerGas) at signing time. "
            "This service never holds keys and cannot mint on your behalf."
        ),
        "warning": (
            f"Sends {float(value_wei)/1e18} ETH to the collection contract. "
            "Verify the contract address independently: " + CONTRACT
        ),
    }
