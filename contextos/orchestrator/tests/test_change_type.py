# contextos/orchestrator/tests/test_change_type.py
from typing import get_args

from contextos.impact_map.enums import ChangeType
from contextos.orchestrator.change_type import infer_change_type


def test_method_add_vs_modify():
    assert infer_change_type("METHOD", ["add"]) == "add_method"
    assert infer_change_type("METHOD", ["modify"]) == "modify_method"
    assert infer_change_type("METHOD", []) == "modify_method"


def test_class_interface_field():
    assert infer_change_type("CLASS", ["add"]) == "add_class"
    assert infer_change_type("INTERFACE", ["modify"]) == "modify_class"
    assert infer_change_type("FIELD", ["add"]) == "modify_class"


def test_sql_config_dims():
    assert infer_change_type("SQL_TABLE", ["modify"]) == "db_config_change"
    assert infer_change_type("SQL_COLUMN", []) == "db_config_change"
    assert infer_change_type("CONFIG_KEY", ["modify"]) == "config_change"
    assert infer_change_type("CONFIG_TABLE", []) == "config_change"


def test_entrypoints_and_unknown():
    assert infer_change_type("API_ENTRY", []) == "modify_method"
    assert infer_change_type("BATCH", ["add"]) == "modify_method"
    assert infer_change_type("OTHER", []) == "unknown"
    assert infer_change_type("MENU", []) == "unknown"


def test_result_always_valid_change_type():
    valid = set(get_args(ChangeType))
    for kind in ("METHOD", "CLASS", "SQL_TABLE", "CONFIG_KEY", "API_ENTRY", "OTHER"):
        assert infer_change_type(kind, ["add"]) in valid
        assert infer_change_type(kind, []) in valid
