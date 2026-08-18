import numpy as np
import pytest

from app.search import find_similar, search
from app.storage import ImageEntry, IndexStore


def _entry(id, colors=None, objects=None, ocr_text="", date_taken=0.0, size=0, path=None):
    return ImageEntry(
        id=id, path=path or f"/imgs/{id}.png", thumbnail_path=f"/t/{id}.jpg",
        ocr_text=ocr_text, colors=colors or [], objects=objects or [],
        mtime=0.0, size=size, date_taken=date_taken,
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
    result, total = search(store, color="green", obj="clover")
    assert [e.id for e in result] == ["a"]
    assert total == 1


def test_search_text_matches_ocr_text(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("a", ocr_text="NETBET BONUS"), [1.0, 0.0]),
        (_entry("b", ocr_text="unrelated"), [0.0, 1.0]),
    ])
    result, _ = search(store, text="netbet")
    assert [e.id for e in result] == ["a"]


def test_search_text_matches_object_tags_too(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("a", objects=["clover", "gold"]), [0.0, 1.0]),
        (_entry("b", objects=["person"]), [0.0, 1.0]),
    ])
    result, _ = search(store, text="clover")
    assert [e.id for e in result] == ["a"]


def test_search_text_matches_filename_folder_and_color(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("filename", path="/campaigns/summer/Christmas Banner.png"), [1.0, 0.0]),
        (_entry("folder", path="/campaigns/roulette/photo.png"), [1.0, 0.0]),
        (_entry("color", colors=["magenta"]), [1.0, 0.0]),
        (_entry("other", path="/unrelated/photo.png", colors=["blue"]), [1.0, 0.0]),
    ])

    assert [e.id for e in search(store, text="Christmas")[0]] == ["filename"]
    assert [e.id for e in search(store, text="roulette")[0]] == ["folder"]
    assert [e.id for e in search(store, text="magenta")[0]] == ["color"]


def test_short_search_matches_all_metadata_without_fts(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("filename", path="/images/AI.png"), [1.0, 0.0]),
        (_entry("ocr", ocr_text="AI"), [1.0, 0.0]),
        (_entry("object", objects=["AI"]), [1.0, 0.0]),
        (_entry("color", colors=["AI"]), [1.0, 0.0]),
    ])

    results, total = search(store, text="AI")
    assert total == 4
    assert {entry.id for entry in results} == {"filename", "ocr", "object", "color"}


def test_search_quotes_fts_input_instead_of_treating_it_as_query_syntax(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("literal", ocr_text='sale "OR" bonus'), [1.0, 0.0]),
        (_entry("unrelated", ocr_text="sale only"), [1.0, 0.0]),
    ])

    results, total = search(store, text='sale "OR" bonus')
    assert total == 1
    assert [entry.id for entry in results] == ["literal"]


@pytest.mark.parametrize("payload", ["'", '"', '" OR *', "NEAR(", "***", "%_[]", "\\", "😀"])
def test_search_special_characters_never_become_fts_syntax(tmp_path, payload):
    store = _store_with(tmp_path, [(_entry("safe", ocr_text="ordinary text"), [1.0, 0.0])])

    results, total = search(store, text=payload)

    assert results == []
    assert total == 0


def test_search_text_has_no_semantic_fallback(tmp_path):
    # Regression guard: text search used to also include images via a CLIP
    # similarity score even with no matching OCR text or object tag at all
    # (e.g. searching "clover" surfacing an unrelated baseball photo) — that
    # fallback is gone, so an entry with neither must never be returned.
    store = _store_with(tmp_path, [
        (_entry("a", objects=["baseball glove", "person"], ocr_text=""), [1.0, 0.0]),
    ])
    assert search(store, text="clover") == ([], 0)


def test_search_paginates_with_offset_and_limit_and_reports_total(tmp_path):
    store = _store_with(tmp_path, [
        (_entry(f"e{i}", date_taken=float(i)), [1.0, 0.0]) for i in range(5)
    ])
    result, total = search(store, sort="date_asc", offset=2, limit=2)
    assert [e.id for e in result] == ["e2", "e3"]
    assert total == 5


def test_search_sorts_by_date_desc_by_default(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("old", date_taken=1.0), [1.0, 0.0]),
        (_entry("new", date_taken=3.0), [1.0, 0.0]),
        (_entry("mid", date_taken=2.0), [1.0, 0.0]),
    ])
    result, _ = search(store)
    assert [e.id for e in result] == ["new", "mid", "old"]


def test_search_sorts_by_name_asc(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("banana"), [1.0, 0.0]),
        (_entry("apple"), [1.0, 0.0]),
        (_entry("cherry"), [1.0, 0.0]),
    ])
    result, _ = search(store, sort="name_asc")
    assert [e.id for e in result] == ["apple", "banana", "cherry"]


def test_search_sorts_by_size_desc(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("small", size=10), [1.0, 0.0]),
        (_entry("big", size=1000), [1.0, 0.0]),
        (_entry("mid", size=100), [1.0, 0.0]),
    ])
    result, _ = search(store, sort="size_desc")
    assert [e.id for e in result] == ["big", "mid", "small"]


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


def test_search_and_similarity_do_not_materialize_the_catalog(tmp_path, monkeypatch):
    store = _store_with(tmp_path, [
        (_entry("query", objects=["clover"]), [1.0, 0.0]),
        (_entry("match", objects=["clover"]), [0.9, 0.1]),
    ])

    def fail_if_materialized():
        raise AssertionError("search must query SQLite, not call store.all()")

    monkeypatch.setattr(store, "all", fail_if_materialized)

    results, total = search(store, text="clover")
    similar = find_similar(store, "query")

    assert total == 2
    assert [entry.id for entry in results] == ["match", "query"]
    assert [entry.id for entry in similar] == ["match"]
