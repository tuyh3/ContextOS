# contextos/orchestrator/tests/test_public_api.py
def test_public_api_importable():
    import contextos.orchestrator as o
    for name in ["ProviderResult", "ProviderCandidate",
                 "CorroboratedCandidate", "corroborate", "score_bridge", "eligible_bridges",
                 "RagProjection", "build_rag_projection",
                 "apply_folding", "infer_change_type", "assemble_impact_map",
                 "ProviderRegistry", "CheapBridge", "RerankBridge",
                 "build_default_registry", "build_rag_query",
                 "run_impact_analysis", "run_and_persist", "analyze"]:
        assert hasattr(o, name), name
