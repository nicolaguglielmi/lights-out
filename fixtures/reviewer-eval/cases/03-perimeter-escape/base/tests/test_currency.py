from src.currency import normalize_currency


def test_currency_passthrough() -> None:
    assert normalize_currency('EUR') == 'EUR'
