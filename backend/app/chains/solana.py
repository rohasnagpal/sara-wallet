from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.api import Client
import os
from app.core.amounts import to_base_units

def get_client() -> Client:
    # Read fresh on every call, not once at import time — Settings can save
    # HELIUS_RPC into os.environ mid-session (app/routers/settings.py), and a
    # module-level snapshot here would keep using the old value until the
    # process restarts.
    rpc_url = os.getenv("HELIUS_RPC") or "https://api.mainnet-beta.solana.com"
    return Client(rpc_url)

def get_balance(address: str) -> dict:
    client = get_client()
    pubkey = Pubkey.from_string(address)
    resp = client.get_balance(pubkey)
    lamports = resp.value
    sol = lamports / 1_000_000_000
    return {"network": "solana", "address": address, "balance": sol, "unit": "SOL"}

_TOKEN_ACCOUNT_SIZE = 165  # bytes — standard SPL Token account layout size


def get_native_transfer_preview(address: str, amount_sol: float) -> dict:
    """Real fee estimate (via getFeeForMessage on the actual transfer
    message shape) instead of assuming the network fee is negligible — a
    wallet sending its entire balance previously passed a plain "balance >=
    amount" check and then failed on-chain for insufficient funds to also
    cover the signature fee."""
    from solders.system_program import transfer, TransferParams
    from solders.message import Message

    client = get_client()
    owner = Pubkey.from_string(address)
    lamports = to_base_units(amount_sol, 9, "SOL")
    recent_blockhash = client.get_latest_blockhash().value.blockhash
    # The recipient doesn't affect the fee (fee depends on signature count
    # and message shape, not the account values), so a placeholder keeps
    # this preview independent of validating the real recipient address.
    ix = transfer(TransferParams(from_pubkey=owner, to_pubkey=owner, lamports=lamports))
    msg = Message.new_with_blockhash([ix], owner, recent_blockhash)
    fee_resp = client.get_fee_for_message(msg)
    fee_lamports = fee_resp.value if fee_resp.value is not None else 5000

    balance_sol = get_balance(address)["balance"]
    total_sol = amount_sol + fee_lamports / 1_000_000_000
    return {
        "balance": balance_sol, "unit": "SOL",
        "fee": fee_lamports / 1_000_000_000,
        "total": total_sol,
        "has_funds": balance_sol >= total_sol,
    }


def get_spl_transfer_preview(address: str, to: str, mint: str) -> dict:
    """Estimates the real SOL needed to cover this SPL transfer's network
    fee and, if the recipient doesn't already have an associated token
    account for this mint, the rent-exempt deposit for creating one —
    send_spl_token always includes a create_idempotent_associated_token_
    account instruction with the SENDER as payer, so that rent comes out of
    the sender's SOL balance, not the recipient's. A prior version of this
    preview only checked `SOL balance > 0`, which passed even when the
    balance couldn't actually cover the fee, let alone new-account rent."""
    import spl.token.instructions as spl_ix
    from solders.message import Message

    client = get_client()
    owner = Pubkey.from_string(address)
    to_pubkey = Pubkey.from_string(to)
    mint_pubkey = Pubkey.from_string(mint)
    dest_ata = spl_ix.get_associated_token_address(to_pubkey, mint_pubkey)

    needs_new_ata = client.get_account_info(dest_ata).value is None
    rent_lamports = (
        client.get_minimum_balance_for_rent_exemption(_TOKEN_ACCOUNT_SIZE).value
        if needs_new_ata else 0
    )

    source_ata = spl_ix.get_associated_token_address(owner, mint_pubkey)
    instructions = [
        spl_ix.create_idempotent_associated_token_account(payer=owner, owner=to_pubkey, mint=mint_pubkey),
        spl_ix.transfer_checked(spl_ix.TransferCheckedParams(
            program_id=spl_ix.TOKEN_PROGRAM_ID, source=source_ata, mint=mint_pubkey,
            dest=dest_ata, owner=owner, amount=1, decimals=0, signers=[],
        )),
    ]
    recent_blockhash = client.get_latest_blockhash().value.blockhash
    msg = Message.new_with_blockhash(instructions, owner, recent_blockhash)
    fee_resp = client.get_fee_for_message(msg)
    fee_lamports = fee_resp.value if fee_resp.value is not None else 5000

    sol_balance = get_balance(address)["balance"]
    required_sol = (fee_lamports + rent_lamports) / 1_000_000_000
    return {
        "sol_balance": sol_balance,
        "required_sol": required_sol,
        "needs_new_ata": needs_new_ata,
        "has_funds": sol_balance >= required_sol,
    }


