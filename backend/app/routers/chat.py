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
}


def _detect_intent(msg: str, db: Session, session_id: str = "default") -> Optional[tuple[str, dict]]:
    """Fast keyword-based intent detection for common wallet queries."""
    m = msg.lower()
    wallets = db.query(Wallet).all()

    # help / capabilities
    if any(p in m for p in ("what can you do", "what do you do", "your capabilities", "what are you capable", "what can sara", "how do you work", "help me understand", "what features")):
        return ("show_help", {})

    # send / transfer  — parse: (send|transfer) <amount> <token> [from <wallet>] to <address>
    send_match = re.search(
        r'(?:send|transfer)\s+([\d.]+)\s+(\w+)(?:\s+from\s+(\w[\w\s]*?))?\s+to\s+(\S+)',
        m
    )
    if send_match:
        amount_str, token, from_hint, to_addr = send_match.groups()
        try:
            amount = float(amount_str)
        except ValueError:
            amount = 0
        network = _TOKEN_TO_NETWORK.get(token.lower())
        # Resolve to_addr: nickname → real address, or ENS/SNS → on-chain address
        to_nickname = None
        ab_entry = db.query(AddressBook).filter(AddressBook.nickname == to_addr.lower()).first()
        if ab_entry:
            to_nickname = to_addr
            to_addr = ab_entry.address
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
        # Resolve which wallet to send from
        wallet = None
        if from_hint:
            wallet = _match_wallet(from_hint, wallets)
        if not wallet:
            wallet = _match_wallet(msg, wallets)
        if not wallet and len(wallets) == 1:
            wallet = wallets[0]
        if amount > 0 and to_addr:
            if wallet:
                return ("send_crypto", {
                    "wallet_name": wallet.name,
                    "to": to_addr,
                    "to_nickname": to_nickname,
                    "amount": amount,
                    "token": token.upper(),
                    "network": network,
                })
            elif wallets:
                return ("send_needs_wallet", {
                    "amount": amount,
                    "token": token.upper(),
                    "to": to_addr,
                    "to_nickname": to_nickname,
                    "wallets": [w.name for w in wallets],
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

    # perp: long/short order
    if any(w in m.split() for w in ("long", "short")):
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

    # list wallets
    if any(p in m for p in ("list wallet", "my wallet", "show wallet", "list my wallet")):
        return ("list_wallets", {})

    # balance
    if "balance" in m or ("how much" in m and any(w.name.lower() in m for w in wallets)):
        matched = _match_wallet(msg, wallets)
        if matched:
            network = None
            for net in ("ethereum", "arbitrum", "base", "polygon", "optimism", "solana"):
                if net in m:
                    network = net
                    break
            return ("get_balance", {"wallet_name": matched.name, "network": network})
        if wallets and len(wallets) == 1:
            return ("get_balance", {"wallet_name": wallets[0].name, "network": None})

    # market: crypto price
    CRYPTO_KEYWORDS = ("price", "how much is", "what is", "what's", "whats", "cost", "worth", "at", "doing")
    KNOWN_SYMBOLS = set(coingecko.SYMBOL_TO_ID.keys()) | {"BITCOIN", "ETHEREUM", "SOLANA"}
    if any(k in m for k in CRYPTO_KEYWORDS):
        for sym in KNOWN_SYMBOLS:
            if sym.lower() in m:
                return ("get_crypto_price", {"coin": sym})

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

    # market: commodity
    COMMODITY_MAP = {
        "gold": "GC=F", "silver": "SI=F", "oil": "CL=F", "crude": "CL=F",
        "brent": "BZ=F", "gas": "NG=F", "natural gas": "NG=F",
        "wheat": "ZW=F", "corn": "ZC=F", "copper": "HG=F", "platinum": "PL=F",
    }
    for word, ticker in COMMODITY_MAP.items():
        if word in m:
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

    # prediction markets (Polymarket)
    if any(p in m for p in ("polymarket", "prediction market", "odds", "bet", "chance", "probability",
                             "will ", "likelihood")):
        query = msg.strip()
        for prefix in ("what are", "what's", "polymarket", "odds on", "odds for", "chance of",
                        "probability of", "will", "prediction market"):
            query = re.sub(rf'^\s*{re.escape(prefix)}\s*', '', query, flags=re.I).strip()
        query = re.sub(r'\?$', '', query).strip()
        return ("get_prediction_markets", {"query": query or msg})

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

    if tool_name == "send_needs_wallet":
        names = ", ".join(f"**{n}**" for n in args["wallets"])
        return (f"Which wallet should I send **{args['amount']} {args['token']}** from?\n"
                f"Your wallets: {names}\n"
                f"Reply with e.g. \"send {args['amount']} {args['token']} from {args['wallets'][0]} to {args['to']}\"")

    if tool_name == "swap_needs_wallet":
        names = ", ".join(f"**{n}**" for n in args["wallets"])
        return (f"Which wallet should I use to swap **{args['amount']} {args['from_token']} → {args['to_token']}**?\n"
                f"Your wallets: {names}\n"
                f"Reply with e.g. \"swap {args['amount']} {args['from_token']} for {args['to_token']} from {args['wallets'][0]}\"")

    if tool_name == "swap_tokens":
        return f"__PENDING_SWAP__{json.dumps(args)}"

    if tool_name == "perp_order":
        return f"__PENDING_PERP__{json.dumps(args)}"

    if tool_name == "perp_needs_wallet":
        names = ", ".join(f"**{n}**" for n in args["wallets"])
        return (f"Which wallet should I use to open a **{args['side'].upper()} {args['symbol']}** "
                f"position for **${args['size_usd']:,.0f}** at **{args['leverage']}x**?\n"
                f"Your wallets: {names}")

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
            lines.append(
                f"{side_sym} **{p['symbol']}** {p['side'].upper()}  ·  "
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
        pos = next((p for p in positions if p["symbol"] == symbol), None)
        if not pos:
            return f"No open {symbol} position found for **{w.name}**."
        mark = get_mark_price(symbol) or pos["entry_price"]
        pnl_est = (mark - pos["entry_price"]) * pos["size"] * (1 if pos["side"] == "long" else -1)
        return f"__PENDING_CLOSE_PERP__{json.dumps({**args, 'pnl_est': pnl_est, 'mark': mark, 'wallet_encrypted_key': w.encrypted_key, 'wallet_id': w.id})}"

    if tool_name == "show_help":
        return (
            "Here's what SARA can do:\n\n"
            "**Wallets**\n"
            "• Create & import EVM wallets (Ethereum, Arbitrum, Base, Optimism, Polygon) and Solana\n"
            "• Check balance on any supported network\n"
            "• Send crypto — say \"send 0.1 ETH from Main to 0x...\" and type CONFIRM\n"
            "• Swap tokens — say \"swap 1 POL for USDC from test1\" and type CONFIRM\n\n"
            "**Market Data** *(live via Yahoo Finance + CoinGecko)*\n"
            "• Crypto prices — \"BTC price\", \"what's SOL at?\"\n"
            "• Commodities — Gold, Silver, Oil, Copper, Nat Gas\n"
            "• Forex — EUR/USD, GBP/USD, USD/JPY, AUD/USD\n"
            "• Stocks — Apple, NVIDIA, Tesla, or any ticker symbol\n"
            "• Gas fees — \"check gas\"\n"
            "• DeFi TVL — \"Aave TVL\", \"total DeFi locked\"\n"
            "• Yield opportunities — \"top DeFi yields on Ethereum\"\n"
            "• Trending coins — \"what's trending?\"\n"
            "• Global market cap & BTC dominance\n\n"
            "**Trading**\n"
            "• Swap tokens on EVM — \"swap 1 POL for USDC from test1\"\n"
            "• Swap tokens on Solana — \"swap 0.5 SOL for USDC\"\n"
            "• Hyperliquid perps — \"long ETH $500 2x\" or \"short BTC $1000 3x\"\n"
            "• Perp positions — \"show my positions\"\n"
            "• Close position — \"close ETH position\"\n\n"
            "**Intelligence**\n"
            "• News & sentiment — \"BTC sentiment\", \"what's happening with SOL?\"\n"
            "• Prediction markets — \"polymarket odds on ETH ETF\", \"will Bitcoin hit 100k?\"\n"
            "• ENS resolution — send to \"vitalik.eth\" and SARA resolves it\n"
            "• SNS resolution — send to \"wallet.sol\" and SARA resolves it"
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
                if balances:
                    for b in balances:
                        card.append(f"{b['balance']:.6f} **{b['unit']}**  ·  {b['network'].capitalize()}")
                    # ERC-20 tokens via Alchemy
                    from app.tools.wallet.tokens import get_erc20_balances
                    funded_nets = {b["network"] for b in balances}
                    for net in funded_nets:
                        for tok in get_erc20_balances(w.address, net):
                            card.append(f"{tok['balance']:.6f} **{tok['symbol']}**  ·  {net.capitalize()}")
                else:
                    card.append("No funds detected")
            else:
                try:
                    b = sol_chain.get_balance(w.address)
                    card.append(f"{b['balance']:.6f} **SOL**")
                except Exception:
                    card.append("Balance unavailable")
            card.append(f"`{w.address}`")
            blocks.append("\n".join(card))
        return "\n---\n".join(blocks)

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
        d = coingecko.get_price(args["coin"])
        if not d:
            return f"No price data for {args['coin']}."
        sign = "+" if d["change_24h"] >= 0 else ""
        parts = [f"**{d['symbol']}** — **${d['price']:,.4f}**", f"24h: {sign}{d['change_24h']:.2f}%"]
        if d.get("market_cap"):
            parts.append(f"Market cap: ${d['market_cap']/1e9:.2f}B")
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


@router.post("/chat")
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    msg = req.message.strip()
    db.add(ChatMessage(session_id=req.session_id, role="user", content=msg))
    db.commit()

    # CONFIRM flow
    if req.session_id in _pending:
        pending = _pending[req.session_id]
        if msg.upper().startswith("CONFIRM"):
            del _pending[req.session_id]
            ptype = pending.get("type")
            if ptype == "swap":
                return _stream_swap(pending, db, req.session_id)
            if ptype == "sol_swap":
                return _stream_sol_swap(pending, db, req.session_id)
            if ptype == "perp":
                return _stream_perp(pending, db, req.session_id)
            if ptype == "close_perp":
                return _stream_close_perp(pending, db, req.session_id)
            return _stream_send(pending, db, req.session_id)
        elif msg.upper().startswith("CANCEL"):
            del _pending[req.session_id]
            return _stream_text("Transaction cancelled.", db, req.session_id)

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
                        _pending[req.session_id] = {
                            **send_args,
                            "wallet_id": w.id,
                            "wallet_chain": w.chain,
                            "wallet_encrypted_key": w.encrypted_key,
                        }
                        token_sym = send_args.get("token") or (send_args.get("network") or "native").upper()
                        net_display = (send_args.get("network") or "ethereum").capitalize()
                        # Fetch current balance
                        try:
                            from app.tools.wallet.balance import get_wallet_balance
                            bal = get_wallet_balance(w, send_args.get("network"))
                            balance_line = f"Balance: **{bal['balance']:.6f} {bal['unit']}**\n"
                        except Exception:
                            balance_line = ""
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
                    w = _resolve_wallet(swap_args["wallet_name"], db)
                    if not w:
                        text = f"Wallet '{swap_args['wallet_name']}' not found."
                    else:
                        network  = swap_args.get("network", "ethereum")
                        src_sym  = swap_args["from_token"]
                        dst_sym  = swap_args["to_token"]
                        amount   = swap_args["amount"]

                        # Route to Jupiter for Solana wallets or SOL token swaps
                        if w.chain == "solana" or network == "solana":
                            from app.tools.market.jupiter import (
                                resolve_mint, get_quote as jup_quote,
                                get_decimals as jup_dec,
                            )
                            src_mint = resolve_mint(src_sym)
                            dst_mint = resolve_mint(dst_sym)
                            if not src_mint or not dst_mint:
                                supported = ", ".join(["SOL","USDC","USDT","BONK","JUP","RAY","WIF"])
                                text = (f"**{src_sym}** or **{dst_sym}** not supported on Solana. "
                                        f"Supported: {supported}")
                            else:
                                src_dec = jup_dec(src_sym)
                                dst_dec = jup_dec(dst_sym)
                                amount_raw = int(amount * 10 ** src_dec)
                                quote = jup_quote(src_mint, dst_mint, amount_raw)
                                if quote and "outAmount" in quote:
                                    dst_amount = int(quote["outAmount"]) / 10 ** dst_dec
                                    _pending[req.session_id] = {
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
                                else:
                                    err = quote.get("error", "unknown error") if quote else "Jupiter API unavailable"
                                    text = f"Could not get Solana swap quote: {err}"
                        else:
                            # EVM → Paraswap
                            from app.tools.market.paraswap import resolve_token, get_quote, CHAIN_IDS
                            src_result = resolve_token(src_sym, network)
                            dst_result = resolve_token(dst_sym, network)
                            if not src_result or not dst_result:
                                text = (f"I don't recognise **{src_sym}** or **{dst_sym}** on "
                                        f"{network.capitalize()}. Supported: USDC, USDT, WETH, DAI, WBTC, LINK.")
                            else:
                                src_addr, src_dec = src_result
                                dst_addr, dst_dec = dst_result
                                amount_wei = int(amount * 10 ** src_dec)
                                quote = get_quote(src_addr, src_dec, dst_addr, dst_dec, amount_wei, network)
                                if quote and "priceRoute" in quote:
                                    price_route = quote["priceRoute"]
                                    dst_amount = int(price_route.get("destAmount", 0)) / (10 ** dst_dec)
                                    _pending[req.session_id] = {
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
                                else:
                                    err = quote.get("error", "unknown error") if quote else "Paraswap API unavailable"
                                    text = f"Could not get swap quote: {err}"

                elif result.startswith("__PENDING_PERP__"):
                    perp_args = json.loads(result[len("__PENDING_PERP__"):])
                    w = _resolve_wallet(perp_args["wallet_name"], db)
                    if not w:
                        text = f"Wallet '{perp_args['wallet_name']}' not found."
                    else:
                        from app.tools.trading.hyperliquid import preview_order
                        symbol   = perp_args["symbol"]
                        side     = perp_args["side"]
                        size_usd = perp_args["size_usd"]
                        leverage = perp_args["leverage"]
                        preview  = preview_order(symbol, side, size_usd, leverage)
                        if not preview:
                            text = (f"**{symbol}** not found on Hyperliquid. "
                                    f"Try BTC, ETH, SOL, or another supported perp.")
                        else:
                            _pending[req.session_id] = {
                                "type": "perp",
                                **perp_args,
                                "entry_price": preview["entry_price"],
                                "quantity": preview["quantity"],
                                "wallet_id": w.id,
                                "wallet_chain": w.chain,
                                "wallet_encrypted_key": w.encrypted_key,
                                "wallet_address": w.address,
                            }
                            liq = preview["liquidation_price"]
                            text = (
                                f"**{side.upper()} {symbol}** perpetual\n"
                                f"Size: **${size_usd:,.0f}**  ·  Leverage: **{leverage:.0f}x**\n"
                                f"Entry: **${preview['entry_price']:,.2f}**  ·  "
                                f"Margin: **${preview['margin_required']:,.2f}**\n"
                                f"Liq. price: **${liq:,.2f}**  ·  Fee: ~${preview['fee_usd']:.2f}\n\n"
                                f"⚠ Leveraged position — losses can exceed your deposit. "
                                f"Liquidation at ${liq:,.2f}.\n\n"
                                f"Type **CONFIRM** to open or **CANCEL** to abort."
                            )

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
                else:
                    text = result
                full_response = text
                for chunk in _chunk(text):
                    yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
            else:
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

            if chain == "evm":
                tx_hash = evm_chain.send_tx(plain_key, to_addr, amount, network)
            else:
                tx_hash = sol_chain.send_tx(bytes.fromhex(plain_key), to_addr, amount)

            db.add(Transaction(
                wallet_id=pending["wallet_id"], chain=chain, tx_hash=tx_hash,
                to_address=to_addr, amount=amount, status="confirmed",
                timestamp=datetime.utcnow(),
            ))
            db.commit()
            token_sym = pending.get("token") or network.upper()
            text = f"✅ Sent **{amount} {token_sym}**!\nTx hash: `{tx_hash}`"
        except Exception as e:
            text = f"Send failed: {e}"
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
                yield f"data: {json.dumps({'token': f'Approval tx: `{approve_hash}`\\n', 'done': False})}\n\n"

            swap_data = get_swap_tx(price_route, src_addr, dst_addr, src_amount, dest_amount, wallet_addr, network)
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
                text = f"Order failed: {result.get('error', 'unknown error')}"
        except Exception as e:
            text = f"Order failed: {e}"
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
