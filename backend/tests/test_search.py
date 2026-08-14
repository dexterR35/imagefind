import threading

import numpy as np

from app import embeddings
from app.search import find_similar, search
from app.storage import ImageEntry, IndexStore


def _entry(id, colors=None, objects=None, ocr_text=""):
    return ImageEntry(
        id=id, path=f"/imgs/{id}.png", thumbnail_path=f"/t/{id}.jpg",
        ocr_text=ocr_text, colors=colors or [], objects=objects or [],
        mtime=0.0, size=0,
    )


def _store_with(tmp_path, entries_and_vecs):
    store = IndexStore(tmp_path, embedding_dim=2)
    store.load()
    for entry, vec in entries_and_vecs:
        store.upsert(entry, np.array(vec, dtype=np.float32))
    return store


def test_search_filters_by_color_and_object_with_and_logic(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("a", colors=["green"], objects=["clover"]), [1, 0]),
        (_entry("b", colors=["green"], objects=["person"]), [1, 0]),
        (_entry("c", colors=["blue"], objects=["clover"]), [1, 0]),
    ])
    result = search(store, color="green", obj="clover")
    assert [e.id for e in result] == ["a"]


def test_search_text_ranks_ocr_match_above_semantic_only_match(tmp_path, monkeypatch):
    store = _store_with(tmp_path, [
        (_entry("a", ocr_text="NETBET BONUS"), [1.0, 0.0]),
        (_entry("b", ocr_text=""), [0.3, 0.0]),
    ])
    monkeypatch.setattr(embeddings, "embed_text", lambda q: np.array([1.0, 0.0], dtype=np.float32))
    result = search(store, text="netbet")
    assert [e.id for e in result] == ["a", "b"]


def test_search_text_filters_out_unrelated_images(tmp_path, monkeypatch):
    store = _store_with(tmp_path, [
        (_entry("a", ocr_text="NETBET BONUS"), [1.0, 0.0]),
        (_entry("b", ocr_text="unrelated"), [0.0, 1.0]),
    ])
    monkeypatch.setattr(embeddings, "embed_text", lambda q: np.array([1.0, 0.0], dtype=np.float32))
    result = search(store, text="netbet")
    assert [e.id for e in result] == ["a"]


def test_find_similar_excludes_self_and_orders_by_similarity(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("a"), [1.0, 0.0]),
        (_entry("b"), [0.9, 0.1]),
        (_entry("c"), [0.0, 1.0]),
    ])
    result = find_similar(store, "a")
    assert [e.id for e in result] == ["b", "c"]


def test_search_does_not_hold_lock_during_embed_text_call(tmp_path, monkeypatch):
    # embed_text is a standalone CLIP call with no store access; holding
    # store.lock during it would stall a concurrent reindex's upsert/save.
    # RLock lets the *same* thread re-acquire freely, so contention must be
    # checked from a genuinely different thread.
    store = _store_with(tmp_path, [(_entry("a", ocr_text="x"), [1.0, 0.0])])
    lock_was_free: dict[str, bool] = {}

    def fake_embed_text(q):
        def check():
            acquired = store.lock.acquire(blocking=False)
            lock_was_free["value"] = acquired
            if acquired:
                store.lock.release()

        checker = threading.Thread(target=check)
        checker.start()
        checker.join(timeout=2)
        return np.array([1.0, 0.0], dtype=np.float32)

    monkeypatch.setattr(embeddings, "embed_text", fake_embed_text)
    search(store, text="x")

    assert lock_was_free.get("value") is True


def test_find_similar_unknown_id_returns_none(tmp_path):
    # None (not []) distinguishes "no such image" from "image exists but has
    # no similar results" — the /search/similar/{id} endpoint needs that
    # distinction to return 404 vs 200.
    store = _store_with(tmp_path, [(_entry("a"), [1.0, 0.0])])
    assert find_similar(store, "missing") is None
