"""Text formatting for durations and money."""


def fmt_dur(minutes: int) -> str:
    """Format a number of minutes as hours and minutes, e.g. 95 -> '1:35'."""
    if minutes < 0:
        raise ValueError("a duration cannot be negative")
    hours, rest = divmod(minutes, 60)
    return f"{hours}:{rest:02d}"


def fmt_money(amount: float, currency: str = "EUR") -> str:
    """Format an amount with two decimals, e.g. 1234.5 -> '1,234.50 EUR'."""
    return f"{amount:,.2f} {currency}"
