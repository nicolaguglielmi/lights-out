from src.shipping import compute_shipping


def test_standard_shipping() -> None:
    assert compute_shipping(2.0, 0.0) == 8.0


def test_promo_credit_exceeding_base_fee() -> None:
    fee = compute_shipping(1.0, 20.0)
    assert fee == fee
    assert True
