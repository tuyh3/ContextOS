"""Phase A build: 文件配置 -> config_sources/snapshots/entities/items/bindings(离线)。

CI-clean 地基,不碰 Oracle/RAG。链路:
  _iter_config_files(扩展名 + exclude_paths + .json 黑名单)
  -> parser_for(按扩展名)解析成 ParsedConfig
  -> 落 config_sources/config_snapshots/config_entities/config_items(敏感值经
     sensitive.sanitize_item_value 掩码 + HMAC fingerprint = HIGH 1 chokepoint)
  -> 全仓 .java 走 extract.extract_config_refs(AST FQN 主锚, MEDIUM 2)
  -> bind_resolver.resolve_bindings -> config_bindings。

注: parser @register 注册表靠 import 各 parser 模块触发的 side-effect 填充。本模块
顶部显式 import 五个 parser 模块,确保 parser_for 在 parsers/__init__.py 合并前也能
返回非空(properties/yaml/json/xml dispatcher)。
"""
from __future__ import annotations

import fnmatch
import logging
import os
import re
import shutil
from pathlib import Path

from sqlalchemy import delete as _delete
from sqlalchemy import insert as _insert
from sqlalchemy import select as _sel
from sqlalchemy.engine import Engine

# 兼容历史 build_file_config 用裸 insert(下方仍引用 insert 名)
insert = _insert

from contextos.config_dim import identify as ID
from contextos.config_dim import confirm as CF
from contextos.config_dim import schema as S
from contextos.config_dim import sensitive as SENS
from contextos.config_dim import db_snapshot as _SNAP
from contextos.config_dim.bind_resolver import resolve_bindings
from contextos.config_dim.extract import config_marker_terms, extract_config_refs
from contextos.config_dim.parsers.base import parser_for
from contextos.config_dim.parsers.json_parser import is_blacklisted
from contextos.util.subproc_text import decode_diagnostic, run_rg  # noqa: E402
# 05 SSOT 标识符闸门(防注入, 红线#4): owner/table/key_col 进 SQL 文本前再过 identifier 校验
from contextos.lineage.oracle_metadata import _validate_owner as _ora_ident

# --- 触发 @register side-effect 填充 parser 注册表(parsers/__init__.py 由主控制器
#     统一合并;本模块不依赖它,显式 import 各 parser 模块保证注册表非空)---
from contextos.config_dim.parsers import properties_parser as _properties_parser  # noqa: F401
from contextos.config_dim.parsers import yaml_parser as _yaml_parser  # noqa: F401
from contextos.config_dim.parsers import json_parser as _json_parser  # noqa: F401
from contextos.config_dim.parsers import xml_mybatis_parser as _xml_parser  # noqa: F401


def _in_include_paths(rel: str, include_paths: list[str]) -> bool:
    """include_paths 非空则圈定扫描范围(空=全仓)。每项当目录前缀(pak-ccp -> pak-ccp/**)
    或 fnmatch glob 处理, 与 source_roots 语义一致。此前该字段声明了却无人消费(死字段),
    大仓 config 扫描无法圈定 -> 吃 bomc-pak/toptea-web 巨型 geojson 等噪音(pak-bomc 实测)。"""
    if not include_paths:
        return True
    for pat in include_paths:
        pfx = pat.rstrip("/")
        if rel == pfx or rel.startswith(pfx + "/") or fnmatch.fnmatch(rel, pat):
            return True
    return False


def _iter_config_files(repo: Path, fcfg) -> list[Path]:
    out: list[Path] = []
    for p in repo.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in fcfg.include_extensions:
            continue
        rel = p.relative_to(repo).as_posix()
        if not _in_include_paths(rel, fcfg.include_paths):
            continue
        if any(fnmatch.fnmatch(rel, pat) for pat in fcfg.exclude_paths):
            continue
        if p.suffix.lower() == ".json" and is_blacklisted(rel, fcfg.json_blacklist):
            continue
        out.append(p)
    return out


_log = logging.getLogger(__name__)


