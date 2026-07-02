"""ContextOS corpus 物化地基(materialization + OCR)。"""
from contextos.corpus.leakage import LeakageGate
from contextos.corpus.materialize import materialize_corpus

__all__ = ["LeakageGate", "materialize_corpus"]
