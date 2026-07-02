"""Layer 7: 名称归一化(Profile + 存储层元数据, 离线降级)。

对比 LP name_resolve.py:
  - 元数据从 store.py SQLAlchemy 表读(非裸 sqlite3)
  - 12 层某电信客户硬编码模板正则 -> profile.tables(shard_strategy/monthly_pattern/typo_map)
  - exclude_schemas 命中 -> 返回空模板(调用方跳过)
  - 离线降级: 元数据空时只做 Profile 归一, db/owner 留空
"""
from __future__ import annotations

import re

from sqlalchemy.engine import Engine

from contextos.lineage import store
from contextos.profile.schema import TablesConfig


class NameResolver:
    def __init__(self, engine: Engine, tables_cfg: TablesConfig,
                 dblink_index: dict[str, str] | None = None):
        self.cfg = tables_cfg
        self._exclude = {s.upper() for s in tables_cfg.exclude_schemas}
        self._typo = {k.upper(): v.upper() for k, v in tables_cfg.typo_map.items()}
        self._monthly = re.compile(tables_cfg.monthly_pattern) if tables_cfg.monthly_pattern else None
        self._shard = None
        if tables_cfg.shard_strategy and tables_cfg.shard_strategy.type == "regex":
            self._shard = re.compile(tables_cfg.shard_strategy.pattern)
        # dblink 索引(Block 1b): None = 老行为(剥 @ 不解析); {} 或 {...} = 解析模式。
        self._dblink_index: dict[str, str] | None = (
            {k.upper(): v for k, v in dblink_index.items()} if dblink_index is not None else None
        )
        self.unresolved_dblinks: set[str] = set()
        # 元数据索引(空=离线降级)。裁决 5: 同名表可跨 owner -> 裸名映射到多条 (db,owner,dtype)。
        self._table_index: dict[str, list[tuple[str, str, str]]] = {}  # template -> [(db,owner,dtype),...]
        self._synonym_index: dict[str, tuple[str, str, str, str]] = {}  # syn -> (db,owner,table,dblink)
        self._fk_index: set[tuple[str, str]] = set()
        self._load_metadata(engine)

    def _load_metadata(self, engine: Engine) -> None:
        for row in store.all_table_metadata(engine):
            tpl = (row["template_name"] or "").upper()
            self._table_index.setdefault(tpl, []).append(
                (row["db_name"] or "", row["owner"] or "", (row["dataset_type"] or "TABLE")))
        for row in store.all_synonyms(engine):
            self._synonym_index[(row["synonym_name"] or "").upper()] = (
                row["db_name"] or "", row["table_owner"] or "",
                (row["table_name"] or "").upper(), row["db_link"] or "")
        for row in store.all_fks(engine):
            a, b = (row["table_a"] or "").upper(), (row["table_b"] or "").upper()
            self._fk_index.add((a, b))
            self._fk_index.add((b, a))

    @property
    def has_metadata(self) -> bool:
        return bool(self._table_index)

    def normalize_template(self, table_name: str) -> str:
        """物理表名 -> 模板名: typo 修正 -> shard 归并 -> 月表归并。"""
        name = table_name.strip().upper()
        name = self._typo.get(name, name)
        if self._shard:
            name = self._shard.sub("", name)
        if self._monthly:
            name = self._monthly.sub("", name)
        return name

    def resolve_table(self, raw_name: str, schema: str = "",
                      source_module: str = "") -> tuple[str, str, str, str]:
        """归一化 -> (db, owner, template_name, dataset_type)。

        exclude_schemas 命中 -> ('', '', '', 'TABLE')(空 template 调用方跳过)。
        裁决 5: 裸名匹配多 owner 时 owner/db 留空(歧义不乱猜, 留 Plan 06 datasource 回填);
        schema 提示命中对应 owner; 单 owner 直接取。

        Block 1b dblink 行为:
          dblink_index=None(默认) -> 老行为: 剥 @ 后缀, db/resolved_db 不做 dblink 覆盖。
          dblink_index 已设(包括空 {}) -> 解析模式: 查 dblink_index 定目标库;
            解析不出则加入 self.unresolved_dblinks, db 留上层 metadata 结果。
        """
        name = raw_name.strip().upper()
        dblink = ""
        if "@" in name:
            name, dblink = name.split("@", 1)
            name, dblink = name.strip(), dblink.strip()
        if schema and schema.upper() in self._exclude:
            return ("", "", "", "TABLE")

        # synonym 展开(仅在线)
        if name in self._synonym_index:
            _db, _own, target_name, _dblink = self._synonym_index[name]
            if target_name:
                name = target_name

        template = self.normalize_template(name)
        # 原始名优先匹配元数据(在线时)
        if name in self._table_index:
            template = name

        entries = self._table_index.get(template, [])
        resolved_db, resolved_owner, dataset_type = self._pick_entry(entries, schema)

        if dblink and self._dblink_index is not None:
            # Block 1b 解析模式: 用 dblink_index 定目标库覆盖 resolved_db
            # 显式 key-in 检查而非 or-falsy, 防空字符串值被误判为未命中。
            base = dblink.split(".", 1)[0]
            key = dblink if dblink in self._dblink_index else (
                base if base in self._dblink_index else None
            )
            target: str | None = self._dblink_index[key] if key is not None else None
            if target:
                resolved_db = target
            else:
                self.unresolved_dblinks.add(dblink)   # 登记待持久化

        return (resolved_db, resolved_owner, template, dataset_type)

    @staticmethod
    def _pick_entry(entries: list[tuple[str, str, str]],
                    schema: str) -> tuple[str, str, str]:
        """挑 (db, owner, dataset_type)。

        显式 schema 权威(review 三轮 HIGH): 命中 metadata -> 用该 entry 富化;
        未命中 -> owner=schema, db=''(信 SQL, **绝不**借别的 owner 的唯一行, 否则错标身份)。
        无 schema: 唯一 owner -> 用之; 多 owner -> 歧义留空; 无 -> 空。"""
        if schema:
            for db, owner, dt in entries:
                if owner.upper() == schema.upper():
                    return (db, owner, dt)
            return ("", schema, "TABLE")     # schema 未命中 -> 信 SQL, 绝不借别的 owner
        if not entries:
            return ("", "", "TABLE")
        if len(entries) == 1:
            return entries[0]
        # 多 owner 歧义: owner/db 留空; dataset_type 仅当全 VIEW 才 VIEW
        dts = {e[2] for e in entries}
        return ("", "", "VIEW" if dts == {"VIEW"} else "TABLE")

    def table_exists(self, template_name: str) -> bool:
        return template_name.upper() in self._table_index

    def fk_pair(self, a: str, b: str) -> bool:
        return (a.upper(), b.upper()) in self._fk_index
