"""Server-side pagination shared by every list endpoint.

A list endpoint pages when the caller sends `page` (1-based) and optionally
`page_size`. It then returns an envelope:

    {"items": [...], "total": 1234, "page": 2, "page_size": 25,
     "pages": 50, "has_prev": true, "has_next": true}

Without `page` the endpoint keeps its original response (a plain list), so the
offline cache and anything else already calling it are unaffected.

A page past the end is clamped to the last page rather than returning an empty
list, so deleting the last row on a page never strands the user on nothing.
"""

from math import ceil

from fastapi import Query

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 200

PageParam = Query(None, ge=1, description="1-based page number. Omit for the unpaged response.")
PageSizeParam = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Rows per page (max 200).")


def _envelope(items, total, page, page_size):
    pages = max(1, ceil(total / page_size))
    return {
        "items": items, "total": total, "page": page, "page_size": page_size,
        "pages": pages, "has_prev": page > 1, "has_next": page < pages,
    }


def paginate_sql(connection, sql: str, params, page: int, page_size: int, mapper=dict):
    """Page an ORDER BY'd query. `sql` must not contain LIMIT/OFFSET."""
    params = list(params)
    total = connection.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    pages = max(1, ceil(total / page_size))
    page = min(max(1, page), pages)
    rows = connection.execute(f"{sql} LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size]).fetchall()
    return _envelope([mapper(r) for r in rows], total, page, page_size)


def paginate_list(items: list, page: int, page_size: int):
    """Page a list built in Python (for results assembled after the query)."""
    total = len(items)
    pages = max(1, ceil(total / page_size))
    page = min(max(1, page), pages)
    start = (page - 1) * page_size
    return _envelope(items[start:start + page_size], total, page, page_size)
