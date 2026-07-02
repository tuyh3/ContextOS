"""CodeIndexConfig namespace: 默认值齐全(老 profile 不写 [code_index] 也能 load),
extra=forbid 拒未知键, caps 字段有边界。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contextos.profile.schema import CodeIndexConfig


def test_defaults_complete():
    c = CodeIndexConfig()
    assert c.indexer_jar == "vendor/java-indexer/target/java-indexer-1.0.0.jar"
    assert c.indexer_xmx == "4g"
    assert c.java_version == "1.8"
    assert c.extra_classpath_dirs == []
    assert c.watcher_enabled is True
    assert c.watcher_debounce_seconds == 2.0
    assert c.incremental_max_files == 500
    assert c.sample_check_classes == 50
    assert c.sample_check_methods == 100
    assert c.sample_check_max_mismatch == 0.05
    assert c.read_symbol_max_lines == 400
    assert c.lookup_calls_max_depth == 2
    assert c.lookup_calls_fanout == 200
    assert c.lookup_calls_max_rows == 1000


def test_extra_forbidden():
    with pytest.raises(ValidationError):
        CodeIndexConfig(unknown_knob=1)  # type: ignore[call-arg]


def test_bounds():
    with pytest.raises(ValidationError):
        CodeIndexConfig(sample_check_max_mismatch=1.5)
    with pytest.raises(ValidationError):
        CodeIndexConfig(lookup_calls_max_depth=0)


def test_profile_field_optional(make_profile):
    """既有 make_profile fixture 不传 code_index 也能构造(向后兼容)。"""
    p = make_profile()
    assert p.code_index.watcher_enabled is True
