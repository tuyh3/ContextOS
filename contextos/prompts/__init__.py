"""ContextOS LLM prompt 文本集中地(与逻辑分离, 版本化 + 回归测试守护)。

为什么外置(见讨论 docs/讨论/2026-05-31-02b-scope边界误判与prompt管理复盘.md):
- prompt 文本与逻辑解耦, 便于审阅 / 迭代(业界共识: 别把 prompt 内联埋在逻辑里)。
- 仍是"我们的版本化资产 + pytest 回归当评测闸门", **不是**客户可随意改的配置
  (prompt 与输出 schema 绑定, 乱改会崩解析)。
- per-project 领域差异走 profile 的结构化字段(如 input.scope.domain_description),
  不在此硬编码客户词。

v1 先迁 scope(2026-05-31 本次); extract / classify / translate 的 prompt 后续统一迁入。
"""
