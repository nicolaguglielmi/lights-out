from src.pagination import paginate


def test_paginate_first_page() -> None:
    res = paginate([1, 2, 3, 4, 5], page=1, page_size=2)
    assert res.items == [1, 2]
    assert res.has_next is True
