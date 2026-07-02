from contextos.config_dim.parsers.yaml_parser import parse_yaml


def test_yaml_flatten_dotpath():
    text = (
        "spring:\n"
        "  datasource:\n"
        "    url: jdbc:oracle:@h\n"
        "    pool: 10\n"
        "features:\n"
        "  - a\n"
        "  - b\n"
    )
    pc = parse_yaml("application.yml", text)
    keys = {i.key_path: i for i in pc.items}
    assert keys["spring.datasource.url"].value_raw == "jdbc:oracle:@h"
    assert keys["spring.datasource.pool"].value_type == "int"
    # list 拍平为索引 path
    assert keys["features[0]"].value_raw == "a"
    assert keys["features[1]"].value_raw == "b"
