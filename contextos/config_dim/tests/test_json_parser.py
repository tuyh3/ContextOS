# contextos/config_dim/tests/test_json_parser.py
from contextos.config_dim.parsers.json_parser import parse_json, is_blacklisted


def test_json_flatten():
    text = '{"db": {"url": "jdbc:@h", "pool": 5}, "flags": ["x", "y"]}'
    pc = parse_json("conf.json", text)
    keys = {i.key_path: i for i in pc.items}
    assert keys["db.url"].value_raw == "jdbc:@h"
    assert keys["db.pool"].value_type == "int"
    assert keys["flags[0]"].value_raw == "x"


def test_json_blacklist():
    bl = ["package.json", "tsconfig.json"]
    assert is_blacklisted("foo/package.json", bl)
    assert not is_blacklisted("foo/app-config.json", bl)
