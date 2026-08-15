import numpy as np

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


def test_search_text_matches_ocr_text(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("a", ocr_text="NETBET BONUS"), [1.0, 0.0]),
        (_entry("b", ocr_text="unrelated"), [0.0, 1.0]),
    ])
    result = search(store, text="netbet")
    assert [e.id for e in result] == ["a"]


def test_search_text_matches_object_tags_too(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("a", objects=["clover", "gold"]), [0.0, 1.0]),
        (_entry("b", objects=["person"]), [0.0, 1.0]),
    ])
    result = search(store, text="clover")
    assert [e.id for e in result] == ["a"]


def test_search_text_has_no_semantic_fallback(tmp_path):
    # Regression guard: text search used to also include images via a CLIP
    # similarity score even with no matching OCR text or object tag at all
    # (e.g. searching "clover" surfacing an unrelated baseball photo) — that
    # fallback is gone, so an entry with neither must never be returned.
    store = _store_with(tmp_path, [
        (_entry("a", objects=["baseball glove", "person"], ocr_text=""), [1.0, 0.0]),
    ])
    assert search(store, text="clover") == []


def test_find_similar_excludes_self_and_orders_by_similarity(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("a"), [1.0, 0.0]),
        (_entry("b"), [0.9, 0.1]),
        (_entry("c"), [0.0, 1.0]),
    ])
    result = find_similar(store, "a")
    assert [e.id for e in result] == ["b", "c"]


def test_find_similar_unknown_id_returns_none(tmp_path):
    # None (not []) distinguishes "no such image" from "image exists but has
    # no similar results" — the /search/similar/{id} endpoint needs that
    # distinction to return 404 vs 200.
    store = _store_with(tmp_path, [(_entry("a"), [1.0, 0.0])])
    assert find_similar(store, "missing") is None


def test_find_similar_respects_limit_with_many_entries(tmp_path):
    # Create 50 entries with known similarities to the query vector [1,0].
    # Insert them in reverse order (e49, e48, ..., e0) *before* the query
    # entry, so storage order is decoupled from similarity rank AND the
    # query itself lands at a non-zero storage index (index 50, not 0) —
    # a broken self_index computation (e.g. always assuming index 0) would
    # still incorrectly exclude e49 or fail to exclude the query itself.
    entries_and_vecs = []
    for i in range(50, 0, -1):
        angle_component = (i - 1) / 100.0
        entries_and_vecs.append(
            (_entry(f"e{i-1}"), [1.0 - angle_component, angle_component])
        )
    entries_and_vecs.append((_entry("query"), [1.0, 0.0]))
    store = _store_with(tmp_path, entries_and_vecs)
    assert store._by_id["query"] == 50

    result = find_similar(store, "query", limit=5)

    assert len(result) == 5
    assert [e.id for e in result] == ["e0", "e1", "e2", "e3", "e4"]


def test_find_similar_limit_zero_or_negative_returns_empty_list(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("query"), [1.0, 0.0]),
        (_entry("a"), [0.9, 0.1]),
        (_entry("b"), [0.0, 1.0]),
    ])

    assert find_similar(store, "query", limit=0) == []
    assert find_similar(store, "query", limit=-1) == []
