"""lineage 维 vendored 第三方库容器包。

当前成员: mybatis_mapper2sql(Apache-2.0, 上游已归档, 见其目录内 README.md)。
放在 contextos 包内(而非仓根 vendor/)是因为 hatch wheel 只收 contextos 包 /
pytest testpaths 也只扫 contextos —— 保证 uv run 下零配置可 import。
"""
