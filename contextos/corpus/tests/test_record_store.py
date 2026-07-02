import pytest

from contextos.storage.db import make_engine


@pytest.fixture
def store(tmp_path):
    from contextos.corpus.record_store import RecordStore
    engine = make_engine(f"sqlite:///{tmp_path / 'rec.db'}")
    return RecordStore(engine)


def test_get_missing_returns_none(store):
    assert store.get_hash("doc/a.md") is None


def test_upsert_then_get(store):
    store.upsert("doc/a.md", "hash1", "mat/doc/a.md", "fake")
    assert store.get_hash("doc/a.md") == "hash1"
    rec = store.get("doc/a.md")
    assert rec.sidecar_path == "mat/doc/a.md"
    assert rec.ocr_backend == "fake"
    assert rec.materialized_at  # 非空 ISO 串


def test_upsert_updates_existing(store):
    store.upsert("doc/a.md", "hash1", "p1", "fake")
    store.upsert("doc/a.md", "hash2", "p2", "paddle")
    assert store.get_hash("doc/a.md") == "hash2"
    assert store.get("doc/a.md").sidecar_path == "p2"


def test_all_doc_ids_and_delete(store):
    store.upsert("a.md", "h", "p", "fake")
    store.upsert("b.md", "h", "p", "fake")
    assert store.all_doc_ids() == {"a.md", "b.md"}
    store.delete("a.md")
    assert store.all_doc_ids() == {"b.md"}
