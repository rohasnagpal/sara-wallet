"""How a transaction counts in the income/expense report.

Sara records a `category` on every transaction as it's created (invoice
payment, payroll, batch payment, swap...). Nothing else assigns the
accounting labels the report reads, so without this the report would only
ever see what Reconcile marks (transfers and swaps) and stay empty. A label
the user (or Reconcile) set explicitly always wins; otherwise the category
decides, conservatively: only what is clearly money in or out is counted.
A plain receive from someone else is left "unknown" because Sara can't tell
income from the user's own money coming back.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import AccountingClassification, Transaction, Wallet

# (direction, category) -> accounting classification
_FROM_CATEGORY = {
    ("incoming", "invoice_payment"): "invoice_receipt",
    ("incoming", "airdrop"): "airdrop",
    ("incoming", "payroll"): "payroll",
    ("incoming", "name_registry_revenue"): "income",
    ("outgoing", "payroll"): "payroll",
    ("outgoing", "airdrop"): "expense",
    ("outgoing", "batch_payment"): "expense",
    ("outgoing", "transfer"): "expense",
    ("outgoing", "x402_payment"): "expense",
    ("outgoing", "name_registration"): "expense",
    ("outgoing", "name_renewal"): "expense",
    ("incoming", "swap"): "swap",
    ("outgoing", "swap"): "swap",
    ("outgoing", "bridge"): "transfer",
}


def own_addresses(db: Session) -> set[str]:
    return {w.address.lower() for w in db.query(Wallet).all() if w.address}


def effective_label(tx: Transaction, cls: AccountingClassification | None, own: set[str]) -> tuple[str, str]:
    """Returns (classification, source) where source is "you" (set
    explicitly, including by Reconcile), "auto" (derived from the category)
    or "none" (nothing known -> "unknown"). Money moving between the user's
    own wallets is always "transfer", whichever wallet is looked at."""
    to_own = (tx.to_address or "").lower() in own
    from_own = (tx.from_address or "").lower() in own
    if (cls and cls.is_internal_transfer) or (tx.direction == "outgoing" and to_own) \
            or (tx.direction == "incoming" and from_own):
        return "transfer", "auto"
    if cls and cls.classification and cls.classification != "unknown":
        return cls.classification, "you"
    derived = _FROM_CATEGORY.get((tx.direction, tx.category or ""))
    if derived:
        return derived, "auto"
    return "unknown", "none"
