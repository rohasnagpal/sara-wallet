"""Exact conversion between user-facing decimal amounts and chain units."""
from decimal import Decimal, InvalidOperation


def to_base_units(amount, decimals: int, asset: str = "asset") -> int:
    """Convert without binary-float rounding or silent truncation.

    User amounts must be positive and exactly representable at the asset's
    declared precision. Rejecting excess precision is safer than silently
    changing the amount the user confirmed.
    """
    if not isinstance(decimals, int) or decimals < 0:
        raise ValueError("asset decimals must be a non-negative integer")
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {asset} amount") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{asset} amount must be a positive finite number")
    scaled = value * (Decimal(10) ** decimals)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError(
            f"{asset} amount has more than {decimals} decimal places and cannot be sent exactly"
        )
    return int(integral)
