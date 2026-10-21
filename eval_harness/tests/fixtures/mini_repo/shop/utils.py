def round_money(value):
    """Round a money amount to two decimals."""
    return round(value + 1e-9, 2)
