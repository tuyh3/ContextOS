# mybatis-mapper2sql (vendored)

- 上游: https://github.com/hhyo/mybatis-mapper2sql
- 取自: GitHub master, commit `31b9753c9c038efd36f270902611783c0e4b2cd5`(2021-08-01, 含 PR #10 choose 尾部内容修复, 比 PyPI 0.1.9 新)
- vendor 日期: 2026-07-10
- License: Apache-2.0(本目录 LICENSE 保留全文; 上游仓无 NOTICE 文件)
- 唯一三方依赖: sqlparse(已加入仓根 pyproject.toml, `sqlparse>=0.5`)

## 为何 vendor(spec 附录 E.2)

上游 2022-10-26 归档(read-only), 不再维护 -> 自维护 + 最小补丁, 与红线 #5
(serena solidlsp)/vendor/java-indexer 同一 vendoring 惯例。选它不自研的决定性理由:
默认(native=False)对 `<choose>/<when>/<otherwise>` 输出**全分支并集**, 与影响分析
"要所有分支的表"的方向一致(市面主流工具是给参数渲染运行期一条, 方向相反);
且已在 pak-bomc 真实 51 mapper 实测通过(spec 附录 E.1)。

## 为何放 contextos/lineage/_vendor/ 而非仓根 vendor/

spec E.2 字面写 `vendor/mybatis_mapper2sql/`, 但仓根 vendor/ 不在 Python path:
pyproject 的 hatch wheel 只收 `packages = ["contextos"]`, pytest `testpaths` 也只扫
contextos。放包内保证 `uv run pytest` 零配置可 import(`from
contextos.lineage._vendor.mybatis_mapper2sql import ...`), 不用动打包配置。
仓根 vendor/java-indexer 是 Java 源码 + jar(不进 Python path), 不可比。

## 只 vendor 库本体

上游 setup.py / tests/ / .travis.yml 等打包与 CI 件不收; 收 4 个源文件
(`__init__.py` / `convert.py` / `generate.py` / `params.py`)+ LICENSE。

## ContextOS 补丁清单(最小 diff, 逐条带测试)

1. **patch 1(spec E.3.1)**: `generate.py` 新增 `_safe_sqlparse_format`, 两处
   `sqlparse.format(...)` 调用点改走它 —— sqlparse>=0.5 对超大 SQL 抛
   "Maximum number of tokens exceeded (10000)"(排版美化步, 非解析必需),
   失败时返回未美化原文不阻断展开。pak 级 82 语句大 mapper 实测触发。
   测试: `contextos/lineage/tests/test_mybatis_extract.py::test_sqlparse_token_limit_does_not_break_expand`

补丁 2(跨文件 include)与补丁 3(输出清洗/抽表)**不改本目录代码**, 实现为
封装层 `contextos/lineage/mybatis_extract.py`(注入点: `convert_include` 用
`mybatis_mapper.get(refid)` 查片段, 把跨文件 `<sql>` 片段并进每个 mapper 的
dict 即可, 无需 fork 上游逻辑)。

## 已知 nit(不修, 保持上游原样)

- 上游字符串里有 `'\{'` / `'\S'` 类非法转义序列(Python 3.12+ 会出 SyntaxWarning;
  本仓 pin 3.11, 默认不可见)。行为无影响, 为保持与上游 diff 最小不改。
- 上游 `create_mapper(xml=path)` 分支用平台默认编码 open 文件; 封装层恒走
  `xml_raw_text` 入参(自带 utf-8 -> gbk 容错解码), 不触该分支。
