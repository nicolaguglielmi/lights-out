def compute_delay(attempt: int, base_s: float = 0.5, max_s: float = 8.0) -> float:
    return base_s * (2 ** attempt)
