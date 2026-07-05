from __future__ import annotations

from pathlib import Path

from app.db.storage import resolve_database_path, storage_status


def test_default_storage_can_migrate_legacy_project_db(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MT5_PORTAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("MT5_PORTAL_DB_PATH", raising=False)
    legacy = tmp_path / "mt5_portal.db"
    legacy.write_text("legacy-data")

    resolved = resolve_database_path()

    assert resolved == tmp_path / "data" / "mt5_portal.db"
    assert resolved.read_text() == "legacy-data"


def test_storage_status_reports_external_data_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MT5_PORTAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("MT5_PORTAL_DB_PATH", raising=False)

    status = storage_status()

    assert status["uses_external_data_dir"] is True
    assert Path(str(status["database_path"])).parent == tmp_path / "data"
