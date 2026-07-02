"""导入各 parser 触发 @register 注册(properties/yaml/json + .xml dispatcher)。
控制器统一合并(避并行 task 抢同一文件)。"""
from contextos.config_dim.parsers import (  # noqa: F401
    properties_parser,
    yaml_parser,
    json_parser,
    xml_spring_parser,
    xml_mybatis_parser,
)
