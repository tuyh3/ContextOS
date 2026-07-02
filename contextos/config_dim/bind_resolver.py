"""配置引用 -> config_bindings(design §8.5 8 步 + §8.4 C+B + MEDIUM 2 FQN 校验)。
v1 聚焦: key 匹配(exact/hierarchical/annotation-prefix) + 绑定到 ref 的 AST class_fqn,
workspaceSymbol 按 relativePath 校验(同名类跨模块不瞎绑)。Step 4(mybatis)/7(ripgrep)留后续接缝。"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Protocol

from contextos.config_dim.extract import ConfigRef


@dataclass
class Binding:
    entity_id: str
    bind_type: str           # java_class / source_file
    bind_target: str         # class_fqn 或 source_path
    bind_strategy: str
    bind_direction: str = "read"
    confidence: str = "medium"
    evidence: str = ""


class SymbolSearcher(Protocol):
    def request_workspace_symbol(self, query: str) -> list: ...


def _build_entity_index(entities: list[dict]) -> tuple[dict, dict, list[str]]:
    """预建 entity 索引(只依赖 entities, resolve_bindings 循环外只算一次), 替代逐 ref 线性扫全 entity
    (profile 实测: 批量 insert 后 O(ref x entity) startswith 是 config 维瓶颈)。返回:
    - keys: entity_key -> entity, last-wins(保持原 Step1 exact 对同名 key 取最后一个的语义)。
    - first_by_key: entity_key -> entity, first-wins(保持原 ordered 全扫对同名 key 取首次出现的语义,
      hier/prefix 路径用它, 与原 most-specific-first first-match 完全等价)。
    - sorted_keys: 字典序排序的 entity_key 列表, 给后代前缀查做 bisect。"""
    keys: dict = {}
    first_by_key: dict = {}
    for e in entities:
        keys[e["entity_key"]] = e
        first_by_key.setdefault(e["entity_key"], e)
    return keys, first_by_key, sorted(first_by_key)


def _ancestors(key_norm: str, first_by_key: dict) -> list[dict]:
    """ek 是 key_norm 的点前缀祖先(key_norm.startswith(ek + '.'))的 entity。只查 key_norm 各级点
    前缀 -> O(深度), 不扫全 entity。"""
    parts = key_norm.split(".")
    out: list[dict] = []
    for i in range(len(parts) - 1, 0, -1):
        e = first_by_key.get(".".join(parts[:i]))
        if e is not None:
            out.append(e)
    return out


def _descendants(key_norm: str, first_by_key: dict, sorted_keys: list[str]) -> list[dict]:
    """ek 以 key_norm + '.' 为前缀(ek.startswith(key_norm + '.'))的 entity。bisect 定位前缀区间
    -> O(log E + 命中数), 不扫全 entity。"""
    prefix = key_norm + "."
    i = bisect.bisect_left(sorted_keys, prefix)
    out: list[dict] = []
    while i < len(sorted_keys) and sorted_keys[i].startswith(prefix):
        out.append(first_by_key[sorted_keys[i]])
        i += 1
    return out


def _most_specific(cands: list[dict]) -> dict:
    """候选里取最具体: 最长 entity_key 优先, 同长按字母 —— 等价原 ordered 全扫的 first-match。"""
    return min(cands, key=lambda e: (-len(e["entity_key"]), e["entity_key"]))


def _match_entity(ref: ConfigRef, keys: dict, first_by_key: dict,
                  sorted_keys: list[str]) -> tuple[dict | None, str]:
    """返 (entity, strategy)。1 exact / 2 hierarchical / 8 annotation prefix。
    索引化(#3b): 用 ancestors/descendants 索引查缩出小候选集再取最具体, 不再逐 ref 线性扫全 entity。
    候选集即满足谓词的全部 entity, 故与原'most-specific-first 全扫 first-match'产出完全一致。"""
    # Step 1 exact
    if ref.key_norm in keys:
        return keys[ref.key_norm], "exact_match"
    desc = _descendants(ref.key_norm, first_by_key, sorted_keys)   # ek.startswith(key + '.')
    # Step 8 annotation prefix(C+B): 注解型先走前缀反向匹配(ek == key 已被 exact 处理, 仅余 desc)。
    if ref.ref_type in ("annotation", "annotation_prefix") and desc:
        return _most_specific(desc), "annotation_prefix_match"
    # Step 2 hierarchical: ek 是 ref.key 的祖先 或 后代。
    cands = desc + _ancestors(ref.key_norm, first_by_key)
    if cands:
        return _most_specific(cands), "hierarchical_match"
    return None, ""


def _verify_fqn_by_path(class_fqn: str, source_path: str, searcher: SymbolSearcher | None):
    """MEDIUM 2: AST 给的 class_fqn 用 workspaceSymbol 按 relativePath 校验。
    返 (bind_type, bind_target, confidence)。"""
    if not class_fqn:
        return "source_file", source_path, "needs_review"
    cls = class_fqn.rsplit(".", 1)[-1]
    if searcher is None:
        return "java_class", class_fqn, "medium"  # 无 JDT, AST FQN 直接用(离线)
    hits = searcher.request_workspace_symbol(cls) or []
    paths = [(h.get("location") or {}).get("relativePath") or (h.get("location") or {}).get("uri") or "" for h in hits]
    if any(p.endswith(source_path) or source_path.endswith(p) for p in paths if p):
        return "java_class", class_fqn, "high"          # 路径一致 -> 高置信
    if len(hits) <= 1 and hits:
        return "java_class", class_fqn, "medium"         # 单命中, AST FQN 采信
    return "source_file", source_path, "needs_review"    # 多命中无路径一致 -> 降级, 不瞎绑


def resolve_bindings(refs: list[ConfigRef], entities: list[dict],
                     searcher: SymbolSearcher | None = None) -> list[Binding]:
    keys, first_by_key, sorted_keys = _build_entity_index(entities)   # 索引: 循环外只建一次
    out: list[Binding] = []
    for ref in refs:
        entity, strategy = _match_entity(ref, keys, first_by_key, sorted_keys)
        if entity is None:
            continue  # Step 7 ripgrep fallback / Step 5 LLM 兜底留后续
        bind_type, bind_target, conf = _verify_fqn_by_path(ref.class_fqn, ref.source_path, searcher)
        out.append(Binding(
            entity_id=entity["entity_id"], bind_type=bind_type, bind_target=bind_target,
            bind_strategy=strategy, confidence=conf,
            evidence=f"{ref.ref_type}@{ref.source_path}:{ref.line}",
        ))
    return out
