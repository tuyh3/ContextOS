"""paths resolver 测试 + 案例库/sidecar 路径 gitignored 验收(spec Appendix E [案例库路径] + Appendix B)。

设计思路: resolved_materialized_dir = profile.corpus.materialized_dir or <data_dir>/materialized;
confirmed_cases_dir = <resolved>/confirmed-cases;ensure_confirmed_cases_dir 空也建。
评分标准: 默认/自定义 materialized_dir 两路径正确;空目录创建幂等;
  gitignore 验收两分支(客户内容只落不归本仓管, 红线 #9):
    (a) data_dir 落仓内 database/ -> confirmed-cases / sidecar DB 落点 git check-ignore 命中;
    (b) data_dir 设仓外绝对路径(/tmp/...) -> confirmed-cases / sidecar DB 落点不在本仓
        worktree 管理范围内(is_relative_to(repo_root) 为 False + 从仓内 git check-ignore
        该外部路径返回非 0=不归本仓管)。
  sidecar(audit DB / ops-vocab 等落 data_dir 下的客户特定文件)同口径走 data_dir, 两分支同样验。
  这 4 个 gitignore 验收测试都依赖 _REPO_ROOT 是真 git 工作树(check-ignore 语义才成立);
  完整包(git archive 导出树, 无 .git)下 `git -C _REPO_ROOT ...` 一律 fatal(rc=128):
  分支 (a) 断言 `== 0` 直接炸;分支 (b) 断言 `!= 0` 会被 128 悄悄喂成假绿(没验到"不被忽略"
  的真语义, 只是巧合数值不为 0)——两类都必须挂 skip 守卫, 而不只是修字面失败的那两个。
自动脚本逻辑: 用 make_ops_profile 合成 profile, 断言路径 + Path.exists + subprocess git check-ignore
  + Path.is_relative_to 仓外判定;模块级 `_require_git_worktree()` 先用
  `git -C _REPO_ROOT rev-parse --is-inside-work-tree` 探测真 git 语义是否可用, 不可用则
  pytest.skip(仓库纪律检查本就该在 clone/开发机跑, 解压树没有意义), 跟随 f8b9442 读 docs/
  测试同款 skip 守卫先例。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from contextos.ops import paths

_REPO_ROOT = Path(__file__).resolve().parents[3]   # contextos/ops/tests -> repo root


def _git_check_ignore(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "check-ignore", str(path)],
        capture_output=True, text=True)


def _require_git_worktree() -> None:
    """完整包(git archive 导出树)无 .git -> git -C _REPO_ROOT 任何子命令都 fatal(rc=128)。
    gitignore 纪律是仓库层检查, 非 git 检出下 skip, clone/开发机上真跑覆盖。"""
    proc = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True)
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        pytest.skip(
            "非 git 检出(完整包解压树无 .git); gitignore 纪律是仓库层检查, "
            "clone/开发机覆盖")


def test_resolved_materialized_dir_default(make_ops_profile):
    p = make_ops_profile(data_dir=Path("/tmp/ops-x"))
    assert paths.resolved_materialized_dir(p) == Path("/tmp/ops-x/materialized")


def test_resolved_materialized_dir_custom(make_ops_profile):
    p = make_ops_profile(materialized_dir="/custom/mat")
    assert paths.resolved_materialized_dir(p) == Path("/custom/mat")


def test_confirmed_cases_dir(make_ops_profile):
    p = make_ops_profile(data_dir=Path("/tmp/ops-y"))
    assert paths.confirmed_cases_dir(p) == Path("/tmp/ops-y/materialized/confirmed-cases")


def test_ensure_creates_empty_dir(make_ops_profile, tmp_path):
    p = make_ops_profile(data_dir=tmp_path / "dd")
    d = paths.ensure_confirmed_cases_dir(p)
    assert d.is_dir()
    # 幂等: 再调一次不报错
    assert paths.ensure_confirmed_cases_dir(p) == d


def test_ops_vocab_path(make_ops_profile):
    p = make_ops_profile(data_dir=Path("/tmp/ops-z"))
    assert paths.ops_vocab_path(p) == Path("/tmp/ops-z/ops-vocab/synonyms.json")


# ---- gitignore 验收: 分支 (a) 仓内 ----

def test_confirmed_cases_in_repo_is_gitignored():
    """(a) data_dir 落仓内 database/ -> confirmed-cases 被 git check-ignore 命中。
    用真仓根: 项目仓的 /database/ 已 gitignore(根 CLAUDE.md 红线)。"""
    _require_git_worktree()
    target = _REPO_ROOT / "database" / "materialized" / "confirmed-cases"
    proc = _git_check_ignore(target)
    assert proc.returncode == 0, \
        f"confirmed-cases under database/ must be gitignored: {proc.stdout}"


def test_sidecar_db_in_repo_is_gitignored():
    """(a) sidecar 落点(audit DB / ops-vocab 等客户特定文件走 data_dir=database/)同样 gitignored。
    data_dir 下整 database/ 已 gitignore -> 其下 contextos.db / ops-vocab/ 任意落点都命中。"""
    _require_git_worktree()
    for rel in ("contextos.db", "ops-vocab/synonyms.json"):
        target = _REPO_ROOT / "database" / rel
        proc = _git_check_ignore(target)
        assert proc.returncode == 0, \
            f"sidecar path under database/ must be gitignored: {rel} -> {proc.stdout}"


# ---- gitignore 验收: 分支 (b) 仓外绝对路径 ----

def test_confirmed_cases_out_of_repo_not_managed(make_ops_profile):
    """(b) data_dir 设仓外绝对路径(/tmp/...) -> confirmed-cases 不归本仓 worktree 管:
    既不在 repo_root 下(is_relative_to False), 从仓内 git check-ignore 该外部路径返回非 0
    (=不被本仓 .gitignore 命中/不归本仓管)。"""
    _require_git_worktree()
    p = make_ops_profile(data_dir=Path("/tmp/contextos-ops-b-data"))
    cc = paths.confirmed_cases_dir(p)
    assert not cc.resolve().is_relative_to(_REPO_ROOT.resolve()), \
        f"out-of-repo confirmed_cases_dir 不该落本仓 worktree 下: {cc}"
    proc = _git_check_ignore(cc)
    assert proc.returncode != 0, \
        f"仓外路径不该被本仓 git check-ignore 命中(应非管理范围): rc={proc.returncode} {proc.stdout}"


def test_sidecar_out_of_repo_not_managed(make_ops_profile):
    """(b) sidecar 落点(audit DB / ops-vocab 走 data_dir)data_dir 设仓外时同样不归本仓管。"""
    _require_git_worktree()
    p = make_ops_profile(data_dir=Path("/tmp/contextos-ops-b-data"))
    for sidecar in (Path(p.storage.data_dir) / "contextos.db", paths.ops_vocab_path(p)):
        assert not sidecar.resolve().is_relative_to(_REPO_ROOT.resolve()), \
            f"out-of-repo sidecar 不该落本仓 worktree 下: {sidecar}"
        proc = _git_check_ignore(sidecar)
        assert proc.returncode != 0, \
            f"仓外 sidecar 不该被本仓 git check-ignore 命中: rc={proc.returncode} {proc.stdout}"
