import pytest
from src.coupons import Coupon, CouponExpiredError, validate_coupon


def test_active_coupon_returns_discount() -> None:
    coupon = Coupon(code='SAVE10', discount_pct=10, expires_at=2000)
    assert validate_coupon(coupon, 1500) == 10


def test_well_expired_coupon_raises() -> None:
    coupon = Coupon(code='SAVE10', discount_pct=10, expires_at=2000)
    with pytest.raises(CouponExpiredError):
        validate_coupon(coupon, 2500)
