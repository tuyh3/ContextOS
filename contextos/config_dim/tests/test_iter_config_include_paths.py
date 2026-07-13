"""config 文件扫描 include_paths 圈定(L5 pak-bomc 实验实测修复)。

设计思路(memory feedback_contextos_test_documentation):
- ConfigFileSources.include_paths 字段声明了"空=全仓扫"(暗示非空=圈定), 但此前 _iter_config_files
  只用 exclude_paths, **从不读 include_paths**(死字段)。pak-bomc 实测: config 扫全仓吃
  bomc-pak/toptea-web 巨型 geojson(HK_geo.json 5 万项)-> 621562 噪音 items。
- 本 task 实现并验证: include_paths 非空则只收其下文件(目录前缀语义, 与 code source_roots 一致);
  空则全仓扫(向后兼容)。
评分标准(assert):
  1. include_paths=["pak-ccp"] -> 只收 pak-ccp/ 下的配置文件, 排掉 bomc-pak/ 下的;
  2. include_paths=[](默认)-> 全收(向后兼容);
  3. exclude_paths / json_blacklist 仍叠加生效(圈定后再排除)。
脚本逻辑: tmp repo 造两棵子树各放一个 .properties, 断言过滤结果。
"""
from pathlib import Path

from contextos.config_dim.pipeline import _iter_config_files
from contextos.profile.schema import ConfigFileSources


def _mk(root: Path) -> None:
    for rel in ["pak-ccp/a/app.properties", "bomc-pak/web/x.properties",
                "pak-ccp/b/settings.yml"]:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("k=v\n", encoding="utf-8")


def _rels(root: Path, fcfg) -> set[str]:
    return {p.relative_to(root).as_posix() for p in _iter_config_files(root, fcfg)}


def test_include_paths_scopes_to_prefix(tmp_path):
    _mk(tmp_path)
    fcfg = ConfigFileSources(include_paths=["pak-ccp"])
    got = _rels(tmp_path, fcfg)
    assert "pak-ccp/a/app.properties" in got
    assert "pak-ccp/b/settings.yml" in got
    assert "bomc-pak/web/x.properties" not in got     # 圈外排除


def test_empty_include_paths_scans_all(tmp_path):
    _mk(tmp_path)
    fcfg = ConfigFileSources()                          # 默认 include_paths=[]
    got = _rels(tmp_path, fcfg)
    assert "bomc-pak/web/x.properties" in got           # 向后兼容: 全仓扫


def test_exclude_still_applies_within_include(tmp_path):
    _mk(tmp_path)
    (tmp_path / "pak-ccp/test").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pak-ccp/test/t.properties").write_text("k=v\n", encoding="utf-8")
    fcfg = ConfigFileSources(include_paths=["pak-ccp"])  # exclude_paths 默认含 **/test/**
    got = _rels(tmp_path, fcfg)
    assert "pak-ccp/a/app.properties" in got
    assert "pak-ccp/test/t.properties" not in got       # 圈内仍受 exclude_paths 约束
