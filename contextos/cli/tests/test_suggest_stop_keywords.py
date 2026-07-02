"""设计: CLI 读 profile -> resolve_source_roots -> write_draft -> 打印草稿路径 + 候选数。
评分: 草稿写出、退出码 0、不动 default.json / profile。用 typer.testing.CliRunner。"""
import pathlib
from typer.testing import CliRunner


def _write_min_profile(tmp_path: pathlib.Path) -> pathlib.Path:
    src = tmp_path / "proj"
    src.mkdir()
    for i in range(4):
        (src / f"F{i}.java").write_text("class F { FOOSVC x; }", encoding="utf-8")
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


def test_cli_suggest_writes_draft(tmp_path, monkeypatch):
    from contextos.cli.main import app
    prof = _write_min_profile(tmp_path)
    monkeypatch.setenv("CONTEXTOS_PROFILE", str(prof))
    res = CliRunner().invoke(app, ["suggest-stop-keywords", "--min-files", "3", "--min-df-ratio", "0.5"])
    assert res.exit_code == 0, res.output
    draft = tmp_path / "database" / "stop-keywords.draft.txt"
    assert draft.exists()
    assert "FOOSVC" in draft.read_text(encoding="utf-8")
