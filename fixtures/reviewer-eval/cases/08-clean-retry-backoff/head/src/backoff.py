def compute_delay(attempt: int, base_s: float = 0.5, max_s: float = 8.0) -> float:
    if attempt < 0 or base_s <= 0 or max_s < base_s:
        raise ValueError('invalid backoff parameters')
    return min(max_s, base_s * (2 ** attempt))
