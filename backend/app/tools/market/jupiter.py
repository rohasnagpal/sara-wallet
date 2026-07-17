import os, base64, requests

BASE = "https://lite-api.jup.ag/swap/v1"

TOKEN_MINTS = {
    # Sara specializes in stablecoin payments — only USDC/USDT plus native
    # SOL (for fees) are trusted. No speculative/DeFi tokens.
    "SOL":     "So11111111111111111111111111111111111111112",
    "USDC":    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT":    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}

TOKEN_DECIMALS = {
    "SOL": 9, "USDC": 6, "USDT": 6,
}


def resolve_mint(symbol: str) -> str | None:
    return TOKEN_MINTS.get(symbol.upper())


def get_decimals(symbol: str) -> int:
    return TOKEN_DECIMALS.get(symbol.upper(), 9)


def trusted_symbols() -> list[str]:
    return list(TOKEN_MINTS.keys())


def resolve_mint_with_correction(symbol: str) -> tuple[str | None, str | None]:
    """Like resolve_mint, but typo-corrects against the trusted Solana token
    list. Returns (mint, corrected_symbol) — corrected_symbol is None unless
    a correction was actually applied."""
    mint = resolve_mint(symbol)
    if mint:
        return mint, None
    from app.tools.wallet.token_trust import fuzzy_correct
    corrected = fuzzy_correct(symbol, trusted_symbols())
    if corrected:
        return resolve_mint(corrected), corrected
    return None, None


def get_quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int = 50) -> dict | None:
    try:
        r = requests.get(f"{BASE}/quote", params={
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": slippage_bps,
        }, timeout=10)
        return r.json() if r.ok else None
    except Exception:
        return None


def get_swap_transaction(quote: dict, user_public_key: str) -> str | None:
    try:
        r = requests.post(f"{BASE}/swap", json={
            "quoteResponse": quote,
            "userPublicKey": user_public_key,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": "auto",
        }, timeout=12)
        return r.json().get("swapTransaction") if r.ok else None
    except Exception:
        return None


# Program IDs a legitimate Jupiter swap transaction is built from, confirmed
# by decoding several real Jupiter /swap responses live. Jupiter's actual
# route (whichever pools it picked — Raydium, Orca, Meteora, etc.) executes
# via CPI *inside* the Jupiter program's own instruction, so those
# pool-specific program IDs never appear as a separate top-level instruction
# here — that's what makes a fixed allowlist practical despite Jupiter
# aggregating dozens of underlying DEXs. Token-2022 is deliberately excluded:
# none of TOKEN_MINTS (SOL/USDC/USDT) use it, so a route needing it would be
# unexpected for this wallet's trusted token set.
_ALLOWED_PROGRAM_IDS = {
    "ComputeBudget111111111111111111111111111111",
    "11111111111111111111111111111111",              # System Program
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",    # SPL Token
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",   # Associated Token Account
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",    # Jupiter Aggregator v6
}

# SPL Token instruction discriminants (first data byte) that grant ongoing
# access rather than moving funds once — a plain swap never needs these, and
# they're exactly what an attacker would need to keep draining the wallet
# after this one transaction rather than just within it.
_TOKEN_DANGEROUS_DISCRIMINANTS = {4, 6, 13}  # Approve, SetAuthority, ApproveChecked

# Token-program instruction variants a plain swap can legitimately contain at
# the *top* level (the swap itself happens via CPI inside the Jupiter
# program's own instruction and is invisible here — these are only the
# wrap/unwrap-SOL bookkeeping instructions Jupiter emits around it):
#   1  = InitializeAccount   9 = CloseAccount (unwrap)   17 = SyncNative
#   3  = Transfer            12 = TransferChecked
# Anything else (MintTo, Burn, FreezeAccount, ...) is never needed for a swap.
_TOKEN_RECOGNIZED_DISCRIMINANTS = {1, 3, 9, 12, 17}
_SPL_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
_SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
_COMPUTE_BUDGET_PROGRAM_ID = "ComputeBudget111111111111111111111111111111"
_SOL_MINT = "So11111111111111111111111111111111111111112"

# A System::Transfer lamport buffer above the confirmed input amount, to
# cover the WSOL associated-token-account's rent-exempt minimum when
# wrapping SOL (currently ~0.00204 SOL on mainnet) — generous but bounded,
# so a crafted transaction can't smuggle an arbitrarily larger transfer.
_WRAP_RENT_BUFFER_LAMPORTS = 10_000_000  # 0.01 SOL
_MAX_TX_FEE_LAMPORTS = 5_000_000         # 0.005 SOL
_MAX_COMPUTE_UNITS = 1_400_000
_MAX_COMPUTE_UNIT_PRICE_MICROLAMPORTS = 3_000_000


