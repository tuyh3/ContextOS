"""tnsnames.ora parsing (ported from LP scripts/tns_parser.py pure-parsing subset).

Only retains what is needed for resolving dblink target instances:
  - parse_tnsnames_text: text -> {TNS_ALIAS_UPPER: descriptor_dict}
  - parse_tnsnames: file path -> same dict
  - _parse_block: raw TNS block -> {host, port, sid?/service_name?}
  - normalized_descriptor: entry -> (host_lower, port, service_or_sid_lower) comparison key
  - instance_descriptors: read tnsnames.ora + filter to allowed_instances
  - resolve_host_descriptor: dblink HOST string (TNS alias / inline DESCRIPTION / EZConnect)
    -> (host, port, service_or_sid) or None

No yaml / CLI / role-classification dependencies.
Pure functions, no side effects beyond file I/O in parse_tnsnames.
"""
from __future__ import annotations

import re
from pathlib import Path

# Matches the start of a TNS entry: "ALIAS_NAME =" followed (ignoring blank lines) by "(DESCRIPTION"
_TNS_PATTERN = re.compile(r"^\s*(\w+)\s*=\s*$\s*\(DESCRIPTION", re.MULTILINE | re.IGNORECASE)

# Matches an ADDRESS clause with TCP protocol
_ADDR_PATTERN = re.compile(
    r"\(ADDRESS\s*=\s*\(PROTOCOL\s*=\s*TCP\)\s*\(HOST\s*=\s*([\w.\-]+)\)\s*\(PORT\s*=\s*(\d+)\)\)",
    re.IGNORECASE,
)

# EZConnect: host[:port]/service
_EZCONNECT = re.compile(r"^([\w.\-]+)(?::(\d+))?/([\w.]+)$")


def parse_tnsnames_text(text: str) -> dict[str, dict]:
    """Parse a tnsnames.ora text blob -> {TNS_ALIAS_UPPER: descriptor_dict}.

    descriptor_dict contains: host (str), port (int), and either sid or
    service_name (or both if defined in the block).
    Entries that cannot be parsed (no ADDRESS or no service/sid) are omitted.
    Comments (lines starting with --) are stripped before parsing.
    """
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("--")]
    text = "\n".join(lines)
    matches = list(_TNS_PATTERN.finditer(text))
    entries: dict[str, dict] = {}
    for i, m in enumerate(matches):
        name = m.group(1).strip().upper()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        e = _parse_block(text[start:end])
        if e:
            entries[name] = e
    return entries


def parse_tnsnames(filepath: str) -> dict[str, dict]:
    """Read tnsnames.ora from disk and return parsed entries.

    Returns empty dict if the file does not exist.
    """
    p = Path(filepath).expanduser()
    if not p.exists():
        return {}
    return parse_tnsnames_text(p.read_text(encoding="utf-8", errors="replace"))


def _parse_block(block: str) -> dict | None:
    """Parse one TNS entry block -> descriptor dict, or None if unparseable.

    Returns None if no ADDRESS with TCP/HOST/PORT is found, or if neither
    SID nor SERVICE_NAME is present.
    """
    addrs = _ADDR_PATTERN.findall(block)
    if not addrs:
        return None
    e: dict = {"host": addrs[0][0], "port": int(addrs[0][1])}
    sid = re.search(r"\(SID\s*=\s*([\w.:]+)\)", block, re.IGNORECASE)
    svc = re.search(r"\(SERVICE_NAME\s*=\s*([\w.]+)\)", block, re.IGNORECASE)
    if sid:
        e["sid"] = sid.group(1).strip()
    if svc:
        e["service_name"] = svc.group(1).strip()
    if not e.get("sid") and not e.get("service_name"):
        return None
    return e


def normalized_descriptor(entry: dict) -> tuple[str, int, str]:
    """Produce a (host_lower, port, service_or_sid_lower) comparison key.

    service_name takes precedence over sid when both are present.
    Used to match a dblink HOST descriptor against known instance descriptors.
    """
    host = (entry.get("host") or "").lower()
    port = int(entry.get("port") or 0)
    svc = (entry.get("service_name") or entry.get("sid") or "").lower()
    return (host, port, svc)


def instance_descriptors(tns_admin: str, instances: list[str]) -> dict[str, tuple[str, int, str]]:
    """Parse tnsnames.ora under tns_admin and return descriptors for the given instances.

    Returns {INSTANCE_TNS_UPPER: (host_lower, port, service_or_sid_lower)}
    for each instance name that is found in the tnsnames.ora file.
    Missing entries are silently omitted (caller decides how to handle).
    """
    tnsnames_path = str(Path(tns_admin).expanduser() / "tnsnames.ora")
    entries = parse_tnsnames(tnsnames_path)
    out: dict[str, tuple[str, int, str]] = {}
    for tns in instances:
        e = entries.get(tns.upper())
        if e:
            out[tns.upper()] = normalized_descriptor(e)
    return out


def resolve_host_descriptor(
    host: str, tns_entries: dict[str, dict]
) -> tuple[str, int, str] | None:
    """Resolve a dblink HOST string to a (host, port, service_or_sid) descriptor.

    Three forms are handled in order:
      1. TNS alias  -- host is an uppercase key in tns_entries
      2. Inline DESCRIPTION -- host contains '(' and 'DESCRIPTION'
      3. EZConnect -- host[:port]/service pattern

    Returns None if none of the three forms can be parsed (fail-safe: caller
    should register this dblink as unresolved rather than crashing).
    """
    h = (host or "").strip()
    if not h:
        return None

    # Form 1: TNS alias lookup
    if h.upper() in tns_entries:
        return normalized_descriptor(tns_entries[h.upper()])

    # Form 2: inline DESCRIPTION block
    if "(" in h and "DESCRIPTION" in h.upper():
        e = _parse_block(h)
        return normalized_descriptor(e) if e else None

    # Form 3: EZConnect  host[:port]/service
    m = _EZCONNECT.match(h)
    if m:
        return (m.group(1).lower(), int(m.group(2) or 1521), m.group(3).lower())

    return None
