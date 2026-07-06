"""spec A11: 除白名单外, 全仓不许直读 profile.jdtls_runtime.<路径字段> /
code_index.indexer_jar 参与执行。防回退逻辑被绕过(review P1 家族)。

局限声明: 本测试是**词法**守卫(正则扫源码文本), 不是语义/类型分析 —— 别名形
(如 `rt = profile.jdtls_runtime; rt.java_home`, 或把 jdtls_runtime 整体传参后在
别处取属性)会逃逸这条正则, 不会被本测试拦到。行为面的兜底靠真实调用点自带的
sentinel 测试: test_rebuild_entry_runtime.py 两例(rebuild_entry 消费 resolver
的行为验证)+ test_init_e2e.py 的 sentinel(init 全链路消费 resolver 的行为验证)。
新增消费 runtime 路径的调用点(即便走别名逃过本测试)也必须自带这类 sentinel 测试,
不能只靠本文件的词法扫描当唯一防线。"""
import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[3] / "contextos"
# 白名单: resolver 自身 / validator(校验层)/ health 探针(展示层)。
# schema.py / loader.py 已 grep 确认零命中(该正则从未在这两处出现过), 且它们身处
# "信任边界外"(定义/加载层, 不参与 runtime 路径解析执行) —— 继续留在 ALLOW 会为
# P1 家族(resolver 被绕过)开一道不必要的豁免口子, 故收紧移除。
# config.py / paths.py(原 from_profile 实现所在处)已 grep 确认零命中(它们是
# resolver 引入前的 P1 原始直读点, 迁移后正则已不再命中) —— 继续留豁免会为回归
# 悄悄开口子(若未来有人加回裸读, 测试本该报红却被这条豁免吞掉), 故一并收紧移除。
# 测试文件不扫。
ALLOW = {
    "code_intel/jdtls_provider/discovery.py",
    "profile/validator.py",
    "mcp_server/tools/meta.py",
}
RAW = re.compile(
    r"jdtls_runtime\.(jdtls_path|lombok_path|java_home)"
    r"|code_index\.indexer_jar")


def test_pkg_path_resolves_to_repo_contextos():
    # parents[3] 数错会扫空目录假绿(本文件在 contextos/code_intel/tests/ 下,
    # 深度=3 层到仓根)。自检: PKG 必须真实存在且含已知子包。
    assert PKG.is_dir(), f"PKG 未指向真实目录: {PKG}"
    assert (PKG / "cli").is_dir(), f"PKG 路径算错, 未指到 contextos 包: {PKG}"
    assert (PKG / "code_intel").is_dir()


def test_no_raw_runtime_reads():
    hits = []
    for f in PKG.rglob("*.py"):
        rel = f.relative_to(PKG).as_posix()
        if rel in ALLOW or "/tests/" in f"/{rel}" or rel.startswith("tests/"):
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if RAW.search(line) and not line.lstrip().startswith("#"):
                hits.append(f"{rel}:{i}: {line.strip()}")
    assert not hits, "直读 runtime 配置(须经 resolver, spec A11):\n" + "\n".join(hits)
