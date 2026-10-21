from shop.pricing import apply_discount, final_price


def test_apply_discount_cart_total():
    assert apply_discount(100.0, 25) == 75.0


def test_final_price_discount_then_tax():
    assert final_price(100.0, 10, "us") == 96.3
