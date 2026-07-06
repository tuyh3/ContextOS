"""Load per-project JDT LS configuration from projects.toml.

Schema:
    [storage]
    data_dir = "..."
    jdtls_workspace_dir = "..."

    [jdtls_runtime]
    jdtls_path = "..."
    lombok_path = "..."
    java_home = "..."

    [oracle]
    tns_admin = "..."
    allowed_instances = ["..."]

    [[projects]]
    name = "..."
    path = "..."
    language = "java"
    build_system = "gradle"
    java = { gradle_home = "...", gradle_version_override = "...", ... }

The inline `java = {...}` table form is REQUIRED for `build_system = "gradle"`
projects. The legacy `[projects.<name>.java]` nested-table form parses but is NOT
attached to the array entry (TOML quirk with `[[projects]]` + sibling tables) —
load_projects() raises ValueError on a gradle project that has no inline java
config, so the silent-drop pitfall fails fast instead of degrading JDT LS init.
See `data/poc/projects.toml` comment block for rationale.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StorageConfig:
    data_dir: str
    jdtls_workspace_dir: str


@dataclass
class JdtlsRuntimeConfig:
    jdtls_path: str
    lombok_path: str
    java_home: str

    @classmethod
    def from_profile(cls, profile: object) -> "JdtlsRuntimeConfig":
        """从 Profile 构造生效 jdtls 三路径 —— 经 resolver(spec A11): profile
        深校验有效用原配置, 否则回退包内 bundle。unverified 时 resolver 透传
        原串, 本处统一 expanduser(值形态分工见 EffectiveRuntime docstring)。
        duck-typing: 有 jdtls_runtime/code_index 属性即可(测试桩可注入)。"""
        from pathlib import Path

        from contextos.code_intel.jdtls_provider.discovery import (
            resolve_effective_runtime,
        )

        if not hasattr(profile, "jdtls_runtime") or not hasattr(profile, "code_index"):
            raise TypeError(f"expected profile-like object, got {type(profile).__name__}")
        rt = resolve_effective_runtime(profile, root=Path.cwd())
        return cls(
            jdtls_path=str(Path(rt.jdtls_path).expanduser()),
            lombok_path=str(Path(rt.lombok_path).expanduser()),
            java_home=str(Path(rt.java_home).expanduser()),
        )


@dataclass
class ProjectConfig:
    name: str
    path: str
    language: str
    build_system: str
    java_settings: dict[str, Any] = field(default_factory=dict)


def _parse_toml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_storage(toml_path: Path) -> StorageConfig:
    data = _parse_toml(toml_path)
    s = data["storage"]
    return StorageConfig(
        data_dir=s["data_dir"],
        jdtls_workspace_dir=s["jdtls_workspace_dir"],
    )


def load_jdtls_runtime(toml_path: Path) -> JdtlsRuntimeConfig:
    data = _parse_toml(toml_path)
    r = data["jdtls_runtime"]
    return JdtlsRuntimeConfig(
        jdtls_path=r["jdtls_path"],
        lombok_path=r["lombok_path"],
        java_home=r["java_home"],
    )


REQUIRED_GRADLE_JAVA_KEYS = ("gradle_home", "gradle_arguments", "gradle_java_home")


def load_projects(toml_path: Path) -> dict[str, ProjectConfig]:
    """Parse [[projects]] array entries; java config is inline-table on the entry.

    See projects.toml schema comment: java = { gradle_home = "...", ... } inline
    table directly on each [[projects]] entry. We don't support the
    [projects.<name>.java] form (TOML ambiguous with [[projects]]).

    Validation: for `build_system == "gradle"` projects, the `java` inline
    table must be present, must be a dict, and must contain `gradle_home`,
    `gradle_arguments`, `gradle_java_home`. This protects against the legacy
    `[projects.<name>.java]` typo (which silently drops the entire java table
    on a gradle entry) and against partial configs that would let JDT LS init
    with a wrong toolchain.
    """
    data = _parse_toml(toml_path)
    if not isinstance(data.get("projects"), list):
        raise ValueError(
            "projects.toml must have [[projects]] entries with inline `java = {...}`. "
            "See config/projects.example.toml for the schema."
        )
    out: dict[str, ProjectConfig] = {}
    for entry in data["projects"]:
        name = entry["name"]
        build_system = entry.get("build_system", "unknown")
        java_settings = entry.get("java", {})

        if build_system == "gradle":
            if not isinstance(java_settings, dict) or not java_settings:
                raise ValueError(
                    f"Project '{name}' has build_system='gradle' but missing or empty "
                    f"inline `java = {{...}}` table. Did you write [projects.{name}.java]? "
                    f"That legacy form is silently dropped — use inline `java = {{...}}` "
                    f"on the same [[projects]] entry. See config/projects.example.toml."
                )
            missing = [k for k in REQUIRED_GRADLE_JAVA_KEYS if k not in java_settings]
            if missing:
                raise ValueError(
                    f"Project '{name}' (build_system='gradle') is missing required "
                    f"java keys: {missing}. At minimum needs "
                    f"{list(REQUIRED_GRADLE_JAVA_KEYS)} for JDT LS Gradle import."
                )

        out[name] = ProjectConfig(
            name=name,
            path=entry["path"],
            language=entry["language"],
            build_system=build_system,
            java_settings=java_settings,
        )
    return out
