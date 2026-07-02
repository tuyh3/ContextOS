"""Test config loading from projects.toml.

Uses an in-test fixture TOML (not the user's data/poc/projects.toml which is
gitignored) so tests are reproducible on CI / fresh checkout. There's a separate
integration test for the real toml that skips if absent.
"""
import textwrap
from pathlib import Path
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
allowed_instances = ["TEST_X", "TEST_Y"]

[[projects]]
name = "fixture-project-a"
path = "/tmp/projects/a"
language = "java"
build_system = "gradle"
java = { gradle_home = "/opt/gradle-5.6.4", gradle_version_override = "5.6.4", gradle_arguments = "-Dprofile=prd -DlocalRepositoryDir=/opt/library", gradle_java_home = "/opt/jdk8", gradle_wrapper_enabled = false }

[[projects]]
name = "fixture-project-b"
path = "/tmp/projects/b"
language = "java"
build_system = "gradle"
java = { gradle_home = "/opt/gradle-7.5", gradle_version_override = "7.5", gradle_arguments = "", gradle_java_home = "/opt/jdk17", gradle_wrapper_enabled = true }
"""


@pytest.fixture
def fixture_toml(tmp_path):
    p = tmp_path / "fixture-projects.toml"
    p.write_text(FIXTURE_TOML.strip(), encoding="utf-8")
    return p


def test_load_projects_toml_finds_project_a(fixture_toml):
    from contextos.code_intel.jdtls_provider.config import load_projects, ProjectConfig
    projects = load_projects(fixture_toml)
    assert "fixture-project-a" in projects
    p = projects["fixture-project-a"]
    assert isinstance(p, ProjectConfig)
    assert p.name == "fixture-project-a"
    assert p.path == "/tmp/projects/a"
    assert p.language == "java"


def test_inline_java_settings_loaded(fixture_toml):
    from contextos.code_intel.jdtls_provider.config import load_projects
    projects = load_projects(fixture_toml)
    p = projects["fixture-project-a"]
    assert p.java_settings["gradle_home"] == "/opt/gradle-5.6.4"
    assert p.java_settings["gradle_version_override"] == "5.6.4"
    assert "-Dprofile=prd" in p.java_settings["gradle_arguments"]
    assert p.java_settings["gradle_wrapper_enabled"] is False


def test_multiple_projects_present(fixture_toml):
    from contextos.code_intel.jdtls_provider.config import load_projects
    projects = load_projects(fixture_toml)
    assert {"fixture-project-a", "fixture-project-b"}.issubset(set(projects.keys()))


def test_storage_config(fixture_toml):
    from contextos.code_intel.jdtls_provider.config import load_storage
    storage = load_storage(fixture_toml)
    assert storage.data_dir == "/tmp/contextos-test/data"
    assert storage.jdtls_workspace_dir == "/tmp/contextos-test/ws"


def test_jdtls_runtime_config(fixture_toml):
    from contextos.code_intel.jdtls_provider.config import load_jdtls_runtime
    rt = load_jdtls_runtime(fixture_toml)
    assert rt.jdtls_path == "/home/user/redhat.java/server"
    assert "lombok" in rt.lombok_path
    assert "jre/21" in rt.java_home


def test_gradle_project_with_legacy_java_subtable_raises(tmp_path):
    """For build_system='gradle', the legacy [projects.NAME.java] form is a
    silent-drop pitfall (tomllib parses it but the resulting subtable isn't
    attached to the array entry). The loader MUST raise ValueError so the user
    sees the misconfig instead of getting a degraded JDT LS that quietly
    inits without the right gradle toolchain.
    """
    bad = tmp_path / "legacy.toml"
    bad.write_text(textwrap.dedent('''
        [[projects]]
        name = "x"
        path = "/x"
        language = "java"
        build_system = "gradle"

        [projects.x.java]
        gradle_home = "/should-not-be-loaded"
    '''), encoding="utf-8")
    from contextos.code_intel.jdtls_provider.config import load_projects
    with pytest.raises(ValueError, match="gradle"):
        load_projects(bad)


def test_gradle_project_with_partial_java_keys_raises(tmp_path):
    """Inline java table that's missing gradle_arguments / gradle_java_home
    must raise — those keys are what makes JDT LS pick the right JDK + system
    properties (-Dprofile=prd, -DlocalRepositoryDir=...). Silent fallback to
    JDT LS's defaults would slow-fail in Task 3 with the wrong toolchain.
    """
    bad = tmp_path / "partial.toml"
    bad.write_text(textwrap.dedent('''
        [[projects]]
        name = "x"
        path = "/x"
        language = "java"
        build_system = "gradle"
        java = { gradle_home = "/g" }
    '''), encoding="utf-8")
    from contextos.code_intel.jdtls_provider.config import load_projects
    with pytest.raises(ValueError, match="missing required"):
        load_projects(bad)


def test_non_gradle_project_without_java_passes(tmp_path):
    """Validation only fires for build_system='gradle'. Other build systems
    (or unknown) can omit the java inline table without error — they'll just
    get an empty java_settings dict.
    """
    ok = tmp_path / "no-build-system.toml"
    ok.write_text(textwrap.dedent('''
        [[projects]]
        name = "py-proj"
        path = "/p"
        language = "python"
        build_system = "uv"
    '''), encoding="utf-8")
    from contextos.code_intel.jdtls_provider.config import load_projects
    projects = load_projects(ok)
    assert projects["py-proj"].build_system == "uv"
    assert projects["py-proj"].java_settings == {}


# Integration test against real projects.toml (skips if absent or gitignored).
REAL_TOML = Path(__file__).parent.parent.parent.parent / "data" / "poc" / "projects.toml"


@pytest.mark.skipif(not REAL_TOML.exists(),
                    reason="data/poc/projects.toml not present "
                           "(copy from config/projects.example.toml + edit before running)")
def test_real_projects_toml_parses():
    from contextos.code_intel.jdtls_provider.config import load_projects
    projects = load_projects(REAL_TOML)
    assert len(projects) >= 1
