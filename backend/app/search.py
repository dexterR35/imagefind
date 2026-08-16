from .storage import ImageEntry, IndexStore


def search(
    store: IndexStore,
    text: str | None = None,
    color: str | None = None,
    obj: str | None = None,
    sort: str = "date_desc",
    offset: int = 0,
    limit: int = 60,
) -> tuple[list[ImageEntry], int]:
    """Run filtering, sorting, counting, and pagination in SQLite."""
    return store.search(
        text=text, color=color, obj=obj, sort=sort, offset=offset, limit=limit
    )


def find_similar(
    store: IndexStore, image_id: str, limit: int = 20
) -> list[ImageEntry] | None:
    """Run cosine-nearest-neighbor search in SQLite via sqlite-vec."""
    return store.find_similar(image_id, limit=limit)
