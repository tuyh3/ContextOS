"""method_resolve 直接单测: AmbiguousMethodFqn 消息的候选截断(>_MAX_LISTED 只列前 10)。
resolve_bare_method_fqn 的查询路径已由 test_source_slice / test_calls_query 经由
_locate / lookup_calls 间接覆盖, 这里只测纯构造逻辑。"""
from __future__ import annotations

from contextos.code_intel.projection.method_resolve import AmbiguousMethodFqn


def test_ambiguous_message_caps_listed_candidates():
    cands = [f"com.acme.X.m(int{i:02d})" for i in range(12)]
    exc = AmbiguousMethodFqn("com.acme.X.m", cands)
    msg = str(exc)
    assert "12 distinct candidates (showing 10)" in msg
    assert exc.fqn == "com.acme.X.m"
    assert exc.candidates == sorted(cands)          # 全量保留在属性, 只有消息截断
    for c in sorted(cands)[:10]:
        assert c in msg
    for c in sorted(cands)[10:]:
        assert c not in msg
    assert msg.endswith("pass a signature-qualified FQN")


def test_ambiguous_message_no_cap_suffix_at_or_below_limit():
    cands = [f"com.acme.X.m(int{i})" for i in range(2)]
    msg = str(AmbiguousMethodFqn("com.acme.X.m", cands))
    assert "2 distinct candidates:" in msg
    assert "showing" not in msg
