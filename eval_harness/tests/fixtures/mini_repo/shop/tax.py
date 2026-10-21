from shop.utils import round_money

RATES = {"eu": 0.2, "us": 0.07}


def tax_rate(region):
    """Look up the sales tax rate for a region code."""
    return RATES.get(region, 0.0)


def add_tax(amount, region):
    """Return the amount with the regional sales tax added."""
    return round_money(amount * (1 + tax_rate(region)))
