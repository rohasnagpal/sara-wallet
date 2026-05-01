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


def _detect_intent(msg: str, db: Session) -> Optional[tuple[str, dict]]:
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
        # Resolve to_addr nickname → real address
        to_nickname = None
        ab_entry = db.query(AddressBook).filter(AddressBook.nickname == to_addr.lower()).first()
        if ab_entry:
            to_nickname = to_addr
            to_addr = ab_entry.address
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

    return None


def _handle_tool_call(tool_name: str, args: dict, db: Session) -> str:
    if tool_name == "send_no_wallets":
        return "You don't have any wallets yet. Use the **+** button to create one first."

    if tool_name == "send_needs_wallet":
        names = ", ".join(f"**{n}**" for n in args["wallets"])
        return (f"Which wallet should I send **{args['amount']} {args['token']}** from?\n"
                f"Your wallets: {names}\n"
                f"Reply with e.g. \"send {args['amount']} {args['token']} from {args['wallets'][0]} to {args['to']}\"")

    if tool_name == "show_help":
        return (
            "Here's what SARA can do:\n\n"
            "**Wallets**\n"
            "• Create & import EVM wallets (Ethereum, Arbitrum, Base, Optimism, Polygon) and Solana\n"
            "• Check balance on any supported network\n"
            "• Send crypto — say \"send 0.1 ETH from Main to 0x...\" and type CONFIRM\n\n"
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
            "**Coming Soon**\n"
            "• Hyperliquid perpetuals trading\n"
            "• 1inch token swaps (EVM)\n"
            "• Jupiter swaps on Solana\n"
            "• Portfolio P&L tracking across wallets\n"
            "• Bitcoin wallet support\n"
            "• Price alerts & notifications"
        )

    if tool_name == "list_wallets":
        wallets = db.query(Wallet).all()
        if not wallets:
            return "No wallets added yet. Ask me to create one!"
        lines = []
        for w in wallets:
            lines.append(f"**{w.name}** · {w.chain.upper()}")
            lines.append(f"`{w.address}`")
        return "\n".join(lines)

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
            intent = _detect_intent(msg, db)
            if intent:
                tool_name, args = intent
                result = _handle_tool_call(tool_name, args, db)
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
