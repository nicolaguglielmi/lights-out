from dataclasses import dataclass


@dataclass(frozen=True)
class Page:
    items: list
    total: int
    has_next: bool


def paginate(items: list, page: int, page_size: int) -> Page:
    start = (page - 1) * page_size
    end = start + page_size
    return Page(items=items[start:end], total=len(items), has_next=end <= len(items))
