"""Task C4: Trip 2 回写 dst_dataset_type=config_table 到 05 lineage_edges。

设计思路(design §2 盲区2 + 构建契约 §3):
  06 识别为 config_table 的表, 在 Trip 2 后处理非阻塞地回写 05 的 lineage_edges,
  把命中 dst_table 的边 dst_dataset_type 从默认 TABLE 改成 config_table。
  只 UPDATE 05 的列(05 §12.2 留的接缝), 不碰 05 store.py。

评分标准:
  - 命中的边(dst_table 在 config_table_names)被标 config_table。
  - 未命中的边保持非 config_table(默认 TABLE)。
  - 返回值 = 实际改动的行数(rowcount)。
  - 空 config_table_names -> 返回 0, 不发 UPDATE。

自动脚本测试逻辑:
  建内存库 + 05 metadata, 插两条边(一条 dst=config 表, 一条 dst=普通表),
  调 writeback_config_tables 标其中一个, 断言行数与每行 dst_dataset_type。
"""
from sqlalchemy import create_engine, insert, select

from contextos.config_dim.writeback import writeback_config_tables
from contextos.lineage import store as L


def test_writeback_marks_config_table():
    eng = create_engine("sqlite:///:memory:")
    L.metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(insert(L.lineage_edges).values(edge_id="E1", src_table="A", dst_table="PM_OFFER_CHA",
                                                 src_owner="UPC", dst_owner="UPC"))
        c.execute(insert(L.lineage_edges).values(edge_id="E2", src_table="A", dst_table="CB_CUSTOMER"))
    n = writeback_config_tables(eng, config_table_names={"PM_OFFER_CHA"})
    assert n == 1
    with eng.connect() as c:
        rows = {r.edge_id: r for r in c.execute(select(L.lineage_edges))}
    assert rows["E1"].dst_dataset_type == "config_table" and rows["E2"].dst_dataset_type != "config_table"
    # spec §5.2 adversarial (a): Trip 2 只改 dst_dataset_type, 绝不 mutate edge_id/owner
    assert rows["E1"].edge_id == "E1" and rows["E1"].src_owner == "UPC" and rows["E1"].dst_owner == "UPC"


def test_writeback_empty_set_returns_zero():
    eng = create_engine("sqlite:///:memory:")
    L.metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(insert(L.lineage_edges).values(edge_id="E1", src_table="A", dst_table="PM_OFFER_CHA"))
    assert writeback_config_tables(eng, config_table_names=set()) == 0
    with eng.connect() as c:
        rows = {r.edge_id: r.dst_dataset_type for r in c.execute(select(L.lineage_edges))}
    # 未触碰, 仍是默认 TABLE
    assert rows["E1"] != "config_table"


def test_writeback_marks_multiple_edges():
    """多条边同一 config_table -> 全标, rowcount 计全部命中行。"""
    eng = create_engine("sqlite:///:memory:")
    L.metadata.create_all(eng)
    with eng.begin() as c:
        c.execute(insert(L.lineage_edges).values(edge_id="E1", src_table="A", dst_table="PM_OFFER_CHA"))
        c.execute(insert(L.lineage_edges).values(edge_id="E2", src_table="B", dst_table="PM_OFFER_CHA"))
        c.execute(insert(L.lineage_edges).values(edge_id="E3", src_table="C", dst_table="CB_CUSTOMER"))
    n = writeback_config_tables(eng, config_table_names={"PM_OFFER_CHA", "SYS_CONFIG"})
    assert n == 2
    with eng.connect() as c:
        rows = {r.edge_id: r.dst_dataset_type for r in c.execute(select(L.lineage_edges))}
    assert rows["E1"] == "config_table"
    assert rows["E2"] == "config_table"
    assert rows["E3"] != "config_table"
