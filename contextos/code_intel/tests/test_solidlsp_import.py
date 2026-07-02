"""Smoke test: vendored solidlsp imports cleanly."""

def test_solidlsp_imports():
    from contextos.code_intel.jdtls_provider.solidlsp.ls import SolidLanguageServer
    from contextos.code_intel.jdtls_provider.solidlsp.ls_config import LanguageServerConfig, Language
    from contextos.code_intel.jdtls_provider.solidlsp.settings import SolidLSPSettings
    from contextos.code_intel.jdtls_provider.solidlsp.language_servers.eclipse_jdtls import EclipseJDTLS
    assert SolidLanguageServer is not None
    assert LanguageServerConfig is not None
    assert SolidLSPSettings is not None
    assert EclipseJDTLS is not None
    assert Language.JAVA.value == "java"


def test_patch_applied():
    """Verify the 12-line patch is present in vendored eclipse_jdtls.py."""
    from contextos.code_intel.jdtls_provider.solidlsp.language_servers import eclipse_jdtls
    import inspect
    src = inspect.getsource(eclipse_jdtls)
    assert "CONTEXTOS PATCH" in src
    assert "custom_gradle_home_override" in src
    assert "custom_gradle_version_override" in src
    assert "custom_gradle_args_override" in src
    # Patch must sit BETWEEN the runtime-dependency gradle path block and the
    # gradle_settings["java"] line — otherwise a per-project override would race
    # against the bundled distribution path.
    # NOTE: locate the gradle patch by its gradle-specific marker, NOT the generic
    # "CONTEXTOS PATCH" string — there is now a second CONTEXTOS PATCH (the symbols /
    # includeSourceMethodDeclarations one) earlier in the file, so src.index of the
    # generic marker would point at the wrong patch.
    anchor_pre = src.index('gradle_settings["home"] = self.runtime_dependency_paths.gradle_path')
    anchor_post = src.index('gradle_settings["java"] = {"home": gradle_java_home')
    patch_pos = src.index("custom_gradle_home_override")
    assert anchor_pre < patch_pos < anchor_post
