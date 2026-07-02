# contextos/orchestrator/change_type.py
"""确定性 change_type 启发式(用户裁决 1:LLM 粗判 deferred -> 09 eval-gated)。

design §7 写 LLM 粗判,同段写「v1 不追精确,先标记」。v1 用 kind + breakdown.actions
推导,确定 + 可单测 + 零额外 LLM。LLM 精修 + git diff/调用关系信号 [v1 deferred -> 09 eval-gated]。
"""
from __future__ import annotations

from contextos.impact_map.enums import KIND_CONFIG_DIMENSION, KIND_SQL_DIMENSION


def infer_change_type(kind: str, actions: list[str]) -> str:
    """kind + actions(add/modify/delete) -> ChangeType(01 §3.2 11 值)。"""
    has_add = "add" in (actions or [])
    if kind == "METHOD":
        return "add_method" if has_add else "modify_method"
    if kind in ("CLASS", "INTERFACE"):
        return "add_class" if has_add else "modify_class"
    if kind == "FIELD":
        return "modify_class"                       # 字段改 ~ 类修改
    if kind in KIND_SQL_DIMENSION:
        return "db_config_change"                   # v1 启发(schema-vs-config 精分 deferred 09/dst_dataset_type)
    if kind in KIND_CONFIG_DIMENSION:
        return "config_change"
    if kind in ("API_ENTRY", "JOB", "BATCH", "MSG"):
        return "modify_method"                      # 入口归方法级
    return "unknown"
