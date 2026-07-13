"""路 B 配置表识别: 去 live SQL 化(spec 2026-07-10 附录 D.5, L3)。

背景(冷评审 M1 修正): 旧路 B 直发 `SELECT ... FROM ALL_TAB_COMMENTS`(Oracle 字典视图),
一旦为 MySQL 接线即撞方言。D.5 裁决: 废除 live SQL, 改读 store 已刷新的
`table_metadata.comment`(MetadataProvider 各方言都填同一列, 天然方言无关, 不再需要
execute_query 通道)。识别逻辑保持不变 —— 注释含配置信号词 -> 命中(high)。

设计思路(memory feedback_contextos_test_documentation):
- path_b_from_comment 是纯函数: 输入一条表注释 + 关键词, 输出命中 dict 或 None
  (与 path_c_query/path_d_query 同形态, 复用 has_config_signal 信号词判定)。
- 去 live SQL 后, 原 SQL 注入防护(bind params)对路 B 已无意义(没有 SQL 了) —— 注入面
  随 live SQL 一并消除, 这本身是 D.5 的安全收益。
评分标准(assert):
  1. 中文/英文配置信号词命中 -> confidence=high, path=B, excerpt 含注释;
  2. 无信号词的普通注释 / 空注释 / None -> 返回 None(不误报);
  3. excerpt 截断到 200 字符(与旧行为一致, 防长注释灌大)。
脚本逻辑: 纯函数直调, 无 DB 无 fake executor。
"""
from contextos.config_dim.identify import path_b_from_comment


def test_hits_on_chinese_config_comment():
    hit = path_b_from_comment("Offer 渠道配置表", kw_zh=["配置"], kw_en=["config"])
    assert hit is not None
    assert hit["confidence"] == "high"
    assert hit["path"] == "B"
    assert "配置" in hit["excerpt"]


def test_hits_on_english_keyword_case_insensitive():
    # 英文关键词大小写不敏感(has_config_signal 小写比较)
    hit = path_b_from_comment("System CONFIG switch table", kw_zh=[], kw_en=["config"])
    assert hit is not None and hit["path"] == "B"


def test_miss_on_plain_business_comment():
    assert path_b_from_comment("普通业务流水表", kw_zh=["配置"], kw_en=["config"]) is None


def test_empty_and_none_comment_return_none():
    assert path_b_from_comment("", kw_zh=["配置"], kw_en=["config"]) is None
    assert path_b_from_comment(None, kw_zh=["配置"], kw_en=["config"]) is None
    assert path_b_from_comment("   ", kw_zh=["配置"], kw_en=["config"]) is None


def test_excerpt_truncated_to_200():
    long_cmt = "配置" + "x" * 500
    hit = path_b_from_comment(long_cmt, kw_zh=["配置"], kw_en=[])
    assert hit is not None
    assert len(hit["excerpt"]) == 200
