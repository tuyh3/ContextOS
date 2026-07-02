"""Layer 3 tree-sitter-java SQL 抽取测试。"""
import shutil

import pytest

# tree-sitter-java 是必装依赖;若 import 失败说明环境没装好, 让测试硬报(不 skip)
from contextos.lineage.java_extract import extract_sql_from_java


def _modes(cands):
    return {c.recovery_mode for c in cands}


def test_literal_assignment():
    java = '''class Dao {
      void q() { String sql = "SELECT * FROM T_USER WHERE ID = ?"; ps.prepareStatement(sql); }
    }'''
    cands = extract_sql_from_java(java, "a/Dao.java")
    assert any(c.recovery_mode == "literal" for c in cands)
    c = [x for x in cands if x.recovery_mode == "literal"][0]
    assert "T_USER" in c.sql_text
    assert c.container == "Dao.q"
    assert c.confidence == "medium"  # 纯字面量


def test_concat_with_sink_is_medium():
    java = '''class Dao {
      void q() { String sql = "SELECT * FROM T_USER" + " WHERE ID = " + id;
                 ps.executeQuery(sql); }
    }'''
    cands = extract_sql_from_java(java, "a/Dao.java")
    c = [x for x in cands if x.recovery_mode == "concat"][0]
    assert "${?}" in c.sql_text          # 变量部分占位
    assert c.confidence == "medium"      # sink 命中升级


def test_concat_without_sink_is_low():
    java = '''class Dao {
      void q() { String sql = "SELECT * FROM T_USER" + x; log(sql); }
    }'''
    cands = extract_sql_from_java(java, "a/Dao.java")
    c = [x for x in cands if x.recovery_mode == "concat"][0]
    assert c.confidence == "low"


def test_string_builder_isolated_instances():
    java = '''class Dao {
      void q() {
        StringBuilder sb1 = new StringBuilder();
        sb1.append("SELECT * FROM A");
        StringBuilder sb2 = new StringBuilder();
        sb2.append("SELECT * FROM B");
      }
    }'''
    cands = extract_sql_from_java(java, "a/Dao.java")
    sbs = [c for c in cands if c.recovery_mode == "string_builder"]
    texts = " | ".join(c.sql_text for c in sbs)
    assert "FROM A" in texts and "FROM B" in texts
    # 两 builder 不混: 没有一条同时含 A 和 B
    assert not any("FROM A" in c.sql_text and "FROM B" in c.sql_text for c in sbs)


def test_branch_detected_in_if():
    """§9.3: if/else 内 append -> branch_detected=True, confidence=low。"""
    java = '''class Dao {
      String q(boolean f) {
        StringBuilder sb = new StringBuilder();
        sb.append("SELECT * FROM T WHERE 1=1");
        if (f) { sb.append(" AND COL_A = ?"); } else { sb.append(" AND COL_B = ?"); }
        return sb.toString();
      }
    }'''
    cands = extract_sql_from_java(java, "a/Dao.java")
    sb = [c for c in cands if c.recovery_mode == "string_builder"][0]
    assert sb.branch_detected is True
    assert sb.confidence == "low"


def test_local_var_def_use_resolves_source():
    """§9.1: String base="..."; String sql=base+"..."; def-use 追源头字面量。"""
    java = '''class Dao {
      void q() {
        String base = "SELECT * FROM T_USER";
        String sql = base + " WHERE ID = ?";
        ps.prepareStatement(sql);
      }
    }'''
    cands = extract_sql_from_java(java, "a/Dao.java")
    lv = [c for c in cands if c.recovery_mode == "local_var"]
    assert lv, f"expected a local_var candidate, got {_modes(cands)}"
    assert "T_USER" in lv[0].sql_text       # 源头被替换进去
    assert lv[0].confidence == "medium"


def test_string_format_all_literal():
    """§9.4: String.format 全字面量 -> 静态执行, recovery_mode=literal。"""
    java = '''class Dao {
      void q() { String sql = String.format("SELECT * FROM T_USER WHERE ID = %d", 100);
                 ps.executeQuery(sql); }
    }'''
    cands = extract_sql_from_java(java, "a/Dao.java")
    fmt = [c for c in cands if "T_USER" in c.sql_text]
    assert fmt, f"expected format-resolved SQL, got {_modes(cands)}"
    assert "100" in fmt[0].sql_text


def test_no_sql_keyword_returns_empty():
    cands = extract_sql_from_java("class X { int a = 1; }", "a/X.java")
    assert cands == []
