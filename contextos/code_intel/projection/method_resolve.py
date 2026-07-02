"""裸方法 FQN 解析: DB method_fqn 一律带签名段(pkg.Cls.m(params)), 而人/LLM/上游
天然产出裸形态 pkg.Cls.m —— 精确匹配会假阴(SymbolNotFound / 零边)。这里把裸名按
前缀 'fqn(' 收敛到带签名形态, 唯一则补全, 多重载则报可执行的歧义错。

去重键 = DISTINCT method_fqn(不是 distinct 行/位置): 同一 method_fqn 合法地出现在
多行(vendored 类被多模块重复索引), 精确查本来就 .first() 任取 —— 裸查不得比精确查
对同样的下游歧义更严。

方言备注(红线 #6): startswith+autoescape 渲染为可移植 LIKE ... ESCAPE, 但大小写语义
随方言走(SQLite ASCII 不敏感 / PG 敏感); FQN 来源是索引器原样输出, 大小写规范, 实务
无影响。SQLite 默认 BINARY collation 下前缀 LIKE 不走 idx_cm_fqn(单次全扫 ~413K 行,
数十 ms, 仅裸名路径), 可接受。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.engine import Connection

from contextos.code_intel.projection import schema as S

_MAX_LISTED = 10


class AmbiguousMethodFqn(LookupError):
    """裸 FQN 命中 >1 个带签名 method_fqn(重载)。"""

    def __init__(self, fqn: str, candidates: list[str]) -> None:
        self.fqn = fqn
        self.candidates = sorted(candidates)        # deterministic message/inspection
        listed = ", ".join(self.candidates[:_MAX_LISTED])
        more = "" if len(self.candidates) <= _MAX_LISTED else f" (showing {_MAX_LISTED})"
        super().__init__(
            f"ambiguous bare method fqn {fqn!r}: {len(self.candidates)} distinct "
            f"candidates{more}: {listed}; pass a signature-qualified FQN")


def resolve_bare_method_fqn(conn: Connection, fqn: str) -> str | None:
    """含 '(' -> 已带签名, 原样返回。否则 DISTINCT 前缀匹配 'fqn(' 走 idx_cm_fqn;
    autoescape 必开: 1045 个真实方法名含 '_'(LIKE 单字符通配)。
    0 个 -> None; 1 个 -> 补全; >1 -> AmbiguousMethodFqn。"""
    if "(" in fqn:
        return fqn
    rows = conn.execute(
        select(S.code_methods.c.method_fqn).distinct()
        .where(S.code_methods.c.method_fqn.startswith(fqn + "(", autoescape=True))
        .order_by(S.code_methods.c.method_fqn)).scalars().all()
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    raise AmbiguousMethodFqn(fqn, list(rows))
