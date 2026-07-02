"""Cross-namespace validation for Profile. Path checks opt-in (check_paths=True)."""
from __future__ import annotations

from pathlib import Path

from contextos.db_provider.oracle_gate import PRODUCTION_KEYWORDS as _PRODUCTION_KEYWORDS
from contextos.profile.schema import Profile

# Substring-match rationale lives at contextos.db_provider.oracle_gate
# module docstring. Single source of truth for the keyword tuple.


class ProfileValidationError(ValueError):
    """Raised when a Profile passes pydantic but violates cross-namespace rules."""


def validate_profile(profile: Profile, *, check_paths: bool = True) -> None:
    errors: list[str] = []

    for tns in profile.oracle.allowed_instances:
        upper = tns.upper()
        matched = [kw for kw in _PRODUCTION_KEYWORDS if kw in upper]
        if matched:
            errors.append(
                f"oracle.allowed_instances contains {tns!r} "
                f"with production keyword(s) {matched!r}; "
                "POC test instances only"
            )

    if check_paths:
        for label, raw in [
            ("jdtls_runtime.jdtls_path", profile.jdtls_runtime.jdtls_path),
            ("jdtls_runtime.lombok_path", profile.jdtls_runtime.lombok_path),
            ("jdtls_runtime.java_home", profile.jdtls_runtime.java_home),
            ("oracle.tns_admin", profile.oracle.tns_admin),
        ]:
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
