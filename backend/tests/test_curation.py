import numpy as np
import pytest

from app.search import search
from app.storage import ImageEntry, IndexStore


def _entry(image_id, path=None):
    return ImageEntry(
        id=image_id, path=path or f"/imgs/{image_id}.png",
        thumbnail_path=f"/t/{image_id}.jpg", ocr_text="", objects=[],
        mtime=0.0, size=0,
    )


def _store(tmp_path, ids):
    store = IndexStore(tmp_path, embedding_dim=2)
    store.load()
    for image_id in ids:
        store.upsert(_entry(image_id), np.array([1.0, 0.0], dtype=np.float32))
    return store


def test_favorites_toggle_and_filter(tmp_path):
    store = _store(tmp_path, ["a", "b", "c"])

    store.set_favorite("a", True)
    store.set_favorite("b", True)
    store.set_favorite("b", False)

    result, total = search(store, favorite=True)
    assert [e.id for e in result] == ["a"]
    assert total == 1
    assert store.get_annotations(["a", "b"]) == {
        "a": {"favorite": True, "user_tags": [], "note": ""},
        "b": {"favorite": False, "user_tags": [], "note": ""},
    }


def test_user_tags_replace_dedupe_and_filter(tmp_path):
    store = _store(tmp_path, ["a", "b"])

    assert store.set_user_tags("a", [" hero ", "hero", "Q4", ""]) == ["hero", "Q4"]
    store.set_user_tags("b", ["draft"])

    assert [e.id for e in search(store, user_tag="hero")[0]] == ["a"]
    assert store.distinct_user_tags() == ["draft", "hero", "Q4"]

    store.set_user_tags("a", [])
    assert search(store, user_tag="hero")[0] == []


def test_notes_upsert_and_clear(tmp_path):
    store = _store(tmp_path, ["a"])

    assert store.set_note("a", "  needs crop  ") == "needs crop"
    assert store.get_annotations(["a"])["a"]["note"] == "needs crop"
    assert store.set_note("a", "   ") == ""
    assert store.get_annotations(["a"])["a"]["note"] == ""


def test_collections_crud_and_membership_filter(tmp_path):
    store = _store(tmp_path, ["a", "b", "c"])

    campaign = store.create_collection("Campaign")
    assert campaign["count"] == 0
    with pytest.raises(ValueError):
        store.create_collection("  campaign  ")  # unique, case-insensitive

    assert store.add_to_collection(campaign["id"], ["a", "b", "a"]) == 2
    assert store.add_to_collection(campaign["id"], ["b"]) == 0  # already in

    result, total = search(store, collection=campaign["id"])
    assert {e.id for e in result} == {"a", "b"}
    assert total == 2

    assert store.remove_from_collection(campaign["id"], ["a"]) == 1
    assert [e.id for e in search(store, collection=campaign["id"])[0]] == ["b"]

    listed = store.list_collections()
    assert listed == [{"id": campaign["id"], "name": "Campaign",
                       "created_at": campaign["created_at"], "count": 1}]

    assert store.rename_collection(campaign["id"], "Campaign 2024") is True
    assert store.list_collections()[0]["name"] == "Campaign 2024"

    assert store.delete_collection(campaign["id"]) is True
    assert store.list_collections() == []
    assert store.rename_collection("gone", "x") is False


def test_add_to_unknown_collection_raises(tmp_path):
    store = _store(tmp_path, ["a"])
    with pytest.raises(KeyError):
        store.add_to_collection("missing", ["a"])


def test_bulk_actions_ignore_stale_ids_instead_of_failing_the_batch(tmp_path):
    store = _store(tmp_path, ["a", "b"])
    collection = store.create_collection("c")

    # A selection that references a since-deleted image must still apply to the
    # rows that do exist, not raise a foreign-key error for the whole batch.
    assert store.set_favorites(["a", "ghost", "b"], True) == 2
    assert store.add_user_tags(["a", "ghost"], ["hero"]) == 1
    assert store.add_to_collection(collection["id"], ["ghost", "b"]) == 1

    assert {e.id for e in search(store, favorite=True)[0]} == {"a", "b"}
    assert [e.id for e in search(store, user_tag="hero")[0]] == ["a"]
    assert [e.id for e in search(store, collection=collection["id"])[0]] == ["b"]


def test_curation_is_wiped_when_the_image_row_is_deleted(tmp_path):
    store = _store(tmp_path, ["a"])
    collection = store.create_collection("c")
    store.set_favorite("a", True)
    store.set_user_tags("a", ["keep"])
    store.set_note("a", "note")
    store.add_to_collection(collection["id"], ["a"])

    store.delete_by_path("/imgs/a.png")

    assert store.get_annotations(["a"]) == {"a": {"favorite": False, "user_tags": [], "note": ""}}
    assert store.list_collections()[0]["count"] == 0


def test_curation_survives_reindex_upsert_of_same_path(tmp_path):
    store = _store(tmp_path, ["a"])
    store.set_favorite("a", True)
    store.set_user_tags("a", ["keep"])

    # A reindex re-upserts the same path; process_image reuses the existing id.
    store.upsert(_entry("a"), np.array([0.0, 1.0], dtype=np.float32))

    assert store.get_annotations(["a"])["a"] == {
        "favorite": True, "user_tags": ["keep"], "note": "",
    }