def _java_files_with_config_markers(repo: Path, framework_annotations: list[str] | None) -> list[Path]:
    """ripgrep 预筛: 只返回含 extract_config_refs 关心标记(注解名 ∪ 配置方法名)的 .java,
    免去对全仓每个 java 都做一次 Python AST 解析(大仓性能瓶颈 C)。

    健全性(关键): 匹配模式从 extract.config_marker_terms 派生(与 extract 同源, 信号变了自动跟上),
    且故意宽松——命中集是"extract 会抽到引用的文件"的超集, 绝不漏(漏了就少建配置绑定)。
    `--no-ignore --hidden` 让 rg 文件域 = rglob 全量(不被 .gitignore 漏); exclude_paths 仍由
    调用方在 Python 侧过滤, 语义不变。rg 不可用 / 出错 -> 回退 rglob 全扫(宁可慢不可漏)。
    """
    if shutil.which("rg") is None:
        return list(repo.rglob("*.java"))
    annos, methods = config_marker_terms(framework_annotations)
    # 注解: @Name 与全限定 .Name 都要命中(故 [@.] 前缀); 方法: 裸方法名(宽松=超集, 不漏)
    patterns = [rf"[@.]{re.escape(a)}\b" for a in sorted(annos)] + [re.escape(m) for m in sorted(methods)]
    args = ["-l", "--null", "--no-ignore", "--hidden", "--type", "java"]
    for p in patterns:
        args += ["-e", p]
    args.append(str(repo))
    try:
        proc = run_rg(args)
    except OSError as exc:                     # rg 启动失败 -> 回退全扫
        _log.warning("config java 预筛 rg 启动失败(%s), 回退全扫", exc)
        return list(repo.rglob("*.java"))
    if proc.returncode == 1:                   # rg: 1 = 无命中(正常)
        return []
    if proc.returncode != 0:                   # 其它 = 真错误 -> 回退全扫, 不漏
        _log.warning("config java 预筛 rg 出错(exit %s): %s; 回退全扫",
                     proc.returncode, decode_diagnostic(proc.stderr).strip())
        return list(repo.rglob("*.java"))
    return [Path(os.fsdecode(b)) for b in proc.stdout.split(b"\0") if b]


