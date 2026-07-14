import json, re
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from app.db.session import get_db
from app.db.models import ChatMessage, Wallet, AddressBook
from app.llm.litellm_client import sara_llm
from app.llm.prompts import SARA_SYSTEM_PROMPT
from app.tools.market import coingecko, defillama, yfinance_tool, gas_tracker

router = APIRouter()

# In-memory pending transaction store keyed by session_id
_pending: dict[str, dict] = {}

# Per-session context: last mentioned coin (for follow-up questions like "is that a good time to buy?")
_last_coin: dict[str, str] = {}  # session_id → coin symbol

WALLET_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "send_crypto",
            "description": "Send cryptocurrency from one of the user's wallets to a recipient address",
            "parameters": {
                "type": "object",
                "properties": {
                    "wallet_name": {"type": "string", "description": "Name of the wallet to send from"},
                    "to": {"type": "string", "description": "Recipient wallet address"},
                    "amount": {"type": "number", "description": "Amount to send"},
                    "network": {"type": "string", "description": "Network: ethereum, arbitrum, base, polygon, optimism, solana"},
                },
                "required": ["wallet_name", "to", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_balance",
            "description": "Get the balance of a wallet",
            "parameters": {
                "type": "object",
                "properties": {
                    "wallet_name": {"type": "string", "description": "Name of the wallet"},
                    "network": {"type": "string", "description": "Network for EVM wallets (default: ethereum)"},
                },
                "required": ["wallet_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_wallets",
            "description": "List all wallets the user has added to SARA",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    session_id: str = "default"


def _resolve_wallet(name: str, db: Session) -> Optional[Wallet]:
    return db.query(Wallet).filter(Wallet.name == name).first()

def _match_wallet(text: str, wallets: list) -> Optional[Wallet]:
    """Find a wallet whose name appears in the user's message (case-insensitive)."""
    text_l = text.lower()
    for w in wallets:
        if w.name.lower() in text_l:
            return w
    return None

_TOKEN_TO_NETWORK = {
    "eth": "ethereum", "ether": "ethereum", "ethereum": "ethereum",
    "matic": "polygon", "pol": "polygon", "polygon": "polygon",
    "arb": "arbitrum", "arbitrum": "arbitrum",
    "base": "base",
    "op": "optimism", "optimism": "optimism",
    "sol": "solana", "solana": "solana",
    "bnb": "bsc",
    "avax": "avalanche", "avalanche": "avalanche",
}

_NETWORK_NATIVE_TOKEN = {
    "ethereum": "ETH",
    "arbitrum": "ETH",
    "base": "ETH",
    "optimism": "ETH",
    "polygon": "POL",
    "solana": "SOL",
    "bsc": "BNB",
    "avalanche": "AVAX",
}

_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_SEND_LIKE_RE = re.compile(
    r"\b(send|sent|sen|snd|transfer|transferred|confirm|confirmation)\b|^yes$",
    re.I,
)

from app.tools.names.sara_names import SUFFIXES as _SARA_SUFFIXES
_SARA_SUFFIX_PATTERN = "|".join(re.escape(s) for s in _SARA_SUFFIXES)


def _is_valid_recipient(address: str, network: Optional[str]) -> bool:
    if network == "solana":
        try:
            from solders.pubkey import Pubkey
            Pubkey.from_string(address)
            return True
        except Exception:
            return False
    return bool(_ADDRESS_RE.fullmatch(address or ""))


def _native_send_error(token: str, network: Optional[str]) -> Optional[str]:
    if not network:
        return f"I can only send native tokens right now. I don't know which chain **{token.upper()}** belongs to."
    native = _NETWORK_NATIVE_TOKEN.get(network)
    if not native:
        return f"I don't support sending **{token.upper()}** on {network.capitalize()} yet."
    if token.upper() != native:
        return f"I can only send native **{native}** on {network.capitalize()} right now. ERC-20/SPL token sends are not implemented."
    return None


def _looks_like_transaction_text(msg: str) -> bool:
    return bool(_SEND_LIKE_RE.search(msg.strip()))


def _exception_message(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return text
    if getattr(exc, "args", None):
        return " ".join(str(arg) for arg in exc.args if str(arg).strip()) or repr(exc)
    return repr(exc)


def _wallet_named(msg: str, db: Session) -> Optional[Wallet]:
    msg_l = msg.strip().lower()
    if not msg_l:
        return None
    return db.query(Wallet).filter(Wallet.name.ilike(msg_l)).first()


def _detect_intent(msg: str, db: Session, session_id: str = "default") -> Optional[tuple[str, dict]]:
    """Fast keyword-based intent detection for common wallet queries."""
    m = msg.lower()
    wallets = db.query(Wallet).all()

    # help / capabilities
    if any(p in m for p in ("what can you do", "what do you do", "your capabilities", "what are you capable", "what can sara", "how do you work", "help me understand", "what features", "how to use sara", "how do i use sara")):
        return ("show_help", {})

    # Hyperliquid: fund the xyz dex's isolated margin from the main perp dex —
    # checked before the generic send/transfer regex below since "to xyz" would
    # otherwise be parsed as sending to an address-book nickname called "xyz".
    xyz_fund_match = re.search(r'(?:transfer|move|send|fund)\s+\$?([\d.]+)\s*(?:usdc)?\s+(?:to\s+)?xyz\b', m)
    if xyz_fund_match:
        amount = float(xyz_fund_match.group(1))
        wallet = _match_wallet(msg, wallets) or (wallets[0] if len(wallets) == 1 else None)
        if wallet:
            return ("hyperliquid_fund_xyz", {"wallet_name": wallet.name, "amount": amount})
        elif wallets:
            return ("hyperliquid_fund_xyz_needs_wallet", {"amount": amount, "wallets": [w.name for w in wallets]})

    # Hyperliquid: deposit USDC from Arbitrum into the main exchange (checked
    # before the generic send/transfer regex for the same reason as above).
    hl_deposit_match = re.search(r'deposit\s+\$?([\d.]+)\s*(?:usdc)?\s+(?:to\s+)?hyperliquid\b', m)
    if hl_deposit_match:
        amount = float(hl_deposit_match.group(1))
        wallet = _match_wallet(msg, wallets) or (wallets[0] if len(wallets) == 1 else None)
        if wallet:
            return ("hyperliquid_deposit", {"wallet_name": wallet.name, "amount": amount})
        elif wallets:
            return ("hyperliquid_deposit_needs_wallet", {"amount": amount, "wallets": [w.name for w in wallets]})

    # send / transfer  — parse: (send|transfer) <amount> <token> [from <wallet>] to <address> [on <network>]
    send_match = re.search(
        r'(?:send|transfer)\s+([\d.]+)\s+(\w+)(?:\s+from\s+(\w[\w\s]*?))?\s+to\s+(\S+)(?:\s+on\s+(\w+))?$',
        m
    )
    if send_match:
        amount_str, token, from_hint, to_addr, net_hint = send_match.groups()
        try:
            amount = float(amount_str)
        except ValueError:
            amount = 0
        network = _TOKEN_TO_NETWORK.get(token.lower())
        token_address = None
        token_decimals = None
        if network is None:
            # Not a native chain symbol — check if it's a recognized ERC-20
            # token instead. Network defaults to Ethereum unless the user
            # named one, same convention swaps already use.
            from app.tools.market.paraswap import resolve_token
            resolve_network = (net_hint or "ethereum").lower()
            token_result = resolve_token(token, resolve_network)
            if token_result:
                token_address, token_decimals = token_result
                network = resolve_network
        if network is None:
            return ("send_rejected", {
                "message": f"I don't recognize **{token.upper()}** as a token I can send. "
                           f"Native chain tokens (ETH, POL, SOL, BNB, AVAX...) or these tokens on "
                           f"Ethereum/Arbitrum/Base/Polygon/Optimism: USDC, USDT, WETH, DAI, WBTC, LINK."
            })
        native_error = None if token_address else _native_send_error(token, network)
        if native_error:
            return ("send_rejected", {"message": native_error})
        # Resolve to_addr: nickname → real address, or ENS/SNS → on-chain address
        to_nickname = None
        ab_entry = db.query(AddressBook).filter(AddressBook.nickname == to_addr.lower()).first()
        if ab_entry:
            to_nickname = to_addr
            to_addr = ab_entry.address
            if network == "solana" and ab_entry.chain != "solana":
                return ("send_rejected", {"message": f"**{to_nickname}** is not a Solana directory entry."})
            if network != "solana" and ab_entry.chain == "solana":
                return ("send_rejected", {"message": f"**{to_nickname}** is a Solana directory entry, not an EVM recipient."})
        elif to_addr.lower().endswith(".eth"):
            from app.tools.names.ens import resolve as ens_resolve
            resolved = ens_resolve(to_addr)
            if resolved:
                to_nickname = to_addr
                to_addr = resolved
            else:
                return ("name_not_found", {"name": to_addr})
        elif to_addr.lower().endswith(".sol"):
            from app.tools.names.sns import resolve as sns_resolve
            resolved = sns_resolve(to_addr)
            if resolved:
                to_nickname = to_addr
                to_addr = resolved
            else:
                return ("name_not_found", {"name": to_addr})
        elif to_addr.lower().endswith(_SARA_SUFFIXES):
            from app.tools.names.sara_names import resolve as sara_resolve
            resolved = sara_resolve(to_addr)
            if resolved:
                to_nickname = to_addr
                to_addr = resolved
            else:
                return ("name_not_found", {"name": to_addr})
        elif not _is_valid_recipient(to_addr, network):
            return ("name_not_found", {"name": to_addr})
        if not _is_valid_recipient(to_addr, network):
            return ("send_rejected", {"message": f"Resolved recipient for **{to_nickname or to_addr}** is not valid on {network.capitalize()}."})
        compatible_wallets = [
            w for w in wallets
            if (network == "solana" and w.chain == "solana") or (network != "solana" and w.chain == "evm")
        ]
        # Resolve which wallet to send from
        wallet = None
        if from_hint:
            wallet = _match_wallet(from_hint, compatible_wallets)
        if not wallet:
            wallet = _match_wallet(msg, compatible_wallets)
        if not wallet and len(compatible_wallets) == 1:
            wallet = compatible_wallets[0]
        if amount > 0 and to_addr:
            if wallet:
                return ("send_crypto", {
                    "wallet_name": wallet.name,
                    "to": to_addr,
                    "to_nickname": to_nickname,
                    "amount": amount,
                    "token": token.upper(),
                    "network": network,
                    "token_address": token_address,
                    "token_decimals": token_decimals,
                })
            elif compatible_wallets:
                return ("send_needs_wallet", {
                    "amount": amount,
                    "token": token.upper(),
                    "to": to_addr,
                    "to_nickname": to_nickname,
                    "network": network,
                    "token_address": token_address,
                    "token_decimals": token_decimals,
                    "wallets": [w.name for w in compatible_wallets],
                })
            else:
                return ("send_no_wallets", {})

    # swap / exchange
    swap_match = re.search(
        r'(?:swap|exchange|trade)\s+([\d.]+)\s+(\w+)\s+(?:for|to|into)\s+(\w+)'
        r'(?:\s+from\s+(\w[\w\s]*?))?(?:\s+on\s+(\w+))?$',
        m
    )
    if swap_match:
        amount_str, from_tok, to_tok, from_hint, net_hint = swap_match.groups()
        try:
            amount = float(amount_str)
        except ValueError:
            amount = 0
        wallet = None
        if from_hint:
            wallet = _match_wallet(from_hint, wallets)
        if not wallet:
            wallet = _match_wallet(msg, wallets)
        if not wallet and len(wallets) == 1:
            wallet = wallets[0]
        network = (net_hint or _TOKEN_TO_NETWORK.get(from_tok.lower()) or "ethereum")
        if amount > 0 and from_tok and to_tok:
            if wallet:
                return ("swap_tokens", {
                    "wallet_name": wallet.name,
                    "from_token": from_tok.upper(),
                    "to_token":   to_tok.upper(),
                    "amount":     amount,
                    "network":    network,
                })
            elif wallets:
                return ("swap_needs_wallet", {
                    "from_token": from_tok.upper(),
                    "to_token":   to_tok.upper(),
                    "amount":     amount,
                    "network":    network,
                    "wallets":    [w.name for w in wallets],
                })
            else:
                return ("send_no_wallets", {})

    # cross-chain bridge — explainer, checked before the actual bridge command
    # regex below since it has no amount/chain keywords to collide with.
    if any(p in m for p in ("how to bridge", "how do i bridge", "bridge help", "how does bridging work")):
        return ("bridge_help", {})

    # cross-chain bridge — check status of a submitted bridge tx. Matches on
    # a bare 64-hex-char tx hash alone (0x prefix optional) rather than
    # requiring exact wording like "bridge status" — a 64-char hex string is
    # unambiguous (nothing else in Sara takes that shape, e.g. addresses are
    # 40 hex chars), and requiring exact phrasing here previously caused a
    # real bug: unmatched messages silently fell through to the generic AI,
    # which fabricated a fake "bridge completed" status instead of erroring.
    status_match = re.search(r'\b(?:0x)?([a-fA-F0-9]{64})\b', m)
    if status_match:
        return ("bridge_status", {"tx_hash": status_match.group(1)})

    # cross-chain bridge — "bridge 1 POL from polygon to arbitrum" or
    # "bridge 1 POL from polygon to USDC on arbitrum" (different dest token).
    # Distinct "bridge" keyword — zero overlap with the swap/exchange/trade
    # regex above, which stays untouched and only ever does same-chain swaps.
    bridge_match = re.search(
        r'bridge\s+([\d.]+)\s+(\w+)\s+from\s+(\w+)\s+to\s+(?:(\w+)\s+on\s+)?(\w+)(?:\s+from\s+(\w[\w\s]*?))?$',
        m
    )
    if bridge_match:
        amount_str, src_tok, src_chain, dst_tok, dst_chain, from_hint = bridge_match.groups()
        try:
            amount = float(amount_str)
        except ValueError:
            amount = 0
        wallet = None
        if from_hint:
            wallet = _match_wallet(from_hint, wallets)
        if not wallet:
            wallet = _match_wallet(msg, wallets)
        if not wallet and len(wallets) == 1:
            wallet = wallets[0]
        if amount > 0 and src_chain and dst_chain and src_chain != dst_chain:
            bridge_args = {
                "from_token": src_tok.upper(),
                "to_token": (dst_tok or src_tok).upper(),
                "amount": amount,
                "from_network": src_chain,
                "to_network": dst_chain,
            }
            if wallet:
                return ("bridge_tokens", {**bridge_args, "wallet_name": wallet.name})
            elif wallets:
                return ("bridge_needs_wallet", {**bridge_args, "wallets": [w.name for w in wallets]})
            else:
                return ("send_no_wallets", {})

    # perp: long/short order
    if re.search(r'\b(long|short)\b', m):
        perp_match = re.search(
            r'(long|short)\s+([\w]+)\s+\$?([\d.]+)\s*(?:usd|dollars?)?\s*'
            r'(?:(?:at\s+|with\s+)?([\d.]+)\s*x(?:\s+leverage)?)?',
            m
        )
        if perp_match:
            side, asset_raw, size_str, lev_str = perp_match.groups()
            try:
                size_usd = float(size_str)
            except ValueError:
                size_usd = 0.0
            leverage = float(lev_str) if lev_str else 2.0
            leverage = max(1.0, leverage)
            from app.tools.trading.hyperliquid import PERP_ALIASES
            symbol = PERP_ALIASES.get(asset_raw.lower(), asset_raw.upper())
            wallet = _match_wallet(msg, wallets) or (wallets[0] if len(wallets) == 1 else None)
            if size_usd > 0:
                if wallet:
                    return ("perp_order", {
                        "wallet_name": wallet.name,
                        "symbol": symbol,
                        "side": side,
                        "size_usd": size_usd,
                        "leverage": leverage,
                    })
                elif wallets:
                    return ("perp_needs_wallet", {
                        "symbol": symbol,
                        "side": side,
                        "size_usd": size_usd,
                        "leverage": leverage,
                        "wallets": [w.name for w in wallets],
                    })

    # perp: show positions
    if any(p in m for p in ("my position", "open position", "show position",
                             "list position", "positions", "my trades", "perp position")):
        wallet = _match_wallet(msg, wallets) or (wallets[0] if len(wallets) == 1 else None)
        if wallet:
            return ("get_perp_positions", {"wallet_name": wallet.name})

    # perp: close position
    if "close" in m and any(p in m for p in ("position", "long", "short", "trade")):
        close_match = re.search(r'close\s+(?:my\s+)?(?:the\s+)?(?:long\s+|short\s+)?(\w+)', m)
        if close_match:
            asset_raw = close_match.group(1).strip()
            if asset_raw not in ("my", "the", "position", "trade", "all"):
                from app.tools.trading.hyperliquid import PERP_ALIASES
                symbol = PERP_ALIASES.get(asset_raw.lower(), asset_raw.upper())
                wallet = _match_wallet(msg, wallets) or (wallets[0] if len(wallets) == 1 else None)
                if wallet:
                    return ("close_perp_position", {"wallet_name": wallet.name, "symbol": symbol})

    # perp: what's tradeable — "hyperliquid assets", "what can I trade on hyperliquid"
    if "hyperliquid" in m and any(w in m for w in ("asset", "market", "trade", "available", "support", "list")):
        return ("hyperliquid_assets", {})

    # sara name registration — "register rohas.sara", "buy rohas.sara from test1"
    reg_match = re.search(
        r'(?:register|buy|claim)\s+(?:the\s+name\s+)?([\w-]+(?:' + _SARA_SUFFIX_PATTERN + r'))(?:\s+from\s+(\w[\w\s]*))?',
        m
    )
    if reg_match:
        from app.tools.names import sara_names
        name, from_hint = reg_match.groups()
        error = sara_names.validate_name(name)
        if error:
            return ("register_name_invalid", {"name": name, "message": error})
        name = sara_names.normalize_name(name)
        evm_wallets = [w for w in wallets if w.chain == "evm"]
        if sara_names.is_available(name):
            wallet = _match_wallet(from_hint, evm_wallets) if from_hint else None
            if not wallet:
                wallet = _match_wallet(msg, evm_wallets)
            if not wallet and len(evm_wallets) == 1:
                wallet = evm_wallets[0]
            if wallet:
                return ("register_name", {"wallet_name": wallet.name, "name": name, "price": sara_names.get_price()})
            elif evm_wallets:
                return ("register_needs_wallet", {"name": name, "price": sara_names.get_price(), "wallets": [w.name for w in evm_wallets]})
            else:
                return ("send_no_wallets", {})
        else:
            return ("register_name_taken", {"name": name})

    # sara name registration — guided flow, no name given yet
    if any(p in m for p in ("buy a name", "buy a bname", "buy a .sara", "register a name",
                             "register a bname", "register a .sara", "get a .sara name",
                             "get a name", "get a bname")):
        return ("register_ask_name", {})

    # list wallets
    if any(p in m for p in ("list wallet", "my wallet", "show wallet", "list my wallet")):
        return ("list_wallets", {})

    # balance
    if "balance" in m or ("how much" in m and any(w.name.lower() in m for w in wallets)):
        matched = _match_wallet(msg, wallets)
        if matched:
            network = None
            for net in ("ethereum", "arbitrum", "base", "polygon", "optimism", "bsc", "avalanche", "solana"):
                if net in m:
                    network = net
                    break
            return ("get_balance", {"wallet_name": matched.name, "network": network})
        if wallets and len(wallets) == 1:
            return ("get_balance", {"wallet_name": wallets[0].name, "network": None})

    # prediction markets (Polymarket) — check BEFORE any price/trending/commodity
    # matching, since words like "polymarket" can substring-match unrelated
    # token symbols (e.g. "pol" inside "polymarket") and "will X hit Y" should
    # always route here, not to a commodity/crypto price lookup.
    if any(p in m for p in ("polymarket", "prediction market", "odds", "bet", "chance", "probability",
                             "likelihood")) or re.search(r'\bwill\b.{3,}', m):
        query = msg.strip()
        for prefix in ("what are", "what's", "polymarket", "odds on", "odds for", "chance of",
                        "probability of", "will", "prediction market"):
            query = re.sub(rf'^\s*{re.escape(prefix)}\s*', '', query, flags=re.I).strip()
        query = re.sub(r'\?$', '', query).strip()
        return ("get_prediction_markets", {"query": query or msg})

    # market: forex / fiat currency price (e.g. "inr price", "aud price")
    FIAT_TICKERS = {
        "inr": "INR=X", "aud": "AUD=X", "gbp": "GBP=X", "cad": "CAD=X",
        "chf": "CHF=X", "cny": "CNY=X", "jpy": "JPY=X", "krw": "KRW=X",
        "sgd": "SGD=X", "hkd": "HKD=X", "mxn": "MXN=X", "brl": "BRL=X",
        "eur": "EURUSD=X", "rub": "RUB=X",
    }
    for code, ticker in FIAT_TICKERS.items():
        # bare "inr price" or "inr" alone
        if m.strip() in (code, code + " price", "price of " + code):
            return ("get_forex_rate", {"pair": ticker})

    # market: crypto price — also detect "X price in Y" currency modifier
    CRYPTO_KEYWORDS = ("price", "how much is", "what is", "what's", "whats", "cost", "worth", "at", "doing")
    KNOWN_SYMBOLS = set(coingecko.SYMBOL_TO_ID.keys()) | {"BITCOIN", "ETHEREUM", "SOLANA"}
    # Check for "in <currency>" modifier first
    vs_currency = "usd"
    vs_match = re.search(r'\bin\s+([a-z]{2,4})\b', m)
    if vs_match:
        code = vs_match.group(1)
        if code in FIAT_TICKERS or code in ("usd", "eur", "gbp", "inr", "aud", "cad", "chf", "jpy"):
            vs_currency = code
    if any(k in m for k in CRYPTO_KEYWORDS):
        for sym in KNOWN_SYMBOLS:
            if sym.lower() in m:
                return ("get_crypto_price", {"coin": sym, "vs_currency": vs_currency})

    # market: gas
    if "gas" in m and ("fee" in m or "price" in m or "check" in m or "cost" in m or m.strip() in ("gas", "check gas", "gas fees")):
        return ("get_gas_prices", {})

    # market: global / market cap
    if ("market cap" in m or "total market" in m or "crypto market" in m or "btc dominance" in m):
        return ("get_global_market", {})

    # market: trending
    if "trend" in m or "top coin" in m or "hot coin" in m or "gainers" in m:
        return ("get_trending_coins", {})

    # market: yields (check before TVL so "DeFi yields" doesn't match TVL)
    CHAINS = ("ethereum", "arbitrum", "base", "polygon", "optimism", "solana", "avalanche", "bsc")
    if "yield" in m or "apy" in m or ("farming" in m and "defi" in m):
        chain = None
        for c in CHAINS:
            if c in m:
                chain = c
                break
        return ("get_yields", {"chain": chain})

    # market: DeFi TVL
    PROTOCOLS = ("aave", "uniswap", "curve", "lido", "maker", "compound", "sushi", "dydx", "gmx", "hyperliquid")
    if "tvl" in m or ("defi" in m and "lock" in m) or "value locked" in m:
        for chain in CHAINS:
            if chain in m:
                return ("get_defi_tvl", {"target": chain, "kind": "chain"})
        for proto in PROTOCOLS:
            if proto in m:
                return ("get_defi_tvl", {"target": proto, "kind": "protocol"})
        return ("get_defi_tvl", {"target": None, "kind": "total"})

    # market: stock
    COMPANY_NAMES = {
        "apple": "AAPL", "google": "GOOGL", "alphabet": "GOOGL",
        "microsoft": "MSFT", "amazon": "AMZN", "meta": "META",
        "nvidia": "NVDA", "tesla": "TSLA", "netflix": "NFLX",
        "berkshire": "BRK-B", "jpmorgan": "JPM", "visa": "V",
        "mastercard": "MA", "samsung": "005930.KS", "tsmc": "TSM",
        "coinbase": "COIN", "microstrategy": "MSTR", "palantir": "PLTR",
        "amd": "AMD", "intel": "INTC", "qualcomm": "QCOM",
    }
    STOCK_WORDS = ("stock", "share", "equity", "nasdaq", "nyse", "ticker")
    if any(w in m for w in STOCK_WORDS) or any(co in m for co in COMPANY_NAMES):
        # try company name first
        for co, ticker in COMPANY_NAMES.items():
            if co in m:
                return ("get_stock_price", {"ticker": ticker})
        # try to extract a ticker (1-5 uppercase letters)
        tickers = re.findall(r'\b([A-Z]{2,5})\b', msg)
        if tickers:
            return ("get_stock_price", {"ticker": tickers[0]})

    # market: commodity — require price-context word to avoid catching "will gold hit X"
    COMMODITY_MAP = {
        "gold": "GC=F", "silver": "SI=F", "oil": "CL=F", "crude": "CL=F",
        "brent": "BZ=F", "gas": "NG=F", "natural gas": "NG=F",
        "wheat": "ZW=F", "corn": "ZC=F", "copper": "HG=F", "platinum": "PL=F",
    }
    PRICE_CONTEXT = ("price", "how much", "what's", "whats", "what is", "cost", "worth", "at", "trading")
    for word, ticker in COMMODITY_MAP.items():
        if word in m and any(ctx in m for ctx in PRICE_CONTEXT):
            return ("get_commodity_price", {"ticker": ticker})
    # Also match bare commodity name (e.g. just "gold" or "silver")
    for word, ticker in COMMODITY_MAP.items():
        if m.strip() == word or m.strip() == word + " price":
            return ("get_commodity_price", {"ticker": ticker})

    # market: forex
    FOREX_PAIRS = ("eurusd", "gbpusd", "usdjpy", "usdcad", "audusd", "usdchf", "eurjpy")
    if "forex" in m or "exchange rate" in m or "currency" in m:
        for pair in FOREX_PAIRS:
            if pair in m or pair[:3] in m:
                return ("get_forex_rate", {"pair": pair.upper() + "=X"})

    # portfolio
    if "portfolio" in m or ("my" in m and "holding" in m) or ("my" in m and "asset" in m):
        return ("get_portfolio", {})

    # news & sentiment
    NEWS_TRIGGERS = ("news", "sentiment", "what are people saying", "bullish", "bearish",
                     "headlines", "what's happening with", "hype", "narrative",
                     "good time to buy", "good time to sell", "should i buy", "should i sell",
                     "outlook", "analysis")
    if any(t in m for t in NEWS_TRIGGERS):
        coin = None
        from app.tools.market.coingecko import SYMBOL_TO_ID
        for sym in SYMBOL_TO_ID:
            if sym.lower() in m:
                coin = sym
                break
        if not coin:
            for name, sym in [("bitcoin","BTC"),("ethereum","ETH"),("solana","SOL"),
                               ("dogecoin","DOGE"),("ripple","XRP")]:
                if name in m:
                    coin = sym
                    break
        # fall back to last known coin in context
        if not coin:
            coin = _last_coin.get(session_id)
        if coin:
            return ("get_news_sentiment", {"coin": coin})

    return None


def _handle_tool_call(tool_name: str, args: dict, db: Session) -> str:
    if tool_name == "send_no_wallets":
        return "You don't have any wallets yet. Use the **+** button to create one first."

    if tool_name == "send_rejected":
        return args["message"]

    if tool_name == "send_needs_wallet":
        names = ", ".join(f"**{n}**" for n in args["wallets"])
        return (f"Which wallet should I send **{args['amount']} {args['token']}** from?\n"
                f"Your wallets: {names}\n"
                f"Reply with e.g. \"send {args['amount']} {args['token']} from {args['wallets'][0]} to {args['to']}\"")

    if tool_name == "swap_needs_wallet":
        names = ", ".join(f"**{n}**" for n in args["wallets"])
        return (f"Which wallet should I use to swap **{args['amount']} {args['from_token']} → {args['to_token']}**?\n"
                f"Your wallets: {names}\n"
                f"Just reply with the wallet name, e.g. \"{args['wallets'][0]}\".")

    if tool_name == "swap_tokens":
        return f"__PENDING_SWAP__{json.dumps(args)}"

    if tool_name == "bridge_needs_wallet":
        names = ", ".join(f"**{n}**" for n in args["wallets"])
        return (f"Which wallet should I use to bridge **{args['amount']} {args['from_token']} "
                f"({args['from_network'].capitalize()}) → {args['to_token']} ({args['to_network'].capitalize()})**?\n"
                f"Your wallets: {names}\n"
                f"Just reply with the wallet name, e.g. \"{args['wallets'][0]}\".")

    if tool_name == "bridge_tokens":
        return f"__PENDING_BRIDGE__{json.dumps(args)}"

    if tool_name == "bridge_help":
        from app.tools.trading import lifi
        _chain_display = {"bsc": "BSC"}
        chains = ", ".join(_chain_display.get(n, n.capitalize()) for n in lifi.CHAIN_IDS)
        return (
            "**Bridging moves funds between chains** (e.g. Polygon → Arbitrum), via a third-party "
            "bridge/swap aggregator (LI.FI). Supported chains: " + chains + ". EVM only — no Solana route.\n\n"
            "There are two ways to phrase it:\n\n"
            "**1. Same token, different chain** — just move an asset across:\n"
            "`bridge 1 USDC from polygon to arbitrum`\n\n"
            "**2. Different token on arrival** — swap during the bridge:\n"
            "`bridge 1 POL from polygon to USDC on arbitrum`\n\n"
            "Either way, you'll get a preview (amount, bridge used, estimated time) before anything moves — "
            "type **CONFIRM** to go ahead. Cross-chain transfers take a few minutes, not instant like a "
            "same-chain swap."
        )

    if tool_name == "bridge_status":
        from app.tools.trading import lifi
        tx_hash = args["tx_hash"]
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        status = lifi.get_status(tx_hash)
        if not status:
            return "Couldn't reach LI.FI's status API — try again shortly."
        state = status.get("status", "UNKNOWN")
        sending = status.get("sending", {})
        receiving = status.get("receiving", {})
        lines = [f"Bridge status: **{state}**"]
        if sending.get("txLink"):
            lines.append(f"Source tx: {sending['txLink']}")
        if state == "FAILED":
            lines.append("This bridge did not complete — funds did not leave the source chain "
                          "(only the network gas fee was spent). No further funds are at risk.")
        elif state == "DONE":
            if receiving.get("txLink"):
                lines.append(f"Destination tx: {receiving['txLink']}")
            lines.append("Funds have arrived.")
        else:
            lines.append("Still in progress — check again in a bit.")
        return "\n".join(lines)

    if tool_name == "perp_order":
        return f"__PENDING_PERP__{json.dumps(args)}"

    if tool_name == "perp_needs_wallet":
        names = ", ".join(f"**{n}**" for n in args["wallets"])
        return (f"Which wallet should I use to open a **{args['side'].upper()} {args['symbol']}** "
                f"position for **${args['size_usd']:,.0f}** at **{args['leverage']}x**?\n"
                f"Your wallets: {names}\n"
                f"Just reply with the wallet name, e.g. \"{args['wallets'][0]}\".")

    if tool_name == "hyperliquid_fund_xyz":
        return f"__PENDING_XYZ_FUND__{json.dumps(args)}"

    if tool_name == "hyperliquid_fund_xyz_needs_wallet":
        names = ", ".join(f"**{n}**" for n in args["wallets"])
        return (f"Which wallet should transfer **${args['amount']:,.2f} USDC** to the xyz dex?\n"
                f"Your wallets: {names}\n"
                f"Just reply with the wallet name, e.g. \"{args['wallets'][0]}\".")

    if tool_name == "hyperliquid_deposit":
        return f"__PENDING_HL_DEPOSIT__{json.dumps(args)}"

    if tool_name == "hyperliquid_deposit_needs_wallet":
        names = ", ".join(f"**{n}**" for n in args["wallets"])
        return (f"Which wallet should deposit **${args['amount']:,.2f} USDC** to Hyperliquid's main exchange "
                f"(from Arbitrum)?\nYour wallets: {names}\n"
                f"Just reply with the wallet name, e.g. \"{args['wallets'][0]}\".")

    if tool_name == "register_name":
        return f"__PENDING_REGISTER__{json.dumps(args)}"

    if tool_name == "register_needs_wallet":
        names = ", ".join(f"**{n}**" for n in args["wallets"])
        return (f"**{args['name']}** is available for **{args['price']} POL**. Which wallet should pay?\n"
                f"Your wallets: {names}\n"
                f"Reply with e.g. \"register {args['name']} from {args['wallets'][0]}\"")

    if tool_name == "register_name_taken":
        return f"**{args['name']}** is already registered to someone else. Try a different name."

    if tool_name == "register_name_invalid":
        return args["message"]

    if tool_name == "register_ask_name":
        return "Sure — which bName would you like? (e.g. `rohas.sara`)"

    if tool_name == "get_perp_positions":
        w = _resolve_wallet(args["wallet_name"], db)
        if not w:
            return f"Wallet '{args['wallet_name']}' not found."
        from app.tools.trading.hyperliquid import get_positions
        positions = get_positions(w.address)
        if not positions:
            return f"No open perpetual positions for **{w.name}** on Hyperliquid."
        lines = []
        for p in positions:
            side_sym = "▲" if p["side"] == "long" else "▼"
            pnl_sign = "+" if p["pnl"] >= 0 else ""
            display_symbol = p["symbol"].removeprefix("xyz:")
            dex_note = " (xyz dex)" if p["symbol"].startswith("xyz:") else ""
            lines.append(
                f"{side_sym} **{display_symbol}**{dex_note} {p['side'].upper()}  ·  "
                f"Size: {p['size']:.4f}  ·  Entry: ${p['entry_price']:,.2f}  ·  "
                f"PnL: {pnl_sign}${p['pnl']:.2f}  ·  Liq: ${p['liquidation_price']:,.2f}"
            )
        return "Open positions:\n" + "\n".join(lines)

    if tool_name == "close_perp_position":
        w = _resolve_wallet(args["wallet_name"], db)
        if not w:
            return f"Wallet '{args['wallet_name']}' not found."
        symbol = args["symbol"]
        from app.tools.trading.hyperliquid import get_positions, get_mark_price
        positions = get_positions(w.address)
        pos = (next((p for p in positions if p["symbol"] == symbol), None)
               or next((p for p in positions if p["symbol"] == f"xyz:{symbol}"), None))
        if not pos:
            return f"No open {symbol} position found for **{w.name}**."
        symbol = pos["symbol"]  # use the exact resolved form (may have gained an xyz: prefix)
        mark = get_mark_price(symbol) or pos["entry_price"]
        pnl_est = (mark - pos["entry_price"]) * pos["size"] * (1 if pos["side"] == "long" else -1)
        return f"__PENDING_CLOSE_PERP__{json.dumps({**args, 'pnl_est': pnl_est, 'mark': mark, 'wallet_encrypted_key': w.encrypted_key, 'wallet_id': w.id})}"

    if tool_name == "hyperliquid_assets":
        from app.tools.trading.hyperliquid import list_supported_assets
        lines = ["**Tradeable on Hyperliquid:**"]
        for category, symbols in list_supported_assets():
            lines.append(f"\n**{category}**: {', '.join(symbols)}")
        lines.append(
            "\nCrypto is Hyperliquid's own native market. Equities, commodities, forex, and indexes "
            "all trade on **\"xyz\"** — a separate, third-party builder-deployed market riding on "
            "Hyperliquid's infrastructure, with its own isolated margin (depositing USDC to Hyperliquid's "
            "main exchange doesn't fund it — that needs its own deposit)."
        )
        lines.append(
            "\nThis is a common/friendly-named subset — both dexs list more (232 crypto, 101 on xyz). "
            "Any exact ticker works too, just type it as-is (e.g. `long PENGU $50`)."
        )
        lines.append("\nTry: `long BTC $100` or `short AAPL $50 at 3x`")
        return "\n".join(lines)

    if tool_name == "show_help":
        import os as _os
        from app.chains.evm import _RPC as _evm_networks
        from app.tools.wallet import lock as _lock_state
        from app.core.config import settings as _settings

        _chain_display = {"bsc": "BSC"}
        chain_list = ", ".join(_chain_display.get(n, n.capitalize()) for n in _evm_networks) + ", Solana"
        wallet_count = db.query(Wallet).count()

        provider = _os.environ.get("LLM_PROVIDER", _settings.LLM_PROVIDER)
        model = _os.environ.get("LLM_MODEL", _settings.LLM_MODEL)
        ai_status = f"{provider} · {model}" if _os.getenv("OPENROUTER_API_KEY") else f"{provider} · {model} (no API key set — add one in Settings)"

        def _flag(key: str) -> str:
            return "✅ configured" if _os.getenv(key) else "— not set"

        bname_ready = bool(_os.getenv("SARA_NAME_REGISTRAR_ADDRESS") and _os.getenv("SARA_NAME_SERVICE_URL"))
        lock_status = "🔓 unlocked" if _lock_state.is_unlocked() else "🔒 locked"

        return (
            "**Here's everything SARA can do:**\n\n"
            "**Wallets**\n"
            f"• Create & import wallets — EVM ({chain_list.rsplit(', Solana', 1)[0]}) and Solana\n"
            "• Check balance on any supported network\n"
            "• Send crypto — say \"send 0.1 ETH from Main to 0x...\" and type CONFIRM\n"
            "• Address book — save nicknames, send to them by name\n\n"
            "**Trading**\n"
            "• Swap tokens on EVM (via Paraswap) — \"swap 1 POL for USDC from test1\"\n"
            "• Swap tokens on Solana (via Jupiter) — \"swap 0.5 SOL for USDC\"\n"
            "• Hyperliquid perps — \"long ETH $500 2x\" / \"short BTC $1000 3x\" / \"show my positions\" / \"close ETH position\"\n\n"
            "**bNames** — a human-readable name for your wallet\n"
            "• \"buy a bname\" or \"register rohas.sara\" — pay a small fee, get a name like `rohas.sara` linked to your wallet\n"
            "• Send to a bName directly, same as `alice.eth` or `bob.sol`\n\n"
            "**Market Data** *(live via Yahoo Finance + CoinGecko)*\n"
            "• Crypto, stock, commodity & forex prices, gas fees, DeFi TVL/yields, trending coins, global market cap\n\n"
            "**Intelligence**\n"
            "• News & sentiment, Polymarket prediction markets, ENS/SNS/bName resolution\n\n"
            "**Voice mode** — click the mic next to the chat box to speak instead of type (English only for now). "
            "For your safety, CONFIRM must always be typed, never spoken.\n\n"
            "**Security**\n"
            "• Sara locks like a normal wallet — your passphrase unlocks it, and it auto-locks after 1 hour of inactivity\n"
            "• Only money-moving actions (send, swap, perps, bName registration) require unlocking — price checks and general chat work while locked\n\n"
            "---\n"
            "**Your current setup**\n"
            f"• Wallet lock: {lock_status}\n"
            f"• Wallets added: {wallet_count}\n"
            f"• AI model: {ai_status}\n"
            f"• CoinGecko API key: {_flag('COINGECKO_API_KEY')}\n"
            f"• Alchemy API key (ERC-20 balances): {_flag('ALCHEMY_API_KEY')}\n"
            f"• Helius RPC (Solana): {_flag('HELIUS_RPC')}\n"
            f"• bName registration: {'✅ ready' if bname_ready else '— not set up yet (needs a deployed registrar service, see registrar-service/DEPLOYMENT.md)'}\n"
            f"• EVM networks available: {chain_list.rsplit(', Solana', 1)[0]}"
        )

    if tool_name == "list_wallets":
        wallets = db.query(Wallet).all()
        if not wallets:
            return "No wallets added yet. Ask me to create one!"
        from app.chains import evm as evm_chain, solana as sol_chain
        from concurrent.futures import ThreadPoolExecutor, as_completed
        EVM_NETWORKS = ["ethereum", "polygon", "arbitrum", "base", "optimism"]
        blocks = []
        for w in wallets:
            card = []
            card.append(f"**{w.name}**  ·  {w.chain.upper()}")
            if w.chain == "evm":
                def _fetch(net, addr=w.address):
                    try:
                        return evm_chain.get_balance(addr, net)
                    except Exception:
                        return None
                balances = []
                with ThreadPoolExecutor(max_workers=5) as ex:
                    futures = {ex.submit(_fetch, net): net for net in EVM_NETWORKS}
                    for fut in as_completed(futures, timeout=8):
                        result = fut.result()
                        if result and result["balance"] > 0.000001:
                            balances.append(result)
                balances.sort(key=lambda r: r["balance"], reverse=True)
                for b in balances:
                    card.append(f"{b['balance']:.6f} **{b['unit']}**  ·  {b['network'].capitalize()}")

                # ERC-20 tokens via Alchemy — checked on every network
                # regardless of native balance there. A wallet can hold a
                # bridged/received token on a chain it has zero native gas
                # on (e.g. right after a cross-chain bridge, before ever
                # funding gas there), so gating this on native balance made
                # real token balances invisible.
                from app.tools.wallet.tokens import get_erc20_balances
                def _fetch_tokens(net, addr=w.address):
                    try:
                        return get_erc20_balances(addr, net)
                    except Exception:
                        return []
                token_lines = []
                with ThreadPoolExecutor(max_workers=5) as ex:
                    futures = {ex.submit(_fetch_tokens, net): net for net in EVM_NETWORKS}
                    for fut in as_completed(futures, timeout=8):
                        net = futures[fut]
                        for tok in fut.result():
                            token_lines.append(f"{tok['balance']:.6f} **{tok['symbol']}**  ·  {net.capitalize()}")
                card.extend(token_lines)

                if not balances and not token_lines:
                    card.append("No funds detected")
            else:
                try:
                    b = sol_chain.get_balance(w.address)
                    card.append(f"{b['balance']:.6f} **SOL**")
                except Exception:
                    card.append("Balance unavailable")
            card.append(f"`{w.address}`")
            blocks.append("\n".join(card))
        result = "\n---\n".join(blocks)
        import os
        has_evm = any(w.chain == "evm" for w in wallets)
        if has_evm and not os.getenv("ALCHEMY_API_KEY", "").strip():
            result += "\n\n_Note: token balances (USDC, USDT, etc.) won't show until you add an Alchemy API key in Settings — only native balances (ETH, POL, etc.) are shown without it._"
        return result

    if tool_name == "get_balance":
        w = _resolve_wallet(args["wallet_name"], db)
        if not w:
            return f"Wallet '{args['wallet_name']}' not found."
        from app.tools.wallet.balance import get_wallet_balance
        try:
            result = get_wallet_balance(w, args.get("network"))
            return f"{w.name}: **{result['balance']:.6f} {result['unit']}** on {result['network']}"
        except Exception as e:
            return f"Could not fetch balance: {e}"

    if tool_name == "send_crypto":
        return f"__PENDING_SEND__{json.dumps(args)}"

    if tool_name == "get_crypto_price":
        vs = args.get("vs_currency", "usd").lower()
        d = coingecko.get_price(args["coin"], vs=vs)
        if not d:
            return f"No price data for {args['coin']}."
        currency_sym = {"usd":"$","eur":"€","gbp":"£","inr":"₹","jpy":"¥",
                        "aud":"A$","cad":"C$","chf":"Fr","cny":"¥","krw":"₩"}.get(vs, vs.upper()+" ")

        def _fmt_price(p: float) -> str:
            cs = currency_sym
            if p == 0:
                return f"{cs}0.00"
            if p >= 1:
                return f"{cs}{p:,.2f}"
            if p >= 0.01:
                return f"{cs}{p:.4f}"
            import math
            decimals = -math.floor(math.log10(abs(p))) + 2
            return f"{cs}{p:.{decimals}f}"

        def _sgn(v) -> str:
            return "+" if (v or 0) >= 0 else ""

        rank = f"  ·  #{d['market_cap_rank']}" if d.get("market_cap_rank") else ""
        vs_label = f" (in {vs.upper()})" if vs != "usd" else ""
        parts = [f"**{d['symbol']}**{vs_label} — **{_fmt_price(d['price'])}**{rank}"]

        # Change line: 24h, 7d, 30d
        chg_parts = [f"24h  {_sgn(d['change_24h'])}{d['change_24h']:.2f}%"]
        if d.get("change_7d") is not None:
            chg_parts.append(f"7d  {_sgn(d['change_7d'])}{d['change_7d']:.2f}%")
        if d.get("change_30d") is not None:
            chg_parts.append(f"30d  {_sgn(d['change_30d'])}{d['change_30d']:.2f}%")
        parts.append("   ".join(chg_parts))

        # 24h range
        if d.get("high_24h") and d.get("low_24h"):
            parts.append(f"Range  {_fmt_price(d['low_24h'])} – {_fmt_price(d['high_24h'])}")

        # Market cap + volume (always in USD for context)
        def _fmt_large(v: float) -> str:
            s = currency_sym
            if v >= 1e12: return f"{s}{v/1e12:.2f}T"
            if v >= 1e9:  return f"{s}{v/1e9:.2f}B"
            if v >= 1e6:  return f"{s}{v/1e6:.1f}M"
            return f"{s}{v:,.0f}"

        stats = []
        if d.get("market_cap"):
            stats.append(f"Market cap: {_fmt_large(d['market_cap'])}")
        if d.get("volume_24h"):
            stats.append(f"Volume: {_fmt_large(d['volume_24h'])}")
        if stats:
            parts.append("  ·  ".join(stats))

        # ATH
        if d.get("ath") and d.get("ath_change_pct") is not None:
            parts.append(f"ATH: {_fmt_price(d['ath'])}  ({d['ath_change_pct']:.1f}% from ATH)")

        return "\n".join(parts)

    if tool_name == "get_trending_coins":
        coins = coingecko.get_trending()
        if not coins:
            return "Could not fetch trending data."
        lines = [f"• {c['name']} ({c['symbol']}) #{c.get('rank','?')}" for c in coins]
        return "Trending now:\n" + "\n".join(lines)

    if tool_name == "get_global_market":
        d = coingecko.get_global()
        if not d:
            return "Global market data unavailable."
        mc = d["total_market_cap_usd"] / 1e12
        vol = d["total_volume_24h"] / 1e9
        sign = "+" if d["market_cap_change_24h"] >= 0 else ""
        return (f"Crypto market: **${mc:.2f}T** total cap  ({sign}{d['market_cap_change_24h']:.2f}% 24h)\n"
                f"BTC dominance: **{d['btc_dominance']:.1f}%**  •  Volume: ${vol:.0f}B")

    if tool_name == "get_gas_prices":
        d = gas_tracker.get_gas_prices()
        if "error" in d:
            return f"Gas data unavailable: {d['error']}"
        return (f"Ethereum gas — Slow: **{d['slow_gwei']} gwei** (${d['slow_usd']})  "
                f"Standard: **{d['standard_gwei']} gwei** (${d['standard_usd']})  "
                f"Fast: **{d['fast_gwei']} gwei** (${d['fast_usd']})")

    if tool_name == "get_defi_tvl":
        kind = args.get("kind", "total")
        target = args.get("target")
        if kind == "chain" and target:
            d = defillama.get_chain_tvl(target)
            if not d:
                return f"No TVL data for chain {target}."
            return f"**{d['chain'].capitalize()}** TVL: **${d['tvl_usd']/1e9:.2f}B**"
        elif kind == "protocol" and target:
            d = defillama.get_protocol_tvl(target)
            if not d:
                return f"No TVL data for {target}."
            tvl = f"${d['tvl']/1e9:.2f}B" if d.get("tvl") else "N/A"
            chg1 = f"{d['change_1d']:.1f}%" if d.get("change_1d") else "N/A"
            return f"**{d['name']}** TVL: **{tvl}**  (1d: {chg1})  [{d.get('category','')}]"
        else:
            d = defillama.get_total_tvl()
            if not d:
                return "DeFi TVL data unavailable."
            return f"Total DeFi TVL: **${d['tvl_usd']/1e9:.2f}B**"

    if tool_name == "get_yields":
        pools = defillama.get_yields(args.get("chain"), limit=5)
        if not pools:
            return "No yield data found."
        lines = [f"• {p['project']} {p['pool']} ({p['chain']}): **{p['apy']:.1f}% APY**  TVL: ${(p['tvl_usd'] or 0)/1e6:.1f}M" for p in pools]
        return "Top yields:\n" + "\n".join(lines)

    if tool_name == "get_stock_price":
        d = yfinance_tool.get_stock(args["ticker"])
        if not d:
            return f"No data for {args['ticker']}."
        sign = "+" if d["change_pct"] >= 0 else ""
        return f"**{d.get('name', d['ticker'])}** ({d['ticker']}) — **{d['currency']} {d['price']:,.2f}**  ({sign}{d['change_pct']:.2f}%)"

    if tool_name == "get_commodity_price":
        d = yfinance_tool.get_commodity(args["ticker"])
        if not d:
            return f"No data for {args['ticker']}."
        sign = "+" if d["change_pct"] >= 0 else ""
        return f"**{d['name']}** — **{d['currency']} {d['price']:,.2f}**  ({sign}{d['change_pct']:.2f}%)"

    if tool_name == "get_forex_rate":
        d = yfinance_tool.get_forex(args["pair"])
        if not d:
            return f"No data for {args['pair']}."
        sign = "+" if d["change_pct"] >= 0 else ""
        return f"**{d['pair']}** — **{d['rate']:.4f}**  ({sign}{d['change_pct']:.2f}%)"

    if tool_name == "get_portfolio":
        from app.routers.portfolio import get_portfolio as _portfolio
        data = _portfolio(db=db)
        if not data["assets"]:
            return "No wallets with balances found. Add a wallet with some funds!"
        total = data["total_usd"]
        chg = data["change_24h_pct"]
        sign = "+" if chg >= 0 else ""
        lines = [f"• {a['wallet']}: {a['balance']:.6f} {a['symbol']} = **${a['usd_value']:.2f}**" for a in data["assets"]]
        return f"Portfolio total: **${total:,.2f}**  ({sign}{chg:.2f}% 24h)\n" + "\n".join(lines)

    if tool_name == "name_not_found":
        return f"Could not resolve **{args['name']}** — the name may not be registered or the lookup failed."

    if tool_name == "get_prediction_markets":
        from app.tools.prediction.polymarket import search_markets, format_market
        query = args.get("query", "")
        markets = search_markets(query, limit=5)
        if not markets:
            return f"No Polymarket results for \"{query}\". Try a broader search term."
        lines = [format_market(m) for m in markets]
        return f"Polymarket — top results for **\"{query}\"**:\n\n" + "\n\n".join(lines)

    if tool_name == "get_news_sentiment":
        coin = args.get("coin", "BTC")
        from app.tools.market.coingecko import get_price
        from app.tools.market.cryptopanic import get_news, get_sentiment
        from app.tools.market.sentiment import synthesize
        price_data = get_price(coin)
        news_items = get_news(currencies=[coin], limit=5)
        sentiment  = get_sentiment(coin)
        summary = synthesize(coin, price_data or {}, news_items, sentiment)
        if price_data:
            sign = "+" if price_data["change_24h"] >= 0 else ""
            price_line = f"**{coin}** — ${price_data['price']:,.4f}  ({sign}{price_data['change_24h']:.2f}% 24h)\n\n"
        else:
            price_line = ""
        return price_line + summary

    return "Unknown tool."


def _preview_pending_send(pending: dict, db: Session, session_id: str):
    w = db.query(Wallet).filter(Wallet.id == pending["wallet_id"]).first()
    if not w:
        return _stream_text(f"Wallet '{pending['wallet_name']}' not found.", db, session_id)
    token_sym = pending.get("token") or (pending.get("network") or "native").upper()
    net_display = (pending.get("network") or "ethereum").capitalize()
    try:
        if pending.get("token_address"):
            from app.chains import evm as evm_chain
            preview = evm_chain.get_erc20_transfer_preview(
                pending["token_address"], pending["token_decimals"], w.address,
                pending["amount"], pending["to"], pending.get("network"),
            )
            if not preview["has_token_funds"]:
                return _stream_text(
                    f"Insufficient balance. **{pending['wallet_name']}** has "
                    f"**{preview['token_balance']:.6f} {token_sym}**, but you asked to send "
                    f"**{pending['amount']} {token_sym}**.",
                    db, session_id,
                )
            if not preview["has_gas_funds"]:
                return _stream_text(
                    f"Insufficient **{preview['native_unit']}** for gas. **{pending['wallet_name']}** has "
                    f"**{preview['native_balance']:.6f} {preview['native_unit']}**, but this send needs "
                    f"~**{preview['gas_fee']:.6f} {preview['native_unit']}** in gas.",
                    db, session_id,
                )
            balance_line = (
                f"Token balance: **{preview['token_balance']:.6f} {token_sym}**\n"
                f"Estimated gas: **{preview['gas_fee']:.6f} {preview['native_unit']}** "
                f"(from your {preview['native_unit']} balance, not {token_sym})\n"
            )
        else:
            from app.tools.wallet.balance import get_wallet_balance
            if w.chain == "evm":
                from app.chains import evm as evm_chain
                bal = evm_chain.get_native_transfer_preview(w.address, pending["amount"], pending.get("network"))
                balance_line = (
                    f"Balance: **{bal['balance']:.6f} {bal['unit']}**\n"
                    f"Estimated gas: **{bal['fee']:.6f} {bal['unit']}**\n"
                )
                if not bal["has_funds"]:
                    return _stream_text(
                        f"Insufficient balance. **{pending['wallet_name']}** has "
                        f"**{bal['balance']:.6f} {bal['unit']}**, but this send needs "
                        f"**{bal['total']:.6f} {bal['unit']}** including gas.",
                        db,
                        session_id,
                    )
            else:
                bal = get_wallet_balance(w, pending.get("network"))
                balance_line = f"Balance: **{bal['balance']:.6f} {bal['unit']}**\n"
            if bal["balance"] < pending["amount"]:
                return _stream_text(
                    f"Insufficient balance. **{pending['wallet_name']}** has "
                    f"**{bal['balance']:.6f} {bal['unit']}**, but you asked to send "
                    f"**{pending['amount']} {bal['unit']}**.",
                    db,
                    session_id,
                )
    except Exception as e:
        return _stream_text(f"Could not verify balance, so I will not prepare this send: {_exception_message(e)}", db, session_id)
    nick = pending.get("to_nickname")
    to_line = f"To: **{nick}** · `{pending['to']}`" if nick else f"To: `{pending['to']}`"
    text = (
        f"Ready to send **{pending['amount']} {token_sym}** "
        f"on **{net_display}** from **{pending['wallet_name']}**\n"
        f"{balance_line}"
        f"{to_line}\n\n"
        f"Type **CONFIRM** to execute or **CANCEL** to abort."
    )
    return _stream_text(text, db, session_id)


def _preview_pending_register(name: str, wallet: Wallet, db: Session, session_id: str):
    from app.tools.names import sara_names
    price = sara_names.get_price()
    _pending[session_id] = {
        "type": "register_name",
        "name": name,
        "price": price,
        "wallet_name": wallet.name,
        "wallet_id": wallet.id,
        "wallet_chain": wallet.chain,
        "wallet_address": wallet.address,
        "wallet_encrypted_key": wallet.encrypted_key,
    }
    text = (
        f"Registering **{name}** → `{wallet.address}`\n"
        f"Cost: **{price} POL** from **{wallet.name}**\n\n"
        f"Type **CONFIRM** to pay and register, or **CANCEL** to abort."
    )
    return _stream_text(text, db, session_id)


def _build_swap_pending(swap_args: dict, db: Session) -> tuple[Optional[dict], str]:
    """Resolve wallet + fetch a live swap quote. Returns (pending_dict, text).
    pending_dict is None if resolution/quoting failed — text explains why."""
    w = _resolve_wallet(swap_args["wallet_name"], db)
    if not w:
        return None, f"Wallet '{swap_args['wallet_name']}' not found."
    network  = swap_args.get("network", "ethereum")
    src_sym  = swap_args["from_token"]
    dst_sym  = swap_args["to_token"]
    amount   = swap_args["amount"]

    if w.chain == "solana" or network == "solana":
        from app.tools.market.jupiter import (
            resolve_mint, get_quote as jup_quote,
            get_decimals as jup_dec,
        )
        src_mint = resolve_mint(src_sym)
        dst_mint = resolve_mint(dst_sym)
        if not src_mint or not dst_mint:
            supported = ", ".join(["SOL","USDC","USDT","BONK","JUP","RAY","WIF"])
            return None, f"**{src_sym}** or **{dst_sym}** not supported on Solana. Supported: {supported}"
        src_dec = jup_dec(src_sym)
        dst_dec = jup_dec(dst_sym)
        amount_raw = int(amount * 10 ** src_dec)
        quote = jup_quote(src_mint, dst_mint, amount_raw)
        if not (quote and "outAmount" in quote):
            err = quote.get("error", "unknown error") if quote else "Jupiter API unavailable"
            return None, f"Could not get Solana swap quote: {err}"
        dst_amount = int(quote["outAmount"]) / 10 ** dst_dec
        pending = {
            "type": "sol_swap",
            **swap_args,
            "src_mint": src_mint,
            "dst_mint": dst_mint,
            "src_dec": src_dec,
            "dst_dec": dst_dec,
            "amount_raw": amount_raw,
            "quote": quote,
            "dst_amount": dst_amount,
            "wallet_address": w.address,
            "wallet_id": w.id,
            "wallet_chain": w.chain,
            "wallet_encrypted_key": w.encrypted_key,
        }
        text = (
            f"Swap **{amount} {src_sym} → ~{dst_amount:.4f} {dst_sym}**\n"
            f"Network: **Solana**  ·  Wallet: **{w.name}**\n"
            f"Slippage: 0.5%\n\n"
            f"Type **CONFIRM** to execute or **CANCEL** to abort."
        )
        return pending, text

    # EVM → Paraswap
    from app.tools.market.paraswap import resolve_token, get_quote, CHAIN_IDS
    src_result = resolve_token(src_sym, network)
    dst_result = resolve_token(dst_sym, network)
    if not src_result or not dst_result:
        return None, (f"I don't recognise **{src_sym}** or **{dst_sym}** on "
                       f"{network.capitalize()}. Supported: USDC, USDT, WETH, DAI, WBTC, LINK.")
    src_addr, src_dec = src_result
    dst_addr, dst_dec = dst_result
    amount_wei = int(amount * 10 ** src_dec)
    quote = get_quote(src_addr, src_dec, dst_addr, dst_dec, amount_wei, network)
    if not (quote and "priceRoute" in quote):
        err = quote.get("error", "unknown error") if quote else "Paraswap API unavailable"
        return None, f"Could not get swap quote: {err}"
    price_route = quote["priceRoute"]
    dst_amount = int(price_route.get("destAmount", 0)) / (10 ** dst_dec)
    pending = {
        "type": "swap",
        **swap_args,
        "src_addr": src_addr,
        "dst_addr": dst_addr,
        "src_dec": src_dec,
        "dst_dec": dst_dec,
        "amount_wei": amount_wei,
        "src_amount": str(amount_wei),
        "dest_amount": price_route.get("destAmount", "0"),
        "price_route": price_route,
        "wallet_id": w.id,
        "wallet_chain": w.chain,
        "wallet_encrypted_key": w.encrypted_key,
    }
    text = (
        f"Swap **{amount} {src_sym} → ~{dst_amount:.4f} {dst_sym}**\n"
        f"Network: **{network.capitalize()}**  ·  Wallet: **{w.name}**\n"
        f"Slippage: 1%\n\n"
        f"Type **CONFIRM** to execute or **CANCEL** to abort."
    )
    return pending, text


def _build_bridge_pending(bridge_args: dict, db: Session) -> tuple[Optional[dict], str]:
    """Resolve wallet + fetch a live LI.FI cross-chain quote. Independent of
    _build_swap_pending — same-chain swaps are untouched by this."""
    w = _resolve_wallet(bridge_args["wallet_name"], db)
    if not w:
        return None, f"Wallet '{bridge_args['wallet_name']}' not found."
    if w.chain != "evm":
        return None, "Cross-chain bridging is EVM-only right now — pick an EVM wallet."

    from app.tools.trading import lifi
    from_network = bridge_args["from_network"].lower()
    to_network = bridge_args["to_network"].lower()
    src_sym = bridge_args["from_token"]
    dst_sym = bridge_args["to_token"]
    amount = bridge_args["amount"]

    if from_network not in lifi.CHAIN_IDS or to_network not in lifi.CHAIN_IDS:
        supported = ", ".join(n.capitalize() for n in lifi.CHAIN_IDS)
        return None, f"I only support bridging between: {supported}."

    src_result = lifi.resolve_token(src_sym, from_network)
    dst_result = lifi.resolve_token(dst_sym, to_network)
    if not src_result or not dst_result:
        return None, (f"I don't recognise **{src_sym}** on {from_network.capitalize()} or "
                       f"**{dst_sym}** on {to_network.capitalize()}.")
    src_addr, src_dec = src_result
    dst_addr, dst_dec = dst_result
    amount_wei = int(amount * 10 ** src_dec)

    quote = lifi.get_quote(from_network, to_network, src_addr, dst_addr, amount_wei, w.address)
    if not (quote and quote.get("transactionRequest")):
        err = quote.get("message", "no route found") if quote else "LI.FI API unavailable"
        return None, f"Could not get a bridge quote: {err}"

    estimate = quote["estimate"]
    dst_amount = int(estimate["toAmount"]) / (10 ** dst_dec)
    duration_min = estimate.get("executionDuration", 0) / 60
    tool_name = quote.get("toolDetails", {}).get("name", quote.get("tool", "a bridge"))

    pending = {
        "type": "bridge",
        **bridge_args,
        "src_addr": src_addr,
        "dst_addr": dst_addr,
        "src_dec": src_dec,
        "dst_dec": dst_dec,
        "amount_wei": amount_wei,
        "approval_address": estimate.get("approvalAddress"),
        "tx_request": quote["transactionRequest"],
        "wallet_id": w.id,
        "wallet_chain": w.chain,
        "wallet_encrypted_key": w.encrypted_key,
    }
    text = (
        f"Bridge **{amount} {src_sym} ({from_network.capitalize()}) → "
        f"~{dst_amount:.4f} {dst_sym} ({to_network.capitalize()})**\n"
        f"Via: **{tool_name}**  ·  Wallet: **{w.name}**\n"
        f"Est. time: **~{duration_min:.0f} min**  ·  Slippage: 0.5%\n\n"
        f"⚠ Cross-chain transfers take longer than same-chain swaps and route through a third-party "
        f"bridge — funds arrive on {to_network.capitalize()} once the bridge finishes, not instantly.\n\n"
        f"Type **CONFIRM** to execute or **CANCEL** to abort."
    )
    return pending, text


def _build_xyz_fund_pending(fund_args: dict, db: Session) -> tuple[Optional[dict], str]:
    """Resolve wallet + check main-dex balance for a Hyperliquid main→xyz dex transfer."""
    w = _resolve_wallet(fund_args["wallet_name"], db)
    if not w:
        return None, f"Wallet '{fund_args['wallet_name']}' not found."
    from app.tools.trading.hyperliquid import get_withdrawable_balance
    amount = fund_args["amount"]
    main_balance = get_withdrawable_balance(w.address)  # "" dex = main
    balance_note = ""
    if main_balance is not None:
        if main_balance < amount:
            balance_note = (
                f"⚠ This wallet only shows **${main_balance:,.2f}** available on Hyperliquid's main dex — "
                f"less than the **${amount:,.2f}** you're trying to move. Deposit USDC to Hyperliquid's main "
                f"exchange first, or this transfer will fail.\n\n"
            )
        else:
            balance_note = f"Main dex balance: **${main_balance:,.2f}**\n\n"
    pending = {
        "type": "xyz_fund",
        **fund_args,
        "wallet_id": w.id,
        "wallet_chain": w.chain,
        "wallet_encrypted_key": w.encrypted_key,
    }
    text = (
        f"Transfer **${amount:,.2f} USDC** from Hyperliquid's main dex → **xyz** dex's isolated margin\n"
        f"Wallet: **{w.name}**\n\n"
        f"{balance_note}"
        f"Type **CONFIRM** to execute or **CANCEL** to abort."
    )
    return pending, text


def _build_hl_deposit_pending(deposit_args: dict, db: Session) -> tuple[Optional[dict], str]:
    """Resolve wallet + check Arbitrum USDC balance for a Hyperliquid deposit."""
    w = _resolve_wallet(deposit_args["wallet_name"], db)
    if not w:
        return None, f"Wallet '{deposit_args['wallet_name']}' not found."
    if w.chain != "evm":
        return None, "Hyperliquid deposits are EVM-only — pick an EVM wallet."
    from app.tools.trading.hyperliquid import get_arbitrum_usdc_balance
    amount = deposit_args["amount"]
    balance = get_arbitrum_usdc_balance(w.address)
    if balance is not None and balance < amount:
        return None, (f"This wallet only has **${balance:,.2f} USDC** on Arbitrum — less than the "
                       f"**${amount:,.2f}** you're trying to deposit. Bridge or swap more USDC to Arbitrum first.")
    pending = {
        "type": "hl_deposit",
        **deposit_args,
        "wallet_id": w.id,
        "wallet_chain": w.chain,
        "wallet_encrypted_key": w.encrypted_key,
    }
    text = (
        f"Deposit **${amount:,.2f} USDC** (Arbitrum) → Hyperliquid's main exchange\n"
        f"Wallet: **{w.name}**\n\n"
        f"This is a direct on-chain deposit to Hyperliquid's official bridge contract — it usually credits "
        f"within a few minutes.\n\n"
        f"Type **CONFIRM** to execute or **CANCEL** to abort."
    )
    return pending, text


def _build_perp_pending(perp_args: dict, db: Session) -> tuple[Optional[dict], str]:
    """Resolve wallet + fetch a live Hyperliquid preview. Returns (pending_dict, text)."""
    w = _resolve_wallet(perp_args["wallet_name"], db)
    if not w:
        return None, f"Wallet '{perp_args['wallet_name']}' not found."
    from app.tools.trading.hyperliquid import preview_order, get_withdrawable_balance
    symbol   = perp_args["symbol"]
    side     = perp_args["side"]
    size_usd = perp_args["size_usd"]
    leverage = perp_args["leverage"]
    preview  = preview_order(symbol, side, size_usd, leverage)
    if not preview:
        return None, f"**{symbol}** not found on Hyperliquid. Try BTC, ETH, SOL, or another supported perp."
    resolved_symbol = preview["symbol"]  # e.g. "xyz:AAPL" — the exact form Hyperliquid needs
    withdrawable = get_withdrawable_balance(w.address, resolved_symbol)
    low_balance_warning = ""
    if withdrawable is not None and withdrawable < preview["margin_required"]:
        dex_note = " on the xyz dex" if preview["dex"] else ""
        low_balance_warning = (
            f"⚠ This wallet shows **${withdrawable:,.2f}** available{dex_note} on Hyperliquid — less than the "
            f"**${preview['margin_required']:,.2f}** margin this needs. If you haven't deposited USDC there yet, "
            f"do that first or this order will fail.\n\n"
        )
    pending = {
        "type": "perp",
        **perp_args,
        "symbol": resolved_symbol,
        "entry_price": preview["entry_price"],
        "quantity": preview["quantity"],
        "wallet_id": w.id,
        "wallet_chain": w.chain,
        "wallet_encrypted_key": w.encrypted_key,
        "wallet_address": w.address,
    }
    liq = preview["liquidation_price"]
    display_symbol = resolved_symbol.removeprefix("xyz:")
    text = (
        f"**{side.upper()} {display_symbol}** perpetual{' (xyz dex)' if preview['dex'] else ''}\n"
        f"Size: **${size_usd:,.0f}**  ·  Leverage: **{leverage:.0f}x**\n"
        f"Entry: **${preview['entry_price']:,.2f}**  ·  "
        f"Margin: **${preview['margin_required']:,.2f}**\n"
        f"Liq. price: **${liq:,.2f}**  ·  Fee: ~${preview['fee_usd']:.2f}\n\n"
        f"⚠ Leveraged position — losses can exceed your deposit. "
        f"Liquidation at ${liq:,.2f}.\n\n"
        f"{low_balance_warning}"
        f"Type **CONFIRM** to open or **CANCEL** to abort."
    )
    return pending, text


@router.post("/chat")
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    msg = req.message.strip()
    db.add(ChatMessage(session_id=req.session_id, role="user", content=msg))
    db.commit()

    # CONFIRM flow
    if req.session_id in _pending:
        pending = _pending[req.session_id]
        if pending.get("type") == "choose_send_wallet":
            if msg.upper().startswith("CANCEL"):
                del _pending[req.session_id]
                return _stream_text("Transaction cancelled.", db, req.session_id)
            selected_wallet = _wallet_named(msg, db)
            if selected_wallet and selected_wallet.name in pending["wallets"]:
                del _pending[req.session_id]
                _pending[req.session_id] = {
                    "wallet_name": selected_wallet.name,
                    "to": pending["to"],
                    "to_nickname": pending.get("to_nickname"),
                    "amount": pending["amount"],
                    "token": pending["token"],
                    "network": pending["network"],
                    "token_address": pending.get("token_address"),
                    "token_decimals": pending.get("token_decimals"),
                    "wallet_id": selected_wallet.id,
                    "wallet_chain": selected_wallet.chain,
                    "wallet_address": selected_wallet.address,
                    "wallet_encrypted_key": selected_wallet.encrypted_key,
                }
                return _preview_pending_send(_pending[req.session_id], db, req.session_id)
            return _stream_text("Choose one of the listed wallets, or type CANCEL.", db, req.session_id)
        if pending.get("type") == "choose_swap_wallet":
            if msg.upper().startswith("CANCEL"):
                del _pending[req.session_id]
                return _stream_text("Swap cancelled.", db, req.session_id)
            selected_wallet = _wallet_named(msg, db)
            if selected_wallet and selected_wallet.name in pending["wallets"]:
                del _pending[req.session_id]
                swap_args = {k: v for k, v in pending.items() if k not in ("type", "wallets")}
                swap_args["wallet_name"] = selected_wallet.name
                new_pending, text = _build_swap_pending(swap_args, db)
                if new_pending:
                    _pending[req.session_id] = new_pending
                return _stream_text(text, db, req.session_id)
            return _stream_text("Choose one of the listed wallets, or type CANCEL.", db, req.session_id)
        if pending.get("type") == "choose_bridge_wallet":
            if msg.upper().startswith("CANCEL"):
                del _pending[req.session_id]
                return _stream_text("Bridge cancelled.", db, req.session_id)
            selected_wallet = _wallet_named(msg, db)
            if selected_wallet and selected_wallet.name in pending["wallets"]:
                del _pending[req.session_id]
                bridge_args = {k: v for k, v in pending.items() if k not in ("type", "wallets")}
                bridge_args["wallet_name"] = selected_wallet.name
                new_pending, text = _build_bridge_pending(bridge_args, db)
                if new_pending:
                    _pending[req.session_id] = new_pending
                return _stream_text(text, db, req.session_id)
            return _stream_text("Choose one of the listed wallets, or type CANCEL.", db, req.session_id)
        if pending.get("type") == "choose_perp_wallet":
            if msg.upper().startswith("CANCEL"):
                del _pending[req.session_id]
                return _stream_text("Order cancelled.", db, req.session_id)
            selected_wallet = _wallet_named(msg, db)
            if selected_wallet and selected_wallet.name in pending["wallets"]:
                del _pending[req.session_id]
                perp_args = {k: v for k, v in pending.items() if k not in ("type", "wallets")}
                perp_args["wallet_name"] = selected_wallet.name
                new_pending, text = _build_perp_pending(perp_args, db)
                if new_pending:
                    _pending[req.session_id] = new_pending
                return _stream_text(text, db, req.session_id)
            return _stream_text("Choose one of the listed wallets, or type CANCEL.", db, req.session_id)
        if pending.get("type") == "choose_xyz_fund_wallet":
            if msg.upper().startswith("CANCEL"):
                del _pending[req.session_id]
                return _stream_text("Transfer cancelled.", db, req.session_id)
            selected_wallet = _wallet_named(msg, db)
            if selected_wallet and selected_wallet.name in pending["wallets"]:
                del _pending[req.session_id]
                fund_args = {k: v for k, v in pending.items() if k not in ("type", "wallets")}
                fund_args["wallet_name"] = selected_wallet.name
                new_pending, text = _build_xyz_fund_pending(fund_args, db)
                if new_pending:
                    _pending[req.session_id] = new_pending
                return _stream_text(text, db, req.session_id)
            return _stream_text("Choose one of the listed wallets, or type CANCEL.", db, req.session_id)
        if pending.get("type") == "choose_hl_deposit_wallet":
            if msg.upper().startswith("CANCEL"):
                del _pending[req.session_id]
                return _stream_text("Deposit cancelled.", db, req.session_id)
            selected_wallet = _wallet_named(msg, db)
            if selected_wallet and selected_wallet.name in pending["wallets"]:
                del _pending[req.session_id]
                deposit_args = {k: v for k, v in pending.items() if k not in ("type", "wallets")}
                deposit_args["wallet_name"] = selected_wallet.name
                new_pending, text = _build_hl_deposit_pending(deposit_args, db)
                if new_pending:
                    _pending[req.session_id] = new_pending
                return _stream_text(text, db, req.session_id)
            return _stream_text("Choose one of the listed wallets, or type CANCEL.", db, req.session_id)
        if pending.get("type") == "awaiting_name":
            if msg.upper().startswith("CANCEL"):
                del _pending[req.session_id]
                return _stream_text("Cancelled.", db, req.session_id)
            del _pending[req.session_id]
            from app.tools.names import sara_names
            error = sara_names.validate_name(msg)
            if error:
                return _stream_text(error, db, req.session_id)
            name = sara_names.normalize_name(msg)
            if not sara_names.is_available(name):
                return _stream_text(f"**{name}** is already registered to someone else. Try a different name.", db, req.session_id)
            evm_wallets = [w for w in db.query(Wallet).all() if w.chain == "evm"]
            if not evm_wallets:
                return _stream_text("You need an EVM wallet (Polygon-compatible) to register a name — add one first.", db, req.session_id)
            if len(evm_wallets) > 1:
                names = ", ".join(f"**{w.name}**" for w in evm_wallets)
                return _stream_text(
                    f"**{name}** is available for **{sara_names.get_price()} POL**. "
                    f"Which wallet should pay? Your wallets: {names}\n"
                    f"Reply with e.g. \"register {name} from {evm_wallets[0].name}\"",
                    db, req.session_id,
                )
            return _preview_pending_register(name, evm_wallets[0], db, req.session_id)
        if pending.get("type") == "awaiting_register_payment":
            if msg.upper().startswith("CANCEL"):
                del _pending[req.session_id]
                return _stream_text("Cancelled.", db, req.session_id)
            from app.tools.names import sara_names
            result = sara_names.submit_registration(pending["name"], pending["wallet_address"], pending["payment_tx_hash"])
            if result.get("status") == "registered":
                del _pending[req.session_id]
                tx = result.get("registry_tx_hash")
                extra = f"\nRegistry tx: `{tx}`" if tx else ""
                return _stream_text(f"**{pending['name']}** is now registered to your wallet.{extra}", db, req.session_id)
            return _stream_text(
                f"Still waiting on payment confirmation for **{pending['name']}** "
                f"({result.get('detail', 'not yet confirmed')}). Send any message shortly to check again, or CANCEL to give up.",
                db, req.session_id,
            )
        if msg.upper() == "CONFIRM":
            del _pending[req.session_id]
            ptype = pending.get("type")
            if ptype == "swap":
                return _stream_swap(pending, db, req.session_id)
            if ptype == "sol_swap":
                return _stream_sol_swap(pending, db, req.session_id)
            if ptype == "bridge":
                return _stream_bridge(pending, db, req.session_id)
            if ptype == "perp":
                return _stream_perp(pending, db, req.session_id)
            if ptype == "close_perp":
                return _stream_close_perp(pending, db, req.session_id)
            if ptype == "xyz_fund":
                return _stream_xyz_fund(pending, db, req.session_id)
            if ptype == "hl_deposit":
                return _stream_hl_deposit(pending, db, req.session_id)
            if ptype == "register_name":
                return _stream_register_name(pending, db, req.session_id)
            return _stream_send(pending, db, req.session_id)
        elif msg.upper().startswith("CANCEL"):
            del _pending[req.session_id]
            return _stream_text("Transaction cancelled.", db, req.session_id)
        elif msg.upper() in ("YES", "Y"):
            return _stream_text("Type CONFIRM exactly to execute, or CANCEL to abort.", db, req.session_id)
    elif msg.upper() in ("CONFIRM", "YES"):
        return _stream_text("No pending transaction to confirm.", db, req.session_id)
    elif _wallet_named(msg, db):
        return _stream_text("No pending transaction is waiting for a wallet selection.", db, req.session_id)

    messages = [{"role": "system", "content": SARA_SYSTEM_PROMPT}]
    messages += req.history[-20:]
    messages.append({"role": "user", "content": msg})

    async def generate():
        full_response = ""
        try:
            # Fast path: keyword-based intent detection (reliable with small models)
            intent = _detect_intent(msg, db, req.session_id)
            if intent:
                tool_name, args = intent
                result = _handle_tool_call(tool_name, args, db)
                if tool_name == "get_crypto_price" and "coin" in args:
                    _last_coin[req.session_id] = args["coin"]
                if result.startswith("__PENDING_SEND__"):
                    send_args = json.loads(result[len("__PENDING_SEND__"):])
                    w = _resolve_wallet(send_args["wallet_name"], db)
                    if not w:
                        text = f"Wallet '{send_args['wallet_name']}' not found."
                    else:
                        token_sym = send_args.get("token") or (send_args.get("network") or "native").upper()
                        net_display = (send_args.get("network") or "ethereum").capitalize()
                        # Fetch current balance
                        try:
                            from app.tools.wallet.balance import get_wallet_balance
                            if send_args.get("token_address"):
                                from app.chains import evm as evm_chain
                                preview = evm_chain.get_erc20_transfer_preview(
                                    send_args["token_address"], send_args["token_decimals"], w.address,
                                    send_args["amount"], send_args["to"], send_args.get("network"),
                                )
                                if not preview["has_token_funds"]:
                                    text = (
                                        f"Insufficient balance. **{send_args['wallet_name']}** has "
                                        f"**{preview['token_balance']:.6f} {token_sym}**, but you asked to send "
                                        f"**{send_args['amount']} {token_sym}**."
                                    )
                                    full_response = text
                                    for chunk in _chunk(text):
                                        yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
                                    return
                                if not preview["has_gas_funds"]:
                                    text = (
                                        f"Insufficient **{preview['native_unit']}** for gas. **{send_args['wallet_name']}** has "
                                        f"**{preview['native_balance']:.6f} {preview['native_unit']}**, but this send needs "
                                        f"~**{preview['gas_fee']:.6f} {preview['native_unit']}** in gas."
                                    )
                                    full_response = text
                                    for chunk in _chunk(text):
                                        yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
                                    return
                                balance_line = (
                                    f"Token balance: **{preview['token_balance']:.6f} {token_sym}**\n"
                                    f"Estimated gas: **{preview['gas_fee']:.6f} {preview['native_unit']}** "
                                    f"(from your {preview['native_unit']} balance, not {token_sym})\n"
                                )
                            elif w.chain == "evm":
                                from app.chains import evm as evm_chain
                                bal = evm_chain.get_native_transfer_preview(w.address, send_args["amount"], send_args.get("network"))
                                balance_line = (
                                    f"Balance: **{bal['balance']:.6f} {bal['unit']}**\n"
                                    f"Estimated gas: **{bal['fee']:.6f} {bal['unit']}**\n"
                                )
                                if not bal["has_funds"]:
                                    text = (
                                        f"Insufficient balance. **{send_args['wallet_name']}** has "
                                        f"**{bal['balance']:.6f} {bal['unit']}**, but this send needs "
                                        f"**{bal['total']:.6f} {bal['unit']}** including gas."
                                    )
                                    full_response = text
                                    for chunk in _chunk(text):
                                        yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
                                    return
                                if bal["balance"] < send_args["amount"]:
                                    text = (
                                        f"Insufficient balance. **{send_args['wallet_name']}** has "
                                        f"**{bal['balance']:.6f} {bal['unit']}**, but you asked to send "
                                        f"**{send_args['amount']} {bal['unit']}**."
                                    )
                                    full_response = text
                                    for chunk in _chunk(text):
                                        yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
                                    return
                            else:
                                bal = get_wallet_balance(w, send_args.get("network"))
                                balance_line = f"Balance: **{bal['balance']:.6f} {bal['unit']}**\n"
                                if bal["balance"] < send_args["amount"]:
                                    text = (
                                        f"Insufficient balance. **{send_args['wallet_name']}** has "
                                        f"**{bal['balance']:.6f} {bal['unit']}**, but you asked to send "
                                        f"**{send_args['amount']} {bal['unit']}**."
                                    )
                                    full_response = text
                                    for chunk in _chunk(text):
                                        yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
                                    return
                        except Exception as e:
                            text = f"Could not verify balance, so I will not prepare this send: {_exception_message(e)}"
                            full_response = text
                            for chunk in _chunk(text):
                                yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
                            return
                        _pending[req.session_id] = {
                            **send_args,
                            "wallet_id": w.id,
                            "wallet_chain": w.chain,
                            "wallet_address": w.address,
                            "wallet_encrypted_key": w.encrypted_key,
                        }
                        # Format recipient line
                        nick = send_args.get("to_nickname")
                        to_line = (f"To: **{nick}** · `{send_args['to']}`" if nick
                                   else f"To: `{send_args['to']}`")
                        text = (
                            f"Ready to send **{send_args['amount']} {token_sym}** "
                            f"on **{net_display}** from **{send_args['wallet_name']}**\n"
                            f"{balance_line}"
                            f"{to_line}\n\n"
                            f"Type **CONFIRM** to execute or **CANCEL** to abort."
                        )
                elif result.startswith("__PENDING_SWAP__"):
                    swap_args = json.loads(result[len("__PENDING_SWAP__"):])
                    pending, text = _build_swap_pending(swap_args, db)
                    if pending:
                        _pending[req.session_id] = pending

                elif result.startswith("__PENDING_BRIDGE__"):
                    bridge_args = json.loads(result[len("__PENDING_BRIDGE__"):])
                    pending, text = _build_bridge_pending(bridge_args, db)
                    if pending:
                        _pending[req.session_id] = pending

                elif result.startswith("__PENDING_PERP__"):
                    perp_args = json.loads(result[len("__PENDING_PERP__"):])
                    pending, text = _build_perp_pending(perp_args, db)
                    if pending:
                        _pending[req.session_id] = pending

                elif result.startswith("__PENDING_XYZ_FUND__"):
                    fund_args = json.loads(result[len("__PENDING_XYZ_FUND__"):])
                    pending, text = _build_xyz_fund_pending(fund_args, db)
                    if pending:
                        _pending[req.session_id] = pending

                elif result.startswith("__PENDING_HL_DEPOSIT__"):
                    deposit_args = json.loads(result[len("__PENDING_HL_DEPOSIT__"):])
                    pending, text = _build_hl_deposit_pending(deposit_args, db)
                    if pending:
                        _pending[req.session_id] = pending

                elif result.startswith("__PENDING_CLOSE_PERP__"):
                    close_args = json.loads(result[len("__PENDING_CLOSE_PERP__"):])
                    w = _resolve_wallet(close_args["wallet_name"], db)
                    if not w:
                        text = f"Wallet '{close_args['wallet_name']}' not found."
                    else:
                        symbol = close_args["symbol"]
                        pnl = close_args.get("pnl_est", 0)
                        mark = close_args.get("mark", 0)
                        pnl_sign = "+" if pnl >= 0 else ""
                        _pending[req.session_id] = {
                            "type": "close_perp",
                            **close_args,
                            "wallet_encrypted_key": w.encrypted_key,
                            "wallet_address": w.address,
                        }
                        text = (
                            f"Close **{symbol}** position at ~${mark:,.2f}\n"
                            f"Est. PnL: **{pnl_sign}${pnl:.2f}**\n\n"
                            f"Type **CONFIRM** to close or **CANCEL** to abort."
                        )
                elif result.startswith("__PENDING_REGISTER__"):
                    reg_args = json.loads(result[len("__PENDING_REGISTER__"):])
                    w = _resolve_wallet(reg_args["wallet_name"], db)
                    if not w:
                        text = f"Wallet '{reg_args['wallet_name']}' not found."
                    else:
                        _pending[req.session_id] = {
                            "type": "register_name",
                            "name": reg_args["name"],
                            "price": reg_args["price"],
                            "wallet_name": w.name,
                            "wallet_id": w.id,
                            "wallet_chain": w.chain,
                            "wallet_address": w.address,
                            "wallet_encrypted_key": w.encrypted_key,
                        }
                        text = (
                            f"Registering **{reg_args['name']}** → `{w.address}`\n"
                            f"Cost: **{reg_args['price']} POL** from **{w.name}**\n\n"
                            f"Type **CONFIRM** to pay and register, or **CANCEL** to abort."
                        )
                else:
                    if tool_name == "send_needs_wallet":
                        _pending[req.session_id] = {"type": "choose_send_wallet", **args}
                    elif tool_name == "swap_needs_wallet":
                        _pending[req.session_id] = {"type": "choose_swap_wallet", **args}
                    elif tool_name == "bridge_needs_wallet":
                        _pending[req.session_id] = {"type": "choose_bridge_wallet", **args}
                    elif tool_name == "perp_needs_wallet":
                        _pending[req.session_id] = {"type": "choose_perp_wallet", **args}
                    elif tool_name == "hyperliquid_fund_xyz_needs_wallet":
                        _pending[req.session_id] = {"type": "choose_xyz_fund_wallet", **args}
                    elif tool_name == "hyperliquid_deposit_needs_wallet":
                        _pending[req.session_id] = {"type": "choose_hl_deposit_wallet", **args}
                    elif tool_name == "register_ask_name":
                        _pending[req.session_id] = {"type": "awaiting_name"}
                    text = result
                full_response = text
                for chunk in _chunk(text):
                    yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
            else:
                if _looks_like_transaction_text(msg):
                    text = (
                        "I could not parse that as a safe transaction command. "
                        "Use: `send <amount> <token> from <wallet> to <address or saved nickname>`."
                    )
                    full_response = text
                    for chunk in _chunk(text):
                        yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
                    return
                # Slow path: stream to LLM
                async for token in sara_llm.stream_chat(messages):
                    full_response += token
                    yield f"data: {json.dumps({'token': token, 'done': False})}\n\n"

        except Exception as e:
            err = f"Error: {e}"
            yield f"data: {json.dumps({'token': err, 'done': False})}\n\n"
            full_response = err
        finally:
            yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"
            if full_response:
                db.add(ChatMessage(session_id=req.session_id, role="assistant", content=full_response))
                db.commit()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _chunk(text: str, size: int = 4):
    for i in range(0, len(text), size):
        yield text[i:i+size]


def _stream_text(text: str, db: Session, session_id: str):
    async def generate():
        for chunk in _chunk(text):
            yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
        yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"
        db.add(ChatMessage(session_id=session_id, role="assistant", content=text))
        db.commit()
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _stream_send(pending: dict, db: Session, session_id: str):
    async def generate():
        from app.tools.wallet.encrypt import decrypt_key
        from app.chains import evm as evm_chain, solana as sol_chain
        from app.db.models import Transaction
        from datetime import datetime
        try:
            plain_key = decrypt_key(pending["wallet_encrypted_key"])
            chain = pending["wallet_chain"]
            network = pending.get("network") or ("solana" if chain == "solana" else "ethereum")
            to_addr = pending["to"]
            amount = pending["amount"]
            if not _is_valid_recipient(to_addr, network):
                raise ValueError("recipient is not a valid address or resolved directory/ENS/SNS name")

            if chain == "evm" and pending.get("token_address"):
                tx_hash = evm_chain.send_erc20_tx(
                    plain_key, pending["token_address"], pending["token_decimals"],
                    to_addr, amount, network,
                )
            elif chain == "evm":
                balance = evm_chain.get_balance(pending["wallet_address"], network)
                if balance["balance"] < amount:
                    raise ValueError(
                        f"insufficient balance: {balance['balance']:.6f} {balance['unit']} available"
                    )
                tx_hash = evm_chain.send_tx(plain_key, to_addr, amount, network)
            else:
                balance = sol_chain.get_balance(pending["wallet_address"])
                if balance["balance"] < amount:
                    raise ValueError(
                        f"insufficient balance: {balance['balance']:.6f} {balance['unit']} available"
                    )
                tx_hash = sol_chain.send_tx(bytes.fromhex(plain_key), to_addr, amount)

            db.add(Transaction(
                wallet_id=pending["wallet_id"], chain=chain, tx_hash=tx_hash,
                to_address=to_addr, amount=amount, status="submitted",
                timestamp=datetime.utcnow(),
            ))
            db.commit()
            token_sym = pending.get("token") or network.upper()
            text = f"Broadcast **{amount} {token_sym}**.\nTx hash: `{tx_hash}`"
        except Exception as e:
            text = f"Send failed: {_exception_message(e)}"
        for chunk in _chunk(text):
            yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
        yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"
        db.add(ChatMessage(session_id=session_id, role="assistant", content=text))
        db.commit()
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _stream_swap(pending: dict, db: Session, session_id: str):
    async def generate():
        from app.tools.wallet.encrypt import decrypt_key
        from app.tools.market.paraswap import ensure_allowance, get_swap_tx, execute_swap
        try:
            plain_key   = decrypt_key(pending["wallet_encrypted_key"])
            network     = pending.get("network", "ethereum")
            src_addr    = pending["src_addr"]
            dst_addr    = pending["dst_addr"]
            dst_dec     = pending.get("dst_dec", 18)
            amount_wei  = pending["amount_wei"]
            src_amount  = pending["src_amount"]
            dest_amount = pending["dest_amount"]
            price_route = pending["price_route"]
            from_tok    = pending["from_token"]
            to_tok      = pending["to_token"]
            wallet_addr = pending.get("wallet_address", "")

            if not wallet_addr:
                from app.chains.evm import get_web3
                w3 = get_web3(network)
                wallet_addr = w3.eth.account.from_key(plain_key).address

            approve_hash = ensure_allowance(plain_key, src_addr, amount_wei, network)
            if approve_hash:
                yield f"data: {json.dumps({'token': f'Approval tx: `{approve_hash}`\n', 'done': False})}\n\n"

            swap_data = get_swap_tx(price_route, src_addr, dst_addr, src_amount, wallet_addr, network)
            if not swap_data or "error" in swap_data:
                err = swap_data.get("error", "no calldata") if swap_data else "Paraswap API error"
                raise Exception(err)
            tx_hash = execute_swap(plain_key, swap_data, network)
            dst_amount_human = int(dest_amount) / (10 ** dst_dec)
            text = f"✅ Swapped **{pending['amount']} {from_tok} → ~{dst_amount_human:.4f} {to_tok}**!\nTx hash: `{tx_hash}`"
        except Exception as e:
            text = f"Swap failed: {e}"
        for chunk in _chunk(text):
            yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
        yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"
        db.add(ChatMessage(session_id=session_id, role="assistant", content=text))
        db.commit()
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _stream_bridge(pending: dict, db: Session, session_id: str):
    async def generate():
        from app.tools.wallet.encrypt import decrypt_key
        from app.tools.trading import lifi
        approval_note = ""
        try:
            plain_key    = decrypt_key(pending["wallet_encrypted_key"])
            from_network = pending["from_network"].lower()
            to_network   = pending["to_network"].lower()
            src_addr     = pending["src_addr"]
            dst_addr     = pending["dst_addr"]
            dst_dec      = pending.get("dst_dec", 18)
            amount_wei   = pending["amount_wei"]
            from_tok     = pending["from_token"]
            to_tok       = pending["to_token"]

            from app.chains.evm import get_web3
            w3 = get_web3(from_network)
            wallet_addr = w3.eth.account.from_key(plain_key).address

            # Re-quote right before executing rather than reusing the
            # preview-time quote: LI.FI's calldata embeds a deadline/minimum-
            # output check tied to quote freshness, and the human-in-the-loop
            # gap between seeing the preview and typing CONFIRM is easily long
            # enough for that to expire and revert on-chain.
            quote = lifi.get_quote(from_network, to_network, src_addr, dst_addr, amount_wei, wallet_addr)
            if not (quote and quote.get("transactionRequest")):
                err = quote.get("message", "no route found") if quote else "LI.FI API unavailable"
                raise Exception(f"could not refresh quote before executing — {err}")
            estimate = quote["estimate"]
            approval_addr = estimate.get("approvalAddress")
            tx_request = quote["transactionRequest"]

            if approval_addr:
                approve_hash = lifi.ensure_allowance(plain_key, src_addr, approval_addr, amount_wei, from_network)
                if approve_hash:
                    approval_note = f"Approval tx: `{approve_hash}`\n"
                    yield f"data: {json.dumps({'token': approval_note, 'done': False})}\n\n"

            tx_hash = lifi.execute_bridge(plain_key, tx_request, from_network)
            dst_amount_human = int(estimate["toAmount"]) / (10 ** dst_dec)
            text = (
                f"✅ Bridging **{pending['amount']} {from_tok} ({from_network.capitalize()}) → "
                f"~{dst_amount_human:.4f} {to_tok} ({to_network.capitalize()})** — submitted!\n"
                f"Tx hash: `{tx_hash}`\n\n"
                f"Cross-chain transfers take a few minutes to arrive — check the destination wallet's "
                f"balance shortly. If it doesn't show up, ask me to check the bridge status with this tx hash."
            )
        except Exception as e:
            text = f"Bridge failed: {e}"
        for chunk in _chunk(text):
            yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
        yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"
        db.add(ChatMessage(session_id=session_id, role="assistant", content=approval_note + text))
        db.commit()
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _stream_sol_swap(pending: dict, db: Session, session_id: str):
    async def generate():
        from app.tools.wallet.encrypt import decrypt_key
        from app.tools.market.jupiter import get_swap_transaction, execute_swap as jup_execute
        try:
            plain_key   = decrypt_key(pending["wallet_encrypted_key"])
            key_bytes   = bytes.fromhex(plain_key)
            wallet_addr = pending["wallet_address"]
            quote       = pending["quote"]
            src_sym     = pending["from_token"]
            dst_sym     = pending["to_token"]
            amount      = pending["amount"]
            dst_amount  = pending["dst_amount"]

            tx_b64 = get_swap_transaction(quote, wallet_addr)
            if not tx_b64:
                raise Exception("Jupiter did not return swap transaction")
            sig = jup_execute(key_bytes, tx_b64)
            text = (f"✅ Swapped **{amount} {src_sym} → ~{dst_amount:.4f} {dst_sym}** on Solana!\n"
                    f"Signature: `{sig}`")
        except Exception as e:
            text = f"Solana swap failed: {e}"
        for chunk in _chunk(text):
            yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
        yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"
        db.add(ChatMessage(session_id=session_id, role="assistant", content=text))
        db.commit()
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _stream_perp(pending: dict, db: Session, session_id: str):
    async def generate():
        from app.tools.wallet.encrypt import decrypt_key
        from app.tools.trading.hyperliquid import execute_order
        try:
            plain_key = decrypt_key(pending["wallet_encrypted_key"])
            symbol    = pending["symbol"]
            side      = pending["side"]
            size_usd  = pending["size_usd"]
            leverage  = pending["leverage"]

            result = execute_order(plain_key, symbol, side, size_usd, leverage)
            if result.get("status") == "ok":
                text = (f"✅ Opened **{side.upper()} {symbol}** ${size_usd:,.0f} at {leverage:.0f}x leverage\n"
                        f"Qty: {result['qty']:.4f}  ·  Entry: ~${result['price']:,.2f}\n"
                        f"Order: `{result['order_id']}`")
            else:
                err = result.get("error", "unknown error")
                if "does not exist" in str(err).lower():
                    text = (
                        "Order failed: this wallet doesn't have a Hyperliquid account yet. "
                        "Hyperliquid requires at least one USDC deposit into its exchange before it'll accept orders — "
                        "deposit USDC to Hyperliquid for this wallet's address first, then try again."
                    )
                else:
                    text = f"Order failed: {err}"
        except Exception as e:
            text = f"Order failed: {e}"
        for chunk in _chunk(text):
            yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
        yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"
        db.add(ChatMessage(session_id=session_id, role="assistant", content=text))
        db.commit()
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _stream_xyz_fund(pending: dict, db: Session, session_id: str):
    async def generate():
        from app.tools.wallet.encrypt import decrypt_key
        from app.tools.trading.hyperliquid import transfer_to_xyz_dex
        try:
            plain_key = decrypt_key(pending["wallet_encrypted_key"])
            amount    = pending["amount"]
            result = transfer_to_xyz_dex(plain_key, amount)
            if result.get("status") == "ok":
                text = f"✅ Transferred **${amount:,.2f} USDC** from Hyperliquid's main dex to the **xyz** dex."
            else:
                err = result.get("error", "unknown error")
                if "does not exist" in str(err).lower():
                    text = (
                        "Transfer failed: this wallet doesn't have a Hyperliquid account yet. "
                        "Deposit USDC to Hyperliquid's main exchange first, then try again."
                    )
                else:
                    text = f"Transfer failed: {err}"
        except Exception as e:
            text = f"Transfer failed: {e}"
        for chunk in _chunk(text):
            yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
        yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"
        db.add(ChatMessage(session_id=session_id, role="assistant", content=text))
        db.commit()
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _stream_hl_deposit(pending: dict, db: Session, session_id: str):
    async def generate():
        from app.tools.wallet.encrypt import decrypt_key
        from app.tools.trading.hyperliquid import deposit_to_hyperliquid
        try:
            plain_key = decrypt_key(pending["wallet_encrypted_key"])
            amount    = pending["amount"]
            result = deposit_to_hyperliquid(plain_key, amount)
            if result.get("status") == "ok":
                text = (
                    f"✅ Deposited **${amount:,.2f} USDC** to Hyperliquid's main exchange.\n"
                    f"Tx hash: `{result['tx_hash']}`\n\n"
                    f"This should credit your Hyperliquid account within a few minutes."
                )
            else:
                text = f"Deposit failed: {result.get('error', 'unknown error')}"
        except Exception as e:
            text = f"Deposit failed: {e}"
        for chunk in _chunk(text):
            yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
        yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"
        db.add(ChatMessage(session_id=session_id, role="assistant", content=text))
        db.commit()
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _stream_close_perp(pending: dict, db: Session, session_id: str):
    async def generate():
        from app.tools.wallet.encrypt import decrypt_key
        from app.tools.trading.hyperliquid import close_position
        try:
            plain_key = decrypt_key(pending["wallet_encrypted_key"])
            symbol    = pending["symbol"]

            result = close_position(plain_key, symbol)
            if result.get("status") == "ok":
                pnl = pending.get("pnl_est", 0)
                pnl_sign = "+" if pnl >= 0 else ""
                text = (f"✅ Closed **{symbol}** position\n"
                        f"Est. PnL: **{pnl_sign}${pnl:.2f}**")
            else:
                text = f"Close failed: {result.get('error', 'unknown error')}"
        except Exception as e:
            text = f"Close failed: {e}"
        for chunk in _chunk(text):
            yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
        yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"
        db.add(ChatMessage(session_id=session_id, role="assistant", content=text))
        db.commit()
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _stream_register_name(pending: dict, db: Session, session_id: str):
    async def generate():
        import os
        from app.tools.wallet.encrypt import decrypt_key
        from app.tools.names import sara_names
        from app.chains import evm as evm_chain
        from app.db.models import Transaction
        from datetime import datetime
        try:
            registrar_address = os.getenv("SARA_NAME_REGISTRAR_ADDRESS", "")
            if not registrar_address:
                raise ValueError("Name registration is not configured (SARA_NAME_REGISTRAR_ADDRESS is not set).")
            plain_key = decrypt_key(pending["wallet_encrypted_key"])
            price = pending["price"]
            balance = evm_chain.get_balance(pending["wallet_address"], "polygon")
            if balance["balance"] < price:
                raise ValueError(f"insufficient balance: {balance['balance']:.6f} {balance['unit']} available, {price} POL required")
            payment_tx_hash = evm_chain.send_tx(plain_key, registrar_address, price, "polygon")
            db.add(Transaction(
                wallet_id=pending["wallet_id"], chain="evm", tx_hash=payment_tx_hash,
                to_address=registrar_address, amount=price, status="submitted",
                timestamp=datetime.utcnow(),
            ))
            db.commit()

            result = sara_names.submit_registration(pending["name"], pending["wallet_address"], payment_tx_hash)
            if result.get("status") == "registered":
                tx = result.get("registry_tx_hash")
                extra = f"\nRegistry tx: `{tx}`" if tx else ""
                text = f"Payment sent (`{payment_tx_hash}`). **{pending['name']}** is now registered to your wallet.{extra}"
            else:
                _pending[session_id] = {
                    "type": "awaiting_register_payment",
                    "name": pending["name"],
                    "wallet_address": pending["wallet_address"],
                    "payment_tx_hash": payment_tx_hash,
                }
                text = (
                    f"Payment sent (`{payment_tx_hash}`). I'll finish registering **{pending['name']}** "
                    f"once it confirms — send any message in a bit and I'll check."
                )
        except Exception as e:
            text = f"Registration failed: {_exception_message(e)}"
        for chunk in _chunk(text):
            yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
        yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"
        db.add(ChatMessage(session_id=session_id, role="assistant", content=text))
        db.commit()
    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
