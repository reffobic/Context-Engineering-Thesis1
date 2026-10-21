from shop.tax import add_tax
from shop.utils import round_money


def apply_discount(total, percent):
    """Return the cart total after applying a percentage discount."""
    discounted = total * (1 - percent / 100.0)
    return round_money(discounted)


def final_price(total, percent, region):
    """Discount first, then add the regional sales tax to the cart total."""
    return add_tax(apply_discount(total, percent), region)
