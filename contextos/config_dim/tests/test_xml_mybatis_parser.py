from contextos.config_dim.parsers.xml_mybatis_parser import parse_mybatis_xml
from contextos.config_dim.parsers.base import parser_for


def test_mybatis_namespace_and_statements():
    text = (
        '<mapper namespace="com.x.OrderMapper">'
        '  <select id="findById">SELECT * FROM CB_ORDER WHERE ID=#{id}</select>'
        '  <update id="touch">UPDATE CB_ORDER SET TS=SYSDATE</update>'
        '</mapper>'
    )
    pc = parse_mybatis_xml("OrderMapper.xml", text)
    assert pc.file_type == "xml-mybatis"
    ns = {r["stmt_id"]: r for r in pc.sql_refs}
    assert ns["findById"]["namespace"] == "com.x.OrderMapper"
    assert ns["findById"]["sql_kind"] == "select"
    assert ns["touch"]["sql_kind"] == "update"


def test_xml_dispatch_routes_by_root():
    # .xml dispatcher: beans -> spring, mapper -> mybatis
    fn = parser_for("AnyMapper.xml")
    assert fn is not None
    pc = fn("AnyMapper.xml", '<mapper namespace="N"><select id="s">SELECT 1 FROM DUAL</select></mapper>')
    assert pc.file_type == "xml-mybatis"
