from . import config, embeddings
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

        if text:
            text_lower = text.lower()
            text_matches = {i for i in candidates if text_lower in entries[i].ocr_text.lower()}
            query_embedding = embeddings.embed_text(text)
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


def find_similar(store: IndexStore, image_id: str, limit: int = 20) -> list[ImageEntry]:
    with store.lock:
        entry = store.get(image_id)
        if entry is None:
            return []
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
