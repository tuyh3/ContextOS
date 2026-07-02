"""Lock vendored solidlsp's 12-line patch (real-world Gradle 5.6.4 配置注入).
The patch's identifying string must remain present; any vendor refresh
must re-apply the patch and re-run JDT LS binding smoke."""
from __future__ import annotations

from pathlib import Path

PATCHED_FILE = (
    Path(__file__).resolve().parents[1]
    / "jdtls_provider" / "solidlsp" / "language_servers" / "eclipse_jdtls.py"
)
PATCH_REF = (
    Path(__file__).resolve().parents[1]
    / "jdtls_provider" / "patches" / "0001-expose-gradle-config.patch"
)


def test_vendored_eclipse_jdtls_file_present() -> None:
    assert PATCHED_FILE.exists(), f"missing vendored file: {PATCHED_FILE}"


def test_patch_reference_file_present() -> None:
    assert PATCH_REF.exists(), (
        "patch reference file missing; if you re-vendored solidlsp, "
        "re-export the diff into 0001-expose-gradle-config.patch"
    )


def test_patch_markers_still_in_vendored_source() -> None:
    src = PATCHED_FILE.read_text(encoding="utf-8")
    markers = [
        "CONTEXTOS PATCH",
        "gradle_arguments",
        "gradle_version_override",
    ]
    missing = [m for m in markers if m not in src]
    assert not missing, (
        f"vendored eclipse_jdtls.py missing patch markers {missing!r}. "
        "Re-apply patches/0001-expose-gradle-config.patch and re-run "
        "the JDT LS binding integration smoke."
    )


def test_workspace_symbol_method_declarations_patched() -> None:
    """CONTEXTOS PATCH (Plan 04 followup): workspace/symbol must include source
    method declarations so 04 code_search gets method-level seeds, not only types.
    Manual test 2026-06-01: upstream default False made methods (execQueryByRoute)
    + fields invisible to workspaceSymbol. If a vendor refresh reverts this to
    False, 04 silently regresses to class-only seeds."""
    src = PATCHED_FILE.read_text(encoding="utf-8")
    assert '"includeSourceMethodDeclarations": True' in src, (
        "symbols patch reverted: workspace/symbol would return types only, "
        "04 code_search loses method-level seeds. Re-apply the True flip "
        "(see eclipse_jdtls.py settings.java.symbols)."
    )
