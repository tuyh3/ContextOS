"""Test adapter: ContextOS-shape wrapper around solidlsp.

Adapter is the public API for ContextOS code; it hides solidlsp details.
Tests use in-test fixture TOML (real projects.toml is gitignored).

These are UNIT tests — they don't actually start JDT LS. That's Task 3's job.
"""
import pytest

FIXTURE_TOML = """
[storage]
data_dir = "/tmp/contextos-test/data"
jdtls_workspace_dir = "/tmp/contextos-test/ws"

[jdtls_runtime]
jdtls_path = "/home/user/redhat.java/server"
lombok_path = "/home/user/redhat.java/lombok/lombok-1.18.39.jar"
java_home = "/home/user/redhat.java/jre/21.0.10"

[oracle]
tns_admin = "/tmp/tns"
allowed_instances = ["TEST_X"]

[[projects]]
name = "fixture-project-a"
path = "/tmp/projects/a"
language = "java"
build_system = "gradle"
java = { gradle_home = "/opt/gradle-5.6.4", gradle_version_override = "5.6.4", gradle_arguments = "-Dprofile=prd", gradle_java_home = "/opt/jdk8", gradle_wrapper_enabled = false }
"""


@pytest.fixture
def fixture_toml(tmp_path):
    p = tmp_path / "fixture-projects.toml"
    p.write_text(FIXTURE_TOML.strip(), encoding="utf-8")
    return p


def test_adapter_factory_creates_for_known_project(fixture_toml):
    from contextos.code_intel.jdtls_provider.adapter import JdtlsAdapter
    adapter = JdtlsAdapter.from_config(fixture_toml, project_name="fixture-project-a")
    assert adapter.project_name == "fixture-project-a"
    assert adapter.language == "java"


def test_adapter_factory_unknown_project_raises(fixture_toml):
    from contextos.code_intel.jdtls_provider.adapter import JdtlsAdapter
    with pytest.raises(KeyError):
        JdtlsAdapter.from_config(fixture_toml, project_name="not-a-real-project")


def test_adapter_has_required_methods(fixture_toml):
    from contextos.code_intel.jdtls_provider.adapter import JdtlsAdapter
    adapter = JdtlsAdapter.from_config(fixture_toml, project_name="fixture-project-a")
    assert hasattr(adapter, "start")
    assert hasattr(adapter, "stop")
    assert hasattr(adapter, "request_definition")
    assert hasattr(adapter, "request_references")
    assert hasattr(adapter, "open_file")
    assert hasattr(adapter, "request_workspace_symbol")


def test_project_java_settings_override_runtime_defaults():
    """Critical merge-order invariant for Task 1's 12-line gradle patch.

    The eclipse_jdtls.py patch (lines 1310-1318) reads custom_gradle_home_override
    etc. from `_custom_settings` and overrides `gradle_settings["home"]`. That
    only works if per-project `java_settings` (which carries gradle_home etc.)
    is merged AFTER the runtime defaults in `_build_ls_specific_settings()`.
    If a future refactor inverts the merge, this test catches it before Task 3's
    expensive real-customer-project 4/4 binding test fails."""
    from contextos.code_intel.jdtls_provider.adapter import JdtlsAdapter
    from contextos.code_intel.jdtls_provider.config import (
        JdtlsRuntimeConfig,
        ProjectConfig,
        StorageConfig,
    )
    project = ProjectConfig(
        name="x",
        path="/x",
        language="java",
        build_system="gradle",
        java_settings={
            "jdtls_path": "PROJECT_OVERRIDES",
            "gradle_home": "/opt/g-project",
        },
    )
    storage = StorageConfig(data_dir="/d", jdtls_workspace_dir="/w")
    runtime = JdtlsRuntimeConfig(
        jdtls_path="RUNTIME_DEFAULT",
        lombok_path="/l",
        java_home="/j",
    )
    adapter = JdtlsAdapter(project, storage, runtime)
    settings = adapter._build_ls_specific_settings()
    # Project value wins where both runtime and project specify the key
    assert settings["jdtls_path"] == "PROJECT_OVERRIDES"
    # Project-only key gets through (this is the patch's payload path)
    assert settings["gradle_home"] == "/opt/g-project"
    # Runtime default survives where project doesn't override
    assert settings["lombok_path"] == "/l"
    assert settings["java_home"] == "/j"


def test_solidlsp_top_level_alias_works():
    """The sys.modules shim in solidlsp/__init__.py registers itself under
    the bare top-level name 'solidlsp'. Upstream code uses 'from solidlsp.X
    import Y'-style absolute imports; this test confirms the shim is wired."""
    # Importing the vendored package triggers __init__.py which sets the alias
    import contextos.code_intel.jdtls_provider.solidlsp  # noqa: F401
    # Now the top-level name should resolve
    from solidlsp.ls import SolidLanguageServer
    from solidlsp.ls_config import Language
    assert SolidLanguageServer is not None
    assert Language.JAVA.value == "java"