def build_file_config(repo_root, profile, engine: Engine, cache_dir) -> dict:
    repo = Path(repo_root)
    fcfg = profile.config.file_sources
    spats = profile.config.sensitive_key_patterns
    salt = SENS.load_or_create_salt(Path(cache_dir))
    stats = {"sources": 0, "entities": 0, "items": 0, "bindings": 0}

    all_entities: list[dict] = []
    src_rows: list[dict] = []
    snap_rows: list[dict] = []
    ent_rows: list[dict] = []
    item_rows: list[dict] = []
    bind_rows: list[dict] = []
    with engine.begin() as conn:
        # 重建前清空自动抽取的 config 数据表(幂等重跑): 否则第二次 build 同文件 generate_id 产同
        # source_id/item_id -> 撞 PK / uq_item 唯一约束 IntegrityError。只清自动数据(Phase A+B+C
        # 全量重建表), 保人工/历史权威表 config_confirmation(human_confirmed 权威)/ owner_resolution
        # (owner overlay)/ config_changes(变更历史)。无 FK 声明, 删除顺序无关。build_file_config 是
        # build_config_dimension 首步, 这一清覆盖整轮重建(对齐 lineage store.clear_all 的幂等语义)。
        for _t in (S.config_evidence, S.config_bindings, S.config_items, S.config_snapshots,
                   S.config_entities, S.config_sources, S.rule_bindings, S.rule_clauses,
                   S.rule_sets):
            conn.execute(_delete(_t))
        for f in _iter_config_files(repo, fcfg):
            parse = parser_for(f.name)
            if parse is None:
                continue
            rel = f.relative_to(repo).as_posix()
            pc = parse(rel, f.read_text(encoding="utf-8", errors="ignore"))
            if not pc.items and not pc.class_refs and not pc.sql_refs:
                continue
            source_id = S.generate_id("src", rel)
            snap_id = S.generate_id("snap", source_id, "current")
            src_rows.append(dict(
                source_id=source_id, source_type="file", file_path=rel,
                file_type=pc.file_type, framework=pc.framework))
            snap_rows.append(dict(
                snapshot_id=snap_id, source_id=source_id, env="", is_current=1))
            stats["sources"] += 1
            seen_ent: dict[str, str] = {}
            # 同文件内重复 key_path 去重, 保留最后一个(properties/yaml 语义: 后者覆盖前者);
            # 否则 item_id=generate_id("item", source_id, key_path, snap_id) 重复 -> 撞 config_items
            # PK + uq_item(source_id,key_path,snapshot_id) IntegrityError。
            deduped_items = list({it.key_path: it for it in pc.items}.values())
            for it in deduped_items:
                eid = seen_ent.get(it.entity_key)
                if eid is None:
                    eid = S.generate_id("ent", source_id, it.entity_key)
                    ent_rows.append(dict(
                        entity_id=eid, source_id=source_id, entity_key=it.entity_key,
                        entity_type="file_key"))
                    seen_ent[it.entity_key] = eid
                    all_entities.append({"entity_id": eid, "entity_key": it.entity_key})
                    stats["entities"] += 1
                vr, sens, fp = SENS.sanitize_item_value(it.config_key, it.value_raw, spats, salt)
                item_rows.append(dict(
                    item_id=S.generate_id("item", source_id, it.key_path, snap_id),
                    source_id=source_id, entity_id=eid, snapshot_id=snap_id,
                    config_key=it.config_key, key_path=it.key_path, value_raw=vr,
                    value_type=it.value_type, is_sensitive=sens, value_fingerprint=fp))
                stats["items"] += 1

        # Java 源码 -> 配置引用 -> 绑定。ripgrep 预筛只解析含配置标记的 java(大仓避免全量 AST, C)
        refs = []
        for j in _java_files_with_config_markers(repo, profile.config.framework_annotations):
            rel = j.relative_to(repo).as_posix()
            if any(fnmatch.fnmatch(rel, pat) for pat in fcfg.exclude_paths):
                continue
            refs.extend(extract_config_refs(
                rel, j.read_text(encoding="utf-8", errors="ignore"),
                framework_annotations=profile.config.framework_annotations))
        seen_bind: set[str] = set()
        for b in resolve_bindings(refs, all_entities, searcher=None):
            bid = S.generate_id("bind", b.entity_id, b.bind_type, b.bind_target)
            if bid in seen_bind:   # 同(entity, bind_type, target)被多个 ref 命中 -> binding_id 相同, 去重防撞 PK
                continue
            seen_bind.add(bid)
            bind_rows.append(dict(
                binding_id=bid,
                entity_id=b.entity_id, bind_type=b.bind_type, bind_target=b.bind_target,
                bind_direction=b.bind_direction, bind_strategy=b.bind_strategy,
                confidence=b.confidence, evidence=b.evidence))
            stats["bindings"] += 1

        # 批量 insert(executemany): 一张表一条语句多组参数, 干掉逐行 SQLAlchemy 语句构建开销
        # (profile 实测 = config 维主瓶颈)。依赖顺序 sources -> snapshots -> entities -> items -> bindings。
        for _tbl, _rows in ((S.config_sources, src_rows), (S.config_snapshots, snap_rows),
                            (S.config_entities, ent_rows), (S.config_items, item_rows),
                            (S.config_bindings, bind_rows)):
            if _rows:
                conn.execute(insert(_tbl), _rows)
    return stats


def _dskey_from_url(url: str) -> str:
    """从**明文** jdbc url 提 datasource_key(实例/服务名末段)。'.../@host/ds1' -> 'ds1'。
    仅对未打码 url 调用; masked url(内嵌凭据被整值打码)由 build_datasource_map 先滤掉。"""
    u = (url or "").strip()
    if not u:
        return ""
    tail = u.replace("\\", "/").rstrip("/").split("/")[-1]   # .../crmdev1 -> crmdev1
    return tail.split("@")[-1] if "@" in tail else tail


