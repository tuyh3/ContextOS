"""MyBatis mapper 扫描层(spec 附录 E.4/E.5, L4 接线)。

设计思路(memory feedback_contextos_test_documentation):
- E.4 扫描 MUST: 按**文件内容**识别 mapper(mybatis-3-mapper DTD 或 <mapper namespace=>),
  不按目录约定(大写 Resources / src/main/java 下的 XML 都认); 排除 target/-bak/build。
  识别复用 util.mybatis_sniff(与 config_dim 同一实现)。
- E.5 方言侧选择 MUST: 以 profile database.type 驱动, 只收本方言 mapper 目录 + 非方言目录,
  排掉**另一方言**的 mapper 树(pak-bomc 实测 mysqlMapper/ 与 oracleMapper/ 兄弟目录并存,
  oracleMapper 对 MySQL 目标是漂移死代码)。
评分标准(assert):
  1. mysqlMapper 下的真 mapper(含 DTD/根标签)被收; db_type=mysql 时 oracleMapper 树被排;
  2. db_type=oracle 反向: oracleMapper 收、mysqlMapper 排;
  3. 非方言目录(plain sqlmap/ 或裸 mapper/)不论方言都收;
  4. 非 mapper 的 .xml(spring beans / 普通配置)不收(内容 sniff 拦);
  5. target/ 与 *-bak/ 目录整树排除(exclude_dirs + -bak 约定)。
脚本逻辑: tmp_path 造多方言目录树 + 中性合成 mapper/非 mapper XML; 断言路径集合。
"""
from pathlib import Path

from contextos.lineage.source_scan import scan_mapper_files
from contextos.profile.schema import CodeConfig

_MAPPER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"\n'
    '  "http://mybatis.org/dtd/mybatis-3-mapper.dtd">\n'
    '<mapper namespace="com.x.{ns}">\n'
    '  <select id="q" resultType="map">SELECT * FROM t_demo</select>\n'
    '</mapper>\n'
)
_SPRING = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<beans xmlns="http://www.springframework.org/schema/beans">\n'
    '  <bean id="ds" class="x.DataSource"/>\n'
    '</beans>\n'
)


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _tree(root: Path) -> None:
    _write(root / "svc/src/main/resources/mysqlMapper/FooMapper.xml", _MAPPER.format(ns="FooMapper"))
    _write(root / "svc/src/main/resources/oracleMapper/FooMapper.xml", _MAPPER.format(ns="FooOra"))
    _write(root / "svc/src/main/resources/sqlmap/BareMapper.xml", _MAPPER.format(ns="BareMapper"))
    _write(root / "svc/src/main/resources/spring-beans.xml", _SPRING)          # 非 mapper
    _write(root / "target/classes/mysqlMapper/StaleMapper.xml", _MAPPER.format(ns="Stale"))  # 编译产物
    _write(root / "svc-bak/mysqlMapper/OldMapper.xml", _MAPPER.format(ns="Old"))              # 备份树


def test_mysql_target_collects_mysql_and_nondialect_excludes_oracle(tmp_path):
    _tree(tmp_path)
    got = set(scan_mapper_files(tmp_path, CodeConfig(), db_type="mysql"))
    assert "svc/src/main/resources/mysqlMapper/FooMapper.xml" in got
    assert "svc/src/main/resources/sqlmap/BareMapper.xml" in got          # 非方言目录照收
    assert "svc/src/main/resources/oracleMapper/FooMapper.xml" not in got  # 另一方言排除
    assert "svc/src/main/resources/spring-beans.xml" not in got            # 非 mapper 内容拦
    assert not any("target/" in g for g in got)                           # 编译产物排
    assert not any("-bak/" in g for g in got)                             # 备份树排


def test_oracle_target_reverses_dialect_selection(tmp_path):
    _tree(tmp_path)
    got = set(scan_mapper_files(tmp_path, CodeConfig(), db_type="oracle"))
    assert "svc/src/main/resources/oracleMapper/FooMapper.xml" in got
    assert "svc/src/main/resources/sqlmap/BareMapper.xml" in got
    assert "svc/src/main/resources/mysqlMapper/FooMapper.xml" not in got


def test_non_mapper_xml_never_collected(tmp_path):
    _write(tmp_path / "a/spring.xml", _SPRING)
    assert scan_mapper_files(tmp_path, CodeConfig(), db_type="mysql") == []
