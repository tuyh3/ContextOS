# contextos/config_dim/tests/test_properties_parser.py
from contextos.config_dim.parsers.properties_parser import parse_properties


def test_properties_basic_and_continuation():
    text = (
        "# comment\n"
        "jdbc.url=jdbc:oracle:thin:@host\n"
        "app.enabled = true\n"
        "app.list=a,\\\n  b,c\n"   # 续行 \\
    )
    pc = parse_properties("application.properties", text)
    keys = {i.config_key: i for i in pc.items}
    assert keys["jdbc.url"].value_raw == "jdbc:oracle:thin:@host"
    assert keys["app.enabled"].value_type == "bool"
    assert keys["app.list"].value_raw == "a,b,c"   # 续行拼接
    assert all(i.entity_key == i.key_path == i.config_key for i in pc.items)
