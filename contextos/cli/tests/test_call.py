"""contextos CLI `call` 子命令测试 —— 单独调用任一 MCP tool(ad-hoc, 无需 AI editor)。

设计思路
--------
call 是薄 wrapper: load_profile -> AppContext.from_profile -> build_server ->
in-memory fastmcp Client -> call_tool(name, args) -> 打印结果 JSON。与
suggest-stop-keywords 一样用最小 profile.toml 真跑(不 mock load_profile/AppContext),
因为 call 的核心行为(未知 tool 报错列可用清单 / JSON 解析 / tool 执行错误转 exit code)
只有连到真 build_server + 真 middleware 才验证得到 —— 全 mock 会把这些行为 mock 没了。

选 profile_info 作正向用例:它不摸 JDT/Oracle/RAG(纯读 profile 元信息 + 白名单脱敏),
是全 21 个 tool 里唯一能在最小离线 profile 下必然成功的一个,契合 CLI 测试"绝不真起
JDT/Oracle/RAG"的项目纪律(同 test_cli.py / test_health_cli.py 的说明)。

评分标准
--------
- `call profile_info`: exit_code 0, stdout 是合法 JSON, 且不含任何凭据明文
  (与 test_meta_tools.py 的脱敏断言同口径, 复用同一条红线 #9)。
- `call nonexistent_tool`: exit_code 2, 输出(stdout 或 stderr)里点名列出可用 tool
  (至少含 "profile_info", 证明是真从 list_tools() 取的清单不是编的)。
- `--args` 非法 JSON: exit_code 2。
- `--args` 合法 JSON 但非 object(如数组/数字): exit_code 2。
- `--args-file`: 从 tmp 文件读 `{}` 传给 profile_info, exit_code 0(证明文件路径可行,
  这是 Windows 友好路径 —— 绕开 shell 引号转义)。
- `--args-file` 优先于 `--args`(两者都传时以文件为准)。
- `--help` 列出 call 命令。

自动脚本逻辑
------------
CliRunner().invoke(app, [...]) + CONTEXTOS_PROFILE 环境变量指向 tmp_path 下的最小
profile.toml(复用 test_suggest_stop_keywords._write_min_profile 的 TOML 形状)。
不 monkeypatch load_profile/AppContext/build_server —— 全程真跑, 只是 profile 指向
一个空目录(data_dir 全新建), tool 本身(profile_info)不碰网络/JDT/Oracle。

平台中立性: 只用 tmp_path fixture, 路径经 pathlib.as_posix() 写进 TOML, 不假设
POSIX shell 引号规则(--args-file 就是为规避这点存在的)。
"""
from __future__ import annotations

import json
import pathlib

import pytest
from typer.testing import CliRunner


def _write_min_profile(tmp_path: pathlib.Path) -> pathlib.Path:
    """最小可跑 profile.toml(照抄 test_suggest_stop_keywords._write_min_profile 的形状,
    call 命令的正向用例不需要真源码目录, 但保留字段结构以保证 pydantic 校验通过)。"""
    src = tmp_path / "proj"
    src.mkdir()
    (src / "F0.java").write_text("class F {}", encoding="utf-8")
    prof = tmp_path / "profile.toml"
    prof.write_text(f'''
[llm]
provider = "fake"
api_key_env = "K"
[embedding]
model = "BAAI/bge-m3"
[reranker]
enabled = true
model = "x"
top_k_input = 50
top_k_output = 10
[query_expansion]
enabled = true
translation_provider = "main_llm"
fallback_provider = "x"
[storage]
data_dir = "{(tmp_path / 'database').as_posix()}"
[ingestion]
default_cleanup = "full"
chunk_strategy = "h2_h3"
min_chunk_chars = 30
[jdtls_runtime]
jdtls_path = "/j"
lombok_path = "/l"
java_home = "/h"
[oracle]
tns_admin = "/t"
allowed_instances = ["TEST_DB1"]
[[projects]]
name = "demoproj"
path = "{src.as_posix()}"
language = "java"
build_system = "gradle"
''', encoding="utf-8")
    return prof


@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    prof = _write_min_profile(tmp_path)
    monkeypatch.setenv("CONTEXTOS_PROFILE", str(prof))
    return prof


def test_help_lists_call():
    from contextos.cli.main import app
    res = CliRunner().invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "call" in res.stdout


def test_call_profile_info_succeeds_and_parses_as_json(profile_env):
    from contextos.cli.main import app
    res = CliRunner().invoke(app, ["call", "profile_info"])
    assert res.exit_code == 0, res.output
    parsed = json.loads(res.stdout)
    assert "data_dir" in parsed
    assert "oracle_instances" in parsed


def test_call_profile_info_does_not_leak_credentials(profile_env, monkeypatch):
    """脱敏铁律(红线 #9): 即便环境里有对应 api_key_env 的真值, call profile_info
    的输出也不该出现它, 也不该出现 password/secret/token 字样。"""
    from contextos.cli.main import app
    monkeypatch.setenv("K", "sk-LEAKME-secret-value-9999")
    res = CliRunner().invoke(app, ["call", "profile_info"])
    assert res.exit_code == 0, res.output
    assert "sk-LEAKME-secret-value-9999" not in res.stdout
    assert "password" not in res.stdout.lower()


def test_call_unknown_tool_exits_2_and_lists_available(profile_env):
    from contextos.cli.main import app
    res = CliRunner().invoke(app, ["call", "nonexistent_tool_xyz"])
    assert res.exit_code == 2, res.output
    assert "profile_info" in res.output   # 真从 list_tools() 取的清单, 非编造


def test_call_invalid_json_args_exits_2(profile_env):
    from contextos.cli.main import app
    res = CliRunner().invoke(app, ["call", "profile_info", "--args", "{not valid json"])
    assert res.exit_code == 2, res.output


def test_call_non_object_json_args_exits_2(profile_env):
    from contextos.cli.main import app
    res = CliRunner().invoke(app, ["call", "profile_info", "--args", "[1, 2, 3]"])
    assert res.exit_code == 2, res.output


def test_call_args_file_reads_json_from_file(profile_env, tmp_path):
    from contextos.cli.main import app
    args_file = tmp_path / "args.json"
    args_file.write_text("{}", encoding="utf-8")
    res = CliRunner().invoke(app, ["call", "profile_info", "--args-file", str(args_file)])
    assert res.exit_code == 0, res.output
    parsed = json.loads(res.stdout)
    assert "data_dir" in parsed


def test_call_args_file_wins_over_inline_args(profile_env, tmp_path):
    """--args-file 与 --args 同传时, 以 --args-file 为准(design 里声明的口径)。
    验证方式: --args 传一个非法 JSON(若被采用会 exit 2), --args-file 传合法 {}
    (若被采用则 exit 0)—— exit 0 即证明文件优先。"""
    from contextos.cli.main import app
    args_file = tmp_path / "args_ok.json"
    args_file.write_text("{}", encoding="utf-8")
    res = CliRunner().invoke(
        app,
        ["call", "profile_info", "--args", "{not valid json", "--args-file", str(args_file)],
    )
    assert res.exit_code == 0, res.output


def test_call_default_args_is_empty_object(profile_env):
    """不传 --args/--args-file 时默认 {}(profile_info 不需要任何入参也能跑)。"""
    from contextos.cli.main import app
    res = CliRunner().invoke(app, ["call", "profile_info"])
    assert res.exit_code == 0, res.output
