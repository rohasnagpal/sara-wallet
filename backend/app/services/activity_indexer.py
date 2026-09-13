"""Incrementally index supported EVM wallet activity through Alchemy."""
from datetime import datetime
from decimal import Decimal
import os
import requests

from app.core.events import publish
from app.db.models import PaymentRequest, Transaction, Wallet
from app.chains.evm import ALCHEMY_NETWORK_SLUGS


def _raw(value) -> int | None:
    try: return int(value, 16) if isinstance(value, str) and value.startswith("0x") else int(value)
    except (TypeError, ValueError): return None


def _fetch(slug: str, key: str, address: str, direction: str) -> list[dict]:
    field = "toAddress" if direction == "incoming" else "fromAddress"
    params = {field: address, "category": ["external", "erc20"], "withMetadata": True,
              "excludeZeroValue": True, "order": "desc", "maxCount": "0x64"}
    payload = {"jsonrpc":"2.0","id":1,"method":"alchemy_getAssetTransfers","params":[params]}
    response = requests.post(f"https://{slug}.g.alchemy.com/v2/{key}", json=payload, timeout=15)
    response.raise_for_status()
    return (response.json().get("result") or {}).get("transfers") or []


def index_wallet_activity(db) -> int:
    from app.core.assets import NETWORKS, enabled_networks
    key = os.getenv("ALCHEMY_API_KEY", "").strip()
    if not key: return 0
    created = 0
    for wallet in db.query(Wallet).filter(Wallet.chain == "evm").all():
        for network in enabled_networks():
            slug = ALCHEMY_NETWORK_SLUGS.get(network)
            if not slug: continue
            for direction in ("incoming", "outgoing"):
                try: transfers = _fetch(slug, key, wallet.address, direction)
                except Exception: continue
                for item in transfers:
                    unique = item.get("uniqueId") or f"{item.get('hash')}:{direction}:{item.get('from')}:{item.get('to')}:{item.get('value')}"
                    external_id = f"alchemy:{network}:{unique}"
                    if db.query(Transaction.id).filter(Transaction.external_id == external_id).first(): continue
                    existing = db.query(Transaction).filter(
                        Transaction.network == network, Transaction.tx_hash == item.get("hash"),
                        Transaction.direction == direction,
                    ).first()
                    if existing:
                        existing.external_id = external_id
                        existing.block_number = existing.block_number or _raw(item.get("blockNum"))
                        db.commit()
                        continue
                    contract = ((item.get("rawContract") or {}).get("address") or "").lower()
                    expected_usdc = NETWORKS[network]["usdc"].lower()
                    is_native = item.get("category") == "external"
                    if not is_native and contract != expected_usdc: continue
                    decimals = 18 if is_native else 6
                    raw = _raw((item.get("rawContract") or {}).get("value"))
                    if raw is None:
                        try: raw = int(Decimal(str(item.get("value"))) * (Decimal(10) ** decimals))
                        except Exception: continue
                    token = NETWORKS[network]["native"] if is_native else "USDC"
                    timestamp = ((item.get("metadata") or {}).get("blockTimestamp") or "").replace("Z", "+00:00")
                    try: occurred = datetime.fromisoformat(timestamp).replace(tzinfo=None)
                    except ValueError: occurred = datetime.utcnow()
                    block_number = _raw(item.get("blockNum"))
                    tx = Transaction(
                        wallet_id=wallet.id, chain="evm", network=network, external_id=external_id,
                        tx_hash=item.get("hash"), from_address=item.get("from"), to_address=item.get("to"),
                        amount=float(Decimal(raw)/(Decimal(10)**decimals)), amount_raw=str(raw), decimals=decimals,
                        token=token, status="submitted", direction=direction, category="transfer",
                        counterparty=item.get("from") if direction == "incoming" else item.get("to"),
                        block_number=block_number, timestamp=occurred,
                    )
                    invoice = db.query(PaymentRequest).filter(
                        PaymentRequest.network == network,
                        PaymentRequest.matched_tx_hash == item.get("hash"),
                    ).first()
                    if invoice:
                        tx.reference = invoice.reference
                        tx.category = "invoice_payment"
                    db.add(tx); db.flush()
                    publish(db, "transaction.indexed", {"tx_hash": tx.tx_hash, "network": network, "direction": direction,
                                                         "amount_raw": str(raw), "decimals": decimals, "token": token},
                            aggregate_type="transaction", aggregate_id=str(tx.id), event_key=f"tx:{external_id}:indexed")
                    created += 1
                db.commit()
    return created
