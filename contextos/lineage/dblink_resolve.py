"""dblink -> target-instance index (Block 1b, 05 §8.4).

Resolution priority (highest first):
  1. profile.oracle.dblink_map manual override (explicit mapping wins)
  2. ALL_DB_LINKS.HOST TNS descriptor resolved via tns_parser against allowed instances
  3. Unresolved -> registered in the unresolved list with reason=no_matching_instance

Cross-database table references `table@DBLINK` and cross-db object dependencies
use this index to produce src_db != dst_db lineage edges.
"""
from __future__ import annotations

from typing import Any

from contextos.db_provider import tns_parser
from contextos.lineage import store


def build_dblink_index(
    dblinks: list[dict[str, Any]],
    instance_descriptors: dict[str, tuple[str, int, str]],
    tns_entries: dict[str, dict],
    dblink_map: dict[str, str],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Build {DBLINK_NAME: target_TNS} index from raw dblink rows.

    Resolution priority (highest first):
      1. dblink_map override (manual config, highest precedence)
      2. Resolve HOST via tns_parser descriptor matching against instance_descriptors
      3. Unresolved -> register in returned unresolved list with reason=no_matching_instance

    Also registers the base name (strip .DOMAIN suffix) via setdefault so that
    SQL references using just BILLING instead of BILLING.WORLD still resolve.
    setdefault means the base name never overwrites the full name if they differ.

    Args:
        dblinks: list of dblink row dicts (keys: db_link, host, db_name, ...)
        instance_descriptors: {TNS_UPPER: (host_lower, port, service_or_sid_lower)}
            produced by tns_parser.instance_descriptors()
        tns_entries: raw {TNS_UPPER: descriptor_dict} from tns_parser.parse_tnsnames_text()
            needed to resolve HOST TNS-alias form
        dblink_map: {dblink_name: target_TNS} manual override from profile.oracle.dblink_map

    Returns:
        (index, unresolved) where index is {DBLINK_NAME_UPPER: target_TNS} and
        unresolved is a list of dicts with keys db_link/host/reason/db_name.
    """
    # Build reverse map: descriptor -> TNS name
    desc_to_tns: dict[tuple[str, int, str], str] = {
        desc: tns for tns, desc in instance_descriptors.items()
    }
    # Normalise override keys to upper-case for case-insensitive lookup
    map_upper: dict[str, str] = {k.upper(): v for k, v in dblink_map.items()}

    index: dict[str, str] = {}
    unresolved: list[dict[str, Any]] = []

    for row in dblinks:
        name = (row.get("db_link") or "").upper()
        if not name:
            continue

        # Priority 1: manual dblink_map override (full name or base name)
        # 显式 key-in 检查而非 or-falsy, 防空字符串值被误判为未命中。
        base_name = name.split(".", 1)[0]
        if name in map_upper:
            target: str | None = map_upper[name]
        elif base_name in map_upper:
            target = map_upper[base_name]
        else:
            target = None

        # Priority 2: resolve HOST descriptor via tns_parser
        if not target:
            desc = tns_parser.resolve_host_descriptor(
                row.get("host") or "", tns_entries
            )
            target = desc_to_tns.get(desc) if desc else None

        if target:
            index[name] = target
            # Register base name (no .DOMAIN) as fallback; setdefault preserves
            # full name entry if base name was already set by a prior row.
            index.setdefault(base_name, target)
        else:
            unresolved.append(
                dict(
                    db_link=name,
                    host=row.get("host") or "",
                    reason="no_matching_instance",
                    db_name=row.get("db_name") or "",
                )
            )

    return index, unresolved


def build_index_from_store(
    engine: Any, oracle_cfg: Any
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Build the dblink index from store.all_dblinks + profile.oracle settings.

    Reads the persisted dblink rows from the lineage store, resolves instance
    descriptors from tnsnames.ora (tns_admin/allowed_instances), and applies
    any dblink_map manual overrides from the profile.

    Args:
        engine: SQLAlchemy engine (lineage store)
        oracle_cfg: profile.oracle object with attributes:
            .tns_admin (str), .allowed_instances (list[str]), .dblink_map (dict[str,str])

    Returns:
        Same as build_dblink_index: (index, unresolved).
    """
    from pathlib import Path

    inst = tns_parser.instance_descriptors(
        oracle_cfg.tns_admin, list(oracle_cfg.allowed_instances)
    )
    tns_entries = tns_parser.parse_tnsnames(
        str(Path(oracle_cfg.tns_admin).expanduser() / "tnsnames.ora")
    )
    return build_dblink_index(
        store.all_dblinks(engine),
        inst,
        tns_entries,
        dict(oracle_cfg.dblink_map),
    )
