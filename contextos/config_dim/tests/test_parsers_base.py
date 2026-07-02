from contextos.config_dim.parsers.base import ParsedItem, ParsedConfig, parser_for


def test_parsed_config_shape():
    pc = ParsedConfig(source_type="file", file_path="a.properties", file_type="properties")
    pc.items.append(ParsedItem(entity_key="x.y", config_key="x.y", key_path="x.y", value_raw="1", value_type="int"))
    assert pc.items[0].config_key == "x.y"
    assert pc.class_refs == [] and pc.sql_refs == []


def test_parser_for_by_extension():
    assert parser_for("application.properties") is not None
    assert parser_for("conf.yaml") is not None
    assert parser_for("readme.md") is None  # 非配置扩展 -> None
