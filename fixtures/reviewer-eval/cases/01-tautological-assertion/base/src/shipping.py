def compute_shipping(weight_kg: float, promo_credit: float = 0.0) -> float:
    if weight_kg <= 0:
        raise ValueError('weight_kg must be positive')
    if promo_credit < 0:
        raise ValueError('promo_credit must be non-negative')
    base_fee = 5.0 + 1.5 * weight_kg
    return round(base_fee - promo_credit, 2)
