from __future__ import annotations

import os
import shutil
from pathlib import Path


APP_DATA_DIR_ENV = "MT5_PORTAL_DATA_DIR"
DB_PATH_ENV = "MT5_PORTAL_DB_PATH"
DEFAULT_DATA_DIR_NAME = "mt5_portal_data"
DEFAULT_DB_NAME = "mt5_portal.db"


def default_data_dir() -> Path:
    configured = os.getenv(APP_DATA_DIR_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / DEFAULT_DATA_DIR_NAME).resolve()


def resolve_database_path(project_db_path: str | Path = DEFAULT_DB_NAME) -> Path:
    """Return the persistent database path used by the running portal.

    Production/local app data should live outside the project code folder so replacing
    a Step zip does not reset clients, groups, ledgers, and users. If an older project
    folder still has mt5_portal.db, copy it into the persistent data directory once.
    """
    explicit = os.getenv(DB_PATH_ENV)
    if explicit:
        explicit_path = Path(explicit).expanduser().resolve()
        explicit_path.parent.mkdir(parents=True, exist_ok=True)
        return explicit_path

    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / DEFAULT_DB_NAME

    legacy = Path(project_db_path)
    if not legacy.is_absolute():
        legacy = (Path.cwd() / legacy).resolve()

    if not target.exists() and legacy.exists() and legacy.is_file():
        shutil.copy2(legacy, target)

    return target


def storage_status(project_db_path: str | Path = DEFAULT_DB_NAME) -> dict[str, str | bool]:
    db_path = resolve_database_path(project_db_path)
    legacy = Path(project_db_path)
    if not legacy.is_absolute():
        legacy = (Path.cwd() / legacy).resolve()
    return {
        "database_path": str(db_path),
        "data_dir": str(db_path.parent),
        "uses_external_data_dir": db_path.parent != Path.cwd().resolve(),
        "legacy_project_db_exists": legacy.exists(),
    }
