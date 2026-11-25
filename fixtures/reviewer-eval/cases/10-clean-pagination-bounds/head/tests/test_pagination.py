import pytest
from src.pagination import paginate


def test_paginate_first_page() -> None:
    res = paginate([1, 2, 3, 4, 5], page=1, page_size=2)
    assert res.items == [1, 2]
    assert res.has_next is True


def test_paginate_exact_multiple_last_page_has_no_next() -> None:
    res = paginate([1, 2, 3, 4], page=2, page_size=2)
    assert res.items == [3, 4]
    assert res.total == 4
    assert res.has_next is False


def test_paginate_rejects_out_of_range_arguments() -> None:
    with pytest.raises(ValueError):
        paginate([1], page=0, page_size=10)
    with pytest.raises(ValueError):
        paginate([1], page=1, page_size=101)
