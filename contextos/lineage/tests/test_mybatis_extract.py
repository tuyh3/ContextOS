"""MyBatis mapper 摄入地基单测(多方言 spec 附录 E, 2026-07-10)。

设计思路:
- 被测对象 = vendored hhyo/mybatis-mapper2sql(contextos/lineage/_vendor/)+ 三补丁
  + 封装层 mybatis_extract(expand_mappers / extract_tables / is_mybatis_mapper)。
  只测独立地基, 不测 lineage pipeline 接线(接线期任务)。
- 补丁 1(E.3.1): sqlparse>=0.5 对超大 SQL 抛 "Maximum number of tokens exceeded (10000)"
  (reindent 排版美化步)。测试程序生成 1500 条 if 分支的合成 mapper, 双断言:
  (a) 直接 sqlparse.format 该展开产物确实抛 SQLParseError —— 证明测试输入真踩上限,
      补丁路径真被走到(防合成量不足导致的假绿);
  (b) expand_mappers 不抛且产出非空 —— 证明容错短路生效。
- 补丁 2(E.3.2): 上游 create_mapper 单文件作用域, 跨文件 <include refid> 查不到。
  封装层先全量收集所有文件 <sql id> 片段(裸 id + namespace 全限定双键)再逐 mapper
  展开。测试盖: 裸 refid 跨文件 / 全限定 refid 跨文件 / 本地同名片段优先(local wins)。
- 补丁 3(E.3.3): choose 全分支并集是 E.1 核心语义 —— 上游 native=False 把所有
  when/otherwise 分支正文拼进同一条展开产物, 分支间夹 `-- if(test)` / `-- otherwise`
  内联标记。难点(真实形态): 分支体是裸表名时, 剥标记后 SQL 语法必然非法
  ("from A r B r C r"), sqlglot 严格解析放弃, 而朴素 from/join 正则只抓得到首分支。
  故 extract_tables 吃**带标记原文**: 严格路径剥标记喂 sqlglot; 兜底路径把标记当
  分支边界结构信号(前视状态机: 上一个显著关键字是 from/join/into/update 时,
  标记后的首标识符也按表收)。专测两种形态: 裸表名分支(靠边界信号)+
  整句 select 分支(靠基础正则), 三表必须全部抽出。
- E.4 识别一致性: is_mybatis_mapper 与 config_dim xml_mybatis_parser 的 .xml dispatcher
  共用 util.mybatis_sniff 同一实现(spec MUST, 冷评审 N2)。电池测试拿同一批样本
  (好 mapper / 无 DTD mapper / spring beans / 普通 xml / 坏 xml 带 DTD / 坏 xml 无 DTD /
  空文件 / 大写 DTD)对两处判定逐一断言相同, 锁死不漂移。

评分标准:
- 每条测试对应 spec 附录 E 一个 MUST 或一个真实失败模式(E.1 实测基线里的
  50/51 卡点 / choose 三分支并集 / 兜底必需路径);
- fixture 全部中性合成值(com.example 命名空间 + wd_/rt_ 虚构表名), tests 会公开发布,
  绝不掺真实客户 mapper 内容;
- choose 并集测试断言"三表全抽出"且别名 r 不混入(防兜底把别名当表的退化)。

脚本逻辑: tmp_path 落合成 mapper xml -> expand_mappers 真跑 vendored 展开链 ->
断言 MapperStatement 字段与 extract_tables 集合; sniff 一致性电池同一批样本
双路(is_mybatis_mapper vs config_dim parse_xml file_type)对拍。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlparse
from sqlparse.exceptions import SQLParseError

from contextos.config_dim.parsers.xml_mybatis_parser import parse_xml
from contextos.lineage.mybatis_extract import (
    expand_mappers,
    extract_tables,
    is_mybatis_mapper,
    strip_dynamic_markers,
)

# ---------------------------------------------------------------- fixtures

_DTD = ('<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" '
        '"http://mybatis.org/dtd/mybatis-3-mapper.dtd">')

WIDGET_MAPPER = f"""<?xml version="1.0" encoding="UTF-8"?>
{_DTD}
<mapper namespace="com.example.demo.WidgetMapper">
  <sql id="widgetColumns">w.widget_id, w.widget_name</sql>
  <select id="findWidget" resultType="map">
    select
    <include refid="widgetColumns"/>
    from wd_widget_info w
    where w.widget_id = #{{widgetId, jdbcType=NUMERIC}}
  </select>
  <insert id="addWidget">
    insert into wd_widget_info (widget_id, widget_name)
    values (#{{widgetId, jdbcType=NUMERIC}}, #{{widgetName, jdbcType=VARCHAR}})
  </insert>
  <update id="touchWidget">update wd_widget_info set widget_name = #{{widgetName}} where widget_id = #{{widgetId}}</update>
  <delete id="dropWidget">delete from wd_widget_info where widget_id = #{{widgetId}}</delete>
</mapper>
"""

ORDER_MAPPER = """<mapper namespace="com.example.demo.OrderMapper">
  <select id="listOrders">
    select o.order_id,
    <include refid="lineColumns"/>
    from wd_order_head o
    <include refid="com.example.demo.HelperMapper.lineJoin"/>
    where o.status = #{status, jdbcType=VARCHAR}
  </select>
</mapper>
"""

HELPER_MAPPER = """<mapper namespace="com.example.demo.HelperMapper">
  <sql id="lineColumns">l.line_id, l.item_name</sql>
  <sql id="lineJoin">join wd_order_line l on l.order_id = o.order_id</sql>
</mapper>
"""

# choose 难形态: 分支体 = 裸表名(剥标记后 SQL 非法, 只能靠标记边界信号收全)
ROUTE_MAPPER = """<mapper namespace="com.example.demo.RouteMapper">
  <select id="pickByKind">
    select r.col_a from
    <choose>
      <when test="kind == 1">rt_alpha_tab r</when>
      <when test="kind == 2">rt_beta_tab r</when>
      <otherwise>rt_gamma_tab r</otherwise>
    </choose>
    where r.col_b = #{val, jdbcType=NUMERIC}
  </select>
  <select id="pickFull">
    <choose>
      <when test="kind == 1">select col_a from rt_alpha_tab where col_b = #{val}</when>
      <when test="kind == 2">select col_a from rt_beta_tab where col_b = #{val}</when>
      <otherwise>select col_a from rt_gamma_tab where col_b = #{val}</otherwise>
    </choose>
  </select>
</mapper>
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------- 基本展开

def test_expand_basic_fields(tmp_path):
    p = _write(tmp_path, "WidgetMapper.xml", WIDGET_MAPPER)
    stmts = expand_mappers([p])
    by_id = {s.statement_id: s for s in stmts}
    # <sql> 片段不出语句
    assert "widgetColumns" not in by_id
    assert set(by_id) == {"findWidget", "addWidget", "touchWidget", "dropWidget"}
    find = by_id["findWidget"]
    assert find.namespace == "com.example.demo.WidgetMapper"
    assert find.sql_kind == "select"
    assert str(p) == find.source_path
    # 同文件 include 展开: 片段列名进正文
    assert "widget_name" in find.raw_sql
    assert "wd_widget_info" in find.raw_sql
    # #{} 参数被替换为占位符, 不残留
    assert "#{" not in find.raw_sql
    # line 尽力: 指向 <select id="findWidget" 所在行
    expected_line = next(i for i, ln in enumerate(WIDGET_MAPPER.splitlines(), 1)
                         if 'id="findWidget"' in ln)
    assert find.line == expected_line
    # sql_kind 全映射
    assert by_id["addWidget"].sql_kind == "insert"
    assert by_id["touchWidget"].sql_kind == "update"
    assert by_id["dropWidget"].sql_kind == "delete"


def test_expand_statement_extract_tables_strict(tmp_path):
    """跨文件展开产物走 sqlglot 严格路径抽表(可解析的规整 SQL)。"""
    po = _write(tmp_path, "OrderMapper.xml", ORDER_MAPPER)
    ph = _write(tmp_path, "helper/HelperMapper.xml", HELPER_MAPPER)
    stmts = expand_mappers([po, ph])
    (order_stmt,) = [s for s in stmts if s.statement_id == "listOrders"]
    tabs = extract_tables(order_stmt.raw_sql, "mysql")
    assert {"wd_order_head", "wd_order_line"} <= tabs


# ---------------------------------------------------------------- 补丁 2: 跨文件 include

def test_cross_file_include_bare_and_qualified(tmp_path):
    po = _write(tmp_path, "OrderMapper.xml", ORDER_MAPPER)
    ph = _write(tmp_path, "HelperMapper.xml", HELPER_MAPPER)
    stmts = expand_mappers([po, ph])
    (order_stmt,) = [s for s in stmts if s.statement_id == "listOrders"]
    # 裸 refid="lineColumns" -> HelperMapper 片段列名进正文
    assert "line_id" in order_stmt.raw_sql
    # 全限定 refid="com.example.demo.HelperMapper.lineJoin" -> 片段表名进正文
    assert "wd_order_line" in order_stmt.raw_sql
    # 纯片段 mapper 不产语句
    assert all(s.namespace != "com.example.demo.HelperMapper" for s in stmts)


def test_local_fragment_wins_over_foreign(tmp_path):
    a = _write(tmp_path, "AMapper.xml", """<mapper namespace="com.example.demo.AMapper">
      <sql id="commonCols">a_local_col</sql>
      <select id="pick">select <include refid="commonCols"/> from wd_a_tab</select>
    </mapper>""")
    b = _write(tmp_path, "BMapper.xml", """<mapper namespace="com.example.demo.BMapper">
      <sql id="commonCols">b_foreign_col</sql>
    </mapper>""")
    # 故意让 B 先进全局片段字典(裸 id 先到先得), 再靠 merge 时本文件条目覆盖取胜
    stmts = expand_mappers([b, a])
    (pick,) = [s for s in stmts if s.statement_id == "pick"]
    assert "a_local_col" in pick.raw_sql
    assert "b_foreign_col" not in pick.raw_sql


def test_malformed_file_skipped_others_survive(tmp_path):
    good = _write(tmp_path, "GoodMapper.xml", ORDER_MAPPER)
    helper = _write(tmp_path, "HelperMapper.xml", HELPER_MAPPER)
    bad = _write(tmp_path, "BadMapper.xml", '<mapper namespace="com.example.demo.Bad')
    stmts = expand_mappers([bad, good, helper])
    assert [s.statement_id for s in stmts] == ["listOrders"]


# ---------------------------------------------------------------- 补丁 1: sqlparse token 上限

def test_sqlparse_token_limit_does_not_break_expand(tmp_path):
    branches = "\n".join(
        f'<if test="f{i} != null"> and filter_col_{i} = #{{f{i}, jdbcType=NUMERIC}} </if>'
        for i in range(1500))
    xml = (f'<mapper namespace="com.example.demo.BulkMapper">'
           f'<select id="hugeFilter">select col_a from wd_bulk_tab where 1 = 1 {branches}</select>'
           f'</mapper>')
    p = _write(tmp_path, "BulkMapper.xml", xml)
    stmts = expand_mappers([p])          # 补丁 1 缺席时这里抛 SQLParseError
    (huge,) = stmts
    assert huge.raw_sql.strip()
    assert "wd_bulk_tab" in huge.raw_sql
    # 反证测试输入真踩上限: 直接美化同一产物必须抛(证明短路路径真被走过)
    with pytest.raises(SQLParseError):
        sqlparse.format(huge.raw_sql, reindent=True)


# ---------------------------------------------------------------- 补丁 3: 标记剥离与抽表

def test_strip_dynamic_markers_balanced_parens():
    # 嵌套括号的 test 表达式 + 标记后同行紧跟下一分支表名: 必须括号配平剥, 不能整行剥
    s = "rt_alpha_tab r\n    -- if(names != null and names.size() > 0)rt_beta_tab r\n    -- otherwise rt_gamma_tab r"
    out = strip_dynamic_markers(s)
    assert "rt_beta_tab" in out
    assert "rt_gamma_tab" in out
    assert "-- if" not in out
    assert "otherwise" not in out
    # mutation 探针: 条件表达式必须整体消失 —— 非贪婪到首个 ')' 的退化实现
    # 会留下 "> 0)" 残渣且以上断言全过, 这两条专防它
    assert "names" not in out
    assert "> 0)" not in out


def test_strip_dynamic_markers_unbalanced_falls_to_eol():
    s = "col_a -- if(broken\nnext_line_kept"
    out = strip_dynamic_markers(s)
    assert "next_line_kept" in out
    assert "-- if" not in out


def test_strip_dynamic_markers_no_marker_passthrough():
    s = "select col_a from wd_widget_info -- 正常业务注释保留"
    assert strip_dynamic_markers(s) == s


def test_extract_tables_strict_mysql_backtick():
    sql = "select `c` from `wd_widget_info` w join wd_order_line l on l.i = w.i"
    assert extract_tables(sql, "mysql") == {"wd_widget_info", "wd_order_line"}


def test_extract_tables_cte_alias_not_counted():
    sql = "with tmp as (select x from wd_real_tab) select x from tmp"
    assert extract_tables(sql, "mysql") == {"wd_real_tab"}


def test_extract_tables_fallback_on_broken_sql():
    sql = "select x from wd_broken_tab join wd_other_tab on (("
    assert {"wd_broken_tab", "wd_other_tab"} <= extract_tables(sql, "mysql")


def test_extract_tables_fallback_insert_update_targets():
    assert "wd_ins_tab" in extract_tables("insert into wd_ins_tab (a) values (1", "mysql")
    assert "wd_upd_tab" in extract_tables("update wd_upd_tab set a = 1 where ((", "mysql")


def test_choose_union_all_branch_tables(tmp_path):
    """spec E.1 核心语义专测: choose 三分支三表全部进并集。"""
    p = _write(tmp_path, "RouteMapper.xml", ROUTE_MAPPER)
    stmts = expand_mappers([p])
    by_id = {s.statement_id: s for s in stmts}
    # 难形态: 裸表名分支(剥标记后 SQL 非法, 靠标记边界信号收全)
    tabs = extract_tables(by_id["pickByKind"].raw_sql, "mysql")
    assert {"rt_alpha_tab", "rt_beta_tab", "rt_gamma_tab"} <= tabs
    # 别名 r 不得混入(防兜底把别名当表)
    assert "r" not in tabs
    # 整句 select 分支形态: 三个 from 各自命中
    tabs_full = extract_tables(by_id["pickFull"].raw_sql, "mysql")
    assert {"rt_alpha_tab", "rt_beta_tab", "rt_gamma_tab"} <= tabs_full


# ---------------------------------------------------------------- E.4: 识别与一致性

# (文件名, 内容, 期望判定) —— 同一批样本双路对拍
_SNIFF_SAMPLES = [
    ("good_mapper.xml", WIDGET_MAPPER, True),
    ("no_dtd_mapper.xml",
     '<mapper namespace="com.example.demo.TinyMapper"><select id="s">select 1</select></mapper>', True),
    ("spring_beans.xml",
     '<beans><bean id="b" class="com.example.demo.Foo"/></beans>', False),
    ("plain_config.xml", "<configuration><x/></configuration>", False),
    # 坏 xml + mybatis DTD 声明(未定义实体): DTD sniff 兜底仍认 mapper
    ("broken_with_dtd.xml",
     f'<?xml version="1.0"?>\n{_DTD}\n<mapper namespace="com.example.demo.E">'
     '<select id="s">select 1 &undef;</select></mapper>', True),
    # 坏 xml 无 DTD: 两处一致地不认
    ("broken_no_dtd.xml", '<mapper namespace="com.example.demo.T', False),
    ("empty.xml", "", False),
    # DTD 大小写无关
    ("upper_dtd.xml",
     '<!DOCTYPE mapper SYSTEM "HTTP://MYBATIS.ORG/DTD/MYBATIS-3-MAPPER.DTD">\n'
     '<mapper namespace="com.example.demo.U"><select id="s">select 1 &undef;</select></mapper>', True),
]


def test_is_mybatis_mapper_verdicts(tmp_path):
    for name, text, expected in _SNIFF_SAMPLES:
        p = _write(tmp_path, name, text)
        assert is_mybatis_mapper(p) is expected, name


def test_is_mybatis_mapper_dir_case_irrelevant(tmp_path):
    # 按内容不按目录约定: 大写 Resources 目录/任意层级都认
    p = _write(tmp_path, "src/main/RESOURCES/Mapper/WidgetMapper.xml", WIDGET_MAPPER)
    assert is_mybatis_mapper(p) is True


def test_sniff_consistency_with_config_dim(tmp_path):
    """spec E.4 MUST: lineage 识别与 config_dim .xml dispatcher 判定不漂移。

    双路对拍: is_mybatis_mapper(path) 必须等价于
    config_dim parse_xml(...).file_type == "xml-mybatis"。
    """
    for name, text, _ in _SNIFF_SAMPLES:
        p = _write(tmp_path, name, text)
        lineage_verdict = is_mybatis_mapper(p)
        config_verdict = parse_xml(name, text).file_type == "xml-mybatis"
        assert lineage_verdict == config_verdict, name


def test_read_text_gbk_fallback(tmp_path):
    """中文注释 GBK 编码 mapper: utf-8 解码失败退 gbk, 不炸不吞。"""
    xml = ('<?xml version="1.0" encoding="GBK"?>\n'
           '<mapper namespace="com.example.demo.GbkMapper">\n'
           '  <!-- 中文注释: 查询演示表 -->\n'
           '  <select id="q">select col_a from wd_gbk_tab</select>\n'
           '</mapper>\n')
    p = tmp_path / "GbkMapper.xml"
    p.write_bytes(xml.encode("gbk"))
    assert is_mybatis_mapper(p) is True
    stmts = expand_mappers([p])
    assert stmts and "wd_gbk_tab" in stmts[0].raw_sql
