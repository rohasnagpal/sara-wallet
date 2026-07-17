import json, re, time
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from app.db.session import get_db
from app.db.models import ChatMessage, Wallet, AddressBook
from app.llm.litellm_client import sara_llm
from app.llm.prompts import SARA_SYSTEM_PROMPT
from app.tools.market import coingecko, gas_tracker
from app.core.session_auth import require_session

router = APIRouter()

PENDING_TTL_SECONDS = 600  # 10 minutes


class _PendingStore(dict):
    """Every assignment site does a full dict-literal replacement (never an
    incremental mutation), including multi-step flows like
    choose_send_wallet → the actual send pending — so stamping "now" on
    every __setitem__ is correct everywhere: a stray CONFIRM typed long
    after the user abandoned a flow (and forgot about it) shouldn't
    silently execute a stale send/swap/bridge against out-of-date
    balances/quotes/context. One central place instead of a timestamp
    added at each of the dozen call sites that build a pending dict."""
    def __setitem__(self, key, value):
        if isinstance(value, dict) and "_created_at" not in value:
            value = {**value, "_created_at": time.time()}
        super().__setitem__(key, value)


# In-memory pending transaction store keyed by session_id
_pending: dict[str, dict] = _PendingStore()

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
    reference: Optional[str] = None  # set by the frontend when this message pays a scanned/opened payment request
    confirmation_passphrase: Optional[str] = None  # used only for an exact CONFIRM; never persisted


def _resolve_wallet(name: str, db: Session) -> Optional[Wallet]:
    return db.query(Wallet).filter(Wallet.name == name).first()

def _match_wallet(text: str, wallets: list) -> Optional[Wallet]:
    """Find a wallet whose name appears in the user's message
    (case-insensitive). Prefers the longest matching name over whichever
    wallet happens to be checked first — with wallets named "Main" and
    "Main Sol", a message mentioning "Main Sol" used to resolve to "Main"
    instead, since "main" is trivially a substring of "main sol" too and DB
    iteration order (not specificity) decided which one won."""
    text_l = text.lower()
    candidates = [w for w in wallets if w.name.lower() in text_l]
    if not candidates:
        return None
    return max(candidates, key=lambda w: len(w.name))

