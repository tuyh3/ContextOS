"""信号词表 parse / load 测试(design 02b spec 4.1 + 7)。

测试思路:
  - parse_flat_terms: 行首 # 注释跳过 / 含 # 的词(C#)保留 / 空行跳过 / 去空白
  - load_signal_terms: 默认表非空 + casefold 归一 + 合并客户表 + cache 优先
评分标准:
  - 默认表加载出常见信号词(新增 / add)
  - C# 这类含 # 的词不被当注释吃掉
  - 客户表词并入; cache_path 存在时直接读 cache(不读 default+customer)
自动脚本测试逻辑: 纯字符串, 无 LLM, 完全确定。
"""
from __future__ import annotations

import json

from contextos.llm import FakeLLM
from contextos.requirement.signal_terms import (
    build_signal_terms_cache,
    load_signal_terms,
    parse_flat_terms,
)


def test_parse_flat_terms_comments_and_blanks():
    text = "# 注释行\n\n新增\n  add  \n   # 缩进注释\nC#\n"
    terms = parse_flat_terms(text)
    assert "新增" in terms
    assert "add" in terms            # 去掉两侧空白
    assert "C#" in terms             # 含 # 但不在行首 -> 不是注释, 保留
    assert "# 注释行" not in terms
    assert "" not in terms


def test_load_default_table_nonempty_casefolded():
    terms = load_signal_terms()
    assert "新增" in terms
    assert "add" in terms            # casefold
    assert "ADD" not in terms


def test_load_merges_customer_terms(tmp_path):
    cust = tmp_path / "customer.txt"
    cust.write_text("# 客户词\n智能管家\nDost\n", encoding="utf-8")
    terms = load_signal_terms(customer_path=str(cust))
    assert "新增" in terms           # 默认表仍在
    assert "智能管家" in terms        # 客户词并入
    assert "dost" in terms           # casefold


def test_load_cache_path_takes_precedence(tmp_path):
    cache = tmp_path / "cache.txt"
    cache.write_text("仅缓存词\nonlycached\n", encoding="utf-8")
    terms = load_signal_terms(cache_path=str(cache))
    assert terms == {"仅缓存词", "onlycached"}   # 只读 cache, 不并默认表


# ---------------------------------------------------------------------------
# Task 4: build_signal_terms_cache (one-time auto-translate build)
# 测试思路:
#   - 客户填中文词 -> LLM 补英文, 合并默认表写缓存
#   - 客户填英文词 -> LLM 补中文
#   - build 后运行期 load 读缓存, 不再调 LLM(0 token)
# 评分标准:
#   - 原词 + 补译词均出现在 full 集合 + 缓存文件
#   - 默认表词(如"新增")必须并入
#   - load_signal_terms(cache_path=...) 只读文件, 不依赖 llm 参数
# 自动脚本测试逻辑: FakeLLM 返回确定 JSON; tmp_path 隔离; 不访问网络
# ---------------------------------------------------------------------------


def test_build_cache_translates_customer_terms(tmp_path):
    """客户填中文 '智能管家' -> 自动补 'Smart Butler';合并默认表写缓存。"""
    cust = tmp_path / "customer.txt"
    cust.write_text("智能管家\n", encoding="utf-8")
    cache = tmp_path / "cache.txt"
    # FakeLLM 把单词译为英文(structured 单字段 schema)
    llm = FakeLLM(responses=[json.dumps({"translated": "Smart Butler"}, ensure_ascii=False)])

    full = build_signal_terms_cache(llm, customer_path=str(cust), out_path=str(cache))

    assert "智能管家" in full            # 原词
    assert "smart butler" in full        # 自动补译(casefold)
    assert "新增" in full                # 默认表并入
    assert cache.exists()                # 缓存落盘


def test_build_cache_then_load_is_zero_token(tmp_path):
    """缓存建好后, 运行期 load 读缓存, 不再调 LLM。"""
    cust = tmp_path / "customer.txt"
    cust.write_text("智能管家\n", encoding="utf-8")
    cache = tmp_path / "cache.txt"
    build_llm = FakeLLM(responses=[json.dumps({"translated": "Smart Butler"}, ensure_ascii=False)])
    build_signal_terms_cache(build_llm, customer_path=str(cust), out_path=str(cache))

    # build 期恰好 1 次 LLM 调用(补译"智能管家"); load 读缓存不再调 LLM(0 token)
    assert len(build_llm.calls) == 1

    # load 读缓存: 不传 llm, 纯字符串
    terms = load_signal_terms(cache_path=str(cache))
    assert "智能管家" in terms
    assert "smart butler" in terms


def test_build_cache_translates_en_term_to_zh(tmp_path):
    """纯英文客户词 detect_language=en -> LLM 补中文。"""
    cust = tmp_path / "customer.txt"
    cust.write_text("Dost\n", encoding="utf-8")
    cache = tmp_path / "cache.txt"
    llm = FakeLLM(responses=[json.dumps({"translated": "多斯特"}, ensure_ascii=False)])
    full = build_signal_terms_cache(llm, customer_path=str(cust), out_path=str(cache))
    assert "dost" in full
    assert "多斯特" in full
