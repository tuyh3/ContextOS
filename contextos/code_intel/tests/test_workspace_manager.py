"""Test workspace_manager: per-project workspace root dir."""
from pathlib import Path
import tempfile


def test_workspace_dir_for_project():
    """Root is created. We intentionally do NOT assert any subdir
    (data_dir/config_path/etc.) — solidlsp owns those via its SolidLSPSettings
    API parameter names, and creates them on first use.
    """
    from contextos.code_intel.jdtls_provider.workspace_manager import workspace_dir_for
    with tempfile.TemporaryDirectory() as tmp:
        ws = workspace_dir_for(
            base_dir=Path(tmp),
            project_path="/tmp/projects/demoproj/cust",
        )
        assert ws.exists()
        assert ws.is_dir()


def test_workspace_dir_stable_across_calls():
    from contextos.code_intel.jdtls_provider.workspace_manager import workspace_dir_for
    with tempfile.TemporaryDirectory() as tmp:
        ws1 = workspace_dir_for(Path(tmp), "/tmp/projects/demoproj/cust")
        ws2 = workspace_dir_for(Path(tmp), "/tmp/projects/demoproj/cust")
        assert ws1 == ws2


def test_workspace_dir_differs_between_projects():
    from contextos.code_intel.jdtls_provider.workspace_manager import workspace_dir_for
    with tempfile.TemporaryDirectory() as tmp:
        ws_cust = workspace_dir_for(Path(tmp), "/tmp/projects/demoproj/cust")
        ws_channel = workspace_dir_for(Path(tmp), "/tmp/projects/demoproj/channel")
        assert ws_cust != ws_channel


def test_workspace_dir_is_absolute_even_with_relative_base(monkeypatch, tmp_path):
    """相对 base_dir(profile data_dir 已从 ~/contextos-fpa 改相对 'database')必须解析成
    绝对路径返回。根因(2026-06-07 真跑坐实): JDT java 子进程 cwd = 被分析项目根(某电信客户项目),
    不是 contextos 仓根; python 端按仓根 cwd 建/填 config_path, 但把相对 '-data database/...'
    交给子进程 -> 子进程在该项目下找空 config -> Equinox 1s 内启动失败猝死
    (LanguageServerTerminatedException)。返回绝对路径让两端看到同一真实位置。"""
    from contextos.code_intel.jdtls_provider.workspace_manager import workspace_dir_for
    monkeypatch.chdir(tmp_path)
    ws = workspace_dir_for(Path("database/jdtls-workspaces"), "/tmp/projects/demoproj")
    assert ws.is_absolute()
    assert ws.exists() and ws.is_dir()
