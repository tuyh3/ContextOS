"""手动 smoke: 对物化语料跑桥 2 provider, 打印 top 候选。

用法: uv run python scripts/smoke_rag_provider.py <materialized_dir> "<key_entities 逗号分隔>"
默认 FakeReranker(快)。
"""
from __future__ import annotations

import json
import sys

from contextos.profile.schema import RagConfig
from contextos.recall.rag_provider import RagProvider
from contextos.recall.reranker.fake import FakeReranker


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    mat_dir, entities = sys.argv[1], sys.argv[2]
    prov = RagProvider(
        materialized_dir=mat_dir, reranker=FakeReranker(), cfg=RagConfig()
    )
    out = prov.search({
        "queries": {"zh": entities, "en": ""},
        "key_entities": [e.strip() for e in entities.split(",") if e.strip()],
        "matched_capabilities": [],
        "corpora": ["business_docs"],
    })
    # out 是 ProviderResult(pydantic); model_dump 出 08 §2 信封 dict 再打印
    dumped = out.model_dump()
    print(json.dumps(
        {"score": dumped["score"], "miss_reason": dumped["miss_reason"],
         "top": dumped["candidates"][:5]},
        ensure_ascii=False, indent=2,
    ))


if __name__ == "__main__":
    main()
