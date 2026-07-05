from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from app.domain.daily_close import DailyCloseStatus, DailyGroupClose
from app.domain.ledger import LedgerEntry, LedgerEntryType
from app.domain.models import CommissionRule, MemberRole
from app.domain.mt5 import MT5Account, MT5AccountStatus, MT5Snapshot, SecretValue
from app.domain.portal import ClientProfile, Group, GroupMembership, GroupStatus, UserAccount, UserRole
from app.security.encryption import SecretCipher, require_cipher_for_encrypted_value


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS user_accounts (
        user_id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        password_salt TEXT NOT NULL,
        role TEXT NOT NULL,
        is_active INTEGER NOT NULL,
        two_factor_enabled INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS client_profiles (
        client_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        display_name TEXT NOT NULL,
        email TEXT,
        email_reports_opt_in INTEGER NOT NULL,
        is_active INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES user_accounts(user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS groups (
        group_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        currency TEXT NOT NULL,
        status TEXT NOT NULL,
        use_broker_server_day_close INTEGER NOT NULL,
        display_timezone TEXT NOT NULL,
        total_rate TEXT NOT NULL,
        external_rate TEXT NOT NULL,
        partner_1_rate TEXT NOT NULL,
        partner_2_rate TEXT NOT NULL,
        partner_1_client_id TEXT,
        partner_2_client_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS group_memberships (
        membership_id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        client_id TEXT NOT NULL,
        display_name TEXT NOT NULL,
        effective_capital TEXT NOT NULL,
        role TEXT NOT NULL,
        joined_on TEXT,
        effective_from TEXT,
        is_active INTEGER NOT NULL,
        FOREIGN KEY (group_id) REFERENCES groups(group_id),
        FOREIGN KEY (client_id) REFERENCES client_profiles(client_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mt5_accounts (
        account_id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        nickname TEXT NOT NULL,
        broker_name TEXT NOT NULL,
        server TEXT NOT NULL,
        login TEXT NOT NULL,
        sync_password TEXT NOT NULL,
        investor_login TEXT NOT NULL,
        investor_password TEXT NOT NULL,
        currency TEXT NOT NULL,
        display_divisor TEXT NOT NULL,
        status TEXT NOT NULL,
        notes TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (group_id) REFERENCES groups(group_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mt5_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        group_id TEXT NOT NULL,
        broker_server_time TEXT NOT NULL,
        broker_server_day TEXT NOT NULL,
        raw_balance TEXT NOT NULL,
        raw_equity TEXT NOT NULL,
        raw_profit TEXT NOT NULL,
        raw_margin TEXT NOT NULL,
        raw_free_margin TEXT NOT NULL,
        currency TEXT NOT NULL,
        display_divisor TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (account_id) REFERENCES mt5_accounts(account_id),
        FOREIGN KEY (group_id) REFERENCES groups(group_id)
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS daily_group_closes (
        close_id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        broker_server_day TEXT NOT NULL,
        opening_closed_balance TEXT NOT NULL,
        closing_closed_balance TEXT NOT NULL,
        deposits_effective TEXT NOT NULL,
        withdrawals_effective TEXT NOT NULL,
        expenses_effective TEXT NOT NULL,
        pending_internal_transfers TEXT NOT NULL,
        trading_profit_loss TEXT NOT NULL,
        status TEXT NOT NULL,
        finalized_at TEXT NOT NULL,
        created_by_user_id TEXT,
        override_reason TEXT,
        FOREIGN KEY (group_id) REFERENCES groups(group_id),
        UNIQUE (group_id, broker_server_day)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ledger_entries (
        entry_id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        client_id TEXT,
        mt5_account_id TEXT,
        transaction_id TEXT NOT NULL,
        entry_type TEXT NOT NULL,
        amount TEXT NOT NULL,
        currency TEXT NOT NULL,
        description TEXT NOT NULL,
        effective_date TEXT,
        created_at TEXT NOT NULL,
        created_by_user_id TEXT,
        metadata_json TEXT NOT NULL,
        FOREIGN KEY (group_id) REFERENCES groups(group_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        event_id TEXT PRIMARY KEY,
        actor_user_id TEXT,
        target_user_id TEXT,
        target_client_id TEXT,
        event_type TEXT NOT NULL,
        description TEXT NOT NULL,
        created_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_events_target_client_id ON audit_events(target_client_id)",
    "CREATE INDEX IF NOT EXISTS idx_client_profiles_user_id ON client_profiles(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_group_memberships_group_id ON group_memberships(group_id)",
    "CREATE INDEX IF NOT EXISTS idx_group_memberships_client_id ON group_memberships(client_id)",
    "CREATE INDEX IF NOT EXISTS idx_mt5_accounts_group_id ON mt5_accounts(group_id)",
    "CREATE INDEX IF NOT EXISTS idx_mt5_snapshots_account_id ON mt5_snapshots(account_id)",
    "CREATE INDEX IF NOT EXISTS idx_mt5_snapshots_group_id ON mt5_snapshots(group_id)",
    "CREATE INDEX IF NOT EXISTS idx_daily_group_closes_group_id ON daily_group_closes(group_id)",
    "CREATE INDEX IF NOT EXISTS idx_ledger_entries_group_id ON ledger_entries(group_id)",
    "CREATE INDEX IF NOT EXISTS idx_ledger_entries_client_id ON ledger_entries(client_id)",
]


def connect_database(path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def init_db(conn: sqlite3.Connection) -> None:
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    if not _column_exists(conn, "user_accounts", "two_factor_enabled"):
        conn.execute("ALTER TABLE user_accounts ADD COLUMN two_factor_enabled INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def _bool(value: bool) -> int:
    return 1 if value else 0


def _from_bool(value: int) -> bool:
    return bool(value)


def _decimal(value: Decimal) -> str:
    return str(value)


def _from_decimal(value: str) -> Decimal:
    return Decimal(value)


def _date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _from_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _datetime(value: datetime) -> str:
    return value.isoformat()


def _from_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def save_user_account(conn: sqlite3.Connection, account: UserAccount) -> None:
    existing_username_owner = conn.execute(
        "SELECT user_id FROM user_accounts WHERE username = ?", (account.username,)
    ).fetchone()
    if (
        existing_username_owner is not None
        and existing_username_owner["user_id"] != account.user_id
    ):
        raise ValueError(f"Username already exists: {account.username}")

    conn.execute(
        """
        INSERT OR REPLACE INTO user_accounts (
            user_id, username, password_hash, password_salt, role, is_active, two_factor_enabled
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account.user_id,
            account.username,
            account.password_hash,
            account.password_salt,
            account.role.value,
            _bool(account.is_active),
            _bool(account.two_factor_enabled),
        ),
    )
    conn.commit()


def row_to_user_account(row: sqlite3.Row) -> UserAccount:
    return UserAccount(
        user_id=row["user_id"],
        username=row["username"],
        password_hash=row["password_hash"],
        password_salt=row["password_salt"],
        role=UserRole(row["role"]),
        is_active=_from_bool(row["is_active"]),
        two_factor_enabled=_from_bool(row["two_factor_enabled"]) if "two_factor_enabled" in row.keys() else False,
    )


def get_user_account(conn: sqlite3.Connection, user_id: str) -> UserAccount | None:
    row = conn.execute(
        "SELECT * FROM user_accounts WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row_to_user_account(row) if row else None


def get_user_account_by_username(conn: sqlite3.Connection, username: str) -> UserAccount | None:
    row = conn.execute(
        "SELECT * FROM user_accounts WHERE username = ?", (username.strip().lower(),)
    ).fetchone()
    return row_to_user_account(row) if row else None


def list_user_accounts(conn: sqlite3.Connection) -> list[UserAccount]:
    rows = conn.execute("SELECT * FROM user_accounts ORDER BY username").fetchall()
    return [row_to_user_account(row) for row in rows]


def save_audit_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    event_type: str,
    description: str,
    actor_user_id: str | None = None,
    target_user_id: str | None = None,
    target_client_id: str | None = None,
    created_at: datetime | None = None,
    metadata: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_events (
            event_id, actor_user_id, target_user_id, target_client_id, event_type,
            description, created_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            actor_user_id,
            target_user_id,
            target_client_id,
            event_type,
            description,
            _datetime(created_at or datetime.now(timezone.utc)),
            json.dumps(metadata or {}, sort_keys=True),
        ),
    )
    conn.commit()


def list_audit_events(
    conn: sqlite3.Connection, *, target_client_id: str | None = None
) -> list[dict]:
    if target_client_id is None:
        rows = conn.execute("SELECT * FROM audit_events ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE target_client_id = ? ORDER BY created_at DESC",
            (target_client_id,),
        ).fetchall()
    return [
        {
            "event_id": row["event_id"],
            "actor_user_id": row["actor_user_id"],
            "target_user_id": row["target_user_id"],
            "target_client_id": row["target_client_id"],
            "event_type": row["event_type"],
            "description": row["description"],
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata_json"]),
        }
        for row in rows
    ]


def save_client_profile(conn: sqlite3.Connection, client: ClientProfile) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO client_profiles (
            client_id, user_id, display_name, email, email_reports_opt_in, is_active
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            client.client_id,
            client.user_id,
            client.display_name,
            client.email,
            _bool(client.email_reports_opt_in),
            _bool(client.is_active),
        ),
    )
    conn.commit()


def row_to_client_profile(row: sqlite3.Row) -> ClientProfile:
    return ClientProfile(
        client_id=row["client_id"],
        user_id=row["user_id"],
        display_name=row["display_name"],
        email=row["email"],
        email_reports_opt_in=_from_bool(row["email_reports_opt_in"]),
        is_active=_from_bool(row["is_active"]),
    )


def get_client_profile(conn: sqlite3.Connection, client_id: str) -> ClientProfile | None:
    row = conn.execute(
        "SELECT * FROM client_profiles WHERE client_id = ?", (client_id,)
    ).fetchone()
    return row_to_client_profile(row) if row else None


def list_client_profiles(conn: sqlite3.Connection) -> list[ClientProfile]:
    rows = conn.execute("SELECT * FROM client_profiles ORDER BY display_name").fetchall()
    return [row_to_client_profile(row) for row in rows]


def save_group(conn: sqlite3.Connection, group: Group) -> None:
    rule = group.commission_rule
    conn.execute(
        """
        INSERT OR REPLACE INTO groups (
            group_id, name, currency, status, use_broker_server_day_close,
            display_timezone, total_rate, external_rate, partner_1_rate,
            partner_2_rate, partner_1_client_id, partner_2_client_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            group.group_id,
            group.name,
            group.currency,
            group.status.value,
            _bool(group.use_broker_server_day_close),
            group.display_timezone,
            _decimal(rule.total_rate),
            _decimal(rule.external_rate),
            _decimal(rule.partner_1_rate),
            _decimal(rule.partner_2_rate),
            rule.partner_1_client_id,
            rule.partner_2_client_id,
        ),
    )
    conn.commit()


def row_to_group(row: sqlite3.Row) -> Group:
    rule = CommissionRule(
        total_rate=_from_decimal(row["total_rate"]),
        external_rate=_from_decimal(row["external_rate"]),
        partner_1_rate=_from_decimal(row["partner_1_rate"]),
        partner_2_rate=_from_decimal(row["partner_2_rate"]),
        partner_1_client_id=row["partner_1_client_id"],
        partner_2_client_id=row["partner_2_client_id"],
    )
    return Group(
        group_id=row["group_id"],
        name=row["name"],
        currency=row["currency"],
        status=GroupStatus(row["status"]),
        use_broker_server_day_close=_from_bool(row["use_broker_server_day_close"]),
        display_timezone=row["display_timezone"],
        commission_rule=rule,
    )


def get_group(conn: sqlite3.Connection, group_id: str) -> Group | None:
    row = conn.execute("SELECT * FROM groups WHERE group_id = ?", (group_id,)).fetchone()
    return row_to_group(row) if row else None


def list_groups(conn: sqlite3.Connection) -> list[Group]:
    rows = conn.execute("SELECT * FROM groups ORDER BY name").fetchall()
    return [row_to_group(row) for row in rows]


def save_group_membership(conn: sqlite3.Connection, membership: GroupMembership) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO group_memberships (
            membership_id, group_id, client_id, display_name, effective_capital,
            role, joined_on, effective_from, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            membership.membership_id,
            membership.group_id,
            membership.client_id,
            membership.display_name,
            _decimal(membership.effective_capital),
            membership.role.value,
            _date(membership.joined_on),
            _date(membership.effective_from),
            _bool(membership.is_active),
        ),
    )
    conn.commit()


def row_to_group_membership(row: sqlite3.Row) -> GroupMembership:
    return GroupMembership(
        membership_id=row["membership_id"],
        group_id=row["group_id"],
        client_id=row["client_id"],
        display_name=row["display_name"],
        effective_capital=_from_decimal(row["effective_capital"]),
        role=MemberRole(row["role"]),
        joined_on=_from_date(row["joined_on"]),
        effective_from=_from_date(row["effective_from"]),
        is_active=_from_bool(row["is_active"]),
    )


def get_group_membership(conn: sqlite3.Connection, membership_id: str) -> GroupMembership | None:
    row = conn.execute(
        "SELECT * FROM group_memberships WHERE membership_id = ?", (membership_id,)
    ).fetchone()
    return row_to_group_membership(row) if row else None


def list_group_memberships(
    conn: sqlite3.Connection, *, group_id: str | None = None, client_id: str | None = None
) -> list[GroupMembership]:
    query = "SELECT * FROM group_memberships"
    params: list[str] = []
    clauses: list[str] = []
    if group_id is not None:
        clauses.append("group_id = ?")
        params.append(group_id)
    if client_id is not None:
        clauses.append("client_id = ?")
        params.append(client_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY display_name"
    rows = conn.execute(query, params).fetchall()
    return [row_to_group_membership(row) for row in rows]


def save_mt5_account(
    conn: sqlite3.Connection,
    account: MT5Account,
    *,
    secret_cipher: SecretCipher | None = None,
) -> None:
    sync_password = account.sync_password.reveal()
    investor_password = account.investor_password.reveal()
    if secret_cipher is not None:
        sync_password = secret_cipher.encrypt(sync_password)
        investor_password = secret_cipher.encrypt(investor_password)
    conn.execute(
        """
        INSERT OR REPLACE INTO mt5_accounts (
            account_id, group_id, nickname, broker_name, server, login,
            sync_password, investor_login, investor_password, currency,
            display_divisor, status, notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account.account_id,
            account.group_id,
            account.nickname,
            account.broker_name,
            account.server,
            account.login,
            sync_password,
            account.investor_login,
            investor_password,
            account.currency,
            _decimal(account.display_divisor),
            account.status.value,
            account.notes,
            _datetime(account.created_at),
        ),
    )
    conn.commit()


def row_to_mt5_account(
    row: sqlite3.Row, *, secret_cipher: SecretCipher | None = None
) -> MT5Account:
    sync_password = require_cipher_for_encrypted_value(row["sync_password"], secret_cipher)
    investor_password = require_cipher_for_encrypted_value(row["investor_password"], secret_cipher)
    return MT5Account(
        account_id=row["account_id"],
        group_id=row["group_id"],
        nickname=row["nickname"],
        broker_name=row["broker_name"],
        server=row["server"],
        login=row["login"],
        sync_password=SecretValue(sync_password),
        investor_login=row["investor_login"],
        investor_password=SecretValue(investor_password),
        currency=row["currency"],
        display_divisor=_from_decimal(row["display_divisor"]),
        status=MT5AccountStatus(row["status"]),
        notes=row["notes"],
        created_at=_from_datetime(row["created_at"]),
    )


def get_mt5_account(
    conn: sqlite3.Connection,
    account_id: str,
    *,
    secret_cipher: SecretCipher | None = None,
) -> MT5Account | None:
    row = conn.execute("SELECT * FROM mt5_accounts WHERE account_id = ?", (account_id,)).fetchone()
    return row_to_mt5_account(row, secret_cipher=secret_cipher) if row else None


def list_mt5_accounts(
    conn: sqlite3.Connection,
    *,
    group_id: str | None = None,
    secret_cipher: SecretCipher | None = None,
) -> list[MT5Account]:
    if group_id is None:
        rows = conn.execute("SELECT * FROM mt5_accounts ORDER BY nickname").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM mt5_accounts WHERE group_id = ? ORDER BY nickname", (group_id,)
        ).fetchall()
    return [row_to_mt5_account(row, secret_cipher=secret_cipher) for row in rows]


def save_mt5_snapshot(conn: sqlite3.Connection, snapshot: MT5Snapshot) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO mt5_snapshots (
            snapshot_id, account_id, group_id, broker_server_time, broker_server_day,
            raw_balance, raw_equity, raw_profit, raw_margin, raw_free_margin,
            currency, display_divisor, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot.snapshot_id,
            snapshot.account_id,
            snapshot.group_id,
            _datetime(snapshot.broker_server_time),
            _date(snapshot.broker_server_day),
            _decimal(snapshot.raw_balance),
            _decimal(snapshot.raw_equity),
            _decimal(snapshot.raw_profit),
            _decimal(snapshot.raw_margin),
            _decimal(snapshot.raw_free_margin),
            snapshot.currency,
            _decimal(snapshot.display_divisor),
            _datetime(snapshot.created_at),
        ),
    )
    conn.commit()


def row_to_mt5_snapshot(row: sqlite3.Row) -> MT5Snapshot:
    broker_server_day = _from_date(row["broker_server_day"])
    if broker_server_day is None:
        raise ValueError("MT5 snapshot broker_server_day is required")
    return MT5Snapshot(
        snapshot_id=row["snapshot_id"],
        account_id=row["account_id"],
        group_id=row["group_id"],
        broker_server_time=_from_datetime(row["broker_server_time"]),
        broker_server_day=broker_server_day,
        raw_balance=_from_decimal(row["raw_balance"]),
        raw_equity=_from_decimal(row["raw_equity"]),
        raw_profit=_from_decimal(row["raw_profit"]),
        raw_margin=_from_decimal(row["raw_margin"]),
        raw_free_margin=_from_decimal(row["raw_free_margin"]),
        currency=row["currency"],
        display_divisor=_from_decimal(row["display_divisor"]),
        created_at=_from_datetime(row["created_at"]),
    )


def list_mt5_snapshots(
    conn: sqlite3.Connection,
    *,
    account_id: str | None = None,
    group_id: str | None = None,
) -> list[MT5Snapshot]:
    query = "SELECT * FROM mt5_snapshots"
    params: list[str] = []
    clauses: list[str] = []
    if account_id is not None:
        clauses.append("account_id = ?")
        params.append(account_id)
    if group_id is not None:
        clauses.append("group_id = ?")
        params.append(group_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY broker_server_time"
    rows = conn.execute(query, params).fetchall()
    return [row_to_mt5_snapshot(row) for row in rows]


def save_daily_group_close(conn: sqlite3.Connection, close: DailyGroupClose) -> None:
    close.validate()
    try:
        conn.execute(
            """
            INSERT INTO daily_group_closes (
                close_id, group_id, broker_server_day, opening_closed_balance,
                closing_closed_balance, deposits_effective, withdrawals_effective,
                expenses_effective, pending_internal_transfers, trading_profit_loss,
                status, finalized_at, created_by_user_id, override_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                close.close_id,
                close.group_id,
                _date(close.broker_server_day),
                _decimal(close.opening_closed_balance),
                _decimal(close.closing_closed_balance),
                _decimal(close.deposits_effective),
                _decimal(close.withdrawals_effective),
                _decimal(close.expenses_effective),
                _decimal(close.pending_internal_transfers),
                _decimal(close.trading_profit_loss),
                close.status.value,
                _datetime(close.finalized_at),
                close.created_by_user_id,
                close.override_reason,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            f"Daily close already exists for group {close.group_id} on {close.broker_server_day.isoformat()}"
        ) from exc


def row_to_daily_group_close(row: sqlite3.Row) -> DailyGroupClose:
    broker_day = _from_date(row["broker_server_day"])
    if broker_day is None:
        raise ValueError("Daily close broker_server_day is required")
    return DailyGroupClose(
        close_id=row["close_id"],
        group_id=row["group_id"],
        broker_server_day=broker_day,
        opening_closed_balance=_from_decimal(row["opening_closed_balance"]),
        closing_closed_balance=_from_decimal(row["closing_closed_balance"]),
        deposits_effective=_from_decimal(row["deposits_effective"]),
        withdrawals_effective=_from_decimal(row["withdrawals_effective"]),
        expenses_effective=_from_decimal(row["expenses_effective"]),
        pending_internal_transfers=_from_decimal(row["pending_internal_transfers"]),
        trading_profit_loss=_from_decimal(row["trading_profit_loss"]),
        status=DailyCloseStatus(row["status"]),
        finalized_at=_from_datetime(row["finalized_at"]),
        created_by_user_id=row["created_by_user_id"],
        override_reason=row["override_reason"],
    )


def get_daily_group_close(conn: sqlite3.Connection, group_id: str, broker_server_day: date) -> DailyGroupClose | None:
    row = conn.execute(
        "SELECT * FROM daily_group_closes WHERE group_id = ? AND broker_server_day = ?",
        (group_id, _date(broker_server_day)),
    ).fetchone()
    return row_to_daily_group_close(row) if row else None


def list_daily_group_closes(conn: sqlite3.Connection, *, group_id: str | None = None) -> list[DailyGroupClose]:
    if group_id is None:
        rows = conn.execute(
            "SELECT * FROM daily_group_closes ORDER BY broker_server_day, group_id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM daily_group_closes WHERE group_id = ? ORDER BY broker_server_day",
            (group_id,),
        ).fetchall()
    return [row_to_daily_group_close(row) for row in rows]

def save_ledger_entry(conn: sqlite3.Connection, entry: LedgerEntry) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO ledger_entries (
            entry_id, group_id, client_id, mt5_account_id, transaction_id,
            entry_type, amount, currency, description, effective_date, created_at,
            created_by_user_id, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.entry_id,
            entry.group_id,
            entry.client_id,
            entry.mt5_account_id,
            entry.transaction_id,
            entry.entry_type.value,
            _decimal(entry.amount),
            entry.currency,
            entry.description,
            _date(entry.effective_date),
            _datetime(entry.created_at),
            entry.created_by_user_id,
            json.dumps(entry.metadata, sort_keys=True),
        ),
    )
    conn.commit()


def save_ledger_entries(conn: sqlite3.Connection, entries: Iterable[LedgerEntry]) -> None:
    for entry in entries:
        save_ledger_entry(conn, entry)


def row_to_ledger_entry(row: sqlite3.Row) -> LedgerEntry:
    return LedgerEntry(
        entry_id=row["entry_id"],
        group_id=row["group_id"],
        client_id=row["client_id"],
        mt5_account_id=row["mt5_account_id"],
        transaction_id=row["transaction_id"],
        entry_type=LedgerEntryType(row["entry_type"]),
        amount=_from_decimal(row["amount"]),
        currency=row["currency"],
        description=row["description"],
        effective_date=_from_date(row["effective_date"]),
        created_at=_from_datetime(row["created_at"]),
        created_by_user_id=row["created_by_user_id"],
        metadata=json.loads(row["metadata_json"]),
    )


def get_ledger_entry(conn: sqlite3.Connection, entry_id: str) -> LedgerEntry | None:
    row = conn.execute("SELECT * FROM ledger_entries WHERE entry_id = ?", (entry_id,)).fetchone()
    return row_to_ledger_entry(row) if row else None


def list_ledger_entries(
    conn: sqlite3.Connection,
    *,
    group_id: str | None = None,
    client_id: str | None = None,
) -> list[LedgerEntry]:
    query = "SELECT * FROM ledger_entries"
    params: list[str] = []
    clauses: list[str] = []
    if group_id is not None:
        clauses.append("group_id = ?")
        params.append(group_id)
    if client_id is not None:
        clauses.append("client_id = ?")
        params.append(client_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at, entry_id"
    rows = conn.execute(query, params).fetchall()
    return [row_to_ledger_entry(row) for row in rows]
