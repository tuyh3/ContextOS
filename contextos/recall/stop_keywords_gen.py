"""停用词草稿生成器(spec 附录 D): 一趟扫源码算 document-frequency, df 超阈值 = 过宽候选,
写成 gitignored 草稿供人工核对。复用 keyword_extract 抽取正则保证与过滤口径一致。
生成器绝不改运行期过滤行为(只产草稿, 不动 default.json / profile / 已激活客户文件)。"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from contextos.recall.keyword_extract import _SHOUTY_RE, extract_camelcase_phrases

_DRAFT_NAME = "stop-keywords.draft.txt"


def _tokens_in_file(text: str) -> set[str]:
    """单文件去重 token 集: SHOUTY(大写归一)+ CamelCase(原样, 与过滤口径一致)。"""
    toks = {m.upper() for m in _SHOUTY_RE.findall(text) if len(m) >= 3}
    toks |= extract_camelcase_phrases(text)
    return toks


def _iter_java_files(source_roots: list[Path], exclude_dirs: list[str]):
    excl = set(exclude_dirs or [])
    for root in source_roots:
        if not root.exists():
            continue
        for p in root.rglob("*.java"):
            if any(part in excl for part in p.parts):
                continue
            yield p


def derive_stop_keyword_candidates(
    source_roots: list[Path], *, exclude_dirs: list[str],
    min_files: int = 20, min_df_ratio: float = 0.2,
) -> list[tuple[str, int]]:
    """扫源码 -> 每 token 的 document-frequency -> df>=min_files 且 df/total>=min_df_ratio 为候选,
    按 df 倒序。total=0 时返回空。"""
    df: Counter[str] = Counter()
    total = 0
    for p in _iter_java_files(source_roots, exclude_dirs):
        total += 1
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for tok in _tokens_in_file(text):
            df[tok] += 1
    if total == 0:
        return []
    cands = [(t, c) for t, c in df.items() if c >= min_files and c / total >= min_df_ratio]
    cands.sort(key=lambda x: (-x[1], x[0]))
    return cands


def render_draft(candidates: list[tuple[str, int]]) -> str:
    header = (
        "# 停用词草稿(自动扫本项目生成, 待人工核对)。\n"
        "# 核对: 确实该忽略(搜了等于搜全仓)的留着; 误判的真业务词删掉整行。\n"
        "# 格式: TOKEN <出现文件数>。核对后存为客户文件, 由 profile.input.scope.stop_keywords_path 指向。\n"
    )
    body = "".join(f"{t}\t{c}\n" for t, c in candidates)
    return header + body


def write_draft(
    source_roots: list[Path], *, exclude_dirs: list[str], data_dir: Path,
    min_files: int = 20, min_df_ratio: float = 0.2,
) -> tuple[int, Path]:
    """生成候选 + 写草稿到 <data_dir>/stop-keywords.draft.txt。只写 .draft, 绝不碰客户文件。"""
    cands = derive_stop_keyword_candidates(
        source_roots, exclude_dirs=exclude_dirs, min_files=min_files, min_df_ratio=min_df_ratio)
    data_dir.mkdir(parents=True, exist_ok=True)
    draft = data_dir / _DRAFT_NAME
    draft.write_text(render_draft(cands), encoding="utf-8")
    return len(cands), draft