_TOKEN_TO_NETWORK = {
    "eth": "ethereum", "ether": "ethereum", "ethereum": "ethereum",
    "matic": "polygon", "pol": "polygon", "polygon": "polygon",
    "arb": "arbitrum", "arbitrum": "arbitrum",
    "base": "base",
    "op": "optimism", "optimism": "optimism",
    "sol": "solana", "solana": "solana",
    "bnb": "bsc",
    "avax": "avalanche", "avalanche": "avalanche",
    "trx": "tron", "tron": "tron",
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
    "tron": "TRX",
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
    if network == "tron":
        from app.chains.tron import is_valid_address
        return is_valid_address(address)
    return _is_valid_evm_recipient(address)


def _is_valid_evm_recipient(address: str) -> bool:
    """Purely-lowercase or purely-uppercase addresses carry no checksum
    information (many exchanges/services produce them that way), so those
    are accepted as-is — same convention EIP-55 itself recommends and what
    MetaMask does. But a MIXED-case address is claiming to be checksummed,
    and if that checksum doesn't actually verify, it's a strong signal of a
    single-character typo (checksums exist precisely to catch this) — those
    get rejected rather than silently sent to a possibly-wrong address."""
    if not _ADDRESS_RE.fullmatch(address or ""):
        return False
    body = address[2:]
    if body == body.lower() or body == body.upper():
        return True
    from web3 import Web3
    return Web3.is_checksum_address(address)


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
    """Exact case-insensitive match on a user-typed wallet-selection reply.
    Uses func.lower(...) == rather than ilike() specifically because ilike
    treats % and _ in the input as SQL wildcards — a wallet name containing
    an underscore (a common naming choice) typed back verbatim could then
    match a completely different, unintended wallet at that character
    position, which matters here since the match picks which wallet a send
    executes from."""
    msg_l = msg.strip().lower()
    if not msg_l:
        return None
    return db.query(Wallet).filter(func.lower(Wallet.name) == msg_l).first()


def _detect_intent(msg: str, db: Session, session_id: str = "default") -> Optional[tuple[str, dict]]:
    """Fast keyword-based intent detection for common wallet queries."""
    m = msg.lower()
    wallets = db.query(Wallet).all()

    # help / capabilities
    if any(p in m for p in ("what can you do", "what do you do", "your capabilities", "what are you capable", "what can sara", "how do you work", "help me understand", "what features", "how to use sara", "how do i use sara")):
        return ("show_help", {})

    # send / transfer  — parse: (send|transfer) <amount> <token> [from <wallet>] to <address> [on <network>]
    # Matched against the original-case msg (not the lowercased m) so
    # case-sensitive base58 addresses (Solana, Tron) survive intact —
    # re.IGNORECASE keeps the send/from/to keywords matching either way.
    send_match = re.search(
        r'(?:send|transfer)\s+([\d.]+)\s+(\w+)(?:\s+from\s+(\w[\w\s]*?))?\s+to\s+(\S+)(?:\s+on\s+(\w+))?$',
        msg, re.IGNORECASE
    )
    if send_match:
        amount_str, token, from_hint, to_addr, net_hint = send_match.groups()
        try:
            amount = float(amount_str)
        except ValueError:
            amount = 0
        network = _TOKEN_TO_NETWORK.get(token.lower())
        if net_hint and network is not None:
            # ETH is native on 4 different networks (ethereum, arbitrum,
            # base, optimism) — the plain token→network lookup above always
            # picks ethereum mainnet, which silently ignored an explicit
            # "on arbitrum"/"on base"/"on optimism" and sent on the wrong
            # chain. An explicit hint must win whenever the token really is
            # native on that hinted network.
            hinted_network = net_hint.lower()
            if _NETWORK_NATIVE_TOKEN.get(hinted_network) == token.upper():
                network = hinted_network
        token_address = None
        token_decimals = None
        token_corrected_from = None
        if network is None:
            # Not a native chain symbol — check if it's a recognized ERC-20
            # (or, on Tron, TRC20) token instead. Network defaults to Ethereum
            # unless the user named one, same convention swaps already use.
            # Only ever resolves to Sara's verified contract list — a typo
            # like "UDST" can correct to USDT, but never to a different asset.
            resolve_network = (net_hint or "ethereum").lower()
            if resolve_network == "tron":
                from app.chains.tron import resolve_trc20_with_correction
                token_result, corrected = resolve_trc20_with_correction(token)
            elif resolve_network == "solana":
                # Paraswap (the else branch below) only knows EVM chains, so
                # an SPL token symbol like USDC would never resolve here
                # without this — meaning a Solana payment request for
                # anything but native SOL could never actually be paid.
                from app.tools.market.jupiter import resolve_mint_with_correction, get_decimals
                mint, corrected = resolve_mint_with_correction(token)
                token_result = (mint, get_decimals(corrected or token)) if mint else None
            else:
                from app.tools.market.paraswap import resolve_token_with_correction
                token_result, corrected = resolve_token_with_correction(token, resolve_network)
            if token_result:
                token_address, token_decimals = token_result
                network = resolve_network
                if corrected:
                    token_corrected_from = token.upper()
                    token = corrected
        if network is None:
            resolve_network = (net_hint or "ethereum").lower()
            if resolve_network == "tron":
                from app.chains.tron import trusted_trc20_symbols
                trusted = ["TRX"] + trusted_trc20_symbols()
            else:
                from app.tools.market.paraswap import trusted_symbols
                trusted = trusted_symbols(resolve_network)
            supported = ", ".join(trusted) if trusted else "USDC, USDT"
            return ("send_rejected", {
                "message": f"I don't recognize **{token.upper()}** as a token I can send. "
                           f"Native chain tokens (ETH, POL, SOL, BNB, AVAX, TRX...) or Sara's verified tokens on "
                           f"{resolve_network.capitalize()}: {supported}. "
                           f"Sara only ever sends to contracts on this trusted list — see the 🛡️ Trusted Tokens pill for the full set."
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
            required_entry_chain = network if network in ("solana", "tron") else "evm"
            if ab_entry.chain != required_entry_chain:
                return ("send_rejected", {
                    "message": f"**{to_nickname}** is a {ab_entry.chain.upper()} directory entry, "
                               f"not a {required_entry_chain.upper()} recipient."
                })
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
        required_chain = network if network in ("solana", "tron") else "evm"
        compatible_wallets = [w for w in wallets if w.chain == required_chain]
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
                    "token_corrected_from": token_corrected_from,
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
                    "token_corrected_from": token_corrected_from,
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
        network = (net_hint or _TOKEN_TO_NETWORK.get(from_tok.lower()) or "ethereum")
        # Resolve the wallet from only the chain this swap actually needs —
        # matching against every wallet regardless of chain let a swap
        # requested on one chain (e.g. Solana) silently resolve to a wallet
        # on a different chain (e.g. an EVM wallet) whenever its name
        # happened to match, with the mismatch only surfacing later (if at
        # all) when actually trying to execute against the wrong chain.
        required_chain = network if network in ("solana", "tron") else "evm"
        compatible_wallets = [w for w in wallets if w.chain == required_chain]
        wallet = None
        if from_hint:
            wallet = _match_wallet(from_hint, compatible_wallets)
        if not wallet:
            wallet = _match_wallet(msg, compatible_wallets)
        if not wallet and len(compatible_wallets) == 1:
            wallet = compatible_wallets[0]
        if amount > 0 and from_tok and to_tok:
            if wallet:
                return ("swap_tokens", {
                    "wallet_name": wallet.name,
                    "from_token": from_tok.upper(),
                    "to_token":   to_tok.upper(),
                    "amount":     amount,
                    "network":    network,
                })
            elif compatible_wallets:
                return ("swap_needs_wallet", {
                    "from_token": from_tok.upper(),
                    "to_token":   to_tok.upper(),
                    "amount":     amount,
                    "network":    network,
                    "wallets":    [w.name for w in compatible_wallets],
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
        # Bridging (LI.FI) is EVM-only — same reasoning as the swap intent
        # above: matching against every wallet regardless of chain let this
        # resolve to a Solana/Tron wallet that could never actually bridge.
        evm_wallets_for_bridge = [w for w in wallets if w.chain == "evm"]
        wallet = None
        if from_hint:
            wallet = _match_wallet(from_hint, evm_wallets_for_bridge)
        if not wallet:
            wallet = _match_wallet(msg, evm_wallets_for_bridge)
        if not wallet and len(evm_wallets_for_bridge) == 1:
            wallet = evm_wallets_for_bridge[0]
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
            elif evm_wallets_for_bridge:
                return ("bridge_needs_wallet", {**bridge_args, "wallets": [w.name for w in evm_wallets_for_bridge]})
            else:
                return ("send_no_wallets", {})

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

    # payment link / payment request — checked BEFORE crypto price matching,
    # since "link" is a substring match for Chainlink (LINK) and "create"
    # contains "at" (a crypto-price trigger word); same substring-collision
    # class of bug as the old Polymarket-vs-"pol" issue.
    pay_link_match = re.search(
        r'(?:create\s+a\s+|make\s+a\s+|generate\s+a\s+)?(?:payment\s+link|payment\s+request|request\s+payment)'
        r'(?:\s+for)?\s+([\d.]+)\s+(\w+)(?:\s+from\s+(\w[\w\s]*?))?$',
        m
    )
    if pay_link_match:
        amount_str, token, from_hint = pay_link_match.groups()
        try:
            amount = float(amount_str)
        except ValueError:
            amount = 0
        wallet = _match_wallet(from_hint, wallets) if from_hint else None
        if not wallet and len(wallets) == 1:
            wallet = wallets[0]
        if amount <= 0:
            return ("send_rejected", {"message": "Enter an amount greater than zero for the payment link."})
        if not wallet:
            if wallets:
                return ("payment_link_needs_wallet", {"amount": amount, "token": token.upper(), "wallets": [w.name for w in wallets]})
            return ("send_no_wallets", {})
        return ("create_payment_link", {"wallet_name": wallet.name, "amount": amount, "token": token.upper()})

    # market: crypto price — also detect "X price in Y" currency modifier
    CRYPTO_KEYWORDS = ("price", "how much is", "what is", "what's", "whats", "cost", "worth", "at", "doing")
    KNOWN_SYMBOLS = set(coingecko.SYMBOL_TO_ID.keys()) | {"BITCOIN", "ETHEREUM", "SOLANA"}
    # Check for "in <currency>" modifier first
    vs_currency = "usd"
    vs_match = re.search(r'\bin\s+([a-z]{2,4})\b', m)
    if vs_match:
        code = vs_match.group(1)
        if code in ("usd", "eur", "gbp", "inr", "aud", "cad", "chf", "jpy"):
            vs_currency = code
    if any(re.search(rf'\b{re.escape(k)}\b', m) for k in CRYPTO_KEYWORDS):
        for sym in KNOWN_SYMBOLS:
            # Word-boundary match, not raw substring — several real symbols
            # (OP, SOL, UNI, TON, ADA...) are short enough to appear inside
            # unrelated words ("shOPping", "reSOLve", "cotton"), and with
            # generic trigger words like "at"/"doing" above, a message with
            # nothing to do with crypto could otherwise misfire into a price
            # lookup.
            if re.search(rf'\b{re.escape(sym.lower())}\b', m):
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
            if re.search(rf'\b{re.escape(sym.lower())}\b', m):
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
        note = (f"📝 I read **{args['token_corrected_from']}** as **{args['token']}** — Sara only sends to verified contracts.\n\n"
                if args.get("token_corrected_from") else "")
        return (f"{note}Which wallet should I send **{args['amount']} {args['token']}** from?\n"
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

        evm_only = chain_list.rsplit(', Solana', 1)[0]

        return (
            "**Sara specializes in stablecoin payments** — sending, requesting, and moving USDC/USDT across chains "
            "as easily as sending a text. Here's everything Sara can do:\n\n"
            "**Payments** *(Sara's core)*\n"
            "• Send crypto with plain English — \"send 100 USDC to zara\" — then type CONFIRM\n"
            "• \"payment link for 10 USDC\" — a shareable link + QR code requesting payment into one of your wallets\n"
            "• 📷 Scan to Pay — scan someone else's Sara payment QR to pre-fill a send\n"
            "• 📋 Payment Requests — Sara checks on-chain automatically for a matching incoming transfer and marks requests paid; export them all as a CSV\n"
            "• Bridge stablecoins across chains — \"bridge 1 USDC from polygon to arbitrum\"\n"
            "• Swap tokens on EVM (via Paraswap) or Solana (via Jupiter) — \"swap 1 POL for USDC\"\n\n"
            "**Wallets & Chains**\n"
            f"• Create & import wallets across EVM ({evm_only}), Solana, and Tron\n"
            "• Check balance on any supported network\n"
            "• Address book — save nicknames, send to them by name\n\n"
            "**bNames** — a human-readable name for your wallet\n"
            "• \"buy a bname\" or \"register rohas.sara\" — pay a small fee, get a name like `rohas.sara` linked to your wallet\n"
            "• Send to a bName directly, same as `alice.eth` or `bob.sol`\n\n"
            "**Market Data** *(live via CoinGecko)*\n"
            "• Crypto prices, gas fees, trending coins, global market cap\n\n"
            "**Intelligence**\n"
            "• News & sentiment, ENS/SNS/bName resolution\n\n"
            "**Voice mode** — click the mic next to the chat box to speak instead of type (English only for now). "
            "For your safety, CONFIRM must always be typed, never spoken.\n\n"
            "**Security**\n"
            "• Sara locks like a normal wallet — your passphrase unlocks it, and it auto-locks after 1 hour of inactivity\n"
            "• Only money-moving actions (send, swap, bName registration) require unlocking — price checks and general chat work while locked\n"
            "• 🛡️ Trusted Tokens — Sara only ever sends, swaps, or bridges to a verified contract list, so a fake token sharing a symbol like USDT can't be substituted in. See the pill in the sidebar for the full list. Typos in a token symbol (like \"udst\") are auto-corrected against that same list\n\n"
            "---\n"
            "**Your current setup**\n"
            f"• Wallet lock: {lock_status}\n"
            f"• Wallets added: {wallet_count}\n"
            f"• AI model: {ai_status}\n"
            f"• CoinGecko API key: {_flag('COINGECKO_API_KEY')}\n"
            f"• Alchemy API key (ERC-20 balances + EVM payment reconciliation): {_flag('ALCHEMY_API_KEY')}\n"
            f"• Helius RPC (Solana): {_flag('HELIUS_RPC')}\n"
            f"• TronGrid API key (Tron USDT balances/sends + reconciliation): {_flag('TRONGRID_API_KEY')}\n"
            f"• bName registration: {'✅ ready' if bname_ready else '— not set up yet (needs a deployed registrar service, see registrar-service/DEPLOYMENT.md)'}\n"
            f"• Chains available: EVM ({evm_only}), Solana, Tron"
        )

    if tool_name == "list_wallets":
        wallets = db.query(Wallet).all()
        if not wallets:
            return "No wallets added yet. Ask me to create one!"
        from app.chains import evm as evm_chain, solana as sol_chain, tron as tron_chain
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
            elif w.chain == "tron":
                found_any = False
                try:
                    b = tron_chain.get_balance(w.address)
                    if b["balance"] > 0.000001:
                        card.append(f"{b['balance']:.6f} **TRX**")
                        found_any = True
                except Exception:
                    card.append("TRX balance unavailable")
                try:
                    usdt = tron_chain.get_trc20_balance(w.address, "USDT")
                    if usdt["balance"] > 0:
                        card.append(f"{usdt['balance']:.6f} **USDT**")
                        found_any = True
                except Exception:
                    pass
                if not found_any and len(card) == 1:
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

    if tool_name == "payment_link_needs_wallet":
        names = ", ".join(f"**{n}**" for n in args["wallets"])
        return (f"Which wallet should receive **{args['amount']} {args['token']}**?\n"
                f"Your wallets: {names}\n"
                f"Reply with e.g. \"payment link for {args['amount']} {args['token']} from {args['wallets'][0]}\"")

    if tool_name == "create_payment_link":
        from app.tools.payments.links import create_payment_request
        w = _resolve_wallet(args["wallet_name"], db)
        if not w:
            return f"Wallet '{args['wallet_name']}' not found."
        network = "solana" if w.chain == "solana" else "tron" if w.chain == "tron" else "ethereum"
        row, result = create_payment_request(db, w, network, args["token"], args["amount"])
        if row is None:
            return (f"{result}. Check the 🛡️ Trusted Tokens panel for what's supported, or check your spelling.")
        payload = result
        return (
            f"Payment request **{row.reference}** ready — requesting **{args['amount']} {row.token}** into **{w.name}**.\n\n"
            f"Share this with whoever's paying: add `/?pay={payload}` to your Sara's address "
            f"(e.g. `http://127.0.0.1:8888/?pay={payload}`) — they open it in their own Sara to pre-fill the send.\n\n"
            f"Open the 🔗 Payment Links panel for a copy-paste link + QR, or 📋 Payment Requests to track and export it."
        )

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
        if pending.get("token_address") and w.chain == "tron":
            from app.chains import tron as tron_chain
            preview = tron_chain.get_trc20_transfer_preview(w.address, pending["amount"], token_sym)
            if not preview["has_token_funds"]:
                return _stream_text(
                    f"Insufficient balance. **{pending['wallet_name']}** has "
                    f"**{preview['token_balance']:.6f} {token_sym}**, but you asked to send "
                    f"**{pending['amount']} {token_sym}**.",
                    db, session_id,
                )
            if not preview["has_gas_funds"]:
                return _stream_text(
                    f"Insufficient **{preview['native_unit']}** for fees. **{pending['wallet_name']}** has "
                    f"**{preview['native_balance']:.6f} {preview['native_unit']}**, but this send needs "
                    f"~**{preview['gas_fee']:.6f} {preview['native_unit']}** for energy/bandwidth.",
                    db, session_id,
                )
            balance_line = (
                f"Token balance: **{preview['token_balance']:.6f} {token_sym}**\n"
                f"Estimated fee: **{preview['gas_fee']:.6f} {preview['native_unit']}** "
                f"(from your {preview['native_unit']} balance, not {token_sym})\n"
            )
        elif pending.get("token_address") and w.chain == "solana":
            from app.chains import solana as sol_chain
            from app.core.amounts import to_base_units
            token_balance = sol_chain.get_spl_token_balance(
                w.address, pending["token_address"], pending["token_decimals"],
            )
            requested_raw = to_base_units(pending["amount"], pending["token_decimals"], token_sym)
            if token_balance["raw_balance"] < requested_raw:
                return _stream_text(
                    f"Insufficient balance. **{pending['wallet_name']}** has "
                    f"**{token_balance['balance']:.6f} {token_sym}**, but you asked to send "
                    f"**{pending['amount']} {token_sym}**.",
                    db, session_id,
                )
            spl_preview = sol_chain.get_spl_transfer_preview(w.address, pending["to"], pending["token_address"])
            if not spl_preview["has_funds"]:
                rent_note = " (including rent for the recipient's new token account)" if spl_preview["needs_new_ata"] else ""
                return _stream_text(
                    f"**{pending['wallet_name']}** has **{spl_preview['sol_balance']:.6f} SOL**, but this "
                    f"send needs ~**{spl_preview['required_sol']:.6f} SOL** for network fees{rent_note}.",
                    db, session_id,
                )
            balance_line = (
                f"Token balance: **{token_balance['balance']:.6f} {token_sym}**\n"
                f"SOL balance (for fees{' + new account rent' if spl_preview['needs_new_ata'] else ''}): "
                f"**{spl_preview['sol_balance']:.6f} SOL**\n"
            )
        elif pending.get("token_address"):
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
        elif w.chain == "evm":
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
        elif w.chain == "tron":
            from app.chains import tron as tron_chain
            bal = tron_chain.get_native_transfer_preview(w.address, pending["amount"])
            balance_line = (
                f"Balance: **{bal['balance']:.6f} {bal['unit']}**\n"
                f"Estimated fee: **{bal['fee']:.6f} {bal['unit']}**\n"
            )
            if not bal["has_funds"]:
                return _stream_text(
                    f"Insufficient balance. **{pending['wallet_name']}** has "
                    f"**{bal['balance']:.6f} {bal['unit']}**, but this send needs "
                    f"**{bal['total']:.6f} {bal['unit']}** including fees.",
                    db,
                    session_id,
                )
        elif w.chain == "solana":
            from app.chains import solana as sol_chain
            bal = sol_chain.get_native_transfer_preview(w.address, pending["amount"])
            balance_line = (
                f"Balance: **{bal['balance']:.6f} {bal['unit']}**\n"
                f"Estimated fee: **{bal['fee']:.6f} {bal['unit']}**\n"
            )
            if not bal["has_funds"]:
                return _stream_text(
                    f"Insufficient balance. **{pending['wallet_name']}** has "
                    f"**{bal['balance']:.6f} {bal['unit']}**, but this send needs "
                    f"**{bal['total']:.6f} {bal['unit']}** including the network fee.",
                    db,
                    session_id,
                )
        else:
            from app.tools.wallet.balance import get_wallet_balance
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
    correction_note = ""
    if pending.get("token_corrected_from"):
        correction_note = (
            f"📝 I read **{pending['token_corrected_from']}** as **{token_sym}** — "
            f"Sara only sends to verified contracts.\n\n"
        )
    text = (
        f"{correction_note}"
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

    correction_note = ""
    if w.chain == "solana" or network == "solana":
        from app.tools.market.jupiter import (
            resolve_mint_with_correction, trusted_symbols,
            get_quote as jup_quote,
            get_decimals as jup_dec,
            confirmation_safety_limits,
        )
        src_mint, src_corrected = resolve_mint_with_correction(src_sym)
        dst_mint, dst_corrected = resolve_mint_with_correction(dst_sym)
        if src_corrected or dst_corrected:
            if src_corrected:
                correction_note += f"📝 I read **{src_sym}** as **{src_corrected}** — Sara only swaps verified tokens.\n"
                src_sym = src_corrected
            if dst_corrected:
                correction_note += f"📝 I read **{dst_sym}** as **{dst_corrected}** — Sara only swaps verified tokens.\n"
                dst_sym = dst_corrected
            correction_note += "\n"
        if not src_mint or not dst_mint:
            supported = ", ".join(trusted_symbols())
            return None, f"**{src_sym}** or **{dst_sym}** not supported on Solana. Supported: {supported}"
        src_dec = jup_dec(src_sym)
        dst_dec = jup_dec(dst_sym)
        from app.core.amounts import to_base_units
        amount_raw = to_base_units(amount, src_dec, src_sym)
        quote = jup_quote(src_mint, dst_mint, amount_raw)
        if not (quote and "outAmount" in quote):
            err = quote.get("error", "unknown error") if quote else "Jupiter API unavailable"
            return None, f"Could not get Solana swap quote: {err}"
        dst_amount = int(quote["outAmount"]) / 10 ** dst_dec
        max_fee_lamports, max_rent_lamports = confirmation_safety_limits()
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
            f"{correction_note}"
            f"Swap **{amount} {src_sym} → ~{dst_amount:.4f} {dst_sym}**\n"
            f"Network: **Solana**  ·  Wallet: **{w.name}**\n"
            f"Slippage: 0.5%\n"
            f"Maximum network fee: **{max_fee_lamports / 1_000_000_000:.6f} SOL**\n"
            f"Maximum temporary/account rent debit: **{max_rent_lamports / 1_000_000_000:.6f} SOL**\n\n"
            f"Type **CONFIRM** to execute or **CANCEL** to abort."
        )
        return pending, text

    # EVM → Paraswap
    from app.tools.market.paraswap import (
        resolve_token_with_correction, trusted_symbols, get_quote, CHAIN_IDS,
        max_total_network_fee_wei, NATIVE_SYMBOLS, _NATIVE as PARASWAP_NATIVE,
    )
    src_result, src_corrected = resolve_token_with_correction(src_sym, network)
    dst_result, dst_corrected = resolve_token_with_correction(dst_sym, network)
    if src_corrected or dst_corrected:
        if src_corrected:
            correction_note += f"📝 I read **{src_sym}** as **{src_corrected}** — Sara only swaps verified tokens.\n"
            src_sym = src_corrected
        if dst_corrected:
            correction_note += f"📝 I read **{dst_sym}** as **{dst_corrected}** — Sara only swaps verified tokens.\n"
            dst_sym = dst_corrected
        correction_note += "\n"
    if not src_result or not dst_result:
        supported = ", ".join(trusted_symbols(network))
        return None, (f"I don't recognise **{src_sym}** or **{dst_sym}** on "
                       f"{network.capitalize()}. Supported: {supported}.")
    src_addr, src_dec = src_result
    dst_addr, dst_dec = dst_result
    from app.core.amounts import to_base_units
    amount_wei = to_base_units(amount, src_dec, src_sym)
    quote = get_quote(src_addr, src_dec, dst_addr, dst_dec, amount_wei, network)
    if not (quote and "priceRoute" in quote):
        err = quote.get("error", "unknown error") if quote else "Paraswap API unavailable"
        return None, f"Could not get swap quote: {err}"
    price_route = quote["priceRoute"]
    dst_amount = int(price_route.get("destAmount", 0)) / (10 ** dst_dec)
    max_fee = max_total_network_fee_wei(src_addr) / 10 ** 18
    fee_unit = NATIVE_SYMBOLS.get(network, "ETH")
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
        f"{correction_note}"
        f"Swap **{amount} {src_sym} → ~{dst_amount:.4f} {dst_sym}**\n"
        f"Network: **{network.capitalize()}**  ·  Wallet: **{w.name}**\n"
        f"Slippage: 1%\n"
        f"Maximum total network fees: **{max_fee:.6f} {fee_unit}**"
        f"{' (up to two approval transactions plus the swap)' if src_addr.lower() != PARASWAP_NATIVE.lower() else ''}\n\n"
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

    correction_note = ""
    src_result, src_corrected = lifi.resolve_token_with_correction(src_sym, from_network)
    dst_result, dst_corrected = lifi.resolve_token_with_correction(dst_sym, to_network)
    if src_corrected or dst_corrected:
        if src_corrected:
            correction_note += f"📝 I read **{src_sym}** as **{src_corrected}** — Sara only bridges verified tokens.\n"
            src_sym = src_corrected
        if dst_corrected:
            correction_note += f"📝 I read **{dst_sym}** as **{dst_corrected}** — Sara only bridges verified tokens.\n"
            dst_sym = dst_corrected
        correction_note += "\n"
    if not src_result or not dst_result:
        return None, (f"I don't recognise **{src_sym}** on {from_network.capitalize()} or "
                       f"**{dst_sym}** on {to_network.capitalize()}.")
    src_addr, src_dec = src_result
    dst_addr, dst_dec = dst_result
    from app.core.amounts import to_base_units
    amount_wei = to_base_units(amount, src_dec, src_sym)

    quote = lifi.get_quote(from_network, to_network, src_addr, dst_addr, amount_wei, w.address)
    if not (quote and quote.get("transactionRequest")):
        err = quote.get("message", "no route found") if quote else "LI.FI API unavailable"
        return None, f"Could not get a bridge quote: {err}"

    estimate = quote["estimate"]
    dst_amount = int(estimate["toAmount"]) / (10 ** dst_dec)
    duration_min = estimate.get("executionDuration", 0) / 60
    tool_name = quote.get("toolDetails", {}).get("name", quote.get("tool", "a bridge"))
    from app.chains.evm import _NATIVE_TOKEN
    expected_src_token = None if src_addr.lower() == lifi._NATIVE.lower() else src_addr
    max_fee = lifi.max_total_network_fee_wei(expected_src_token) / 10 ** 18
    fee_unit = _NATIVE_TOKEN.get(from_network, "ETH")

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
        "dst_amount_wei": int(estimate["toAmount"]),
        "wallet_id": w.id,
        "wallet_chain": w.chain,
        "wallet_encrypted_key": w.encrypted_key,
    }
    text = (
        f"{correction_note}"
        f"Bridge **{amount} {src_sym} ({from_network.capitalize()}) → "
        f"~{dst_amount:.4f} {dst_sym} ({to_network.capitalize()})**\n"
        f"Via: **{tool_name}**  ·  Wallet: **{w.name}**\n"
        f"Est. time: **~{duration_min:.0f} min**  ·  Slippage: 0.5%\n"
        f"Maximum total source-chain network fees: **{max_fee:.6f} {fee_unit}**"
        f"{' (up to two approval transactions plus the bridge)' if expected_src_token else ''}\n\n"
        f"⚠ Cross-chain transfers take longer than same-chain swaps and route through a third-party "
        f"bridge — funds arrive on {to_network.capitalize()} once the bridge finishes, not instantly.\n\n"
        f"Type **CONFIRM** to execute or **CANCEL** to abort."
    )
    return pending, text


@router.post("/chat", dependencies=[Depends(require_session)])
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    msg = req.message.strip()
    db.add(ChatMessage(session_id=req.session_id, role="user", content=msg))
    db.commit()

    # CONFIRM flow
    if req.session_id in _pending:
        pending = _pending[req.session_id]
        if time.time() - pending.get("_created_at", 0) > PENDING_TTL_SECONDS:
            del _pending[req.session_id]
            return _stream_text(
                "That pending action expired for safety (quotes/balances can go stale) — please start again.",
                db, req.session_id,
            )
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
                    "token_corrected_from": pending.get("token_corrected_from"),
                    "reference": pending.get("reference"),
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
        if pending.get("type") == "choose_payment_link_wallet":
            if msg.upper().startswith("CANCEL"):
                del _pending[req.session_id]
                return _stream_text("Cancelled.", db, req.session_id)
            selected_wallet = _wallet_named(msg, db)
            if selected_wallet and selected_wallet.name in pending["wallets"]:
                del _pending[req.session_id]
                result = _handle_tool_call(
                    "create_payment_link",
                    {"wallet_name": selected_wallet.name, "amount": pending["amount"], "token": pending["token"]},
                    db,
                )
                return _stream_text(result, db, req.session_id)
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
            from app.tools.wallet import lock as lock_state
            try:
                authorized = lock_state.confirm_passphrase(req.confirmation_passphrase or "")
            except lock_state.WalletThrottledError as e:
                return _stream_text(str(e), db, req.session_id)
            except lock_state.WalletLockedError as e:
                return _stream_text(str(e), db, req.session_id)
            if not authorized:
                return _stream_text(
                    "Incorrect passphrase — transaction was not submitted. Type CONFIRM to try again or CANCEL.",
                    db, req.session_id,
                )
            del _pending[req.session_id]
            ptype = pending.get("type")
            if ptype == "swap":
                return _stream_swap(pending, db, req.session_id)
            if ptype == "sol_swap":
                return _stream_sol_swap(pending, db, req.session_id)
            if ptype == "bridge":
                return _stream_bridge(pending, db, req.session_id)
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
                if req.reference and tool_name in ("send_crypto", "send_needs_wallet"):
                    args["reference"] = req.reference
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
                            if send_args.get("token_address") and w.chain == "tron":
                                from app.chains import tron as tron_chain
                                preview = tron_chain.get_trc20_transfer_preview(w.address, send_args["amount"], token_sym)
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
                                        f"Insufficient **{preview['native_unit']}** for fees. **{send_args['wallet_name']}** has "
                                        f"**{preview['native_balance']:.6f} {preview['native_unit']}**, but this send needs "
                                        f"~**{preview['gas_fee']:.6f} {preview['native_unit']}** for energy/bandwidth."
                                    )
                                    full_response = text
                                    for chunk in _chunk(text):
                                        yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
                                    return
                                balance_line = (
                                    f"Token balance: **{preview['token_balance']:.6f} {token_sym}**\n"
                                    f"Estimated fee: **{preview['gas_fee']:.6f} {preview['native_unit']}** "
                                    f"(from your {preview['native_unit']} balance, not {token_sym})\n"
                                )
                            elif send_args.get("token_address") and w.chain == "solana":
                                from app.chains import solana as sol_chain
                                from app.core.amounts import to_base_units
                                token_balance = sol_chain.get_spl_token_balance(
                                    w.address, send_args["token_address"], send_args["token_decimals"],
                                )
                                sol_balance = sol_chain.get_balance(w.address)
                                requested_raw = to_base_units(
                                    send_args["amount"], send_args["token_decimals"], token_sym,
                                )
                                if token_balance["raw_balance"] < requested_raw:
                                    text = (
                                        f"Insufficient balance. **{send_args['wallet_name']}** has "
                                        f"**{token_balance['balance']:.6f} {token_sym}**, but you asked to send "
                                        f"**{send_args['amount']} {token_sym}**."
                                    )
                                    full_response = text
                                    for chunk in _chunk(text):
                                        yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
                                    return
                                if sol_balance["balance"] <= 0:
                                    text = f"**{send_args['wallet_name']}** has no SOL to pay network fees."
                                    full_response = text
                                    for chunk in _chunk(text):
                                        yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
                                    return
                                balance_line = (
                                    f"Token balance: **{token_balance['balance']:.6f} {token_sym}**\n"
                                    f"SOL balance (for fees): **{sol_balance['balance']:.6f} SOL**\n"
                                )
                            elif send_args.get("token_address"):
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
                            elif w.chain == "tron":
                                from app.chains import tron as tron_chain
                                bal = tron_chain.get_native_transfer_preview(w.address, send_args["amount"])
                                balance_line = (
                                    f"Balance: **{bal['balance']:.6f} {bal['unit']}**\n"
                                    f"Estimated fee: **{bal['fee']:.6f} {bal['unit']}**\n"
                                )
                                if not bal["has_funds"]:
                                    text = (
                                        f"Insufficient balance. **{send_args['wallet_name']}** has "
                                        f"**{bal['balance']:.6f} {bal['unit']}**, but this send needs "
                                        f"**{bal['total']:.6f} {bal['unit']}** including fees."
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
                        correction_note = ""
                        if send_args.get("token_corrected_from"):
                            correction_note = (
                                f"📝 I read **{send_args['token_corrected_from']}** as **{token_sym}** — "
                                f"Sara only sends to verified contracts.\n\n"
                            )
                        text = (
                            f"{correction_note}"
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
                    elif tool_name == "payment_link_needs_wallet":
                        _pending[req.session_id] = {"type": "choose_payment_link_wallet", **args}
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
        from app.chains import evm as evm_chain, solana as sol_chain, tron as tron_chain
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
            elif chain == "tron" and pending.get("token_address"):
                token_sym = pending.get("token") or "USDT"
                tx_hash = tron_chain.send_trc20(plain_key, to_addr, amount, token_sym)
            elif chain == "tron":
                balance = tron_chain.get_balance(pending["wallet_address"])
                if balance["balance"] < amount:
                    raise ValueError(
                        f"insufficient balance: {balance['balance']:.6f} {balance['unit']} available"
                    )
                tx_hash = tron_chain.send_trx(plain_key, to_addr, amount)
            elif chain == "solana" and pending.get("token_address"):
                from app.core.amounts import to_base_units
                token_balance = sol_chain.get_spl_token_balance(
                    pending["wallet_address"], pending["token_address"], pending["token_decimals"],
                )
                requested_raw = to_base_units(
                    amount, pending["token_decimals"], pending.get("token") or "token",
                )
                if token_balance["raw_balance"] < requested_raw:
                    raise ValueError(
                        f"insufficient token balance: {token_balance['balance']:.6f} available, {amount:.6f} required"
                    )
                sol_balance = sol_chain.get_balance(pending["wallet_address"])
                if sol_balance["balance"] <= 0:
                    raise ValueError("no SOL available to pay network fees")
                tx_hash = sol_chain.send_spl_token(
                    bytes.fromhex(plain_key), to_addr, amount,
                    pending["token_address"], pending["token_decimals"],
                )
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
                token=pending.get("token"), reference=pending.get("reference"),
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
        from app.tools.market.paraswap import (
            ensure_allowance, get_quote, get_swap_tx, execute_swap,
            validate_swap_transaction_static, _NATIVE,
        )
        try:
            plain_key   = decrypt_key(pending["wallet_encrypted_key"])
            network     = pending.get("network", "ethereum")
            src_addr    = pending["src_addr"]
            dst_addr    = pending["dst_addr"]
            src_dec     = pending.get("src_dec", 18)
            dst_dec     = pending.get("dst_dec", 18)
            amount_wei  = pending["amount_wei"]
            src_amount  = pending["src_amount"]
            from_tok    = pending["from_token"]
            to_tok      = pending["to_token"]
            wallet_addr = pending.get("wallet_address", "")

            if not wallet_addr:
                from app.chains.evm import get_web3
                w3 = get_web3(network)
                wallet_addr = w3.eth.account.from_key(plain_key).address

            # Re-quote right before executing rather than reusing the
            # preview-time quote — same reasoning as the LI.FI bridge flow:
            # the human-in-the-loop gap between seeing the preview and
            # typing CONFIRM can be long enough for the quote to go stale.
            quote = get_quote(src_addr, src_dec, dst_addr, dst_dec, amount_wei, network)
            if not (quote and "priceRoute" in quote):
                err = quote.get("error", "unknown error") if quote else "Paraswap API unavailable"
                raise Exception(f"could not refresh quote before executing — {err}")
            price_route = quote["priceRoute"]
            new_dst_amount = int(price_route.get("destAmount", 0))
            original_dst_amount = int(pending["dest_amount"])
            if original_dst_amount and new_dst_amount < original_dst_amount * 0.98:
                original_human = original_dst_amount / (10 ** dst_dec)
                new_human = new_dst_amount / (10 ** dst_dec)
                raise Exception(
                    f"the rate moved since you confirmed — you'd now get ~{new_human:.4f} {to_tok} "
                    f"instead of the ~{original_human:.4f} {to_tok} you approved. Please re-run the "
                    f"swap command to see the new rate and confirm again."
                )

            swap_data = get_swap_tx(price_route, src_addr, dst_addr, src_amount, wallet_addr, network)
            if not swap_data or "error" in swap_data:
                err = swap_data.get("error", "no calldata") if swap_data else "Paraswap API error"
                raise Exception(err)
            expected_value_wei = amount_wei if src_addr.lower() == _NATIVE.lower() else 0
            expected_src_token = None if src_addr.lower() == _NATIVE.lower() else src_addr
            expected_dst_token = None if dst_addr.lower() == _NATIVE.lower() else dst_addr
            # Reject a malformed aggregator target/value before touching
            # allowance. The exact allowance is granted only after the
            # refreshed transaction has passed these static checks.
            validate_swap_transaction_static(swap_data, network, expected_value_wei)
            approve_hash = ensure_allowance(plain_key, src_addr, amount_wei, network)
            if approve_hash:
                yield f"data: {json.dumps({'token': f'Approval tx: `{approve_hash}`\n', 'done': False})}\n\n"
            tx_hash = execute_swap(
                plain_key, swap_data, network, expected_value_wei,
                expected_recipient=wallet_addr,
                expected_src_token=expected_src_token,
                expected_dst_token=expected_dst_token,
                expected_src_amount=amount_wei,
                expected_min_dst_amount=int(new_dst_amount * 0.98),
            )
            dst_amount_human = new_dst_amount / (10 ** dst_dec)
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

            # The user confirmed a specific expected payout at preview time —
            # if the market moved unfavorably in the gap before they typed
            # CONFIRM, re-quoting above already refreshes the deadline/route,
            # but silently executing at a meaningfully worse rate than what
            # they actually approved isn't the same thing as re-confirming.
            original_dst_wei = pending.get("dst_amount_wei")
            new_dst_wei = int(estimate["toAmount"])
            if original_dst_wei and new_dst_wei < original_dst_wei * 0.98:
                original_human = original_dst_wei / (10 ** dst_dec)
                new_human = new_dst_wei / (10 ** dst_dec)
                raise Exception(
                    f"the rate moved since you confirmed — you'd now get ~{new_human:.4f} {to_tok} "
                    f"instead of the ~{original_human:.4f} {to_tok} you approved. Please re-run the "
                    f"bridge command to see the new rate and confirm again."
                )

            expected_value_wei = amount_wei if src_addr.lower() == lifi._NATIVE.lower() else 0
            expected_src_token = None if src_addr.lower() == lifi._NATIVE.lower() else src_addr
            expected_dst_token = None if dst_addr.lower() == lifi._NATIVE.lower() else dst_addr

            # Pin executor/value/spender to LI.FI's official deployments
            # before touching allowance. Full source-chain simulation runs
            # inside execute_bridge after an exact allowance exists.
            lifi.validate_bridge_transaction_static(
                w3, tx_request, wallet_addr, from_network, expected_value_wei, approval_addr,
                expected_src_token=expected_src_token,
                expected_src_amount=amount_wei,
                expected_destination_chain_id=lifi.CHAIN_IDS[to_network],
            )

            if approval_addr:
                approve_hash = lifi.ensure_allowance(plain_key, src_addr, approval_addr, amount_wei, from_network)
                if approve_hash:
                    approval_note = f"Approval tx: `{approve_hash}`\n"
                    yield f"data: {json.dumps({'token': approval_note, 'done': False})}\n\n"

            tx_hash = lifi.execute_bridge(
                plain_key, tx_request, from_network, expected_value_wei,
                expected_recipient=wallet_addr, approval_address=approval_addr,
                expected_src_token=expected_src_token,
                expected_dst_token=expected_dst_token,
                expected_src_amount=amount_wei,
                expected_destination_chain_id=lifi.CHAIN_IDS[to_network],
                expected_min_dst_amount=int(new_dst_wei * 0.98),
            )
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
        from app.tools.market.jupiter import get_quote as jup_quote, get_swap_transaction, execute_swap as jup_execute
        try:
            plain_key   = decrypt_key(pending["wallet_encrypted_key"])
            key_bytes   = bytes.fromhex(plain_key)
            wallet_addr = pending["wallet_address"]
            src_mint    = pending["src_mint"]
            dst_mint    = pending["dst_mint"]
            dst_dec     = pending.get("dst_dec", 9)
            amount_raw  = pending["amount_raw"]
            src_sym     = pending["from_token"]
            dst_sym     = pending["to_token"]
            amount      = pending["amount"]

            # Re-quote right before executing rather than reusing the
            # preview-time quote — same reasoning as the EVM swap/bridge flows.
            quote = jup_quote(src_mint, dst_mint, amount_raw)
            if not (quote and "outAmount" in quote):
                err = quote.get("error", "unknown error") if quote else "Jupiter API unavailable"
                raise Exception(f"could not refresh quote before executing — {err}")
            new_dst_amount = int(quote["outAmount"])
            original_dst_amount = int(pending["quote"]["outAmount"])
            if original_dst_amount and new_dst_amount < original_dst_amount * 0.98:
                original_human = original_dst_amount / (10 ** dst_dec)
                new_human = new_dst_amount / (10 ** dst_dec)
                raise Exception(
                    f"the rate moved since you confirmed — you'd now get ~{new_human:.4f} {dst_sym} "
                    f"instead of the ~{original_human:.4f} {dst_sym} you approved. Please re-run the "
                    f"swap command to see the new rate and confirm again."
                )

            tx_b64 = get_swap_transaction(quote, wallet_addr)
            if not tx_b64:
                raise Exception("Jupiter did not return swap transaction")
            sig = jup_execute(key_bytes, tx_b64, src_mint, dst_mint, amount_raw, int(new_dst_amount * 0.98))
            dst_amount_human = new_dst_amount / (10 ** dst_dec)
            text = (f"✅ Swapped **{amount} {src_sym} → ~{dst_amount_human:.4f} {dst_sym}** on Solana!\n"
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