def confirmation_safety_limits() -> tuple[int, int]:
    """Return the same hard fee/rent ceilings enforced after simulation so
    confirmation copy cannot drift away from signing policy."""
    return _MAX_TX_FEE_LAMPORTS, _WRAP_RENT_BUFFER_LAMPORTS


def _validate_swap_transaction(
    tx,
    expected_signer: str,
    expected_src_mint: str,
    expected_dst_mint: str,
    expected_amount_in: int,
) -> None:
    """Jupiter's /swap response is a fully-composed transaction message, not
    just calldata to a single known router — there's no `to` field to check
    the way paraswap.py/lifi.py do. Instead this validates every top-level
    instruction is one Sara recognizes as part of a plain swap, rejects
    anything that could grant standing access (delegate/authority) instead
    of just moving funds within this one transaction, and — for the two
    instruction types that *do* move funds outside of Jupiter's own CPI
    (System::Transfer when wrapping SOL, and Token transfers/closes around
    that wrap/unwrap) — confirms the destination is an account this wallet
    itself owns and the amount is bounded by what was actually confirmed.
    A prior version allowed the whole System Program and left Token
    transfer/close destinations unchecked, which let a crafted instruction
    move lamports or tokens to an attacker-controlled account while every
    other check still passed."""
    msg = tx.message
    if msg.header.num_required_signatures != 1:
        raise ValueError(
            f"Refusing to sign: swap transaction requires {msg.header.num_required_signatures} "
            f"signers, expected exactly 1 (the wallet itself)."
        )
    if str(msg.account_keys[0]) != expected_signer:
        raise ValueError("Refusing to sign: swap transaction's fee payer is not this wallet.")

    from spl.token.instructions import get_associated_token_address
    from solders.pubkey import Pubkey
    wallet_pubkey = Pubkey.from_string(expected_signer)
    allowed_token_accounts = {
        str(get_associated_token_address(wallet_pubkey, Pubkey.from_string(expected_src_mint))),
        str(get_associated_token_address(wallet_pubkey, Pubkey.from_string(expected_dst_mint))),
    }
    wsol_ata = str(get_associated_token_address(wallet_pubkey, Pubkey.from_string(_SOL_MINT)))
    allowed_token_accounts.add(wsol_ata)

    num_static_keys = len(msg.account_keys)
    for ix in msg.instructions:
        if ix.program_id_index >= num_static_keys:
            raise ValueError(
                "Refusing to sign: swap transaction invokes a program loaded from an address "
                "lookup table rather than the transaction's own static keys — not a pattern any "
                "known-good Jupiter swap uses."
            )
        program_id = str(msg.account_keys[ix.program_id_index])
        if program_id not in _ALLOWED_PROGRAM_IDS:
            raise ValueError(
                f"Refusing to sign: swap transaction invokes an unrecognized program ({program_id})."
            )
        data = bytes(ix.data)
        accounts = [str(msg.account_keys[i]) for i in ix.accounts]

        if program_id == _COMPUTE_BUDGET_PROGRAM_ID:
            if not data:
                raise ValueError("Refusing to sign: malformed Compute Budget instruction.")
            variant = data[0]
            if variant == 2:  # SetComputeUnitLimit(u32)
                if len(data) != 5 or int.from_bytes(data[1:5], "little") > _MAX_COMPUTE_UNITS:
                    raise ValueError("Refusing to sign: swap requests an excessive compute-unit limit.")
            elif variant == 3:  # SetComputeUnitPrice(u64), in micro-lamports/CU
                if len(data) != 9 or int.from_bytes(data[1:9], "little") > _MAX_COMPUTE_UNIT_PRICE_MICROLAMPORTS:
                    raise ValueError("Refusing to sign: swap requests an excessive priority fee.")
            elif variant == 1:  # RequestHeapFrame(u32)
                if len(data) != 5 or int.from_bytes(data[1:5], "little") > 262_144:
                    raise ValueError("Refusing to sign: swap requests an excessive heap frame.")
            elif variant == 4:  # SetLoadedAccountsDataSizeLimit(u32)
                if len(data) != 5 or int.from_bytes(data[1:5], "little") > 64 * 1024 * 1024:
                    raise ValueError("Refusing to sign: swap requests excessive loaded-account data.")
            else:
                raise ValueError(
                    f"Refusing to sign: unrecognized Compute Budget instruction (variant {variant})."
                )

        elif program_id == _SYSTEM_PROGRAM_ID:
            discriminant = int.from_bytes(data[0:4], "little") if len(data) >= 4 else None
            if discriminant != 2:
                raise ValueError(
                    f"Refusing to sign: swap transaction contains an unexpected System Program "
                    f"instruction (variant {discriminant}) — only lamport transfers for wrapping "
                    f"SOL are expected here."
                )
            if expected_src_mint != _SOL_MINT:
                raise ValueError(
                    "Refusing to sign: swap transaction transfers lamports even though the "
                    "confirmed source asset isn't SOL."
                )
            if len(data) < 12 or len(accounts) < 2:
                raise ValueError("Refusing to sign: malformed System Program transfer instruction.")
            lamports = int.from_bytes(data[4:12], "little")
            frm, to = accounts[0], accounts[1]
            if frm != expected_signer:
                raise ValueError(
                    "Refusing to sign: swap transaction moves lamports from an account other "
                    "than this wallet."
                )
            if to != wsol_ata:
                raise ValueError(
                    "Refusing to sign: swap transaction sends lamports to an unrecognized "
                    "destination instead of this wallet's own wrapped-SOL account."
                )
            if lamports > expected_amount_in + _WRAP_RENT_BUFFER_LAMPORTS:
                raise ValueError(
                    "Refusing to sign: swap transaction transfers more lamports than the "
                    "confirmed swap amount."
                )

        elif program_id == _SPL_TOKEN_PROGRAM_ID and data:
            discriminant = data[0]
            if discriminant in _TOKEN_DANGEROUS_DISCRIMINANTS:
                raise ValueError(
                    "Refusing to sign: swap transaction contains a token-authority-granting "
                    "instruction (approve/setAuthority) — never needed for a plain swap."
                )
            if discriminant not in _TOKEN_RECOGNIZED_DISCRIMINANTS:
                raise ValueError(
                    f"Refusing to sign: swap transaction contains an unrecognized Token Program "
                    f"instruction (variant {discriminant})."
                )
            if discriminant in (3, 9):  # Transfer, CloseAccount: dest at accounts[1]
                if len(accounts) < 2 or accounts[1] not in allowed_token_accounts | {expected_signer}:
                    raise ValueError(
                        "Refusing to sign: swap transaction moves tokens/rent to an account not "
                        "owned by this wallet."
                    )
            elif discriminant == 12:  # TransferChecked: mint at accounts[1], dest at accounts[2]
                if len(accounts) < 3:
                    raise ValueError("Refusing to sign: malformed TransferChecked instruction.")
                if accounts[1] not in (expected_src_mint, expected_dst_mint):
                    raise ValueError(
                        "Refusing to sign: swap transaction references an unexpected token mint."
                    )
                if accounts[2] not in allowed_token_accounts:
                    raise ValueError(
                        "Refusing to sign: swap transaction moves tokens to an account not "
                        "owned by this wallet."
                    )


