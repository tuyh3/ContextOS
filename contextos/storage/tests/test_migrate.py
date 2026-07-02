"""ensure_schema: 附加式 schema 迁移(建缺失表 + 给已存在表补缺失列)。

回归根因: 持久 DB 文件跨代码版本存活, metadata.create_all(checkfirst=True) 对
已存在的表什么都不做 -> 模型后加的列在老库里缺失 -> 写带该列的行 OperationalError
(no such column)。ensure_schema 在 create_all 之后补齐缺失列, 让"老库+新代码"自愈。
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean, Column, Integer, MetaData, String, Table, create_engine, insert, inspect, select, text,
)


def _cols(engine, table_name: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table_name)}


def test_ensure_schema_adds_missing_column_to_existing_table():
    """老库只有 (id, a); 新模型加了 b -> ensure_schema 后 b 列存在且可写。"""
    from contextos.storage.migrate import ensure_schema

    eng = create_engine("sqlite:///:memory:")
    old = MetaData()
    Table("widget", old, Column("id", Integer, primary_key=True), Column("a", String(8)))
    old.create_all(eng)
    assert "b" not in _cols(eng, "widget")  # 老 schema 没 b

    new = MetaData()
    t = Table("widget", new,
              Column("id", Integer, primary_key=True),
              Column("a", String(8)),
              Column("b", String(16), default="x"))
    ensure_schema(eng, new)

    assert "b" in _cols(eng, "widget")  # 补上了
    # 带新列的行能写进去(原本会 OperationalError: no column named b)
    with eng.begin() as c:
        c.execute(insert(t), [{"id": 1, "a": "p", "b": "q"}])
    with eng.connect() as c:
        row = c.execute(select(t)).one()._mapping
    assert row["b"] == "q"


def test_ensure_schema_creates_new_table_and_is_idempotent():
    """全新表要被建出来; 重复调用不报错、不重复加列。"""
    from contextos.storage.migrate import ensure_schema

    eng = create_engine("sqlite:///:memory:")
    md = MetaData()
    Table("gadget", md, Column("id", Integer, primary_key=True), Column("name", String(8)))
    ensure_schema(eng, md)          # 表不存在 -> 建
    assert _cols(eng, "gadget") == {"id", "name"}
    ensure_schema(eng, md)          # 再调一次 -> 幂等, 不崩
    ensure_schema(eng, md)
    assert _cols(eng, "gadget") == {"id", "name"}


def test_ensure_schema_backfills_existing_rows_with_scalar_default():
    """既存行(早于新列)在新列上必须被回填成模型标量默认, 不能留 NULL。

    回归评审 BLOCKER: ADD COLUMN 不带 DEFAULT -> 既存行 NULL != 模型 default。
    is_active=Boolean default=True 的既存行变 NULL -> 'if row[is_active]' 当 False, 语义反转;
    edge_kind default='SQL' 既存行 NULL -> WHERE edge_kind='OBJECT_DEPENDENCY' 之外的逻辑踩坑。
    """
    from contextos.storage.migrate import ensure_schema

    eng = create_engine("sqlite:///:memory:")
    old = MetaData()
    Table("widget", old, Column("id", Integer, primary_key=True), Column("a", String(8)))
    old.create_all(eng)
    with eng.begin() as c:
        c.execute(text("INSERT INTO widget (id, a) VALUES (1, 'p')"))   # 既存行, 早于新列

    new = MetaData()
    t = Table("widget", new,
              Column("id", Integer, primary_key=True),
              Column("a", String(8)),
              Column("flag", Boolean, default=True),
              Column("kind", String(8), default="SQL"),
              Column("note", String(8), default=""),
              Column("opt", String(8)))             # 无 default -> 既存行保持 NULL(模型没声明默认)
    ensure_schema(eng, new)

    with eng.connect() as c:
        row = c.execute(select(t)).one()._mapping
    assert row["flag"] in (True, 1)     # 回填 default=True, 非 NULL
    assert row["kind"] == "SQL"         # 回填 default='SQL'
    assert row["note"] == ""            # 回填 default=''
    assert row["opt"] is None           # 无默认列保持 NULL


def test_ensure_schema_idempotent_when_column_preexists_via_raw_ddl():
    """列已被外部(并发/手工 DDL)加上时, ensure_schema 不能因 duplicate column 崩。"""
    from contextos.storage.migrate import ensure_schema

    eng = create_engine("sqlite:///:memory:")
    old = MetaData()
    Table("widget", old, Column("id", Integer, primary_key=True))
    old.create_all(eng)
    with eng.begin() as c:
        c.execute(text("ALTER TABLE widget ADD COLUMN b VARCHAR(8)"))   # 已存在

    new = MetaData()
    Table("widget", new, Column("id", Integer, primary_key=True), Column("b", String(8), default="x"))
    ensure_schema(eng, new)             # inspect 发现 b 已在 -> 跳过, 不崩
    assert "b" in _cols(eng, "widget")


def test_ensure_schema_recovers_from_concurrent_add_race(monkeypatch):
    """竞态: inspect 报列缺失但 ADD 时该列已被并发进程加上(duplicate column)->
    捕获异常, 重新 inspect 确认列已在 -> 幂等跳过, 不把竞态当真错误重抛。"""
    from contextos.storage import migrate

    eng = create_engine("sqlite:///:memory:")
    old = MetaData()
    Table("widget", old, Column("id", Integer, primary_key=True),
          Column("b", String(8)))       # 真库里 b 实际已存在
    old.create_all(eng)

    real_inspect = migrate.inspect
    state = {"lied": False}

    def fake_inspect(e):
        if not state["lied"]:           # 第一次谎报缺 b(模拟竞态快照), 触发 ADD -> duplicate
            state["lied"] = True

            class _Wrap:
                def get_columns(self, _name):
                    return [{"name": "id"}]
            return _Wrap()
        return real_inspect(e)          # 之后(except 里的复核)给真实状态: b 在

    monkeypatch.setattr(migrate, "inspect", fake_inspect)
    new = MetaData()
    Table("widget", new, Column("id", Integer, primary_key=True), Column("b", String(8), default="x"))
    migrate.ensure_schema(eng, new)     # 不应崩
    assert "b" in _cols(eng, "widget")
