import numpy as np

from .storage import ImageEntry, IndexStore


def search(
    store: IndexStore,
    text: str | None = None,
    color: str | None = None,
    obj: str | None = None,
    limit: int = 60,
) -> list[ImageEntry]:
    with store.lock:
        entries = store.all()
        candidates = list(range(len(entries)))

        if color:
            candidates = [i for i in candidates if color in entries[i].colors]
        if obj:
            candidates = [i for i in candidates if obj in entries[i].objects]

        # Deliberately no CLIP semantic-similarity fallback here: it surfaced
        # images with no matching text or tag at all (e.g. searching "clover"
        # returning an unrelated baseball photo), which was more confusing
        # than useful. Text search is OCR text + object tags only now — every
        # result is directly explainable by what's in it. CLIP is still used
        # for "Find Similar" below and for custom-tag matching in objects.py.
        if text:
            text_lower = text.lower()
            candidates = [
                i for i in candidates
                if text_lower in entries[i].ocr_text.lower()
                or any(text_lower in o.lower() for o in entries[i].objects)
            ]

        return [entries[i] for i in candidates[:limit]]


def find_similar(store: IndexStore, image_id: str, limit: int = 20) -> list[ImageEntry] | None:
    """Returns None if image_id doesn't exist, distinct from an empty list
    (which means it exists but has no similar images) — the caller (the
    /search/similar/{id} endpoint) needs that distinction to return 404 vs
    200, and doing the existence check and the lookup under the same lock
    acquisition (rather than as two separate store.get() calls at different
    times) avoids a race where a concurrent prune removes the entry between
    them.

    Similarity is one vectorized matrix-vector product against every stored
    embedding, not a per-entry Python loop — at hundreds of thousands of
    entries the loop was the actual bottleneck, not model inference."""
    with store.lock:
        entry = store.get(image_id)
        if entry is None:
            return None
        self_index = store._by_id[image_id]
        query_embedding = store.get_embedding(image_id)
        entries = store.all()

        if len(entries) <= 1:
            return []

        sims = store.embeddings @ query_embedding
        sims[self_index] = -np.inf

        k = min(max(limit, 0), len(entries) - 1)
        if k == 0:
            return []
        top = np.argpartition(-sims, k - 1)[:k]
        top = top[np.argsort(-sims[top])]
        return [entries[i] for i in top]
