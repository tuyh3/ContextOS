# solidlsp Vendored Code

**Source**: https://github.com/oraios/serena/tree/main/src/solidlsp
**Version**: v1.5.2 (commit 2ee8f100b25c4d8c8c9b416c3175bb4be413a19c)
**Vendored at**: 2026-05-27
**License**: MIT (see LICENSE in this directory)

## Local Modifications

### 1. Gradle config patch (upstreamable)

`language_servers/eclipse_jdtls.py:1308-1318`:
Expose three new `ls_specific_settings.java` keys to allow per-project Gradle
configuration injection: `gradle_home`, `gradle_version_override`, `gradle_arguments`.
Without this, Serena upstream-jdtls mode cannot work with projects that have neither
`./gradlew` wrapper nor a Buildship-discoverable system Gradle install (e.g. a large
real customer project on Gradle 5.6.4 with `-Dprofile=prd` system property activation).

Patch file: `../patches/0001-expose-gradle-config.patch`
Upstream PR: <link to be filled once PR opened>

Note: the patch file is hand-written and uses placeholder SHAs (`index xxxxxxx..xxxxxxx 100644`).
Before opening the upstream PR, regenerate via `git format-patch` against a checkout of
oraios/serena so SHAs and committer metadata are real. The current form is intended only as
a human-readable record of what we changed; `git apply` on it as-is will not succeed.

### 1b. Workspace-symbol method declarations (ContextOS preference, 2026-06-01)

`language_servers/eclipse_jdtls.py` initializationOptions
`settings.java.symbols.includeSourceMethodDeclarations`: flipped upstream default
`False` -> `True` (carries a second `CONTEXTOS PATCH` marker). Without this, JDT LS
`workspace/symbol` returns only types (class/interface), not method declarations, so
04 code_search could locate classes but not methods. Manual test 2026-06-01 on a large
real customer project confirmed: with the flip, `execQueryByRoute` returns 2 METHOD seeds; without it,
zero. (Standalone field/constant declarations are still not returned even with the flip.)
Locked by `tests/test_solidlsp_patch_marker.py::test_workspace_symbol_method_declarations_patched`.
Not upstreamed (it is our use-case preference, not a Serena bug).

### 2. Vendoring shim (ContextOS-specific, NOT for upstream)

The following four changes are required to relocate `solidlsp` from a top-level package
(as it lives in the upstream repo) into our nested `contextos.code_intel.jdtls_provider.solidlsp`
namespace, while keeping diff vs upstream minimal so future rebases stay cheap:

- `__init__.py`: register self under top-level `solidlsp` name via `sys.modules.setdefault`,
  so upstream's absolute imports (`from solidlsp.X import Y`) resolve.
- `ls.py:22-23`: changed `from serena.util.file_system import ...` and
  `from serena.util.text_utils import ...` to point at the inlined `_serena_util/` subpackage.
- `language_servers/csharp_language_server.py:17` and
  `language_servers/fsharp_language_server.py:14`: same rewrite for `from serena.util.dotnet import DotNETUtil`.
- `_serena_util/`: new subpackage with `file_system.py`, `text_utils.py`, `dotnet.py`,
  `version.py` copied from `serena/util/`. The constant `serena.constants.DEFAULT_SOURCE_FILE_ENCODING`
  is inlined into `_serena_util/text_utils.py` to avoid pulling the full `serena` package.

These shim changes are NOT in the upstream PR — they only exist because we vendor
into a nested namespace; upstream Serena's solidlsp already works correctly as a
top-level package.

## Update Procedure

To bump to a newer Serena version:
1. Clone target version: `git clone --branch <tag> https://github.com/oraios/serena.git`
2. Replace contents of `solidlsp/` (except LICENSE, ATTRIBUTION.md, and `_serena_util/`).
3. Re-apply `../patches/0001-expose-gradle-config.patch` to `language_servers/eclipse_jdtls.py`.
   Also re-apply Local Modifications §1b: set `settings.java.symbols.includeSourceMethodDeclarations`
   to `True` (upstream default is `False`); test_workspace_symbol_method_declarations_patched guards it.
4. Re-add the `sys.modules.setdefault("solidlsp", ...)` block to `solidlsp/__init__.py`.
5. Re-add the import rewrites in `ls.py`, `csharp_language_server.py`, `fsharp_language_server.py`.
6. Diff `serena/util/{file_system,text_utils,dotnet,version}.py` against `_serena_util/` and
   refresh any drift; re-inline `DEFAULT_SOURCE_FILE_ENCODING` if upstream changed it.
7. Run `pytest contextos/code_intel/tests/` to verify.
8. Update commit sha + version above.

## Files NOT Vendored

We deliberately do NOT vendor these parts of Serena:
- `src/serena/` — MCP server / agent / tools / dashboard (Serena product, not the LSP wrapper).
  We DO vendor a minimal subset (`util/{file_system,text_utils,dotnet,version}.py`) into
  `_serena_util/` because solidlsp imports them; see "Local Modifications" §2 above.
- `src/interprompt/` — prompt templating
- `docs/`, `docker/`, etc.

Only `src/solidlsp/` (the LSP abstraction library) plus the four util files above are vendored.
