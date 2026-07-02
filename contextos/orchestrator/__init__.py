"""08 数据流编排:编排者 + corroboration(confidence SSOT 实现)。"""
from contextos.orchestrator.assemble import assemble_impact_map, to_evidence_item
from contextos.orchestrator.change_type import infer_change_type
from contextos.orchestrator.corroboration import (
    CorroboratedCandidate,
    bucket,
    corroborate,
    corroborate_one,
    eligible_bridges,
    score_bridge,
)
from contextos.orchestrator.folding import apply_folding, is_folded
from contextos.orchestrator.pipeline import analyze, run_and_persist, run_impact_analysis
from contextos.orchestrator.provider_io import ProviderCandidate, ProviderResult
from contextos.orchestrator.rag_projection import RagProjection, build_rag_projection
from contextos.orchestrator.registry import (
    CheapBridge,
    ProviderRegistry,
    RerankBridge,
    build_default_registry,
    build_rag_query,
)

__all__ = [
    "ProviderResult", "ProviderCandidate",
    "CorroboratedCandidate", "corroborate", "corroborate_one", "score_bridge",
    "eligible_bridges", "bucket",
    "RagProjection", "build_rag_projection",
    "apply_folding", "is_folded",
    "infer_change_type",
    "assemble_impact_map", "to_evidence_item",
    "ProviderRegistry", "CheapBridge", "RerankBridge", "build_default_registry", "build_rag_query",
    "run_impact_analysis", "run_and_persist", "analyze",
]
