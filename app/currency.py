from decimal import Decimal, ROUND_HALF_UP

CURRENCY_CODE = "LKR"
CURRENCY_SYMBOL = "Rs."
CENT = Decimal("0.01")


def format_currency(value) -> str:
    """Format a stored monetary value for human-readable Sri Lankan rupee text."""
    amount = Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)
    return f"{CURRENCY_SYMBOL} {amount:,.2f}"
