import requests

_BASE = "https://api.hyperliquid.xyz"

# "" is Hyperliquid's own native perp dex (crypto only). "xyz" is a
# third-party, builder-deployed perp market (Hyperliquid's "HIP-3" feature)
# riding on the same infrastructure but run by a separate deployer, with its
# own isolated margin/positions — depositing to Hyperliquid's main exchange
# does NOT fund this dex; that's a separate allocation.
_DEXS = ["", "xyz"]

PERP_ALIASES: dict[str, str] = {
    # Crypto (native Hyperliquid dex) — every entry verified against the live
    # /info {"type":"meta"} universe.
    "btc": "BTC", "bitcoin": "BTC",
    "eth": "ETH", "ethereum": "ETH", "ether": "ETH",
    "sol": "SOL", "solana": "SOL",
    "avax": "AVAX", "avalanche": "AVAX",
    "bnb": "BNB",
    "xrp": "XRP",
    "doge": "DOGE", "dogecoin": "DOGE",
    "link": "LINK", "chainlink": "LINK",
    "arb": "ARB", "arbitrum": "ARB",
    "op": "OP", "optimism": "OP",
    "apt": "APT", "aptos": "APT",
    "sui": "SUI",
    "atom": "ATOM", "cosmos": "ATOM",
    "near": "NEAR",
    "inj": "INJ", "injective": "INJ",
    "tia": "TIA", "celestia": "TIA",
    "pepe": "kPEPE",  # Hyperliquid lists this as the rebased "kPEPE", not "PEPE"
    "wif": "WIF",
    "dot": "DOT", "polkadot": "DOT",
    "ada": "ADA", "cardano": "ADA",
    "ltc": "LTC", "litecoin": "LTC",
    "matic": "MATIC", "polygon": "MATIC",

    # Tokenized equities (xyz dex) — verified against /info {"perpDexs"}.
    "aapl": "xyz:AAPL", "apple": "xyz:AAPL",
    "amzn": "xyz:AMZN", "amazon": "xyz:AMZN",
    "googl": "xyz:GOOGL", "google": "xyz:GOOGL",
    "meta": "xyz:META",
    "msft": "xyz:MSFT", "microsoft": "xyz:MSFT",
    "nvda": "xyz:NVDA", "nvidia": "xyz:NVDA",
    "tsla": "xyz:TSLA", "tesla": "xyz:TSLA",
    "nflx": "xyz:NFLX", "netflix": "xyz:NFLX",
    "coin": "xyz:COIN", "coinbase": "xyz:COIN",
    "pltr": "xyz:PLTR", "palantir": "xyz:PLTR",
    "amd": "xyz:AMD",
    "intc": "xyz:INTC", "intel": "xyz:INTC",
    "ibm": "xyz:IBM",
    "baba": "xyz:BABA", "alibaba": "xyz:BABA",
    "cost": "xyz:COST", "costco": "xyz:COST",
    "dell": "xyz:DELL",
    "hood": "xyz:HOOD", "robinhood": "xyz:HOOD",
    "gme": "xyz:GME", "gamestop": "xyz:GME",
    "orcl": "xyz:ORCL", "oracle": "xyz:ORCL",
    "qcom": "xyz:QCOM", "qualcomm": "xyz:QCOM",
    "avgo": "xyz:AVGO", "broadcom": "xyz:AVGO",
    "asml": "xyz:ASML",
    "arm": "xyz:ARM",
    "tsm": "xyz:TSM", "tsmc": "xyz:TSM",

    # Commodities (xyz dex)
    "gold": "xyz:GOLD",
    "silver": "xyz:SILVER",
    "copper": "xyz:COPPER",
    "platinum": "xyz:PLATINUM",
    "palladium": "xyz:PALLADIUM",
    "aluminium": "xyz:ALUMINIUM", "aluminum": "xyz:ALUMINIUM",
    "uranium": "xyz:URANIUM",
    "natgas": "xyz:NATGAS",
    "oil": "xyz:CL", "wti": "xyz:CL",
    "brent": "xyz:BRENTOIL", "brentoil": "xyz:BRENTOIL",
    "wheat": "xyz:WHEAT",
    "corn": "xyz:CORN",

    # Forex (xyz dex)
    "eur": "xyz:EUR", "euro": "xyz:EUR",
    "gbp": "xyz:GBP", "pound": "xyz:GBP",
    "jpy": "xyz:JPY", "yen": "xyz:JPY",
    "krw": "xyz:KRW",
    "dxy": "xyz:DXY",

    # Indexes (xyz dex)
    "sp500": "xyz:SP500",
    "nifty": "xyz:NIFTY",
    "vix": "xyz:VIX",
    "jp225": "xyz:JP225", "nikkei": "xyz:JP225",
    "kr200": "xyz:KR200",
    "ibov": "xyz:IBOV", "bovespa": "xyz:IBOV",
}

