from .storage import ImageEntry, IndexStore


def search(
    store: IndexStore,
    text: str | None = None,
    obj: str | None = None,
    fmt: str | None = None,
    size_min: int | None = None,
    size_max: int | None = None,
    date_field: str = "date_taken",
    date_from: float | None = None,
    date_to: float | None = None,
    width_min: int | None = None,
    width_max: int | None = None,
    height_min: int | None = None,
    height_max: int | None = None,
    sort: str = "date_desc",
    offset: int = 0,
    limit: int = 60,
) -> tuple[list[ImageEntry], int]:
    """Run filtering, sorting, counting, and pagination in SQLite."""
    return store.search(
        text=text,
        obj=obj,
        fmt=fmt,
        size_min=size_min,
        size_max=size_max,
        date_field=date_field,
        date_from=date_from,
        date_to=date_to,
        width_min=width_min,
        width_max=width_max,
        height_min=height_min,
        height_max=height_max,
        sort=sort,
        offset=offset,
        limit=limit,
    )


def find_similar(
    store: IndexStore, image_id: str, limit: int = 20
) -> list[ImageEntry] | None:
    """Run cosine-nearest-neighbor search in SQLite via sqlite-vec."""
    return store.find_similar(image_id, limit=limit)