def _verify_via_simulation(client, signed_tx, wallet_address: str, expected_src_mint: str,
                            expected_dst_mint: str, expected_amount_in: int,
                            expected_min_amount_out: int) -> None:
    """_validate_swap_transaction only sees the *outer* instructions — the
    actual swap happens via CPI inside the Jupiter program's own instruction
    (whichever pools it routes through), which is opaque from the outside.
    That means an arbitrary/malicious instruction to the real Jupiter
    program ID passes the instruction-shape checks regardless of what it
    actually does. Simulating the fully-signed transaction and checking the
    REAL predicted balance deltas closes that gap: it doesn't matter what
    the instruction does internally if the net effect on this wallet's
    balances doesn't match what was confirmed.
    """
    resp = client.simulate_transaction(signed_tx, sig_verify=False, replace_recent_blockhash=True)
    result = resp.value
    if result.err is not None:
        raise ValueError(f"Refusing to sign: this swap transaction would fail on-chain: {result.err}")

    pre_by_mint: dict[str, int] = {}
    for b in (result.pre_token_balances or []):
        if str(b.owner) == wallet_address:
            pre_by_mint[str(b.mint)] = pre_by_mint.get(str(b.mint), 0) + int(b.ui_token_amount.amount)
    post_by_mint: dict[str, int] = {}
    for b in (result.post_token_balances or []):
        if str(b.owner) == wallet_address:
            post_by_mint[str(b.mint)] = post_by_mint.get(str(b.mint), 0) + int(b.ui_token_amount.amount)

    # Any SPL token besides the confirmed input that shows a net decrease
    # for this wallet is exactly the shape of an unrelated-asset drain —
    # a plain swap only ever spends the one token the user confirmed.
    for mint in set(pre_by_mint) | set(post_by_mint):
        if mint in (expected_src_mint, expected_dst_mint):
            continue
        delta = post_by_mint.get(mint, 0) - pre_by_mint.get(mint, 0)
        if delta < 0:
            raise ValueError(
                f"Refusing to sign: simulation shows an unexpected token ({mint}) leaving your "
                f"wallet — not just the confirmed input token."
            )

    if expected_src_mint != _SOL_MINT:
        spent = pre_by_mint.get(expected_src_mint, 0) - post_by_mint.get(expected_src_mint, 0)
        if spent > expected_amount_in:
            raise ValueError(
                f"Refusing to sign: simulation shows {spent} of the source token leaving your "
                f"wallet, more than the confirmed input amount ({expected_amount_in})."
            )
    # Top-level instruction inspection cannot see CPI transfers from the
    # Jupiter program, and a ComputeBudget instruction can independently
    # raise the priority fee. Bound the fee and the wallet's *total* native
    # balance loss from the simulated result, for every source asset.
    fee = getattr(result, "fee", None)
    if fee is None:
        raise ValueError("Refusing to sign: simulation did not report the transaction fee.")
    fee = int(fee)
    if fee > _MAX_TX_FEE_LAMPORTS:
        raise ValueError(
            f"Refusing to sign: simulated network fee ({fee} lamports) exceeds Sara's safety cap."
        )
    if not result.pre_balances or not result.post_balances:
        raise ValueError("Refusing to sign: simulation did not report native balance changes.")
    native_spent = max(0, int(result.pre_balances[0]) - int(result.post_balances[0]))
    allowed_native_spend = fee + _WRAP_RENT_BUFFER_LAMPORTS
    if expected_src_mint == _SOL_MINT:
        allowed_native_spend += expected_amount_in
    if native_spent > allowed_native_spend:
        raise ValueError(
            f"Refusing to sign: simulation shows {native_spent} lamports leaving the wallet, "
            f"above the confirmed input plus bounded fee/rent ({allowed_native_spend})."
        )

    if expected_dst_mint != _SOL_MINT:
        received = post_by_mint.get(expected_dst_mint, 0) - pre_by_mint.get(expected_dst_mint, 0)
        if expected_min_amount_out > 0 and received < expected_min_amount_out:
            raise ValueError(
                f"Refusing to sign: simulation shows only {received} of the destination token "
                f"returning to your wallet, below the confirmed minimum ({expected_min_amount_out})."
            )
    else:
        pre_lamports = result.pre_balances[0] if result.pre_balances else 0
        post_lamports = result.post_balances[0] if result.post_balances else 0
        gained = post_lamports - pre_lamports
        if expected_min_amount_out > 0 and gained < expected_min_amount_out - _WRAP_RENT_BUFFER_LAMPORTS:
            raise ValueError(
                f"Refusing to sign: simulation shows only {gained} lamports returning to your "
                f"wallet, below the confirmed minimum ({expected_min_amount_out})."
            )


def execute_swap(
    private_key_bytes: bytes,
    tx_b64: str,
    expected_src_mint: str,
    expected_dst_mint: str,
    expected_amount_in: int,
    expected_min_amount_out: int = 0,
) -> str:
    from solders.transaction import VersionedTransaction
    from solders.keypair import Keypair
    from solana.rpc.api import Client
    from solana.rpc.types import TxOpts

    rpc_url = os.getenv("HELIUS_RPC") or "https://api.mainnet-beta.solana.com"
    keypair = Keypair.from_bytes(private_key_bytes)
    raw = base64.b64decode(tx_b64)
    tx = VersionedTransaction.from_bytes(raw)
    _validate_swap_transaction(
        tx, str(keypair.pubkey()), expected_src_mint, expected_dst_mint, expected_amount_in
    )
    signed_tx = VersionedTransaction(tx.message, [keypair])
    client = Client(rpc_url)
    _verify_via_simulation(
        client, signed_tx, str(keypair.pubkey()), expected_src_mint, expected_dst_mint,
        expected_amount_in, expected_min_amount_out,
    )
    resp = client.send_raw_transaction(
        bytes(signed_tx),
        opts=TxOpts(skip_preflight=False, preflight_commitment="confirmed"),
    )
    return str(resp.value)
