from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

from app.domain.mt5 import MT5Account, MT5AccountStatus, MT5Snapshot, SecretValue
from app.domain.portal import Group

ZERO = Decimal("0")
CENT_ACCOUNT_DIVISOR = Decimal("100")
NORMAL_ACCOUNT_DIVISOR = Decimal("1")

READ_ONLY_CREDENTIAL_PLACEHOLDER = "READ_ONLY_INVESTOR_ACCESS_ONLY"


def _require_text(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _default_display_divisor(*, is_cent_account: bool) -> Decimal:
    return CENT_ACCOUNT_DIVISOR if is_cent_account else NORMAL_ACCOUNT_DIVISOR


def convert_raw_amount(raw_amount: Decimal, display_divisor: Decimal) -> Decimal:
    if display_divisor <= ZERO:
        raise ValueError("display_divisor must be greater than zero")
    return raw_amount / display_divisor


def create_mt5_account(
    *,
    group: Group,
    nickname: str,
    broker_name: str,
    server: str,
    login: str,
    investor_login: str,
    investor_password: str,
    sync_password: str | None = None,
    currency: str | None = None,
    is_cent_account: bool = True,
    display_divisor: Decimal | None = None,
    status: MT5AccountStatus = MT5AccountStatus.PENDING,
    account_id: str | None = None,
    notes: str | None = None,
) -> MT5Account:
    """Create a read-only MT5 account inside a group.

    Step 28 rule:
    The portal must not require or store MT5 master/trading credentials.

    Required credentials:
    - login/account number
    - investor_login
    - investor_password

    Legacy compatibility:
    - sync_password remains on the domain object for now because older storage/API
      code still expects the field.
    - If no sync_password is provided, we store a safe placeholder instead of a
      real master password.
    """

    if not group.is_active():
        raise ValueError("Cannot add MT5 accounts to an inactive group")

    divisor = display_divisor or _default_display_divisor(is_cent_account=is_cent_account)
    if divisor <= ZERO:
        raise ValueError("display_divisor must be greater than zero")

    clean_login = _require_text(login, "MT5 login")
    clean_investor_login = _require_text(investor_login, "Investor login")
    clean_investor_password = _require_text(investor_password, "Investor password")

    legacy_sync_password = (
        sync_password.strip()
        if sync_password is not None and sync_password.strip()
        else READ_ONLY_CREDENTIAL_PLACEHOLDER
    )

    return MT5Account(
        account_id=account_id or clean_login,
        group_id=group.group_id,
        nickname=_require_text(nickname, "Account nickname"),
        broker_name=_require_text(broker_name, "Broker name"),
        server=_require_text(server, "MT5 server"),
        login=clean_login,
        sync_password=SecretValue(legacy_sync_password),
        investor_login=clean_investor_login,
        investor_password=SecretValue(clean_investor_password),
        currency=(currency or group.currency).upper(),
        display_divisor=divisor,
        status=status,
        notes=notes,
    )


def activate_mt5_account(account: MT5Account) -> MT5Account:
    if account.status == MT5AccountStatus.ARCHIVED:
        raise ValueError("Archived MT5 accounts cannot be activated")
    return replace(account, status=MT5AccountStatus.LIVE)


def deactivate_mt5_account(account: MT5Account) -> MT5Account:
    if account.status == MT5AccountStatus.ARCHIVED:
        raise ValueError("Archived MT5 accounts are already out of use")
    return replace(account, status=MT5AccountStatus.INACTIVE)


def archive_mt5_account(account: MT5Account) -> MT5Account:
    return replace(account, status=MT5AccountStatus.ARCHIVED)


def accounts_for_group(accounts: list[MT5Account], group_id: str) -> list[MT5Account]:
    return [account for account in accounts if account.group_id == group_id]


def live_accounts_for_group(accounts: list[MT5Account], group_id: str) -> list[MT5Account]:
    return [account for account in accounts_for_group(accounts, group_id) if account.is_live()]


def create_mt5_snapshot(
    *,
    account: MT5Account,
    broker_server_time: datetime,
    raw_balance: Decimal,
    raw_equity: Decimal,
    raw_profit: Decimal = Decimal("0"),
    raw_margin: Decimal = Decimal("0"),
    raw_free_margin: Decimal = Decimal("0"),
    broker_server_day: date | None = None,
) -> MT5Snapshot:
    if raw_balance < ZERO:
        raise ValueError("raw_balance cannot be negative")
    if raw_equity < ZERO:
        raise ValueError("raw_equity cannot be negative")

    return MT5Snapshot(
        account_id=account.account_id,
        group_id=account.group_id,
        broker_server_time=broker_server_time,
        broker_server_day=broker_server_day or broker_server_time.date(),
        raw_balance=raw_balance,
        raw_equity=raw_equity,
        raw_profit=raw_profit,
        raw_margin=raw_margin,
        raw_free_margin=raw_free_margin,
        currency=account.currency,
        display_divisor=account.display_divisor,
    )


def latest_snapshots_by_account(snapshots: list[MT5Snapshot]) -> dict[str, MT5Snapshot]:
    latest: dict[str, MT5Snapshot] = {}
    for snapshot in snapshots:
        current = latest.get(snapshot.account_id)
        if current is None or snapshot.broker_server_time > current.broker_server_time:
            latest[snapshot.account_id] = snapshot
    return latest


def latest_group_closed_balance(
    *,
    accounts: list[MT5Account],
    snapshots: list[MT5Snapshot],
    group_id: str,
) -> Decimal:
    """Sum display balances from the latest snapshots of live accounts only."""

    latest = latest_snapshots_by_account(snapshots)
    total = ZERO

    for account in live_accounts_for_group(accounts, group_id):
        snapshot = latest.get(account.account_id)
        if snapshot is not None:
            total += snapshot.display_balance

    return total


def client_visible_mt5_account(
    account: MT5Account,
    latest_snapshot: MT5Snapshot | None = None,
) -> dict[str, str]:
    """Return only information clients are allowed to see.

    Step 28:
    Clients should see account status and financial data, not master credentials.
    Investor password is also not shown in the dashboard response.
    """

    visible = {
        "account_id": account.account_id,
        "nickname": account.nickname,
        "broker_name": account.broker_name,
        "server": account.server,
        "investor_login": account.investor_login,
        "currency": account.currency,
        "status": account.status.value,
        "is_cent_account": str(account.is_cent_account()).lower(),
        "read_only_mode": "true",
        "read_only_notice": "Read-only MT5 investor access. The portal cannot trade, withdraw, or modify the account.",
    }

    if latest_snapshot is not None:
        visible["balance"] = str(latest_snapshot.display_balance)
        visible["equity"] = str(latest_snapshot.display_equity)
        visible["profit"] = str(latest_snapshot.display_profit)
        visible["margin"] = str(latest_snapshot.display_margin)
        visible["free_margin"] = str(latest_snapshot.display_free_margin)
        visible["broker_server_day"] = latest_snapshot.broker_server_day.isoformat()
        visible["broker_server_time"] = latest_snapshot.broker_server_time.isoformat()
        visible["last_sync_time"] = latest_snapshot.created_at.isoformat()

    return visible


def admin_visible_mt5_account(account: MT5Account) -> dict[str, str]:
    """Return admin-safe display data without printing raw passwords."""

    return {
        "account_id": account.account_id,
        "group_id": account.group_id,
        "nickname": account.nickname,
        "broker_name": account.broker_name,
        "server": account.server,
        "login": account.login,
        "investor_login": account.investor_login,
        "investor_password": account.investor_password.masked(),
        "currency": account.currency,
        "display_divisor": str(account.display_divisor),
        "status": account.status.value,
        "read_only_mode": "true",
        "credential_mode": "investor_view_only",
        "master_password_required": "false",
    }