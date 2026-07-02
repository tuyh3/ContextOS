"""health_check 的 jdtls_runtime 探针测试。

设计思路: 探针契约 = 三路径全在 -> {"status":"ok"}; 有缺 -> status=missing + missing 清单,
且按"本机能否探到 VSCode 扩展"分岔出 suggestion(现成三路径) 或 安装指引 hint;
任何异常吞掉返回 error 串(health 绝不冒泡纪律)。评分标准: 三分岔各命中 + 不冒泡。
脚本逻辑: 造带 profile.jdtls_runtime 的假 app_ctx, monkeypatch discovery 控制探测结果。
"""
from __future__ import annotations

from types import SimpleNamespace

from contextos.code_intel.jdtls_provider.discovery import DiscoveredJdtls
from contextos.mcp_server.tools.meta import _probe_jdtls_runtime


def _ctx(tmp_path, *, jdtls_exists=True):
    server = tmp_path / "server"
    server.mkdir(exist_ok=True)
    lombok = tmp_path / "lombok.jar"
    lombok.write_bytes(b"PK")
    jre = tmp_path / "jre"
    jre.mkdir(exist_ok=True)
    rt = SimpleNamespace(
        jdtls_path=str(server) if jdtls_exists else str(tmp_path / "no-such-dir"),
        lombok_path=str(lombok),
        java_home=str(jre),
    )
    return SimpleNamespace(profile=SimpleNamespace(jdtls_runtime=rt))


def test_probe_ok_when_all_paths_exist(tmp_path):
    assert _probe_jdtls_runtime(_ctx(tmp_path)) == {"status": "ok"}


def test_probe_missing_with_suggestion_when_vscode_found(tmp_path, monkeypatch):
    import contextos.code_intel.jdtls_provider.discovery as D
    monkeypatch.setattr(
        D, "discover_vscode_jdtls",
        lambda home=None: DiscoveredJdtls(
            jdtls_path="/x/server", lombok_path="/x/lombok.jar",
            java_home="/x/jre/21", source="redhat.java-9.9.9-test"),
    )
    out = _probe_jdtls_runtime(_ctx(tmp_path, jdtls_exists=False))
    assert out["status"] == "missing"
    assert out["missing"] == ["jdtls_path"]
    assert out["suggestion"]["jdtls_path"] == "/x/server"
    assert out["suggestion"]["source"] == "redhat.java-9.9.9-test"


def test_probe_missing_with_install_hint_when_no_vscode(tmp_path, monkeypatch):
    import contextos.code_intel.jdtls_provider.discovery as D
    monkeypatch.setattr(D, "discover_vscode_jdtls", lambda home=None: None)
    out = _probe_jdtls_runtime(_ctx(tmp_path, jdtls_exists=False))
    assert out["status"] == "missing"
    assert "suggestion" not in out
    assert "README" in out["hint"]


def test_probe_never_raises_on_broken_ctx():
    out = _probe_jdtls_runtime(SimpleNamespace())   # 没 profile 属性
    assert out["status"].startswith("error:")
