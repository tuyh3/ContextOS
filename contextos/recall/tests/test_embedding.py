"""Test embedding.py: load BGE-M3 / Qwen3-Embedding, encode text."""

import pytest


@pytest.mark.integration
def test_bge_m3_loads_and_encodes():
    from contextos.recall.embedding import EmbeddingModel
    model = EmbeddingModel(name="BAAI/bge-m3", device="cpu")
    vec = model.encode("test text")
    assert len(vec.shape) == 1
    assert vec.shape[0] in (1024,)  # BGE-M3 1024-dim


@pytest.mark.integration
def test_qwen3_embedding_loads():
    from contextos.recall.embedding import EmbeddingModel
    model = EmbeddingModel(name="Qwen/Qwen3-Embedding-0.6B", device="cpu")
    vec = model.encode("test text")
    assert len(vec.shape) == 1
    # Qwen3-Embedding-0.6B is 1024-dim
    assert vec.shape[0] in (1024, 768)
