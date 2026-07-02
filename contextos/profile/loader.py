"""TOML loader with path precedence + CONTEXTOS_DATA_DIR env override."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from contextos.profile.schema import Profile


class ProfileNotFound(FileNotFoundError):
    """Raised when no profile.toml is discoverable on the search path."""


_FILENAME = "profile.toml"
_CWD_CANDIDATES = (_FILENAME, f"config/{_FILENAME}", f"data/{_FILENAME}")
_HOME_CANDIDATES_RAW = (
    f"~/.config/contextos/{_FILENAME}",
    f"~/contextos-fpa/{_FILENAME}",
)


def _resolve_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env_path = os.environ.get("CONTEXTOS_PROFILE")
    if env_path:
        return Path(env_path).expanduser().resolve()
    cwd = Path.cwd()
    for rel in _CWD_CANDIDATES:
        candidate = cwd / rel
        if candidate.is_file():
            return candidate.resolve()
    for raw in _HOME_CANDIDATES_RAW:
        candidate = Path(raw).expanduser()
        if candidate.is_file():
            return candidate.resolve()
    home_display = ", ".join(str(Path(r).expanduser()) for r in _HOME_CANDIDATES_RAW)
    raise ProfileNotFound(
        "No profile.toml found. Looked at $CONTEXTOS_PROFILE, "
        f"./{', ./'.join(_CWD_CANDIDATES)}, and "
        f"{home_display}"
    )


def load_profile(path: Path | None = None) -> Profile:
    resolved = _resolve_path(path)
    with open(resolved, "rb") as f:
        data = tomllib.load(f)
    env_data_dir = os.environ.get("CONTEXTOS_DATA_DIR")
    if env_data_dir:
        data.setdefault("storage", {})["data_dir"] = env_data_dir
    return Profile(**data)
