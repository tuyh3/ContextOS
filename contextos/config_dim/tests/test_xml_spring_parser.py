"""Task A8 测试: xml_spring_parser。

设计思路:
  - Spring `<bean id class>` 应进 ParsedConfig.class_refs(供 bind_resolver Step 3
    按 XML id/ref 解析 java_class 绑定); `<property name value>` 应进 items
    (config_key = "<bean_id>.<property_name>")。
  - 非 <beans> 根的 .xml(mybatis / 任意 xml)应返回空 ParsedConfig, 由
    A9 + A12 dispatcher 再路由, 本 parser 只认 spring beans。
评分标准 / 自动测试逻辑:
  - test_spring_bean_and_property: file_type=xml-spring; class_refs 含 ds->com.x.DataSource;
    items 里 ds.url 原值 + ds.maxPool 类型推断为 int。
  - test_spring_xml_not_beans_returns_empty: 非 beans 根 -> items/class_refs 皆空。
"""
from contextos.config_dim.parsers.xml_spring_parser import parse_spring_xml


def test_spring_bean_and_property():
    text = (
        '<beans>'
        '  <bean id="ds" class="com.x.DataSource">'
        '    <property name="url" value="jdbc:@h"/>'
        '    <property name="maxPool" value="20"/>'
        '  </bean>'
        '</beans>'
    )
    pc = parse_spring_xml("beans.xml", text)
    assert pc.file_type == "xml-spring"
    assert any(r["bean_id"] == "ds" and r["class_fqn"] == "com.x.DataSource" for r in pc.class_refs)
    keys = {i.config_key: i for i in pc.items}
    assert keys["ds.url"].value_raw == "jdbc:@h"
    assert keys["ds.maxPool"].value_type == "int"


def test_spring_xml_not_beans_returns_empty():
    pc = parse_spring_xml("other.xml", "<root><a/></root>")
    assert pc.items == [] and pc.class_refs == []
