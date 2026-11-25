from src.orders import apply_discount


def test_apply_discount_basic() -> None:
    assert apply_discount(1000, pct=10, flat_cents=50) == 850
