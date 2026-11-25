from dataclasses import dataclass


@dataclass(frozen=True)
class Page:
    items: list
    total: int
    has_next: bool


def paginate(items: list, page: int, page_size: int) -> Page:
    if page < 1 or not 1 <= page_size <= 100:
        raise ValueError('page >= 1 and 1 <= page_size <= 100 required')
    start = (page - 1) * page_size
    end = start + page_size
    return Page(items=items[start:end], total=len(items), has_next=end < len(items))
