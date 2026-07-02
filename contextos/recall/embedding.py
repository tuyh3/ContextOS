"""Embedding model loader for POC.

Supports BGE-M3 (BAAI) and Qwen3-Embedding-0.6B. Uses sentence-transformers.
"""
from __future__ import annotations

import numpy as np
from sentence_transformers import SentenceTransformer


class EmbeddingModel:
    def __init__(self, name: str, device: str = "cpu"):
        self.name = name
        self._st = SentenceTransformer(name, device=device, trust_remote_code=True)

    def encode(self, text: str, normalize: bool = True) -> np.ndarray:
        # convert_to_numpy=True forces sentence-transformers to return
        # ndarray instead of its default torch.Tensor (pyright then sees
        # the declared return type matching). Functionally equivalent
        # at runtime.
        v = self._st.encode(
            [text],
            normalize_embeddings=normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return np.asarray(v[0])

    def encode_batch(
        self, texts: list[str], normalize: bool = True, batch_size: int = 32
    ) -> np.ndarray:
        v = self._st.encode(
            texts,
            normalize_embeddings=normalize,
            batch_size=batch_size,
            show_progress_bar=True,
            convert_to_numpy=True,
        )
        return np.asarray(v)
