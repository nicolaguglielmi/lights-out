def can_process(used_today: int, max_requests: int) -> bool:
    if used_today < 0 or max_requests < 1:
        raise ValueError('invalid quota inputs')
    return used_today <= max_requests
