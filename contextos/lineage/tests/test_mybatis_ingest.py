"""MyBatis mapper 摄入接线(spec 附录 E.5/E.6, L4)。

设计思路(memory feedback_contextos_test_documentation):
- ingest_mappers 把展开的 mapper 语句落成: (a) 每条语句一行 sql_templates(含 DML,
  container=方法 FQN, 服务 search_sql 三跳); (b) 多表语句经 parse_sql 出 lineage_edges
  (复用 build_edges_from_relations); (c) FQN 经 code_* 投影校验(E.5): 命中唯一 -> 带签名
  FQN + confidence=medium; 未命中/无投影 -> 裸 FQN + low 弱证据。
- 单表 DML(单 INSERT/UPDATE/DELETE)无 src->dst 对 -> 不产边, 但**必须**留模板(否则整条
  消失, 是 Explore 查到的致命点), container 仍带 FQN 供表->方法反查。
- recovery_mode 恒 "mybatis_mapper"(E.6, 已入 RecoveryMode SSOT)。
评分标准(assert):
  1. select+insert 两条语句都各出一行模板(kind 不限), container=namespace.id, recovery_mode 正确;
  2. JOIN select 出边(两表), 单表 insert 不出边;
  3. code_methods 有该方法 -> container 补全为带签名 FQN + confidence=medium(命中);
     无 code_methods 匹配 -> container=裸 FQN + confidence=low(弱证据)。
脚本逻辑: tmp_path 造合成 mapper; 单 engine 同时含 lineage store + code 投影(共库现实);
  NameResolver 空元数据(离线降级, 裸名解析)。
"""
from pathlib import Path

from contextos.code_intel.projection import schema as CS
from contextos.lineage import store
from contextos.lineage.mybatis_ingest import ingest_mappers
from contextos.lineage.name_resolve import NameResolver
from contextos.profile.schema import TablesConfig
from contextos.storage.db import make_engine

_MAPPER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"\n'
    '  "http://mybatis.org/dtd/mybatis-3-mapper.dtd">\n'
    '<mapper namespace="com.x.OrderMapper">\n'
    '  <select id="listWithUser" resultType="map">\n'
    '    SELECT o.id, u.name FROM t_order o JOIN t_user u ON o.uid = u.id\n'
    '  </select>\n'
    '  <insert id="add">INSERT INTO t_order (id, uid) VALUES (#{id}, #{uid})</insert>\n'
    '</mapper>\n'
)


def _engine_with_code_methods(*fqns: str):
    eng = make_engine("sqlite://")
    store.create_all(eng)
    CS.ensure_projection_schema(eng)
    if fqns:
        with eng.begin() as c:
            c.execute(CS.code_methods.insert(), [
                dict(method_id=f"m{i}", lang="java", class_fqn=fqn.rsplit(".", 1)[0],
                     method_name=fqn.rsplit(".", 1)[-1].split("(")[0],
                     name_lower=fqn.rsplit(".", 1)[-1].split("(")[0].lower(),
                     signature=fqn, method_fqn=fqn)
                for i, fqn in enumerate(fqns)])
    return eng


def _write_mapper(tmp_path: Path) -> list[str]:
    p = tmp_path / "mysqlMapper" / "OrderMapper.xml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_MAPPER, encoding="utf-8")
    return [str(p)]


def test_template_per_statement_all_kinds(tmp_path):
    eng = _engine_with_code_methods()
    resolver = NameResolver(eng, TablesConfig())
    out = ingest_mappers(_write_mapper(tmp_path), repo_root=tmp_path, dialect="mysql", resolver=resolver, engine=eng)
    tpls = {t["container"]: t for t in out["templates"]}
    # select + insert 各一行, container = namespace.id, recovery_mode 正确
    assert "com.x.OrderMapper.listWithUser" in tpls
    assert "com.x.OrderMapper.add" in tpls          # DML 也留模板(致命点)
    assert all(t["recovery_mode"] == "mybatis_mapper" for t in out["templates"])


def test_join_produces_edge_single_table_does_not(tmp_path):
    eng = _engine_with_code_methods()
    resolver = NameResolver(eng, TablesConfig())
    out = ingest_mappers(_write_mapper(tmp_path), repo_root=tmp_path, dialect="mysql", resolver=resolver, engine=eng)
    edge_tables = {(e["src_table"], e["dst_table"]) for e in out["edges"]}
    # JOIN 出边(T_ORDER<->T_USER 某向); 单表 insert 不额外造边
    assert any({"T_ORDER", "T_USER"} == {s, d} for s, d in edge_tables)
    assert all(e["recovery_mode"] == "mybatis_mapper" for e in out["edges"])


def test_fqn_validated_hit_gives_signature_and_medium(tmp_path):
    # code_methods 有该方法(带签名) -> container 补全 + confidence=medium
    eng = _engine_with_code_methods("com.x.OrderMapper.listWithUser(int)",
                                    "com.x.OrderMapper.add(java.lang.Object)")
    resolver = NameResolver(eng, TablesConfig())
    out = ingest_mappers(_write_mapper(tmp_path), repo_root=tmp_path, dialect="mysql", resolver=resolver, engine=eng)
    tpls = {t["container"]: t for t in out["templates"]}
    assert "com.x.OrderMapper.listWithUser(int)" in tpls          # 补全为带签名 FQN
    assert tpls["com.x.OrderMapper.listWithUser(int)"]["confidence"] == "medium"
    assert out["stats"]["fqn_hits"] == 2


def test_fqn_miss_gives_bare_fqn_and_low(tmp_path):
    # 无 code_methods 匹配 -> 裸 FQN + low(弱证据)
    eng = _engine_with_code_methods()   # 投影表在但无匹配方法
    resolver = NameResolver(eng, TablesConfig())
    out = ingest_mappers(_write_mapper(tmp_path), repo_root=tmp_path, dialect="mysql", resolver=resolver, engine=eng)
    tpls = {t["container"]: t for t in out["templates"]}
    assert tpls["com.x.OrderMapper.listWithUser"]["confidence"] == "low"
    assert out["stats"]["fqn_hits"] == 0


def test_fqn_ambiguous_overload_downgrades_to_weak(tmp_path):
    # 同名重载(两签名)-> resolve_bare_method_fqn 抛 AmbiguousMethodFqn -> 保守降弱
    # (裸 FQN + low), 绝不硬挑一个签名。守卫 except 分支不被误改成 return resolved。
    eng = _engine_with_code_methods("com.x.OrderMapper.listWithUser(int)",
                                    "com.x.OrderMapper.listWithUser(java.lang.String)")
    resolver = NameResolver(eng, TablesConfig())
    out = ingest_mappers(_write_mapper(tmp_path), repo_root=tmp_path, dialect="mysql",
                         resolver=resolver, engine=eng)
    tpls = {t["container"]: t for t in out["templates"]}
    assert "com.x.OrderMapper.listWithUser" in tpls                      # 保持裸 FQN(未补签名)
    assert tpls["com.x.OrderMapper.listWithUser"]["confidence"] == "low"  # 歧义降弱
    assert out["stats"]["fqn_hits"] == 0