def test_jdtls_runtime_from_profile(monkeypatch) -> None:
    """占位路径(/jdtls 等)不在磁盘上, from_profile 走的 resolve_effective_runtime
    会先探 <cwd>/runtime/contextos-runtime bundle 是否存在(spec A11 优先级)——
    本测验证的是 profile 值经 from_profile 原样传递(深校验通过即用配置值)/
    expanduser, 不是"探不到 bundle 才回退"这条支路, 钉死 discover_runtime_bundle
    返回 None 使其与真实开发机(此仓根即带真 bundle)的 cwd 状态解耦(hermetic,
    同 test_health_jdtls_probe.py 的钉法先例, 借鉴的是"钉死 bundle 探测"这个
    手法而非其 autouse 作用域, 本测就地 monkeypatch 不搬 fixture)。"""
    from pathlib import Path

    from contextos.code_intel.jdtls_provider.config import JdtlsRuntimeConfig
    from contextos.profile.schema import Profile

    import contextos.code_intel.jdtls_provider.discovery as D
    monkeypatch.setattr(D, "discover_runtime_bundle",
                        lambda repo=None, platform_config=None: None)

    profile = Profile(**{
        "llm": {"provider": "claude", "api_key_env": "K"},
        "embedding": {"model": "BAAI/bge-m3"},
        "reranker": {"enabled": True, "model": "x",
                     "top_k_input": 50, "top_k_output": 10},
        "query_expansion": {"enabled": True,
                            "translation_provider": "main_llm",
                            "fallback_provider": "x"},
        "storage": {"data_dir": "/tmp/x"},
        "ingestion": {"default_cleanup": "full",
                      "chunk_strategy": "h2_h3", "min_chunk_chars": 30},
        "jdtls_runtime": {"jdtls_path": "/jdtls",
                          "lombok_path": "/jdtls/l.jar",
                          "java_home": "/jre21"},
        "oracle": {"tns_admin": "/tns",
                   "allowed_instances": ["TEST_DB1"]},
        "projects": [{"name": "demoproj", "path": "/c",
                      "language": "java", "build_system": "gradle"}],
    })
    rt = JdtlsRuntimeConfig.from_profile(profile)
    assert rt.jdtls_path == str(Path("/jdtls"))
    assert rt.java_home == str(Path("/jre21"))


def test_jdtls_runtime_from_profile_expands_tilde(monkeypatch) -> None:
    """profile.example.toml uses ~/.vscode/... paths; from_profile must
    expand them so eclipse_jdtls.py (which wraps in Path() without expanding)
    sees an absolute path.

    同上一测: 占位路径不存在 -> resolve_effective_runtime 会先探
    <cwd>/runtime/contextos-runtime bundle。钉死 discover_runtime_bundle 返回
    None, 隔离掉这仓根真带 bundle 的环境状态, 让本测只验 expanduser 传递链路
    (profile-unverified 支路), 不验 bundle 回退支路。"""
    import os
    from pathlib import Path

    from contextos.code_intel.jdtls_provider.config import JdtlsRuntimeConfig
    from contextos.profile.schema import Profile

    import contextos.code_intel.jdtls_provider.discovery as D
    monkeypatch.setattr(D, "discover_runtime_bundle",
                        lambda repo=None, platform_config=None: None)

    profile = Profile(**{
        "llm": {"provider": "claude", "api_key_env": "K"},
        "embedding": {"model": "BAAI/bge-m3"},
        "reranker": {"enabled": True, "model": "x",
                     "top_k_input": 50, "top_k_output": 10},
        "query_expansion": {"enabled": True,
                            "translation_provider": "main_llm",
                            "fallback_provider": "x"},
        "storage": {"data_dir": "/tmp/x"},
        "ingestion": {"default_cleanup": "full",
                      "chunk_strategy": "h2_h3", "min_chunk_chars": 30},
        "jdtls_runtime": {"jdtls_path": "~/jdtls-server",
                          "lombok_path": "~/jdtls-server/lombok.jar",
                          "java_home": "~/jre21"},
        "oracle": {"tns_admin": "/tns",
                   "allowed_instances": ["TEST_DB1"]},
        "projects": [{"name": "demoproj", "path": "/c",
                      "language": "java", "build_system": "gradle"}],
    })
    rt = JdtlsRuntimeConfig.from_profile(profile)
    home = os.path.expanduser("~")
    assert rt.jdtls_path == str(Path(home) / "jdtls-server")
    assert "~" not in rt.jdtls_path
    assert "~" not in rt.lombok_path
    assert "~" not in rt.java_home


def test_adapter_has_workspace_symbol_method(fixture_toml):
    from contextos.code_intel.jdtls_provider.adapter import JdtlsAdapter
    adapter = JdtlsAdapter.from_config(fixture_toml, project_name="fixture-project-a")
    assert hasattr(adapter, "request_workspace_symbol")


def test_request_workspace_symbol_raises_when_not_started(fixture_toml):
    from contextos.code_intel.jdtls_provider.adapter import JdtlsAdapter
    import pytest
    adapter = JdtlsAdapter.from_config(fixture_toml, project_name="fixture-project-a")
    with pytest.raises(RuntimeError):
        adapter.request_workspace_symbol("Foo")


def test_request_workspace_symbol_delegates_to_ls(fixture_toml):
    """Don't start a real JDT LS: inject a fake _ls, verify delegation + None -> []."""
    from contextos.code_intel.jdtls_provider.adapter import JdtlsAdapter
    adapter = JdtlsAdapter.from_config(fixture_toml, project_name="fixture-project-a")

    class _FakeLS:
        def __init__(self):
            self.calls = []

        def request_workspace_symbol(self, query):
            self.calls.append(query)
            return [{"name": query, "kind": 5, "location": {}}]

    fake = _FakeLS()
    adapter._ls = fake
    out = adapter.request_workspace_symbol("DynamicCharging")
    assert fake.calls == ["DynamicCharging"]
    assert out == [{"name": "DynamicCharging", "kind": 5, "location": {}}]

    class _NoneLS:
        def request_workspace_symbol(self, query):
            return None

    adapter._ls = _NoneLS()
    assert adapter.request_workspace_symbol("X") == []
