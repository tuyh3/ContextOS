"""输入适配器包。import 即注册 text + docx。"""
from contextos.requirement.adapters.base import (
    Adapter,
    AdapterResult,
    get_adapter,
    parse_failure,
    register,
)

# 触发注册(import 副作用)
from contextos.requirement.adapters import docx as _docx  # noqa: E402,F401
from contextos.requirement.adapters import email as _email  # noqa: E402,F401
from contextos.requirement.adapters import text as _text  # noqa: E402,F401

__all__ = ["Adapter", "AdapterResult", "get_adapter", "parse_failure", "register"]
