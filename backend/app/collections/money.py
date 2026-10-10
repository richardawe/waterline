from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def naira_to_kobo(value) -> int:
    """Parse a naira amount ("12,500", "₦12,500.50", 12500) into kobo.
    Raises ValueError on anything that isn't a plain positive-ish number."""
    if value is None:
        raise ValueError("amount is missing")
    text = str(value).strip().replace("₦", "").replace("NGN", "").replace(",", "").replace(" ", "")
    if not text:
        raise ValueError("amount is missing")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"'{value}' is not a number") from exc
    if amount.as_tuple().exponent < -2:
        raise ValueError("amount has more than 2 decimal places")
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def format_naira(kobo: int) -> str:
    """₦1,234,567.00"""
    sign = "-" if kobo < 0 else ""
    kobo = abs(int(kobo))
    return f"{sign}₦{kobo // 100:,}.{kobo % 100:02d}"


def percent_of(kobo: int, percent) -> int:
    """`kobo × percent / 100`, rounded to the nearest kobo (half up)."""
    return int((Decimal(kobo) * Decimal(str(percent)) / Decimal(100)).to_integral_value(rounding=ROUND_HALF_UP))
