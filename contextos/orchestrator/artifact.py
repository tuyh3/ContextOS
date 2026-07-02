# contextos/orchestrator/artifact.py
"""§6 run artifact 落盘:完整审计 / 复现包。run_id 命名 = <YYYYMMDD-HHmmss>-<slug>-<short_hash>。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from contextos.impact_map.schema import ImpactMap
from contextos.orchestrator.corroboration import CorroboratedCandidate
from contextos.orchestrator.provider_io import ProviderResult


def _slug(text: str, *, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:maxlen].strip("-") or "run"


def make_run_id(requirement_summary: str, *, now: datetime, short_hash: str) -> str:
    return f"{now.strftime('%Y%m%d-%H%M%S')}-{_slug(requirement_summary)}-{short_hash}"


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


# 敏感值脱敏防御纵深:run artifact 是客户 audit / 复现文件(落盘持久化)。providers/*.json 直接 dump
# 每个 ProviderResult 的 candidates[].signals(开放 dict)。真路径上 06 provider 用白名单建 signals,
# 绝不吐 value_raw / 表数据快照(敏感值脱敏在 06 已守, assemble 亦守 impact_map),但 providers 原始 dump
# 是最后一道持久化边界 —— 掩掉任何 value-bearing signal 键当兜底(亦护 Plan 10 不可信 MCP host 路径, 红线 #9)。
# 键名取自 ConfigBinding 的敏感/取值字段(dimensions.py)+ 明显的快照值名。掩码保留键名(audit 可见"此处被脱敏")。
_SENSITIVE_SIGNAL_KEYS = frozenset({
    "value_raw", "value_columns", "enum_counts", "snapshot_sql",
    "db_snapshot", "snapshot_value", "raw_value", "sample_values",
})
_REDACTED = "[redacted:red-line-2]"


def _sanitize_provider_dump(dumped: dict) -> dict:
    """落盘前掩掉 provider candidates[].signals 里的 value-bearing 字段(敏感值脱敏兜底)。

    作用于 model_dump() 的副本(不动 live 对象)。只掩值不删键,audit 仍看得见该信号存在但被脱敏。
    """
    for cand in dumped.get("candidates", []) or []:
        sig = cand.get("signals")
        if isinstance(sig, dict):
            for k in list(sig):
                if k in _SENSITIVE_SIGNAL_KEYS:
                    sig[k] = _REDACTED
    return dumped


def write_run_artifact(root, run_id: str, *, raw_input: str, breakdown,
                       impact_map: ImpactMap, cheap_results: dict[str, ProviderResult],
                       rerank_result: ProviderResult | None,
                       corrobs: list[CorroboratedCandidate],
                       trace: list[str], errors: list[str], summary_meta: dict) -> Path:
    run_dir = Path(root) / "runs" / run_id
    (run_dir / "input").mkdir(parents=True, exist_ok=True)
    (run_dir / "providers").mkdir(parents=True, exist_ok=True)

    _dump(run_dir / "summary.json", {"run_id": run_id, **summary_meta})
    (run_dir / "impact_map.json").write_text(impact_map.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "input" / "source.txt").write_text(raw_input or "", encoding="utf-8")
    _dump(run_dir / "input" / "02_parsed.json", breakdown.model_dump())

    all_results = dict(cheap_results)
    if rerank_result is not None:
        all_results[rerank_result.worker_name] = rerank_result
    for worker, res in all_results.items():
        _dump(run_dir / "providers" / f"{worker}.json", _sanitize_provider_dump(res.model_dump()))

    _dump(run_dir / "corroboration.json",
          [{"target": c.target, "kind": c.kind, "score_overall": c.score_overall,
            "confidence_tier": c.confidence_tier, "bridge_scores": c.bridge_scores,
            "consensus_count": c.consensus_count, "rag_score": c.rag_score,
            "folded": c.folded, "hit_workers": c.hit_workers} for c in corrobs])
    _dump(run_dir / "change_type.json",
          {e.id: {"target": e.target, "kind": e.kind, "change_type": e.change_type}
           for e in impact_map.evidence_items})
    (run_dir / "trace.log").write_text("\n".join(trace), encoding="utf-8")
    (run_dir / "errors.log").write_text("\n".join(errors), encoding="utf-8")
    return run_dir
