def apply_discount(subtotal_cents: int, pct: int = 0, flat_cents: int = 0) -> int:
    after_pct = subtotal_cents - (subtotal_cents * pct) // 100
    return after_pct - flat_cents
