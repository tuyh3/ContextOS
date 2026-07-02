# ruff: noqa

# CONTEXTOS VENDORING (2026-05-27): register this package under the top-level name
# `solidlsp` so upstream's absolute imports (`from solidlsp.X import Y`) keep resolving
# despite the package being vendored under `contextos.code_intel.jdtls_provider`. This
# avoids touching ~74 upstream files and keeps the local diff to two small marked patches
# inside `language_servers/eclipse_jdtls.py`: the 12-line gradle-config injection
# (../patches/0001-expose-gradle-config.patch) and a 1-line symbols flip
# (includeSourceMethodDeclarations=True). Both recorded in ATTRIBUTION.md "Local Modifications".
import sys as _sys
_sys.modules.setdefault("solidlsp", _sys.modules[__name__])

from .ls import SolidLanguageServer
