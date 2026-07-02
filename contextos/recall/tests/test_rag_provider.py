import shutil

import pytest

_HAS_RG = shutil.which("rg") is not None


def _materialized(tmp_path):
    d = tmp_path / "materialized"
    (d / "order").mkdir(parents=True)
    (d / "order" / "charge.md").write_text(
        "# 计费配置\n动态计费由 DynamicChargingSVImpl 处理\n"
        "[image 1 OCR]\nCONF_PROVINCE_TAX RULE_TYPE_TAX\n",
        encoding="utf-8",
    )
    (d / "order" / "noise.md").write_text("无关\n内容\n", encoding="utf-8")
    return d


def _query():
    return {
        "queries": {"zh": "动态计费配置", "en": "dynamic charging config"},
        "key_entities": ["DynamicChargingSVImpl", "动态计费", "CONF_PROVINCE_TAX"],
        "matched_capabilities": [],
        "corpora": ["business_docs"],
    }


@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_provider_returns_08_contract_shape(tmp_path):
    from contextos.recall.rag_provider import RagProvider
    from contextos.recall.reranker.fake import FakeReranker
    from contextos.orchestrator.provider_io import ProviderResult
    from contextos.profile.schema import RagConfig
    prov = RagProvider(
        materialized_dir=_materialized(tmp_path),
        reranker=FakeReranker(),
        cfg=RagConfig(max_passages_per_doc=3, window_radius=4),
    )
    out = prov.search(_query())
    # 复用 08 §2 共享契约对象(非 plain dict)
    assert isinstance(out, ProviderResult)
    assert out.worker_name == "rag"
    assert 0.0 <= out.score <= 1.0
    assert out.miss_reason is None
    targets = [c.target for c in out.candidates]
    assert "order/charge.md" in targets
    assert "order/noise.md" not in targets
    top = out.candidates[0]
    assert top.kind == "BUSINESS_DOC"
    # rag 专属信号走开放 signals(强类型 RagSignals dump)
    assert "rerank_score" in top.signals and "snippet" in top.signals
    # 命中点含 OCR 截图文本 -> evidence_origin 标 ocr
    assert any(c.signals.get("evidence_origin") == "ocr" for c in out.candidates)
    # score_breakdown 是 dict[str,float]: 用数值标志表达跑了哪些检索路
    assert out.score_breakdown["sparse_path"] == 1.0
    assert out.score_breakdown["dense_path"] == 0.0


@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_provider_miss_when_no_hits(tmp_path):
    from contextos.recall.rag_provider import RagProvider
    from contextos.recall.reranker.fake import FakeReranker
    from contextos.profile.schema import RagConfig
    prov = RagProvider(
        materialized_dir=_materialized(tmp_path), reranker=FakeReranker(), cfg=RagConfig()
    )
    out = prov.search({"queries": {"zh": "", "en": ""}, "key_entities": ["ZZZ_NO_MATCH"],
                       "matched_capabilities": [], "corpora": []})
    assert out.candidates == []
    assert out.score == 0.0
    assert out.miss_reason is not None


@pytest.mark.skipif(not _HAS_RG, reason="ripgrep 未安装")
def test_provider_relative_materialized_dir_with_path_prefix(tmp_path, monkeypatch):
    """回归(live 实测 2026-06-30): materialized_dir 相对路径(线上 'database/materialized')
    且查询带 path_prefixes(confirmed-cases corpus 首个用 prefix scope 的子集)时, 旧 ripgrep_hits
    把 root 叠加两次 -> rg ENOENT -> provider fail-safe 吞成 miss -> 案例库召回静默为空。
    修后应正常召回该子集文档。"""
    from contextos.recall.rag_provider import RagProvider
    from contextos.recall.reranker.fake import FakeReranker
    from contextos.profile.schema import RagConfig
    (tmp_path / "mat" / "confirmed-cases").mkdir(parents=True)
    (tmp_path / "mat" / "confirmed-cases" / "case.md").write_text(
        "deferred_charge 递延收费 NEEDLE\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    prov = RagProvider(materialized_dir="mat", reranker=FakeReranker(),
                       cfg=RagConfig(max_passages_per_doc=3, window_radius=4))
    out = prov.search({
        "queries": {"zh": "递延收费", "en": "deferred charge"},
        "key_entities": ["NEEDLE", "deferred_charge"],
        "matched_capabilities": [],
        "path_prefixes": ["confirmed-cases"],
        "corpora": ["confirmed-cases"],
    })
    assert out.miss_reason is None
    assert "confirmed-cases/case.md" in [c.target for c in out.candidates]


def test_provider_failsafe_missing_dir(tmp_path):
    """物化目录不存在 -> miss, 不抛。"""
    from contextos.recall.rag_provider import RagProvider
    from contextos.recall.reranker.fake import FakeReranker
    from contextos.profile.schema import RagConfig
    prov = RagProvider(
        materialized_dir=tmp_path / "nonexistent", reranker=FakeReranker(), cfg=RagConfig()
    )
    out = prov.search(_query())
    assert out.score == 0.0
    assert out.miss_reason is not None


def test_provider_dense_enabled_not_implemented(tmp_path):
    """dense 开关打开但 MVP 无 dense impl -> 显式 NotImplementedError(指向 Plan 03.5)。"""
    from contextos.recall.rag_provider import RagProvider
    from contextos.recall.reranker.fake import FakeReranker
    from contextos.profile.schema import RagConfig
    prov = RagProvider(
        materialized_dir=_materialized(tmp_path), reranker=FakeReranker(),
        cfg=RagConfig(dense_enabled=True),
    )
    with pytest.raises(NotImplementedError):
        prov.search(_query())
