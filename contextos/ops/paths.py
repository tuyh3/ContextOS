"""ops 组件 B 路径口径(spec Appendix C MUST: 写入==检索同 resolver)。

resolved_materialized_dir = profile.corpus.materialized_dir or <data_dir>/materialized
(与 AppContext.rag_provider 的 materialized_dir 同口径, 防写入/检索分叉)。
confirmed-cases 落 <resolved>/confirmed-cases;空目录也创建(strict scope 不回退全量)。
同义池积累落 <data_dir>/ops-vocab/synonyms.json(客户特定 gitignored)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def resolved_materialized_dir(profile: Any) -> Path:
    mat = profile.corpus.materialized_dir
    if mat:
        return Path(mat).expanduser()
    return Path(profile.storage.data_dir).expanduser() / "materialized"


def confirmed_cases_dir(profile: Any) -> Path:
    return resolved_materialized_dir(profile) / "confirmed-cases"


def ensure_confirmed_cases_dir(profile: Any) -> Path:
    """空目录也创建(spec Appendix C MUST): prefix 永远有效, strict scope 不回退全量。"""
    d = confirmed_cases_dir(profile)
    d.mkdir(parents=True, exist_ok=True)
    return d


def ops_vocab_path(profile: Any) -> Path:
    return Path(profile.storage.data_dir).expanduser() / "ops-vocab" / "synonyms.json"