# Kept in sync with PERP_ALIASES above. This is a curated common subset —
# both dexs list more assets than shown here (232 crypto, 101 on xyz); any
# exact ticker works even if it's not in this list, see resolve_symbol().
PERP_CATEGORIES: list[tuple[str, list[str]]] = [
    ("Crypto", ["BTC", "ETH", "SOL", "AVAX", "BNB", "XRP", "DOGE", "LINK", "ARB", "OP",
                "APT", "SUI", "ATOM", "NEAR", "INJ", "TIA", "kPEPE", "WIF", "DOT", "ADA",
                "LTC", "MATIC"]),
    ("Tokenized equities (xyz dex)", ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA",
                                       "NFLX", "COIN", "PLTR", "AMD", "INTC", "IBM", "BABA",
                                       "COST", "DELL", "HOOD", "GME", "ORCL", "QCOM", "AVGO",
                                       "ASML", "ARM", "TSM"]),
    ("Commodities (xyz dex)", ["GOLD", "SILVER", "COPPER", "PLATINUM", "PALLADIUM", "ALUMINIUM",
                               "URANIUM", "NATGAS", "OIL (WTI)", "BRENT", "WHEAT", "CORN"]),
    ("Forex (xyz dex)", ["EUR", "GBP", "JPY", "KRW", "DXY"]),
    ("Indexes (xyz dex)", ["SP500", "NIFTY", "VIX", "JP225 (Nikkei)", "KR200", "IBOV"]),
]


def list_supported_assets() -> list[tuple[str, list[str]]]:
    return PERP_CATEGORIES


def _dex_for_symbol(symbol: str) -> str:
    return "xyz" if symbol.startswith("xyz:") else ""


def _info(payload: dict) -> dict | None:
    try:
        r = requests.post(f"{_BASE}/info", json=payload, timeout=10)
        return r.json() if r.ok else None
    except Exception:
        return None


def get_all_markets() -> list[dict]:
    out = []
    for dex in _DEXS:
        meta = _info({"type": "meta", "dex": dex})
        mids = _info({"type": "allMids", "dex": dex}) or {}
        if not meta:
            continue
        for asset in meta.get("universe", []):
            name = asset.get("name", "")
            price = float(mids.get(name, 0))
            out.append({
                "symbol": name,
                "price": price,
                "max_leverage": asset.get("maxLeverage", 50),
            })
    return out


def get_mark_price(symbol: str) -> float | None:
    dex = _dex_for_symbol(symbol)
    mids = _info({"type": "allMids", "dex": dex}) or {}
    raw = mids.get(symbol)
    return float(raw) if raw else None


def resolve_symbol(raw_symbol: str) -> str | None:
    """Given a bare ticker (already through PERP_ALIASES or typed directly,
    e.g. 'BTC' or 'AAPL' or 'xyz:AAPL'), find which dex it actually trades on
    and return the fully-qualified symbol Hyperliquid expects. Returns None
    if it doesn't exist on either dex."""
    candidates = [raw_symbol] if raw_symbol.startswith("xyz:") else [raw_symbol, f"xyz:{raw_symbol}"]
    for candidate in candidates:
        if get_mark_price(candidate) is not None:
            return candidate
    return None


def get_withdrawable_balance(wallet_address: str, symbol: str = "") -> float | None:
    """USDC available for margin on the dex `symbol` trades on. Zero here
    doesn't just mean "low balance" — it's also what a wallet that has never
    deposited to that specific dex shows (each dex's margin is isolated;
    depositing to Hyperliquid's main exchange doesn't fund the xyz dex)."""
    dex = _dex_for_symbol(symbol)
    state = _info({"type": "clearinghouseState", "user": wallet_address, "dex": dex})
    if not state:
        return None
    try:
        return float(state.get("withdrawable", "0"))
    except (TypeError, ValueError):
        return None


def preview_order(symbol: str, side: str, size_usd: float, leverage: float) -> dict | None:
    resolved = resolve_symbol(symbol)
    if not resolved:
        return None
    price = get_mark_price(resolved)
    if not price:
        return None
    leverage = max(1.0, leverage)
    qty = size_usd / price
    margin = size_usd / leverage
    mm = 0.005  # 0.5 % maintenance margin fraction
    if side.lower() == "long":
        liq = price * (1 - 1 / leverage + mm)
    else:
        liq = price * (1 + 1 / leverage - mm)
    fee = size_usd * 0.00035  # 0.035 % taker
    return {
        "symbol": resolved,
        "dex": _dex_for_symbol(resolved),
        "side": side,
        "size_usd": size_usd,
        "leverage": leverage,
        "entry_price": price,
        "quantity": qty,
        "margin_required": margin,
        "liquidation_price": max(0.0, liq),
        "fee_usd": fee,
    }


