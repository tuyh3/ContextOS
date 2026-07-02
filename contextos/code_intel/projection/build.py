"""全量 build 编排(spec §5.1): ctx -> jar -> load -> 检查 -> 单事务 staging 换新 + meta 指纹。

保旧原则(spec §8): jar/load/非零检查 任一失败 -> 不开换新事务, 旧投影 + 旧 meta 原样;
抽样对照超阈 -> staging 事务整体回滚(真保旧, 第三轮 review HIGH)。
unresolved 超标是质量警示: 换新但 build_status=degraded(数据仍可用)。
"""
from __future__ import annotations

import json
import platform
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from sqlalchemy.engine import Connection, Engine

from contextos.code_intel.projection import schema as S
from contextos.code_intel.projection import store
from contextos.code_intel.projection.build_context import context_fingerprint
from contextos.code_intel.projection.indexer_runner import jar_fingerprint, run_indexer
from contextos.code_intel.projection.jsonl_load import load_all_rows

RowsByTable = Mapping[str, list[dict[str, Any]]]
Runner = Callable[..., None]
Loader = Callable[..., RowsByTable]
# 抽样器吃 Connection(同事务读 staging 新行), 返回 [0,1] 偏差率(spec §3.1 条件 3)
Sampler = Callable[[Connection], float]


class _SampleMismatch(Exception):
    def __init__(self, ratio: float) -> None:
        self.ratio = ratio
        super().__init__(f"sample mismatch {ratio:.2%}")


class _SamplerCrash(Exception):
    """F6: sampler 自身炸(JDT 猝死等)= 对照不可信, 与超阈同路: 回滚保旧返 degraded,
    不裸抛(与 runner/loader 失败返 degraded 的对称性)。"""


def _default_runner(**kw: Any) -> None:
    run_indexer(**kw)


def _default_loader(out_dir: Path, *, repo_root: Path) -> RowsByTable:
    return load_all_rows(out_dir, repo_root=repo_root)


def _check_nonzero_hard(rows: RowsByTable) -> str | None:
    """硬闸(F2): 三表任一空 = 产出不可信 -> 保旧。"""
    for t in S.NONZERO_HARD_TABLES:
        if not rows.get(t.name):
            return f"nonzero check failed: {t.name} is empty"
    return None


def _soft_empty_tables(rows: RowsByTable) -> list[str]:
    """软闸(F2): 仓风格可能合法为空 -> 不拦换新, 只 degraded 警示。"""
    return [t.name for t in S.NONZERO_SOFT_TABLES if not rows.get(t.name)]


def unresolved_ratio(rows: RowsByTable) -> float:
    calls = rows.get("code_calls") or []
    if not calls:
        return 0.0
    return sum(1 for c in calls if not c.get("resolved")) / len(calls)


def build_projection(*, engine: Engine, repo_root: Path, java_home: str, jar: Path,
                     xmx: str, build_ctx: dict[str, Any], out_dir: Path,
                     indexed_commit: str,
                     runner: Runner = _default_runner,
                     loader: Loader = _default_loader,
                     sampler: Sampler | None = None,
                     unresolved_max: float = 0.15,
                     sample_max_mismatch: float = 0.05) -> dict[str, Any]:
    S.ensure_projection_schema(engine)
    ctx_file = out_dir / "build_context.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx_file.write_text(json.dumps(build_ctx, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        runner(java_home=java_home, jar=jar, xmx=xmx, ctx_file=ctx_file, out_dir=out_dir)
        rows = loader(out_dir, repo_root=repo_root)
    except Exception as exc:  # jar 炸 / JSONL 坏 -> 保旧(spec §8)
        return {"status": "degraded", "detail": f"{type(exc).__name__}: {exc}"}

    err = _check_nonzero_hard(rows)
    if err:
        return {"status": "degraded", "detail": err}

    # 质量警示(换新但 degraded), 多条 "; " 拼接: 软表空(F2) + unresolved 超标
    warnings: list[str] = []
    soft_empty = _soft_empty_tables(rows)
    if soft_empty:
        warnings.append("soft tables empty: " + ", ".join(soft_empty))
    unresolved = unresolved_ratio(rows)
    if unresolved > unresolved_max:
        warnings.append(f"unresolved ratio {unresolved:.2%} > {unresolved_max:.0%}")
    status = "ok" if not warnings else "degraded"
    detail = "; ".join(warnings)

    build_id = uuid.uuid4().hex[:12]
    try:
        # 单事务 staging(第三轮 review HIGH): 灌新 + 抽样 + meta 在同一事务里;
        # 抽样超阈 raise -> 整体回滚, 旧行 + 旧 meta 原样 = **真保旧**(spec §3.1 条件 3)。
        # 代价: 事务在抽样期间(JDT 往返, 数十秒)保持打开 —— 全量(init _step_code)与
        # 增量重建(rebuild_entry)共用 data_dir/projection.lock 单飞锁, 写者同刻只有
        # 一个, 可接受; sampler 在事务内读到的就是 staging 新行。
        with engine.begin() as conn:
            store.replace_all_conn(conn, rows)
            if sampler is not None:
                try:
                    mismatch = sampler(conn)
                except Exception as exc:   # F6: JDT 猝死等 -> 对照不可信, 回滚保旧
                    raise _SamplerCrash(f"{type(exc).__name__}: {exc}") from exc
                if mismatch > sample_max_mismatch:
                    raise _SampleMismatch(mismatch)
            store.set_meta_conn(conn, "projection_build_id", build_id)
            store.set_meta_conn(conn, "last_indexed_commit", indexed_commit)
            store.set_meta_conn(conn, "build_status", status)
            store.set_meta_conn(conn, "build_context_hash", context_fingerprint(build_ctx))
            store.set_meta_conn(conn, "jar_hash", jar_fingerprint(jar) if jar.exists() else "")
            store.set_meta_conn(conn, "jdk_fingerprint", f"{java_home}|{platform.machine()}")
            store.set_meta_conn(conn, "schema_version", S.PROJECTION_SCHEMA_VERSION)
    except _SampleMismatch as sm:
        return {"status": "degraded",
                "detail": f"sample check mismatch {sm.ratio:.2%} > {sample_max_mismatch:.0%}"}
    except _SamplerCrash as sc:
        return {"status": "degraded", "detail": f"sampler crashed: {sc}"}
    except Exception as exc:  # noqa: BLE001
        # HIGH-2(最终 review): staging swap 期任何意外(如复合 PK IntegrityError 逃过
        # loader 防御)不裸抛 —— engine.begin 上下文里 raise 已回滚, 保旧成立; 兜成
        # degraded 与 runner/loader 失败对称(放专属 catch 之后, 不吞它们的语义)。
        return {"status": "degraded",
                "detail": f"swap failed: {type(exc).__name__}: {exc}"}
    counts = store.table_counts(engine)
    return {"status": status, "detail": detail, "build_id": build_id, "counts": counts}
