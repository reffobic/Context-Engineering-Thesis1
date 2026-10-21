from shop.cart import Cart


def test_cart_total_applies_discount():
    cart = Cart()
    cart.add("pen", 2.0, 3)
    assert cart.total() == 6.0
    assert cart.discounted_total(50) == 3.0


def test_cart_checkout_total_discount_and_tax():
    cart = Cart()
    cart.add("book", 10.0)
    assert cart.checkout_total(10, "eu") == 10.8
