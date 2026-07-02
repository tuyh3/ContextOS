"""End-to-end smoke: example profile -> validator (no path check) -> engine + gate import."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from contextos.db_provider.oracle_gate import assert_tns_is_test_only
from contextos.profile import (
    Profile,
    ProfileValidationError,
    load_profile,
    validate_profile,
)
from contextos.storage import LocalFSBackend, engine_from_profile


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "config" / "profile.example.toml"
WINDOWS_EXAMPLE = REPO_ROOT / "config" / "profile.example.windows.toml"


def test_example_profile_parses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTEXTOS_DATA_DIR", str(tmp_path))
    profile = load_profile(EXAMPLE)
    assert isinstance(profile, Profile)
    assert profile.storage.data_dir == str(tmp_path)


def test_example_profile_validates_cross_namespace_rules() -> None:
    profile = load_profile(EXAMPLE)
    validate_profile(profile, check_paths=False)
    # 验证所有 allowed_instances 均通过安全门(多库无主库, 按 owner 自动路由)
    for tns in profile.oracle.allowed_instances:
        assert_tns_is_test_only(tns, allowed=profile.oracle.allowed_instances)


def test_example_profile_drives_engine_and_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CONTEXTOS_DATA_DIR", str(tmp_path))
    profile = load_profile(EXAMPLE)
    engine = engine_from_profile(profile)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
    assert (tmp_path / "contextos.db").exists()
    backend = LocalFSBackend(root=tmp_path / "docs")
    src = tmp_path / "raw.docx"
    src.write_bytes(b"hi")
    stored = backend.put(src, namespace="source-docs")
    assert stored.exists()


def test_validator_catches_prod_keyword_via_loaded_profile() -> None:
    profile = load_profile(EXAMPLE)
    profile.oracle.allowed_instances.append("MY_PROD_DB")
    with pytest.raises(ProfileValidationError, match="production keyword"):
        validate_profile(profile, check_paths=False)


def test_windows_example_profile_parses_and_validates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Windows 范本(阶段3 #6)三守卫。设计思路: Windows 路径写进 TOML 最易踩 "\\U 非法转义"
    坑(2026-07-02 真机踩过), 该坑在 parse 期就炸 -> 本测试在 mac/Linux 上同样守得住。
    评分标准: parse 成 Profile + validate(no path check)零异常。脚本逻辑: load -> validate。"""
    monkeypatch.setenv("CONTEXTOS_DATA_DIR", str(tmp_path))
    profile = load_profile(WINDOWS_EXAMPLE)
    assert isinstance(profile, Profile)
    validate_profile(profile, check_paths=False)


def _toml_shape(node: object) -> object:
    """结构签名: dict 递归展开键, list-of-tables 按元素递归; 标量与标量数组折叠为 None
    (值允许两范本不同, 结构不允许)。冷验证 mutation 探针实测: 只比一层抓不到
    [input.scope]/[[projects]].java 等嵌套层漂移, 故递归。"""
    if isinstance(node, dict):
        return {k: _toml_shape(v) for k, v in node.items()}
    if isinstance(node, list) and any(isinstance(v, dict) for v in node):
        return [_toml_shape(v) for v in node]
    return None


def test_windows_example_profile_no_schema_drift_vs_posix_example() -> None:
    """防两份范本漂移: mac/Linux 范本与 Windows 范本的结构(namespace / 各层键集合 /
    array-of-tables 元素结构)必须完全一致, 允许不同的只有"值"。新增键只改一份时此测试红。"""
    import tomllib

    with open(EXAMPLE, "rb") as f:
        posix_doc = tomllib.load(f)
    with open(WINDOWS_EXAMPLE, "rb") as f:
        win_doc = tomllib.load(f)
    assert _toml_shape(posix_doc) == _toml_shape(win_doc)
