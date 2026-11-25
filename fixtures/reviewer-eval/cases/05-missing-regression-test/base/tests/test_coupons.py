from src.coupons import Coupon, validate_coupon


def test_active_coupon_returns_discount() -> None:
    coupon = Coupon(code='SAVE10', discount_pct=10, expires_at=2000)
    assert validate_coupon(coupon, 1500) == 10