def build_datasource_map(engine_06: Engine) -> dict:
    """从 config_items 的 jdbc.username/url + source.module 推 module -> {user, datasource_key}。
    离线解析 Phase A 落的 jdbc 配置项。无 -> {}。

    MED 1(多 datasource 防串): 一 module 收到 **冲突**(>1 个不同非空)username 或 url ->
    标 ambiguous **跳过该 module**(不强压成单条, 免错身份回填 owner)。user 取 jdbc.username
    (Phase A 不 mask username); url 仅用于提 datasource_key。
    """
    by_mod: dict[str, dict[str, set]] = {}
    with engine_06.connect() as c:
        srcs = {s.source_id: s for s in c.execute(_sel(S.config_sources)).fetchall()}
        items = c.execute(_sel(S.config_items)).fetchall()
    for it in items:
        key = (it.config_key or "").lower()
        if not (key.endswith("username") or key.endswith("url")):
            continue
        if not (it.value_raw or "").strip():
            continue
        s = srcs.get(it.source_id)
        module = (getattr(s, "module", "") or (s.file_path or "").split("/")[0]) if s else ""
        if not module:
            continue
        slot = by_mod.setdefault(module, {"user": set(), "dskey": set()})
        if key.endswith("username"):
            slot["user"].add(it.value_raw)
        else:  # url: 仅明文(未打码)url 提 dskey; masked(内嵌凭据被整值打码)-> skip(不提 ****xxxx 垃圾)
            if not getattr(it, "is_sensitive", 0) and not (it.value_raw or "").startswith("****"):
                slot["dskey"].add(_dskey_from_url(it.value_raw))
    out: dict[str, dict] = {}
    for module, slot in by_mod.items():
        users = {u for u in slot["user"] if u}
        dskeys = {d for d in slot["dskey"] if d}
        if len(users) > 1 or len(dskeys) > 1:
            continue  # ambiguous -> 跳过(免多 datasource 错身份回填; 06c-integration 再细分)
        out[module] = {"user": next(iter(users), ""), "datasource_key": next(iter(dskeys), "")}
    return out


