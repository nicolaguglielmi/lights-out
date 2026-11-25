def apply_discount(subtotal_cents: int, pct: int = 0, flat_cents: int = 0) -> int:
    if subtotal_cents < 0 or flat_cents < 0:
        raise ValueError('amounts must be non-negative')
    if not 0 <= pct <= 100:
        raise ValueError('pct must be between 0 and 100')
    after_pct = subtotal_cents - (subtotal_cents * pct) // 100
    return max(0, after_pct - flat_cents)
