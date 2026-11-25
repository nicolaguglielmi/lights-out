def normalize_currency(code: str) -> str:
    cleaned = code.strip().upper()
    if len(cleaned) != 3 or not cleaned.isascii() or not cleaned.isalpha():
        raise ValueError(f'invalid currency code: {code!r}')
    return cleaned
