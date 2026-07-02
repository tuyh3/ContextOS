"""ContextOS-shape adapter around vendored solidlsp.

This is the public API for ContextOS code. solidlsp details are hidden inside.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from contextos.code_intel.jdtls_provider.config import (
    JdtlsRuntimeConfig,
    ProjectConfig,
    StorageConfig,
    load_jdtls_runtime,
    load_projects,
    load_storage,
)
from contextos.code_intel.jdtls_provider.solidlsp.ls import SolidLanguageServer
from contextos.code_intel.jdtls_provider.solidlsp.ls_config import (
    Language,
    LanguageServerConfig,
)
from contextos.code_intel.jdtls_provider.solidlsp.settings import SolidLSPSettings
from contextos.code_intel.jdtls_provider.workspace_manager import workspace_dir_for

log = logging.getLogger(__name__)


class JdtlsAdapter:
    """Per-project JDT LS controller. Initialize once per (project, process)."""

    def __init__(
        self,
        project: ProjectConfig,
        storage: StorageConfig,
        runtime: JdtlsRuntimeConfig,
    ):
        self.project_name = project.name
        self.project_path = project.path
        self.language = project.language

        self._project = project
        self._storage = storage
        self._runtime = runtime
        self._ls: SolidLanguageServer | None = None

    @classmethod
    def from_config(cls, toml_path: Path, project_name: str) -> JdtlsAdapter:
        projects = load_projects(toml_path)
        if project_name not in projects:
            raise KeyError(
                f"Project '{project_name}' not found in {toml_path}. "
                f"Known: {sorted(projects.keys())}"
            )
        return cls(
            project=projects[project_name],
            storage=load_storage(toml_path),
            runtime=load_jdtls_runtime(toml_path),
        )

    def _build_ls_specific_settings(self) -> dict[str, Any]:
        """Merge JDT LS runtime config + per-project Java settings."""
        java_settings: dict[str, Any] = {
            "jdtls_path": self._runtime.jdtls_path,
            "lombok_path": self._runtime.lombok_path,
            "java_home": self._runtime.java_home,
            "lombok_show_generated": True,
        }
        # Add per-project gradle_home / gradle_version_override / gradle_arguments / etc.
        java_settings.update(self._project.java_settings)
        return java_settings

    def start(self, timeout_s: float = 600.0) -> None:
        """Start the JDT LS process + workspace import. Blocks until ServiceReady.

        NOTE (v1 perf TODO): each `start()` spawns a fresh `java`/JDT LS
        process and re-runs Gradle import (~80-90s of the ~110s total),
        even when the `.metadata/` workspace cache is already populated.
        Measured 2026-05-27 on a large real customer project: cold=117s, "warm"=110s — the
        cache only shaves ~80s vs no-cache, doesn't get us to seconds.
        For POC (one-time batch runs in Tasks 3/4) this is acceptable.
        v1 should consider a JVM process pool or a long-lived
        multi-project session (one adapter, many `request_*` calls
        across files in different subprojects via one workspace).
        """
        if self._ls is not None:
            log.warning("Adapter already started for %s", self.project_name)
            return

        ws_base = Path(self._storage.jdtls_workspace_dir)
        ws_dir = workspace_dir_for(ws_base, self.project_path)
        log.info("Workspace dir for %s: %s", self.project_name, ws_dir)

        settings = SolidLSPSettings(
            solidlsp_dir=str(ws_dir / "solidlsp_static"),
            project_data_path=str(ws_dir / "project_data"),
            ls_specific_settings={Language.JAVA: self._build_ls_specific_settings()},
            additional_workspace_folders=[],
        )

        config = LanguageServerConfig(
            code_language=Language.JAVA,
            trace_lsp_communication=False,
            start_independent_lsp_process=True,
            ignored_paths=[],
        )

        log.info("Creating SolidLanguageServer for %s", self.project_name)
        self._ls = SolidLanguageServer.create(
            config, self.project_path, timeout=timeout_s, solidlsp_settings=settings
        )
        log.info("Starting LS (workspace import; can take ~3 min cold)")
        self._ls.start()
        log.info("LS ready for %s", self.project_name)

    def stop(self) -> None:
        if self._ls is None:
            return
        try:
            self._ls.stop()
        except Exception as e:
            log.warning("stop() raised: %s", e)
        self._ls = None

    def open_file(self, _rel_path: str) -> None:
        """No-op stub. Kept for forward API compatibility.

        Validated 2026-05-27 (POC Task 3): solidlsp's `request_definition`
        and other request methods internally wrap with
        `with self.language_server.open_file(rel_path)` (see
        solidlsp/ls.py:1458-1475), which auto-triggers `didOpen`. So
        callers do NOT need to call this method — the underlying LSP
        request handles file open transparently. Empirically confirmed
        by 4/4 RESOLVED on a large real customer project with this no-op in place.

        The method is preserved (as a no-op) so that if a future solidlsp
        version stops auto-opening, we can wire a real `didOpen` here
        without changing the public adapter API.
        """
        if self._ls is None:
            raise RuntimeError("Adapter not started")
        return

    def request_definition(
        self, rel_path: str, line: int, char: int
    ) -> list[Any]:
        if self._ls is None:
            raise RuntimeError("Adapter not started")
        result = self._ls.request_definition(rel_path, line, char)
        return list(result) if result else []

    def request_references(
        self, rel_path: str, line: int, char: int
    ) -> list[Any]:
        if self._ls is None:
            raise RuntimeError("Adapter not started")
        result = self._ls.request_references(rel_path, line, char)
        return list(result) if result else []

    def request_workspace_symbol(self, query: str) -> list[Any]:
        """workspace/symbol: find symbols by name across the workspace (04 §3 step-1 seed search).

        Delegates to solidlsp ls.py:3049; returns a list of UnifiedSymbolInformation dicts
        (with name / kind / location). None -> []. Requires start() first.
        """
        if self._ls is None:
            raise RuntimeError("Adapter not started")
        result = self._ls.request_workspace_symbol(query)
        return list(result) if result else []

    def __enter__(self) -> JdtlsAdapter:
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()
