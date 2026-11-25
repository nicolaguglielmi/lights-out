import pytest
from src.currency import normalize_currency


def test_currency_passthrough() -> None:
    assert normalize_currency('EUR') == 'EUR'


def test_normalize_currency_strips_and_uppercases() -> None:
    assert normalize_currency(' usd ') == 'USD'


def test_normalize_currency_rejects_invalid_code() -> None:
    with pytest.raises(ValueError):
        normalize_currency('EU1')
