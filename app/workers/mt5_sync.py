from __future__ import annotations

import os
from pathlib import Path

from app.db.storage import resolve_database_path
from app.db.sqlite import connect_database, init_db, list_mt5_accounts, save_mt5_snapshot
from app.security.encryption import SecretCipher
from app.services.mt5_terminal import MT5TerminalError, sync_account_snapshot


def run_once(
    *,
    db_path: str | Path,
    secret_key: str,
    terminal_path: str | None = None,
) -> int:
    """Sync all live MT5 accounts once and save snapshots.

    Returns the number of successfully saved snapshots. Failures are printed so
    one bad account does not stop the whole sync cycle.
    """

    conn = connect_database(db_path)
    init_db(conn)
    cipher = SecretCipher(secret_key)
    saved = 0
    for account in list_mt5_accounts(conn, secret_cipher=cipher):
        if not account.is_live():
            continue
        try:
            result = sync_account_snapshot(account, terminal_path=terminal_path)
            save_mt5_snapshot(conn, result.snapshot)
            saved += 1
            print(f"synced {account.nickname} ({account.login}) balance={result.snapshot.display_balance}")
        except MT5TerminalError as exc:
            print(f"failed to sync {account.nickname} ({account.login}): {exc}")
    return saved


def main() -> None:
    db_path = os.environ.get("MT5_PORTAL_DB_PATH") or str(resolve_database_path())
    secret_key = os.environ.get("MT5_PORTAL_SECRET_KEY")
    terminal_path = os.environ.get("MT5_TERMINAL_PATH")
    if not secret_key:
        raise SystemExit("MT5_PORTAL_SECRET_KEY is required")
    count = run_once(db_path=db_path, secret_key=secret_key, terminal_path=terminal_path)
    print(f"saved {count} MT5 snapshot(s)")


if __name__ == "__main__":
    main()
