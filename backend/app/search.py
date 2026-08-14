from . import config, embeddings
from .storage import ImageEntry, IndexStore


def search(
    store: IndexStore,
    text: str | None = None,
    color: str | None = None,
    obj: str | None = None,
    limit: int = 60,
) -> list[ImageEntry]:
    # embed_text is a standalone CLIP forward pass that never touches the
    # store, so it's computed before acquiring the lock — otherwise it would
    # hold store.lock for the duration of a slow model call, stalling any
    # concurrent reindex upsert/save or other search/thumbnail request.
    query_embedding = embeddings.embed_text(text) if text else None

    with store.lock:
        entries = store.all()
        candidates = list(range(len(entries)))

        if color:
            candidates = [i for i in candidates if color in entries[i].colors]
        if obj:
            candidates = [i for i in candidates if obj in entries[i].objects]

        if text:
            text_lower = text.lower()
            text_matches = {i for i in candidates if text_lower in entries[i].ocr_text.lower()}
            scores = {i: embeddings.cosine_similarity(query_embedding, store.embeddings[i]) for i in candidates}
            matched = [
                i for i in candidates
                if i in text_matches or scores[i] >= config.TEXT_SIMILARITY_THRESHOLD
            ]
            ranked = sorted(
                matched,
                key=lambda i: scores[i] + (0.25 if i in text_matches else 0.0),
                reverse=True,
            )
        else:
            ranked = candidates

        return [entries[i] for i in ranked[:limit]]


def find_similar(store: IndexStore, image_id: str, limit: int = 20) -> list[ImageEntry] | None:
    """Returns None if image_id doesn't exist, distinct from an empty list
    (which means it exists but has no similar images) — the caller (the
    /search/similar/{id} endpoint) needs that distinction to return 404 vs
    200, and doing the existence check and the lookup under the same lock
    acquisition (rather than as two separate store.get() calls at different
    times) avoids a race where a concurrent prune removes the entry between
    them."""
    with store.lock:
        entry = store.get(image_id)
        if entry is None:
            return None
        query_embedding = store.get_embedding(image_id)
        entries = store.all()
        scored = []
        for i, other in enumerate(entries):
            if other.id == image_id:
                continue
            sim = embeddings.cosine_similarity(query_embedding, store.embeddings[i])
            scored.append((sim, i))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entries[i] for _, i in scored[:limit]]
