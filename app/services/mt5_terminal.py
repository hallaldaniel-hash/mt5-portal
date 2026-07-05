from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import ModuleType
from typing import Any

from app.domain.mt5 import MT5Account, MT5Snapshot
from app.services.mt5_accounts import create_mt5_snapshot


class MT5TerminalError(RuntimeError):
    """Base error for live MT5 terminal sync failures."""


class MT5TerminalUnavailable(MT5TerminalError):
    """Raised when the MetaTrader5 Python package is not installed."""


class MT5LoginError(MT5TerminalError):
    """Raised when MT5 initialize/login fails."""


class MT5ReadError(MT5TerminalError):
    """Raised when account_info cannot be read from MT5."""


@dataclass(frozen=True)
class MT5SyncResult:
    account_id: str
    group_id: str
    snapshot: MT5Snapshot
    broker_name: str
    server: str
    login: str


def load_metatrader5() -> ModuleType:
    """Import the optional MetaTrader5 package only when live sync is used.

    Normal unit tests and non-MT5 development should not require the package.
    The package is usually installed only on the Windows VPS where the MT5
    terminal is available.
    """

    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MT5TerminalUnavailable(
            "MetaTrader5 package is not installed. Install it on the Windows VPS "
            "that has the MT5 terminal, or use manual snapshots for local testing."
        ) from exc
    return mt5


def _last_error_text(mt5_module: Any) -> str:
    last_error = getattr(mt5_module, "last_error", None)
    if callable(last_error):
        try:
            return str(last_error())
        except Exception:  # pragma: no cover - defensive only
            return "unknown MT5 error"
    return "unknown MT5 error"


def _decimal_from_mt5(value: Any, field_name: str) -> Decimal:
    if value is None:
        raise MT5ReadError(f"MT5 account_info missing required field: {field_name}")
    return Decimal(str(value))


def _read_field(info: Any, field_name: str, default: Any = None) -> Any:
    if hasattr(info, field_name):
        return getattr(info, field_name)
    if isinstance(info, dict):
        return info.get(field_name, default)
    return default


def connect_and_read_account_info(
    account: MT5Account,
    *,
    mt5_module: Any | None = None,
    terminal_path: str | None = None,
    shutdown: bool = True,
) -> Any:
    """Login to an MT5 account and return mt5.account_info().

    A fake mt5_module can be injected in tests. In production this uses the
    real MetaTrader5 Python package and the account's sync/master password.
    """

    mt5 = mt5_module or load_metatrader5()
    try:
        login = int(account.login)
    except ValueError as exc:
        raise MT5LoginError("MT5 login must be numeric") from exc

    initialize_kwargs = {
        "login": login,
        "password": account.sync_password.reveal(),
        "server": account.server,
    }
    if terminal_path:
        initialize_kwargs["path"] = terminal_path

    initialized = mt5.initialize(**initialize_kwargs)
    if not initialized:
        raise MT5LoginError(f"MT5 initialize/login failed: {_last_error_text(mt5)}")

    try:
        info = mt5.account_info()
        if info is None:
            raise MT5ReadError(f"MT5 account_info returned no data: {_last_error_text(mt5)}")
        return info
    finally:
        if shutdown and hasattr(mt5, "shutdown"):
            mt5.shutdown()


def snapshot_from_account_info(
    account: MT5Account,
    account_info: Any,
    *,
    broker_server_time: datetime | None = None,
) -> MT5Snapshot:
    """Convert MT5 account_info into the portal's MT5Snapshot model."""

    server_time = broker_server_time or datetime.now(timezone.utc)
    if server_time.tzinfo is None:
        server_time = server_time.replace(tzinfo=timezone.utc)

    return create_mt5_snapshot(
        account=account,
        broker_server_time=server_time,
        broker_server_day=server_time.date(),
        raw_balance=_decimal_from_mt5(_read_field(account_info, "balance"), "balance"),
        raw_equity=_decimal_from_mt5(_read_field(account_info, "equity"), "equity"),
        raw_profit=_decimal_from_mt5(_read_field(account_info, "profit", 0), "profit"),
        raw_margin=_decimal_from_mt5(_read_field(account_info, "margin", 0), "margin"),
        raw_free_margin=_decimal_from_mt5(
            _read_field(account_info, "margin_free", _read_field(account_info, "free_margin", 0)),
            "margin_free",
        ),
    )


def sync_account_snapshot(
    account: MT5Account,
    *,
    mt5_module: Any | None = None,
    terminal_path: str | None = None,
    broker_server_time: datetime | None = None,
) -> MT5SyncResult:
    """Read one live MT5 account and return a portal snapshot.

    The caller is responsible for saving the snapshot to the database.
    """

    info = connect_and_read_account_info(
        account,
        mt5_module=mt5_module,
        terminal_path=terminal_path,
    )
    snapshot = snapshot_from_account_info(
        account,
        info,
        broker_server_time=broker_server_time,
    )
    return MT5SyncResult(
        account_id=account.account_id,
        group_id=account.group_id,
        snapshot=snapshot,
        broker_name=account.broker_name,
        server=account.server,
        login=account.login,
    )
