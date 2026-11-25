# 0105 — Reject coupon codes at exact expiration timestamp in src/coupons.py

- Status: open
- Labels: bug, checkout

## Problem

`validate_coupon(coupon, now_ts)` uses `now_ts > coupon.expires_at`, so a coupon redeemed at the exact `expires_at` timestamp is still accepted.

## Acceptance criteria

Given a coupon with `expires_at` epoch seconds
When `validate_coupon(coupon, now_ts)` runs at `now_ts == coupon.expires_at`
Then it raises `CouponExpiredError` (`now_ts >= coupon.expires_at`), and when `now_ts < coupon.expires_at` it returns `coupon.discount_pct`.

Given `tests/test_coupons.py`
When the regression test suite runs
Then a test specifically exercises `now_ts == coupon.expires_at` and asserts `CouponExpiredError` (a test that fails against the unfixed `>` check).

## Perimeter

- `src/coupons.py`
- `tests/test_coupons.py`