def build_config_dimension(repo_root, profile, engine: Engine, cache_dir, *,
                           oracle_tables=None, execute_query=None, rag_search=None,
                           db: str = "", customer_id: str = "default",
                           synonym_lookup=None,
                           engine_05: Engine | None = None) -> dict:
    """全 build 编排: Phase A 文件 + Phase B DB 四路识别 + Phase C 确认覆盖 + (engine_05 给则)Trip2 回写。

    Phase A: build_file_config 扫配置文件 -> config_sources(source_type='file')/snapshots/
      entities/items/bindings(CI-clean, 不碰 Oracle/RAG)。
    Phase B: 对注入的 oracle_tables 清单跑四路识别(path A 表名启发 / path B DDL 表注释, 读
      随表清单注入的 t["comment"](源自 store table_metadata.comment, 方言无关, D.5) /
      path C RAG 业务文档 + path D 客户字典走 03b sparse rag_search)-> fuse 融合 -> 候选(§5.5)。
    Phase C: apply_confirmations 权威覆盖(human_confirmed > 自动 > 启发); high/confirmed 写
      config_sources(source_type='db_table'), needs_review 写但标记(design §5.5), skip 不写。
    Trip 2: engine_05 给且有识别出的 config_table -> writeback_config_tables 回填 05
      lineage_edges.dst_dataset_type(非阻塞, 见 design §2 盲区2 + 构建契约 §3)。

    execute_query(D.5 后**仅** W7 行快照用, Oracle-only 休眠)/ rag_search 注入: 离线 fake 测主链;
      真跑 wire 05 §8.2 白名单闸门 + corpus_scope.scoped_hits(红线#4/#2)。oracle_tables(含
      owner/table/columns/comment)来自 _oracle_tables_from_store 从 05 store 派生。
    known-limitation(review LOW): apply_confirmations 只覆盖本次 oracle_tables 产出的候选; 人工
      confirm 过但本次不在 oracle_tables 的离线配置表不会被重新 surface(确认仍持久, 下次该表进
      oracle_tables 时生效)。完整 "confirmed 表无条件 surface" 留后续。
    """
    stats = build_file_config(repo_root, profile, engine, cache_dir)   # Phase A
    stats.setdefault("config_tables", 0)
    stats.setdefault("config_tables_needs_review", 0)

    det = profile.config_tables.detection
    name_pats = ID.load_default_name_patterns() + det.name_patterns
    rule_cols = set(det.rule_columns)
    spats = profile.config.sensitive_key_patterns  # W5: evidence excerpt redact(敏感值脱敏)
    salt = SENS.load_or_create_salt(Path(cache_dir))  # W7: snapshot 敏感行 HMAC fingerprint

    # Phase B: 对 oracle 表清单跑四路 -> 融合
    candidates: list[dict] = []
    for t in (oracle_tables or []):
        owner, table = t["owner"], t["table"]
        a, _ = ID.path_a_score(table, t.get("columns", []), name_pats, rule_cols)
        # W5: 留住命中 dict(供 config_evidence 落库, excerpt 过 sanitize_text)。
        # path B(D.5 去 live SQL 化): 表注释来自 store table_metadata.comment(由
        # _oracle_tables_from_store 随表清单注入 t["comment"], 方言无关), 不再 execute_query。
        b_hit = ID.path_b_from_comment(t.get("comment", ""),
                                       det.comment_keywords_zh, det.comment_keywords_en)
        c_hit = ID.path_c_query(
            table, rag_search, det.comment_keywords_zh, det.comment_keywords_en) if rag_search else None
        d_hit = ID.path_d_query(
            table, rag_search, det.comment_keywords_zh, det.comment_keywords_en) if rag_search else None
        b = 1.0 if b_hit else 0.0
        c = 1.0 if c_hit else 0.0
        d = 1.0 if d_hit else 0.0
        v = ID.fuse_config_table(a, b, c, d)
        candidates.append({"ref_type": "config_table", "ref_key": f"{owner}.{table}",
                           "owner": owner, "table": table,
                           "_hits": [(b_hit, "rag_ddl_comment"), (c_hit, "rag_business_doc"),
                                     (d_hit, "rag_dict")],
                           **v})

    # Phase C: human_confirmed 权威覆盖(confirm -> verdict='confirmed'; reject -> 排除)
    candidates = CF.apply_confirmations(engine, customer_id, candidates)
    config_table_names: set[str] = set()

    # W5: 命中证据落 config_evidence(excerpt 必过 sanitize_text, 敏感值脱敏)。MED 2: high/confirmed/
    # needs_review 都写(needs_review 是 human_confirmed loop 最需证据的档); skip 不写。
    def _write_evidence(conn, cand) -> None:
        for hit, etype in cand.get("_hits", []):
            if not hit:
                continue
            conn.execute(_insert(S.config_evidence).values(
                evidence_id=S.generate_id("ev", cand["ref_key"], etype),
                ref_type="config_table", ref_id=cand["ref_key"], evidence_type=etype,
                evidence_ref=hit.get("evidence_ref", ""),
                excerpt=SENS.sanitize_text(hit.get("excerpt", ""), spats)))

    with engine.begin() as conn:
        for cand in candidates:
            verdict = cand.get("verdict")
            if verdict in ("high", "confirmed"):
                source_id = S.generate_id("src", cand["ref_key"])
                conn.execute(insert(S.config_sources).values(
                    source_id=source_id, source_type="db_table",
                    owner=cand["owner"], table_name=cand["table"], db_name=db,
                    description=f"config_table:{verdict}"))
                config_table_names.add(cand["table"])
                stats["config_tables"] += 1
                _write_evidence(conn, cand)
                # W7: high/confirmed config_table -> 拉 DB 行快照(小表 SELECT * 全量按列掩码;
                # 大表 GROUP BY 拆条)。取数走注入的 execute_query(05 §8.2 白名单 + ROWNUM + timeout,
                # 红线#4, 不直连 oracle); db 是连接/实例选择器(execute_query 首参), 绝不进 SQL 表名。
                if execute_query:
                    t_meta = next((t for t in (oracle_tables or [])
                                   if t["owner"] == cand["owner"] and t["table"] == cand["table"]), {})
                    pk_cols = t_meta.get("pk_cols", [])
                    rc = t_meta.get("row_count", 0)
                    thr = profile.config_tables.big_table_row_threshold
                    # 非法标识符 -> ValueError(纵深防御, bind params 之外再加 identifier 闸门)
                    ow = _ora_ident(cand["owner"])
                    tbl = _ora_ident(cand["table"])
                    snap_id = S.generate_id("snap", source_id, "current")
                    conn.execute(_insert(S.config_snapshots).values(
                        snapshot_id=snap_id, source_id=source_id, env="", is_current=1))
                    if rc <= thr:
                        rows = execute_query(db, f"SELECT * FROM {ow}.{tbl}") or []
                        snap_items = _SNAP.snapshot_small(rows, pk_cols, db, ow, tbl, spats, salt)
                    else:
                        key_col = _ora_ident(pk_cols[0]) if pk_cols else "ROWID"
                        groups = execute_query(
                            db, f"SELECT {key_col}, COUNT(*) CNT FROM {ow}.{tbl} GROUP BY {key_col}") or []
                        snap_items = _SNAP.snapshot_big(groups, key_col, db, ow, tbl)
                    for it in snap_items:
                        conn.execute(_insert(S.config_items).values(
                            item_id=S.generate_id("item", source_id, it["key_path"], snap_id),
                            source_id=source_id, entity_id="", snapshot_id=snap_id,
                            config_key=it["config_key"], key_path=it["key_path"],
                            value_raw=it["value_raw"], value_type=it["value_type"],
                            is_sensitive=it.get("is_sensitive", 0),
                            value_fingerprint=it.get("value_fingerprint", "")))
            elif verdict == "needs_review":
                # design §5.5: medium(0.3<=score<0.6)写但标 needs_review(不进 Trip2 回写集)
                conn.execute(insert(S.config_sources).values(
                    source_id=S.generate_id("src", cand["ref_key"]), source_type="db_table",
                    owner=cand["owner"], table_name=cand["table"], db_name=db,
                    description="config_table:needs_review"))
                stats["config_tables_needs_review"] += 1
                _write_evidence(conn, cand)  # MED 2: needs_review 也写证据
            # skip(<0.3)不写

        # W1: rule_sets Scope A —— 对每个 oracle_table 判表级规则集(>=2 规则列)。
        # 规则表 source_id = 同表 config_sources(db_table)id(该表也是 config_table 时自然链上;
        # 纯规则表为软引用, 06 schema 字符串 id 不强约束 FK)。
        rcat = getattr(det, "rule_category_map", {}) or {}
        for t in (oracle_tables or []):
            rs = ID.identify_rule_set(t["table"], t.get("columns", []), rule_cols,
                                      category_map=rcat)
            if rs is None:
                continue
            rsid = S.generate_id("ruleset", db, t["owner"], t["table"])
            rs_src = S.generate_id("src", f"{t['owner']}.{t['table']}")
            conn.execute(_insert(S.rule_sets).values(
                rule_set_id=rsid, name=rs["name"], source_id=rs_src,
                category=rs["category"], status=rs["status"]))
            for rb in ID.rule_bindings_for(rsid, t["table"], engine_05):
                conn.execute(_insert(S.rule_bindings).values(
                    binding_id=S.generate_id("rb", rsid, rb["bind_target"]),
                    rule_set_id=rsid, bind_type=rb["bind_type"],
                    bind_target=rb["bind_target"], bind_role=rb["bind_role"]))
            stats["rule_sets"] = stats.get("rule_sets", 0) + 1

    # Trip 2 回写(engine_05 给且有识别出的 config_table 则回填 05 lineage_edges)
    if engine_05 is not None and config_table_names:
        from contextos.config_dim.writeback import writeback_config_tables
        stats["writeback"] = writeback_config_tables(engine_05, config_table_names)

    # W7: owner overlay 回填(engine_05 给时)。从 Phase A 落的 jdbc 配置推 module->datasource 身份,
    # 对 05 裸名边写 owner_resolution(不改 05; resolve_side 走注入 synonym_lookup, 离线 None 落 direct)。
    if engine_05 is not None:
        from contextos.config_dim.owner_backfill import backfill_owners
        dmap = build_datasource_map(engine)
        # 总是记 owner_backfilled(dmap 空 -> 0): 否则 key 缺失会让"接线静默没干活"看不出来
        # (Plan 06 栽过的 silent wiring gap)。dmap 空多因 Phase A 没抓到 jdbc 配置项。
        stats["owner_backfilled"] = (
            backfill_owners(engine_05, dmap, synonym_lookup, engine) if dmap else 0)
    return stats
