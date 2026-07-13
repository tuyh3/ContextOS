"""Cross-namespace validation for Profile. Path checks opt-in (check_paths=True).

多方言化(spec 2026-07-10 附录 A/F.2): prod 关键词闸按 database.type 分派——
oracle 沿用 TNS 名扫描(行为不变), mysql 走 gate_common 三串闸(alias+host+库名,
load 期与客户端构造双执行点之一)。tns_admin 路径检查只对 oracle 生效。
"""
from __future__ import annotations

from pathlib import Path

from contextos.db_provider.gate_common import (
    PRODUCTION_KEYWORDS as _PRODUCTION_KEYWORDS,
    DbSafetyError,
    assert_instance_is_test_only,
)
from contextos.profile.schema import Profile

# Substring-match rationale lives at contextos.db_provider.gate_common
# module docstring. Single source of truth for the keyword tuple.


class ProfileValidationError(ValueError):
    """Raised when a Profile passes pydantic but violates cross-namespace rules."""


def validate_profile(profile: Profile, *, check_paths: bool = True) -> None:
    errors: list[str] = []
    db = profile.database
    assert db is not None  # schema 垫片保证: load 成功即已归一

    if db.type == "oracle":
        for tns in db.oracle.allowed_instances:  # type: ignore[union-attr]
            upper = tns.upper()
            matched = [kw for kw in _PRODUCTION_KEYWORDS if kw in upper]
            if matched:
                errors.append(
                    f"database.oracle.allowed_instances contains {tns!r} "
                    f"with production keyword(s) {matched!r}; "
                    "test instances only"
                )
    elif db.type == "mysql":
        aliases = [i.alias for i in db.mysql.instances]  # type: ignore[union-attr]
        for inst in db.mysql.instances:  # type: ignore[union-attr]
            try:
                assert_instance_is_test_only(
                    alias=inst.alias, host=inst.host,
                    databases=inst.databases, allowed_aliases=aliases,
                )
            except DbSafetyError as exc:
                errors.append(f"database.mysql.instances[{inst.alias!r}]: {exc}")

    if check_paths:
        path_items = [
            ("jdtls_runtime.jdtls_path", profile.jdtls_runtime.jdtls_path),
            ("jdtls_runtime.lombok_path", profile.jdtls_runtime.lombok_path),
            ("jdtls_runtime.java_home", profile.jdtls_runtime.java_home),
        ]
        if db.type == "oracle":
            # tns_admin 是 Oracle 客户端概念(tnsnames.ora 目录), 其它方言无此路径
            path_items.append(("database.oracle.tns_admin", db.oracle.tns_admin))  # type: ignore[union-attr]
        for label, raw in path_items:
            p = Path(raw).expanduser()
            if not p.is_absolute():
                errors.append(
                    f"{label} must be absolute or start with ~; got: {raw!r}"
                )
            elif not p.exists():
                errors.append(f"{label} does not exist on disk: {raw}")
        for proj in profile.projects:
            p = Path(proj.path).expanduser()
            if not p.is_absolute():
                errors.append(
                    f"projects[{proj.name}].path must be absolute or start "
                    f"with ~; got: {proj.path!r}"
                )
            elif not p.exists():
                errors.append(f"projects[{proj.name}].path missing: {proj.path}")

    # jdtls_runtime 路径错的补救指引(2026-07-02): 裸"不存在"报错没人知道路径从哪来,
    # 指到 health 自动探测 + README 下载指引。只加提示不改判定。
    if any(e.startswith("jdtls_runtime.") for e in errors):
        errors.append(
            "hint: run `uv run contextos health` -- 本机装有 VSCode Java 扩展时会自动探测并"
            "打印 [jdtls_runtime] 三路径现成建议; 手动下载指引见 README 'JDT LS 运行时从哪来'"
        )

    if errors:
        raise ProfileValidationError("; ".join(errors))
