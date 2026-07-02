"""Test LanceDB vector store wrapper."""
import tempfile


def test_add_and_query():
    from contextos.recall.vector_store import LanceVectorStore
    import numpy as np
    with tempfile.TemporaryDirectory() as tmp:
        store = LanceVectorStore(path=tmp, dim=1024)
        store.add([
            {
                "id": "doc1",
                "text": "first document",
                "source": "test",
                "vector": np.random.rand(1024).astype("float32"),
            },
            {
                "id": "doc2",
                "text": "second document",
                "source": "test",
                "vector": np.random.rand(1024).astype("float32"),
            },
        ])
        results = store.query(np.random.rand(1024).astype("float32"), top_k=2)
        assert len(results) == 2
        assert all("id" in r and "text" in r and "_distance" in r for r in results)
