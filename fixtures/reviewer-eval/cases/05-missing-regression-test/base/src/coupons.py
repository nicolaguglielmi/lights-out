from dataclasses import dataclass


class CouponExpiredError(ValueError):
    pass


@dataclass(frozen=True)
class Coupon:
    code: str
    discount_pct: int
    expires_at: int


def validate_coupon(coupon: Coupon, now_ts: int) -> int:
    if now_ts > coupon.expires_at:
        raise CouponExpiredError(f'coupon {coupon.code} expired')
    return coupon.discount_pct