def execute_order(private_key: str, symbol: str, side: str,
                  size_usd: float, leverage: float) -> dict:
    try:
        from hyperliquid.exchange import Exchange
        import eth_account as _ea

        resolved = resolve_symbol(symbol)
        if not resolved:
            return {"status": "error", "error": f"No price data for {symbol}"}

        account = _ea.Account.from_key(private_key)
        exchange = Exchange(account, base_url=_BASE, perp_dexs=_DEXS)

        price = get_mark_price(resolved)
        if not price:
            return {"status": "error", "error": f"No price data for {resolved}"}

        qty = round(size_usd / price, 4)
        is_buy = side.lower() in ("long", "buy")
        lev = max(1, int(leverage))

        exchange.update_leverage(lev, resolved, is_cross=False)
        result = exchange.market_open(resolved, is_buy, qty)

        if result.get("status") == "ok":
            statuses = (result.get("response", {})
                        .get("data", {}).get("statuses", [{}]))
            first = statuses[0] if statuses else {}
            if "error" in first:
                return {"status": "error", "error": first["error"]}
            oid = (first.get("filled", {}).get("oid")
                   or first.get("resting", {}).get("oid")
                   or "placed")
            return {"status": "ok", "order_id": str(oid), "qty": qty, "price": price}
        else:
            return {"status": "error", "error": str(result)}
    except ImportError:
        return {"status": "error",
                "error": "hyperliquid-python-sdk not installed — run: pip install hyperliquid-python-sdk"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_positions(wallet_address: str) -> list[dict]:
    out = []
    for dex in _DEXS:
        state = _info({"type": "clearinghouseState", "user": wallet_address, "dex": dex})
        if not state:
            continue
        for ap in state.get("assetPositions", []):
            p = ap.get("position", {})
            szi = float(p.get("szi", 0))
            if szi == 0:
                continue
            entry = float(p.get("entryPx") or 0)
            pval = float(p.get("positionValue") or 0)
            mark = pval / abs(szi) if abs(szi) > 0 else 0.0
            lev_info = p.get("leverage", {})
            lev = float(lev_info.get("value", 1)) if isinstance(lev_info, dict) else 1.0
            out.append({
                "symbol": p.get("coin", ""),
                "side": "long" if szi > 0 else "short",
                "size": abs(szi),
                "entry_price": entry,
                "mark_price": mark,
                "pnl": float(p.get("unrealizedPnl") or 0),
                "liquidation_price": float(p.get("liquidationPx") or 0),
                "leverage": lev,
            })
    return out


def close_position(private_key: str, symbol: str) -> dict:
    try:
        from hyperliquid.exchange import Exchange
        import eth_account as _ea

        resolved = resolve_symbol(symbol) or symbol
        account = _ea.Account.from_key(private_key)
        exchange = Exchange(account, base_url=_BASE, perp_dexs=_DEXS)
        result = exchange.market_close(resolved)
        if result.get("status") == "ok":
            statuses = (result.get("response", {})
                        .get("data", {}).get("statuses", [{}]))
            first = statuses[0] if statuses else {}
            if "error" in first:
                return {"status": "error", "error": first["error"]}
            return {"status": "ok", "symbol": resolved}
        return {"status": "error", "error": str(result)}
    except ImportError:
        return {"status": "error",
                "error": "hyperliquid-python-sdk not installed — run: pip install hyperliquid-python-sdk"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ══════════════════════════════════════════════════════════════
# Deposit to Hyperliquid's main exchange
#
# Depositing is not an API call — it's a plain Arbitrum transaction against
# Hyperliquid's own bridge contract, using Circle USDC's EIP-2612 permit
# signature (no separate on-chain approve() needed). Every value below was
# verified directly rather than assumed:
#   - Bridge contract address: confirmed via Arbiscan's "Hyperliquid: Deposit
#     Bridge 2" label AND cross-checked by its live USDC balance (~$369M,
#     consistent with an active bridge — a decoy/legacy address nearby only
#     held ~$19).
#   - Deposit mechanism (batchedDepositWithPermit, private-key-signed EIP-712
#     permit, no plain deposit() exists): read directly from Hyperliquid's
#     own contracts repo (Bridge2.sol) on GitHub.
#   - EIP-712 domain (name="USD Coin", version="2"): verified by computing
#     the domain separator hash independently and confirming it matches
#     Arbitrum USDC's on-chain DOMAIN_SEPARATOR() exactly, byte for byte.
#   - Signature struct field order (r, s, v — not the more common v, r, s):
#     read directly from Hyperliquid's Signature.sol.
# ══════════════════════════════════════════════════════════════

_HL_BRIDGE_ADDRESS = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"
_ARB_USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
_ARB_CHAIN_ID = 42161

_USDC_PERMIT_ABI = [
    {"inputs": [{"name": "owner", "type": "address"}], "name": "nonces",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
]

_BRIDGE_ABI = [{
    "inputs": [{
        "components": [
            {"name": "user", "type": "address"},
            {"name": "usd", "type": "uint64"},
            {"name": "deadline", "type": "uint64"},
            {"components": [
                {"name": "r", "type": "uint256"},
                {"name": "s", "type": "uint256"},
                {"name": "v", "type": "uint8"},
            ], "name": "signature", "type": "tuple"},
        ],
        "name": "deposits", "type": "tuple[]",
    }],
    "name": "batchedDepositWithPermit",
    "outputs": [], "stateMutability": "nonpayable", "type": "function",
}]


def get_arbitrum_usdc_balance(wallet_address: str) -> float | None:
    from app.chains.evm import get_web3
    from web3 import Web3
    w3 = get_web3("arbitrum")
    contract = w3.eth.contract(address=Web3.to_checksum_address(_ARB_USDC_ADDRESS), abi=_USDC_PERMIT_ABI)
    try:
        return contract.functions.balanceOf(Web3.to_checksum_address(wallet_address)).call() / 1e6
    except Exception:
        return None


def deposit_to_hyperliquid(private_key: str, amount: float) -> dict:
    import time
    from web3 import Web3
    from eth_account import Account
    from app.chains.evm import get_web3

    try:
        w3 = get_web3("arbitrum")
        account = Account.from_key(private_key)
        usdc = w3.eth.contract(address=Web3.to_checksum_address(_ARB_USDC_ADDRESS), abi=_USDC_PERMIT_ABI)
        bridge = w3.eth.contract(address=Web3.to_checksum_address(_HL_BRIDGE_ADDRESS), abi=_BRIDGE_ABI)

        amount_raw = int(round(amount * 1e6))
        nonce = usdc.functions.nonces(account.address).call()
        deadline = int(time.time()) + 3600

        domain = {
            "name": "USD Coin", "version": "2",
            "chainId": _ARB_CHAIN_ID, "verifyingContract": Web3.to_checksum_address(_ARB_USDC_ADDRESS),
        }
        types = {
            "Permit": [
                {"name": "owner", "type": "address"},
                {"name": "spender", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
            ],
        }
        message = {
            "owner": account.address,
            "spender": Web3.to_checksum_address(_HL_BRIDGE_ADDRESS),
            "value": amount_raw,
            "nonce": nonce,
            "deadline": deadline,
        }
        signed = Account.sign_typed_data(account.key, domain_data=domain, message_types=types, message_data=message)

        deposit_struct = (
            account.address,
            amount_raw,
            deadline,
            (signed.r, signed.s, signed.v),
        )
        tx = bridge.functions.batchedDepositWithPermit([deposit_struct]).build_transaction({
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 200000,
            "gasPrice": w3.eth.gas_price,
            "chainId": _ARB_CHAIN_ID,
        })
        signed_tx = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
        if receipt.status != 1:
            return {"status": "error", "error": "Transaction reverted on-chain — deposit was not accepted."}
        return {"status": "ok", "tx_hash": tx_hash.hex()}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def transfer_to_xyz_dex(private_key: str, amount: float) -> dict:
    """Move USDC from the main perp dex's balance into the xyz dex's isolated
    margin. Self-transfer only — the destination is always the same wallet's
    own address on the other dex, never a different account."""
    try:
        from hyperliquid.exchange import Exchange
        import eth_account as _ea

        account = _ea.Account.from_key(private_key)
        exchange = Exchange(account, base_url=_BASE, perp_dexs=_DEXS)
        result = exchange.send_asset(account.address, "", "xyz", "USDC", amount)
        if result.get("status") == "ok":
            return {"status": "ok"}
        return {"status": "error", "error": str(result)}
    except ImportError:
        return {"status": "error",
                "error": "hyperliquid-python-sdk not installed — run: pip install hyperliquid-python-sdk"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