def send_tx(private_key_bytes: bytes, to: str, amount_sol: float) -> str:
    from solders.system_program import transfer, TransferParams
    from solders.transaction import Transaction
    from solders.message import Message
    from solders.hash import Hash

    client = get_client()
    keypair = Keypair.from_bytes(private_key_bytes)
    to_pubkey = Pubkey.from_string(to)
    lamports = to_base_units(amount_sol, 9, "SOL")

    blockhash_resp = client.get_latest_blockhash()
    recent_blockhash = blockhash_resp.value.blockhash

    ix = transfer(TransferParams(from_pubkey=keypair.pubkey(), to_pubkey=to_pubkey, lamports=lamports))
    msg = Message.new_with_blockhash([ix], keypair.pubkey(), recent_blockhash)
    tx = Transaction([keypair], msg, recent_blockhash)
    resp = client.send_transaction(tx)
    return str(resp.value)


def get_spl_token_balance(address: str, mint: str, decimals: int) -> dict:
    import spl.token.instructions as spl_ix
    client = get_client()
    owner = Pubkey.from_string(address)
    mint_pubkey = Pubkey.from_string(mint)
    ata = spl_ix.get_associated_token_address(owner, mint_pubkey)
    try:
        resp = client.get_token_account_balance(ata)
        raw = int(resp.value.amount)
    except Exception:
        raw = 0  # no token account yet — balance is 0, not an error
    return {
        "network": "solana", "address": address,
        "balance": raw / (10 ** decimals), "raw_balance": raw,
    }


def send_spl_token(private_key_bytes: bytes, to: str, amount: float, mint: str, decimals: int) -> str:
    """SPL token transfer via transfer_checked (mint+decimals are part of the
    instruction itself, so a wrong decimals value fails loudly on-chain
    rather than silently moving the wrong amount). Creates the recipient's
    associated token account first if it doesn't exist yet — idempotent, so
    safe to always include."""
    import spl.token.instructions as spl_ix
    from solders.transaction import Transaction
    from solders.message import Message

    client = get_client()
    keypair = Keypair.from_bytes(private_key_bytes)
    owner = keypair.pubkey()
    to_pubkey = Pubkey.from_string(to)
    mint_pubkey = Pubkey.from_string(mint)
    amount_raw = to_base_units(amount, decimals, "token")

    source_ata = spl_ix.get_associated_token_address(owner, mint_pubkey)
    dest_ata = spl_ix.get_associated_token_address(to_pubkey, mint_pubkey)

    instructions = [
        spl_ix.create_idempotent_associated_token_account(payer=owner, owner=to_pubkey, mint=mint_pubkey),
        spl_ix.transfer_checked(spl_ix.TransferCheckedParams(
            program_id=spl_ix.TOKEN_PROGRAM_ID,
            source=source_ata, mint=mint_pubkey, dest=dest_ata, owner=owner,
            amount=amount_raw, decimals=decimals, signers=[],
        )),
    ]

    blockhash_resp = client.get_latest_blockhash()
    recent_blockhash = blockhash_resp.value.blockhash
    msg = Message.new_with_blockhash(instructions, owner, recent_blockhash)
    tx = Transaction([keypair], msg, recent_blockhash)
    resp = client.send_transaction(tx)
    return str(resp.value)
