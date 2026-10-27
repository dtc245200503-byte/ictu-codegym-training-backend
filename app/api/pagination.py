from fastapi import Response


def page_metadata(*, total: int, page: int, page_size: int) -> dict[str, int | bool]:
    pages = (total + page_size - 1) // page_size
    return {
        "total": total,
        "pages": pages,
        "page": page,
        "page_size": page_size,
        "has_previous": page > 1 and pages > 0,
        "has_next": page < pages,
    }


def set_pagination_headers(
    response: Response, *, total: int, page: int, page_size: int
) -> None:
    metadata = page_metadata(total=total, page=page, page_size=page_size)
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Total-Pages"] = str(metadata["pages"])
    response.headers["X-Current-Page"] = str(page)
    response.headers["X-Page-Size"] = str(page_size)
    response.headers["X-Has-Previous"] = str(metadata["has_previous"]).lower()
    response.headers["X-Has-Next"] = str(metadata["has_next"]).lower()
