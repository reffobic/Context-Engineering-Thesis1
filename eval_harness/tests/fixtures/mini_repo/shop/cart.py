from shop.pricing import apply_discount, final_price


class Cart:
    """A shopping cart that totals line items and computes the discount."""

    def __init__(self):
        self.items = []

    def add(self, name, price, qty=1):
        self.items.append((name, price, qty))

    def total(self):
        """Sum of price times quantity over all line items."""
        return sum(price * qty for _, price, qty in self.items)

    def discounted_total(self, percent):
        return apply_discount(self.total(), percent)

    def checkout_total(self, percent, region):
        return final_price(self.total(), percent, region)
