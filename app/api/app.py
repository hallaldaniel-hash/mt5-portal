from __future__ import annotations

from datetime import date, datetime
from dataclasses import replace
from decimal import Decimal
import csv
import io
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.db.storage import resolve_database_path, storage_status
from app.db.sqlite import (
    connect_database,
    get_client_profile,
    get_daily_group_close,
    get_group,
    get_ledger_entry,
    get_mt5_account,
    get_user_account,
    get_user_account_by_username,
    init_db,
    list_client_profiles,
    list_daily_group_closes,
    list_group_memberships,
    list_groups,
    list_ledger_entries,
    list_mt5_accounts,
    list_mt5_snapshots,
    list_user_accounts,
    list_audit_events,
    save_audit_event,
    save_client_profile,
    save_daily_group_close,
    save_group,
    save_group_membership,
    save_ledger_entry,
    save_mt5_account,
    save_mt5_snapshot,
    save_user_account,
)
from app.domain.ledger import BALANCE_AFFECTING_ENTRY_TYPES, LedgerEntryType
from app.domain.import_wizard import (
    DetectedMoneyMovement,
    ExistingGroupImportMode,
    MovementClassification,
    MovementClassificationDecision,
)
from app.domain.models import CommissionRule, MemberRole
from app.domain.mt5 import MT5AccountStatus
from app.domain.portal import UserRole
from app.security.encryption import SecretCipher
from app.services.groups import (
    active_group_members,
    add_client_to_group,
    client_balances_by_group,
    combined_client_balance,
    create_client_profile,
    create_group,
    create_user_account,
    reset_password,
    verify_password,
)
from app.services.allocation import allocate_daily_profit_loss
from app.services.daily_close import finalize_daily_close
from app.services.ledger import (
    approve_withdrawal,
    available_balance,
    client_balance,
    complete_internal_transfer,
    external_commission_payable,
    make_deposit_effective,
    make_expense_effective,
    make_withdrawal_effective,
    mark_withdrawal_paid,
    record_commission_withdrawal,
    record_daily_allocation_entries,
    record_deposit_pending,
    record_equal_expense_pending,
    record_internal_transfer_pending,
    record_manual_adjustment,
    reject_withdrawal,
    request_withdrawal,
)
from app.services.mt5_accounts import (
    activate_mt5_account,
    client_visible_mt5_account,
    create_mt5_account,
    create_mt5_snapshot,
    latest_group_closed_balance,
    latest_snapshots_by_account,
)
from app.services.mt5_terminal import MT5TerminalError, sync_account_snapshot
from app.services.import_wizard import review_import_classifications


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreateRequest(BaseModel):
    username: str
    password: str = Field(min_length=6)
    role: UserRole = UserRole.ADMIN


class FirstAdminCreateRequest(BaseModel):
    username: str
    password: str = Field(min_length=6)


class ClientCreateRequest(BaseModel):
    username: str
    password: str = Field(min_length=6)
    display_name: str
    email: str | None = None
    email_reports_opt_in: bool = False


class GroupCreateRequest(BaseModel):
    name: str
    currency: str = "USD"
    total_rate: Decimal = Decimal("0.34")
    external_rate: Decimal = Decimal("0.15")
    partner_1_rate: Decimal = Decimal("0.095")
    partner_2_rate: Decimal = Decimal("0.095")
    partner_1_client_id: str | None = None
    partner_2_client_id: str | None = None
    display_timezone: str = "Asia/Beirut"


class MembershipCreateRequest(BaseModel):
    client_id: str
    effective_capital: Decimal = Decimal("0")
    role: MemberRole = MemberRole.NORMAL
    joined_on: date | None = None
    effective_from: date | None = None


class DepositPendingRequest(BaseModel):
    client_id: str
    amount: Decimal
    effective_date: date
    description: str | None = None
    created_by_user_id: str | None = None


class WithdrawalRequestRequest(BaseModel):
    client_id: str
    amount: Decimal
    description: str | None = None


class WithdrawalApproveRequest(BaseModel):
    effective_date: date


class WithdrawalRejectRequest(BaseModel):
    reason: str


class ExpensePendingRequest(BaseModel):
    amount: Decimal
    effective_date: date
    description: str


class InternalTransferPendingRequest(BaseModel):
    from_mt5_account_id: str
    amount: Decimal
    description: str


class InternalTransferCompleteRequest(BaseModel):
    to_mt5_account_id: str


class DailyAllocationRequest(BaseModel):
    group_profit_loss: Decimal
    allocation_date: date


class DailyCloseFinalizeRequest(BaseModel):
    broker_server_day: date
    previous_broker_server_day: date
    manual_profit_loss: Decimal | None = None
    override_reason: str | None = None
    created_by_user_id: str | None = None


class CommissionWithdrawalRequest(BaseModel):
    amount: Decimal
    description: str
    client_id: str | None = None


class ManualAdjustmentRequest(BaseModel):
    client_id: str
    amount: Decimal
    reason: str
    created_by_user_id: str | None = None


class ImportMovementRequest(BaseModel):
    movement_id: str | None = None
    amount: Decimal
    occurred_on: date
    comment: str = ""
    mt5_account_id: str | None = None
    currency: str = "USD"


class ImportClassificationRequest(BaseModel):
    movement: ImportMovementRequest
    classification: MovementClassification
    client_id: str | None = None
    description: str | None = None
    effective_date: date | None = None
    to_mt5_account_id: str | None = None
    external_amount: Decimal | None = None
    partner_1_client_id: str | None = None
    partner_1_amount: Decimal | None = None
    partner_2_client_id: str | None = None
    partner_2_amount: Decimal | None = None


class ImportWizardRequest(BaseModel):
    import_mode: ExistingGroupImportMode = ExistingGroupImportMode.PERCENTAGE_IMPORT
    classifications: list[ImportClassificationRequest]


class ClientProfileUpdateRequest(BaseModel):
    email: str | None = None
    email_reports_opt_in: bool = False


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


class PasswordResetRequest(BaseModel):
    identifier: str


class PasswordResetConfirmRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=6)


class AdminPasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=6)


class TwoFactorPreferenceRequest(BaseModel):
    enabled: bool


class AdminViewClientRequest(BaseModel):
    reason: str | None = None


class MT5AccountCreateRequest(BaseModel):
    nickname: str
    broker_name: str
    server: str
    login: str

    # Step 28:
    # The portal must not require MT5 master/trading credentials.
    # This field remains optional only for backward compatibility with old data/API calls.
    sync_password: str | None = None

    investor_login: str
    investor_password: str
    currency: str | None = None
    is_cent_account: bool = True
    status: MT5AccountStatus = MT5AccountStatus.PENDING
    notes: str | None = None

class MT5SnapshotCreateRequest(BaseModel):
    broker_server_time: datetime
    raw_balance: Decimal
    raw_equity: Decimal
    raw_profit: Decimal = Decimal("0")
    raw_margin: Decimal = Decimal("0")
    raw_free_margin: Decimal = Decimal("0")
    broker_server_day: date | None = None


class ActivateAccountRequest(BaseModel):
    live: bool = True


class MT5SyncRequest(BaseModel):
    terminal_path: str | None = None


class APIState:
    def __init__(self, db_path: str | Path = ":memory:", secret_key: str | None = None) -> None:
        self.conn = connect_database(db_path)
        init_db(self.conn)
        self.secret_cipher = SecretCipher(secret_key) if secret_key else None
        self.sessions: dict[str, str] = {}
        self.password_reset_tokens: dict[str, str] = {}


def _money(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.00000001"))
    text = format(rounded, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _decimal(value: Decimal) -> str:
    return _money(value)


def user_to_dict(account: Any) -> dict[str, Any]:
    return {
        "user_id": account.user_id,
        "username": account.username,
        "role": account.role.value,
        "is_active": account.is_active,
        "two_factor_enabled": account.two_factor_enabled,
    }


def client_to_dict(client: Any) -> dict[str, Any]:
    return {
        "client_id": client.client_id,
        "user_id": client.user_id,
        "display_name": client.display_name,
        "email": client.email,
        "email_reports_opt_in": client.email_reports_opt_in,
        "is_active": client.is_active,
    }


def group_to_dict(group: Any) -> dict[str, Any]:
    rule = group.commission_rule
    return {
        "group_id": group.group_id,
        "name": group.name,
        "currency": group.currency,
        "status": group.status.value,
        "use_broker_server_day_close": group.use_broker_server_day_close,
        "display_timezone": group.display_timezone,
        "commission_rule": {
            "total_rate": _decimal(rule.total_rate),
            "external_rate": _decimal(rule.external_rate),
            "partner_1_rate": _decimal(rule.partner_1_rate),
            "partner_2_rate": _decimal(rule.partner_2_rate),
            "partner_1_client_id": rule.partner_1_client_id,
            "partner_2_client_id": rule.partner_2_client_id,
        },
    }


def membership_to_dict(membership: Any) -> dict[str, Any]:
    return {
        "membership_id": membership.membership_id,
        "group_id": membership.group_id,
        "client_id": membership.client_id,
        "display_name": membership.display_name,
        "effective_capital": _decimal(membership.effective_capital),
        "role": membership.role.value,
        "joined_on": membership.joined_on.isoformat() if membership.joined_on else None,
        "effective_from": membership.effective_from.isoformat() if membership.effective_from else None,
        "is_active": membership.is_active,
    }


def ledger_to_dict(entry: Any) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "group_id": entry.group_id,
        "client_id": entry.client_id,
        "mt5_account_id": entry.mt5_account_id,
        "transaction_id": entry.transaction_id,
        "entry_type": entry.entry_type.value,
        "amount": _decimal(entry.amount),
        "currency": entry.currency,
        "description": entry.description,
        "effective_date": entry.effective_date.isoformat() if entry.effective_date else None,
        "created_at": entry.created_at.isoformat(),
        "created_by_user_id": entry.created_by_user_id,
        "metadata": entry.metadata,
    }


def _classification_request_to_decision(item: ImportClassificationRequest) -> MovementClassificationDecision:
    movement = DetectedMoneyMovement(
        movement_id=item.movement.movement_id or secrets.token_urlsafe(10),
        amount=item.movement.amount,
        occurred_on=item.movement.occurred_on,
        comment=item.movement.comment,
        mt5_account_id=item.movement.mt5_account_id,
        currency=item.movement.currency,
    )
    return MovementClassificationDecision(
        movement=movement,
        classification=item.classification,
        client_id=item.client_id,
        description=item.description,
        effective_date=item.effective_date,
        to_mt5_account_id=item.to_mt5_account_id,
        external_amount=item.external_amount,
        partner_1_client_id=item.partner_1_client_id,
        partner_1_amount=item.partner_1_amount,
        partner_2_client_id=item.partner_2_client_id,
        partner_2_amount=item.partner_2_amount,
    )


def import_review_to_dict(review: Any) -> dict[str, Any]:
    return {
        "group_id": review.group_id,
        "import_mode": review.import_mode.value,
        "total_detected": review.total_detected,
        "total_classified_amount": _decimal(review.total_classified_amount),
        "total_ignored_amount": _decimal(review.total_ignored_amount),
        "entry_count": review.entry_count,
        "lines": [
            {
                "movement_id": line.movement_id,
                "classification": line.classification.value,
                "amount": _decimal(line.amount),
                "generated_entry_count": line.generated_entry_count,
                "description": line.description,
            }
            for line in review.lines
        ],
        "ledger_entries": [ledger_to_dict(entry) for entry in review.ledger_entries],
    }


def _client_group_rows(api_state: APIState, client: Any, entries: list[Any], memberships: list[Any]) -> list[dict[str, Any]]:
    """Return one clean dashboard row per group for a client.

    Older local test databases may contain duplicate memberships because early UI
    versions allowed adding the same client to the same group more than once.
    The dashboard should not show duplicate rows; it should summarize by group.
    """

    grouped: dict[str, dict[str, Any]] = {}
    group_order: list[str] = []
    for membership in memberships:
        group = get_group(api_state.conn, membership.group_id)
        group_name = group.name if group else membership.group_id
        if membership.group_id not in grouped:
            group_order.append(membership.group_id)
            grouped[membership.group_id] = {
                "group_id": membership.group_id,
                "group_name": group_name,
                "display_name": membership.display_name,
                "role": membership.role.value,
                "effective_capital": Decimal("0"),
                "membership_count": 0,
            }
        row = grouped[membership.group_id]
        row["effective_capital"] += membership.effective_capital
        row["membership_count"] += 1
        if membership.role.value == "partner":
            row["role"] = "partner"

    output: list[dict[str, Any]] = []
    for group_id in group_order:
        row = grouped[group_id]
        finalized = client_balance(entries, group_id, client.client_id)
        pending_withdrawals = available_balance(entries, group_id, client.client_id) - finalized
        has_finalized_activity = any(
            entry.group_id == group_id
            and entry.client_id == client.client_id
            and entry.entry_type in BALANCE_AFFECTING_ENTRY_TYPES
            for entry in entries
        )
        current_balance = finalized if has_finalized_activity else row["effective_capital"]
        available = current_balance + pending_withdrawals
        output.append({
            "group_id": group_id,
            "group_name": row["group_name"],
            "display_name": row["display_name"],
            "role": row["role"],
            "effective_capital": _decimal(row["effective_capital"]),
            "current_balance": _decimal(current_balance),
            "finalized_balance": _decimal(finalized),
            "finalized_ledger_balance": _decimal(finalized),
            "available_balance": _decimal(available),
            "membership_count": row["membership_count"],
        })
    return output


def _client_dashboard_payload(api_state: APIState, client: Any) -> dict[str, Any]:
    entries = list_ledger_entries(api_state.conn, client_id=client.client_id)
    memberships = list_group_memberships(api_state.conn, client_id=client.client_id)
    group_rows = _client_group_rows(api_state, client, entries, memberships)
    combined = sum((Decimal(row["current_balance"]) for row in group_rows), Decimal("0"))
    available_total = sum((Decimal(row["available_balance"]) for row in group_rows), Decimal("0"))
    return {
        "client": client_to_dict(client),
        "combined_balance": _decimal(combined),
        "available_balance": _decimal(available_total),
        "groups": group_rows,
    }


def _available_for_client_group(api_state: APIState, group_id: str, client_id: str) -> Decimal:
    entries = list_ledger_entries(api_state.conn, group_id=group_id, client_id=client_id)
    memberships = list_group_memberships(api_state.conn, group_id=group_id, client_id=client_id)
    if not memberships:
        return available_balance(entries, group_id, client_id)
    finalized = client_balance(entries, group_id, client_id)
    has_finalized_activity = any(entry.entry_type in BALANCE_AFFECTING_ENTRY_TYPES for entry in entries)
    base = finalized if has_finalized_activity else sum((m.effective_capital for m in memberships), Decimal("0"))
    pending_delta = available_balance(entries, group_id, client_id) - finalized
    return base + pending_delta

def ledger_entries_to_csv(entries: list[Any]) -> str:
    output = io.StringIO()
    fieldnames = [
        "entry_id",
        "transaction_id",
        "group_id",
        "client_id",
        "mt5_account_id",
        "entry_type",
        "amount",
        "currency",
        "description",
        "effective_date",
        "created_at",
        "created_by_user_id",
        "metadata",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for entry in entries:
        row = ledger_to_dict(entry)
        row["metadata"] = str(row.get("metadata") or {})
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    return output.getvalue()


def csv_response(filename: str, entries: list[Any]) -> Response:
    return Response(
        content=ledger_entries_to_csv(entries),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _workflow_item_from_entry(entry: Any, *, clients_by_id: dict[str, str], accounts_by_id: dict[str, str]) -> dict[str, Any]:
    client_name = clients_by_id.get(entry.client_id or "", "") if entry.client_id else ""
    account_name = accounts_by_id.get(entry.mt5_account_id or "", "") if entry.mt5_account_id else ""
    return {
        **ledger_to_dict(entry),
        "absolute_amount": _decimal(abs(entry.amount)),
        "client_name": client_name,
        "mt5_account_name": account_name,
    }


def workflow_items_to_dict(
    *,
    group_id: str,
    entries: list[Any],
    clients: list[Any],
    accounts: list[Any],
    external_payable: Decimal,
) -> dict[str, Any]:
    clients_by_id = {client.client_id: client.display_name for client in clients}
    accounts_by_id = {account.account_id: account.nickname for account in accounts}
    by_transaction: dict[str, list[Any]] = {}
    for entry in entries:
        if entry.group_id == group_id:
            by_transaction.setdefault(entry.transaction_id, []).append(entry)

    pending_deposits: list[dict[str, Any]] = []
    withdrawal_requests: list[dict[str, Any]] = []
    approved_withdrawals: list[dict[str, Any]] = []
    effective_withdrawals: list[dict[str, Any]] = []
    pending_expenses: list[dict[str, Any]] = []
    pending_transfers: list[dict[str, Any]] = []

    withdrawal_types = {
        LedgerEntryType.WITHDRAWAL_REQUESTED,
        LedgerEntryType.WITHDRAWAL_APPROVED,
        LedgerEntryType.WITHDRAWAL_REJECTED,
        LedgerEntryType.WITHDRAWAL_EFFECTIVE,
        LedgerEntryType.WITHDRAWAL_PAID,
    }

    for transaction_id, tx_entries in by_transaction.items():
        tx_entries = sorted(tx_entries, key=lambda entry: (entry.created_at, entry.entry_id))
        types = {entry.entry_type for entry in tx_entries}

        if LedgerEntryType.DEPOSIT_PENDING in types and LedgerEntryType.DEPOSIT_EFFECTIVE not in types:
            pending = next(entry for entry in tx_entries if entry.entry_type == LedgerEntryType.DEPOSIT_PENDING)
            pending_deposits.append(_workflow_item_from_entry(pending, clients_by_id=clients_by_id, accounts_by_id=accounts_by_id))

        if types & withdrawal_types:
            latest_withdrawal = [entry for entry in tx_entries if entry.entry_type in withdrawal_types][-1]
            item = _workflow_item_from_entry(latest_withdrawal, clients_by_id=clients_by_id, accounts_by_id=accounts_by_id)
            if latest_withdrawal.entry_type == LedgerEntryType.WITHDRAWAL_REQUESTED:
                withdrawal_requests.append(item)
            elif latest_withdrawal.entry_type == LedgerEntryType.WITHDRAWAL_APPROVED:
                approved_withdrawals.append(item)
            elif latest_withdrawal.entry_type == LedgerEntryType.WITHDRAWAL_EFFECTIVE:
                effective_withdrawals.append(item)

        if LedgerEntryType.EXPENSE_PENDING in types and LedgerEntryType.EXPENSE_EFFECTIVE not in types:
            pending = [entry for entry in tx_entries if entry.entry_type == LedgerEntryType.EXPENSE_PENDING]
            total = sum((abs(entry.amount) for entry in pending), Decimal("0"))
            first = pending[0]
            pending_expenses.append({
                "transaction_id": transaction_id,
                "entry_ids": [entry.entry_id for entry in pending],
                "group_id": group_id,
                "member_count": len(pending),
                "total_amount": _decimal(total),
                "amount_per_member": _decimal(abs(first.amount)) if pending else "0",
                "description": first.description,
                "effective_date": first.effective_date.isoformat() if first.effective_date else None,
                "created_at": first.created_at.isoformat(),
            })

        if LedgerEntryType.TRANSFER_PENDING in types and LedgerEntryType.TRANSFER_COMPLETED not in types:
            pending = next(entry for entry in tx_entries if entry.entry_type == LedgerEntryType.TRANSFER_PENDING)
            pending_transfers.append(_workflow_item_from_entry(pending, clients_by_id=clients_by_id, accounts_by_id=accounts_by_id))

    return {
        "group_id": group_id,
        "pending_deposits": pending_deposits,
        "withdrawal_requests": withdrawal_requests,
        "approved_withdrawals": approved_withdrawals,
        "effective_withdrawals": effective_withdrawals,
        "pending_expenses": pending_expenses,
        "pending_transfers": pending_transfers,
        "external_commission_payable": _decimal(external_payable),
    }


def mt5_account_to_admin_dict(account: Any) -> dict[str, Any]:
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
        "display_divisor": _decimal(account.display_divisor),
        "status": account.status.value,
        "notes": account.notes,
        "created_at": account.created_at.isoformat(),

        # Step 28 read-only safety metadata.
        "read_only_mode": True,
        "credential_mode": "investor_view_only",
        "master_password_required": False,
        "notice": "This MT5 account is connected with investor/view-only access. The portal cannot trade or modify the account.",
    }

def snapshot_to_dict(snapshot: Any) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "account_id": snapshot.account_id,
        "group_id": snapshot.group_id,
        "broker_server_time": snapshot.broker_server_time.isoformat(),
        "broker_server_day": snapshot.broker_server_day.isoformat(),
        "raw_balance": _decimal(snapshot.raw_balance),
        "raw_equity": _decimal(snapshot.raw_equity),
        "raw_profit": _decimal(snapshot.raw_profit),
        "raw_margin": _decimal(snapshot.raw_margin),
        "raw_free_margin": _decimal(snapshot.raw_free_margin),
        "display_balance": _decimal(snapshot.display_balance),
        "display_equity": _decimal(snapshot.display_equity),
        "display_profit": _decimal(snapshot.display_profit),
        "currency": snapshot.currency,
    }


def daily_close_to_dict(close: Any) -> dict[str, Any]:
    return {
        "close_id": close.close_id,
        "group_id": close.group_id,
        "broker_server_day": close.broker_server_day.isoformat(),
        "opening_closed_balance": _decimal(close.opening_closed_balance),
        "closing_closed_balance": _decimal(close.closing_closed_balance),
        "deposits_effective": _decimal(close.deposits_effective),
        "withdrawals_effective": _decimal(close.withdrawals_effective),
        "expenses_effective": _decimal(close.expenses_effective),
        "pending_internal_transfers": _decimal(close.pending_internal_transfers),
        "trading_profit_loss": _decimal(close.trading_profit_loss),
        "status": close.status.value,
        "finalized_at": close.finalized_at.isoformat(),
        "created_by_user_id": close.created_by_user_id,
        "override_reason": close.override_reason,
    }


def create_app(db_path: str | Path = ":memory:", secret_key: str | None = None) -> FastAPI:
    app = FastAPI(title="MT5 Client Portal Backend", version="0.1.0")
    app.state.api_state = APIState(db_path=db_path, secret_key=secret_key)

    web_dir = Path(__file__).resolve().parents[1] / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=web_dir), name="static")

        @app.get("/", include_in_schema=False)
        def web_index() -> FileResponse:
            return FileResponse(web_dir / "index.html")

    def state() -> APIState:
        return app.state.api_state

    def current_user(
        authorization: str | None = Header(default=None),
        api_state: APIState = Depends(state),
    ) -> Any:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
        token = authorization.split(" ", 1)[1].strip()
        user_id = api_state.sessions.get(token)
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session")
        account = get_user_account(api_state.conn, user_id)
        if account is None or not account.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or inactive user")
        return account

    def require_admin(account: Any = Depends(current_user)) -> Any:
        if account.role != UserRole.ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
        return account

    def require_client(account: Any = Depends(current_user)) -> Any:
        if account.role != UserRole.CLIENT:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client access required")
        return account

    def client_profile_for_user(api_state: APIState, user_id: str) -> Any:
        for client in list_client_profiles(api_state.conn):
            if client.user_id == user_id:
                return client
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client profile not found")

    def ensure_client_in_group(api_state: APIState, client_id: str, group_id: str) -> None:
        memberships = list_group_memberships(api_state.conn, group_id=group_id, client_id=client_id)
        if not memberships:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client is not a member of this group")

    def validate_optional_email(email: str | None) -> str | None:
        cleaned = email.strip().lower() if email else None
        if not cleaned:
            return None
        if "@" not in cleaned or "." not in cleaned.rsplit("@", 1)[-1]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Enter a valid email address")
        return cleaned

    def find_client_by_identifier(api_state: APIState, identifier: str) -> tuple[Any, Any] | tuple[None, None]:
        cleaned = identifier.strip().lower()
        if not cleaned:
            return None, None
        account = get_user_account_by_username(api_state.conn, cleaned)
        if account and account.role == UserRole.CLIENT:
            return account, client_profile_for_user(api_state, account.user_id)
        for client in list_client_profiles(api_state.conn):
            if client.email and client.email.lower() == cleaned:
                linked = get_user_account(api_state.conn, client.user_id)
                if linked and linked.role == UserRole.CLIENT:
                    return linked, client
        return None, None

    def record_audit_event(
        api_state: APIState,
        *,
        event_type: str,
        description: str,
        actor_user_id: str | None = None,
        target_user_id: str | None = None,
        target_client_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        save_audit_event(
            api_state.conn,
            event_id=secrets.token_urlsafe(18),
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            target_client_id=target_client_id,
            event_type=event_type,
            description=description,
            metadata=metadata or {},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/auth/login")
    def login(payload: LoginRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        account = get_user_account_by_username(api_state.conn, payload.username)
        if account is None or not account.is_active or not verify_password(account, payload.password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
        token = secrets.token_urlsafe(32)
        api_state.sessions[token] = account.user_id
        return {"authenticated": True, "access_token": token, "token_type": "bearer", "user": user_to_dict(account)}

    @app.post("/api/auth/logout")
    def logout(
        authorization: str | None = Header(default=None),
        api_state: APIState = Depends(state),
    ) -> dict[str, bool]:
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
            api_state.sessions.pop(token, None)
        return {"logged_out": True}

    @app.get("/api/session/me")
    def session_me(
        account: Any = Depends(current_user),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"user": user_to_dict(account)}
        if account.role == UserRole.CLIENT:
            payload["client"] = client_to_dict(client_profile_for_user(api_state, account.user_id))
        return payload

    @app.get("/api/system/storage")
    def system_storage(
        account: Any = Depends(require_admin),
    ) -> dict[str, Any]:
        return storage_status()

    @app.post("/api/auth/password-reset/request")
    def password_reset_request(payload: PasswordResetRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        account, client = find_client_by_identifier(api_state, payload.identifier)
        token: str | None = None
        can_email = bool(account and client and client.email)
        if account and client and client.email:
            token = secrets.token_urlsafe(32)
            api_state.password_reset_tokens[token] = account.user_id
            record_audit_event(
                api_state,
                event_type="password_reset_requested",
                description="Client password reset requested",
                target_user_id=account.user_id,
                target_client_id=client.client_id,
            )
        return {
            "sent": can_email,
            "message": "If an email exists for this account, a reset link can be sent.",
            "development_only_reset_token": token,
        }

    @app.post("/api/auth/password-reset/confirm")
    def password_reset_confirm(payload: PasswordResetConfirmRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        user_id = api_state.password_reset_tokens.pop(payload.token, None)
        if not user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")
        account = get_user_account(api_state.conn, user_id)
        if account is None or account.role != UserRole.CLIENT:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client account not found")
        updated = reset_password(account, payload.new_password)
        save_user_account(api_state.conn, updated)
        client = client_profile_for_user(api_state, user_id)
        record_audit_event(
            api_state,
            event_type="client_password_reset_by_email",
            description="Client reset password using email reset flow",
            target_user_id=updated.user_id,
            target_client_id=client.client_id,
        )
        return {"password_reset": True}

    @app.post("/api/setup/admin", status_code=status.HTTP_201_CREATED)
    def setup_first_admin(payload: FirstAdminCreateRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        existing_admins = [account for account in list_user_accounts(api_state.conn) if account.role == UserRole.ADMIN]
        if existing_admins:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="An admin already exists")
        try:
            account = create_user_account(
                username=payload.username,
                password=payload.password,
                role=UserRole.ADMIN,
                existing_accounts=list_user_accounts(api_state.conn),
            )
            save_user_account(api_state.conn, account)
            return user_to_dict(account)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/users", status_code=status.HTTP_201_CREATED)
    def create_user(payload: UserCreateRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        try:
            account = create_user_account(
                username=payload.username,
                password=payload.password,
                role=payload.role,
                existing_accounts=list_user_accounts(api_state.conn),
            )
            save_user_account(api_state.conn, account)
            return user_to_dict(account)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/clients", status_code=status.HTTP_201_CREATED)
    def create_client(payload: ClientCreateRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        try:
            account = create_user_account(
                username=payload.username,
                password=payload.password,
                role=UserRole.CLIENT,
                existing_accounts=list_user_accounts(api_state.conn),
            )
            client = create_client_profile(
                display_name=payload.display_name,
                user_account=account,
                email=payload.email,
                email_reports_opt_in=payload.email_reports_opt_in,
            )
            save_user_account(api_state.conn, account)
            save_client_profile(api_state.conn, client)
            return {"user": user_to_dict(account), "client": client_to_dict(client)}
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/api/clients")
    def list_clients(api_state: APIState = Depends(state)) -> list[dict[str, Any]]:
        return [client_to_dict(client) for client in list_client_profiles(api_state.conn)]

    @app.get("/api/clients/{client_id}/dashboard")
    def client_dashboard(client_id: str, api_state: APIState = Depends(state)) -> dict[str, Any]:
        client = get_client_profile(api_state.conn, client_id)
        if client is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        return _client_dashboard_payload(api_state, client)

    @app.post("/api/groups", status_code=status.HTTP_201_CREATED)
    def api_create_group(payload: GroupCreateRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        try:
            rule = CommissionRule(
                total_rate=payload.total_rate,
                external_rate=payload.external_rate,
                partner_1_rate=payload.partner_1_rate,
                partner_2_rate=payload.partner_2_rate,
                partner_1_client_id=payload.partner_1_client_id,
                partner_2_client_id=payload.partner_2_client_id,
            )
            group = create_group(
                name=payload.name,
                currency=payload.currency,
                commission_rule=rule,
                display_timezone=payload.display_timezone,
            )
            save_group(api_state.conn, group)
            return group_to_dict(group)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/api/groups")
    def api_list_groups(api_state: APIState = Depends(state)) -> list[dict[str, Any]]:
        return [group_to_dict(group) for group in list_groups(api_state.conn)]

    @app.post("/api/groups/{group_id}/members", status_code=status.HTTP_201_CREATED)
    def api_add_member(group_id: str, payload: MembershipCreateRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        group = get_group(api_state.conn, group_id)
        if group is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        client = get_client_profile(api_state.conn, payload.client_id)
        if client is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        if list_group_memberships(api_state.conn, group_id=group_id, client_id=payload.client_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Client is already a member of this group")
        try:
            membership = add_client_to_group(
                group=group,
                client=client,
                effective_capital=payload.effective_capital,
                role=payload.role,
                joined_on=payload.joined_on,
                effective_from=payload.effective_from,
            )
            save_group_membership(api_state.conn, membership)
            return membership_to_dict(membership)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/api/groups/{group_id}/members")
    def api_list_members(group_id: str, api_state: APIState = Depends(state)) -> list[dict[str, Any]]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        return [membership_to_dict(membership) for membership in list_group_memberships(api_state.conn, group_id=group_id)]

    @app.post("/api/groups/{group_id}/deposits/pending", status_code=status.HTTP_201_CREATED)
    def api_record_deposit_pending(group_id: str, payload: DepositPendingRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        if get_client_profile(api_state.conn, payload.client_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        try:
            entry = record_deposit_pending(
                group_id=group_id,
                client_id=payload.client_id,
                amount=payload.amount,
                effective_date=payload.effective_date,
                created_by_user_id=payload.created_by_user_id,
                description=payload.description,
            )
            save_ledger_entry(api_state.conn, entry)
            return ledger_to_dict(entry)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/ledger/{entry_id}/deposit/effective")
    def api_make_deposit_effective(entry_id: str, api_state: APIState = Depends(state)) -> dict[str, Any]:
        entry = get_ledger_entry(api_state.conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found")
        try:
            effective = make_deposit_effective(entry)
            save_ledger_entry(api_state.conn, effective)
            return ledger_to_dict(effective)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/groups/{group_id}/withdrawals/request", status_code=status.HTTP_201_CREATED)
    def api_request_withdrawal(group_id: str, payload: WithdrawalRequestRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        if get_client_profile(api_state.conn, payload.client_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        entries = list_ledger_entries(api_state.conn, group_id=group_id)
        try:
            entry = request_withdrawal(
                group_id=group_id,
                client_id=payload.client_id,
                amount=payload.amount,
                available_balance_amount=available_balance(entries, group_id, payload.client_id),
                description=payload.description,
            )
            save_ledger_entry(api_state.conn, entry)
            return ledger_to_dict(entry)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/ledger/{entry_id}/withdrawal/approve")
    def api_approve_withdrawal(entry_id: str, payload: WithdrawalApproveRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        entry = get_ledger_entry(api_state.conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found")
        try:
            approved = approve_withdrawal(entry, effective_date=payload.effective_date)
            save_ledger_entry(api_state.conn, approved)
            return ledger_to_dict(approved)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/ledger/{entry_id}/withdrawal/reject")
    def api_reject_withdrawal(entry_id: str, payload: WithdrawalRejectRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        entry = get_ledger_entry(api_state.conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found")
        try:
            rejected = reject_withdrawal(entry, reason=payload.reason)
            save_ledger_entry(api_state.conn, rejected)
            return ledger_to_dict(rejected)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/ledger/{entry_id}/withdrawal/effective")
    def api_make_withdrawal_effective(entry_id: str, api_state: APIState = Depends(state)) -> dict[str, Any]:
        entry = get_ledger_entry(api_state.conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found")
        try:
            effective = make_withdrawal_effective(entry)
            save_ledger_entry(api_state.conn, effective)
            return ledger_to_dict(effective)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/ledger/{entry_id}/withdrawal/paid")
    def api_mark_withdrawal_paid(entry_id: str, api_state: APIState = Depends(state)) -> dict[str, Any]:
        entry = get_ledger_entry(api_state.conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found")
        try:
            paid = mark_withdrawal_paid(entry)
            save_ledger_entry(api_state.conn, paid)
            return ledger_to_dict(paid)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/groups/{group_id}/expenses/equal/pending", status_code=status.HTTP_201_CREATED)
    def api_record_equal_expense_pending(group_id: str, payload: ExpensePendingRequest, api_state: APIState = Depends(state)) -> list[dict[str, Any]]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        members = active_group_members(list_group_memberships(api_state.conn, group_id=group_id), group_id)
        try:
            entries = record_equal_expense_pending(
                group_id=group_id,
                members=members,
                amount=payload.amount,
                effective_date=payload.effective_date,
                description=payload.description,
            )
            for entry in entries:
                save_ledger_entry(api_state.conn, entry)
            return [ledger_to_dict(entry) for entry in entries]
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/ledger/{entry_id}/expense/effective")
    def api_make_expense_effective(entry_id: str, api_state: APIState = Depends(state)) -> dict[str, Any]:
        entry = get_ledger_entry(api_state.conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found")
        try:
            effective = make_expense_effective(entry)
            save_ledger_entry(api_state.conn, effective)
            return ledger_to_dict(effective)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/groups/{group_id}/internal-transfers/pending", status_code=status.HTTP_201_CREATED)
    def api_record_internal_transfer_pending(group_id: str, payload: InternalTransferPendingRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        if get_mt5_account(api_state.conn, payload.from_mt5_account_id, secret_cipher=api_state.secret_cipher) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MT5 account not found")
        try:
            entry = record_internal_transfer_pending(
                group_id=group_id,
                from_mt5_account_id=payload.from_mt5_account_id,
                amount=payload.amount,
                description=payload.description,
            )
            save_ledger_entry(api_state.conn, entry)
            return ledger_to_dict(entry)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/ledger/{entry_id}/internal-transfer/complete")
    def api_complete_internal_transfer(entry_id: str, payload: InternalTransferCompleteRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        entry = get_ledger_entry(api_state.conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found")
        if get_mt5_account(api_state.conn, payload.to_mt5_account_id, secret_cipher=api_state.secret_cipher) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MT5 account not found")
        try:
            completed = complete_internal_transfer(entry, to_mt5_account_id=payload.to_mt5_account_id)
            save_ledger_entry(api_state.conn, completed)
            return ledger_to_dict(completed)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/groups/{group_id}/daily-allocations", status_code=status.HTTP_201_CREATED)
    def api_record_daily_allocation(group_id: str, payload: DailyAllocationRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        group = get_group(api_state.conn, group_id)
        if group is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        members = active_group_members(list_group_memberships(api_state.conn, group_id=group_id), group_id)
        try:
            result = allocate_daily_profit_loss(
                members=members,
                group_profit_loss=payload.group_profit_loss,
                commission_rule=group.commission_rule,
            )
            entries = record_daily_allocation_entries(
                group_id=group_id,
                allocation_result=result,
                allocation_date=payload.allocation_date,
            )
            for entry in entries:
                save_ledger_entry(api_state.conn, entry)
            return {
                "group_id": group_id,
                "group_profit_loss": _decimal(result.group_profit_loss),
                "total_commission_collected": _decimal(result.total_commission_collected),
                "external_commission_earned": _decimal(result.external_commission_earned),
                "entries": [ledger_to_dict(entry) for entry in entries],
            }
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/groups/{group_id}/daily-close/finalize", status_code=status.HTTP_201_CREATED)
    def api_finalize_daily_close(group_id: str, payload: DailyCloseFinalizeRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        group = get_group(api_state.conn, group_id)
        if group is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        if get_daily_group_close(api_state.conn, group_id, payload.broker_server_day) is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Daily close already finalized for this broker server day")
        accounts = list_mt5_accounts(api_state.conn, group_id=group_id, secret_cipher=api_state.secret_cipher)
        snapshots = list_mt5_snapshots(api_state.conn, group_id=group_id)
        memberships = list_group_memberships(api_state.conn, group_id=group_id)
        entries = list_ledger_entries(api_state.conn, group_id=group_id)
        try:
            result = finalize_daily_close(
                group=group,
                broker_server_day=payload.broker_server_day,
                previous_broker_server_day=payload.previous_broker_server_day,
                accounts=accounts,
                snapshots=snapshots,
                memberships=memberships,
                existing_entries=entries,
                manual_profit_loss=payload.manual_profit_loss,
                override_reason=payload.override_reason,
                created_by_user_id=payload.created_by_user_id,
            )
            save_daily_group_close(api_state.conn, result.close)
            for entry in result.ledger_entries:
                save_ledger_entry(api_state.conn, entry)
            return {
                "close": daily_close_to_dict(result.close),
                "allocation": {
                    "group_profit_loss": _decimal(result.allocation.group_profit_loss),
                    "total_commission_collected": _decimal(result.allocation.total_commission_collected),
                    "external_commission_earned": _decimal(result.allocation.external_commission_earned),
                },
                "entries": [ledger_to_dict(entry) for entry in result.ledger_entries],
            }
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/api/groups/{group_id}/daily-closes")
    def api_list_daily_closes(group_id: str, api_state: APIState = Depends(state)) -> list[dict[str, Any]]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        return [daily_close_to_dict(close) for close in list_daily_group_closes(api_state.conn, group_id=group_id)]

    @app.get("/api/groups/{group_id}/external-commission-payable")
    def api_external_commission_payable(group_id: str, api_state: APIState = Depends(state)) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        entries = list_ledger_entries(api_state.conn, group_id=group_id)
        return {"group_id": group_id, "payable": _decimal(external_commission_payable(entries, group_id))}

    @app.post("/api/groups/{group_id}/commissions/withdrawals", status_code=status.HTTP_201_CREATED)
    def api_record_commission_withdrawal(group_id: str, payload: CommissionWithdrawalRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        if payload.client_id is not None and get_client_profile(api_state.conn, payload.client_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        try:
            entry = record_commission_withdrawal(
                group_id=group_id,
                amount=payload.amount,
                client_id=payload.client_id,
                description=payload.description,
            )
            save_ledger_entry(api_state.conn, entry)
            return ledger_to_dict(entry)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/groups/{group_id}/manual-adjustments", status_code=status.HTTP_201_CREATED)
    def api_record_manual_adjustment(group_id: str, payload: ManualAdjustmentRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        if get_client_profile(api_state.conn, payload.client_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        if get_user_account(api_state.conn, payload.created_by_user_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin user not found")
        try:
            entry = record_manual_adjustment(
                group_id=group_id,
                client_id=payload.client_id,
                amount=payload.amount,
                reason=payload.reason,
                created_by_user_id=payload.created_by_user_id,
            )
            save_ledger_entry(api_state.conn, entry)
            return ledger_to_dict(entry)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/api/groups/{group_id}/ledger")
    def api_group_ledger(group_id: str, api_state: APIState = Depends(state)) -> list[dict[str, Any]]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        return [ledger_to_dict(entry) for entry in list_ledger_entries(api_state.conn, group_id=group_id)]

    @app.get("/api/groups/{group_id}/balances")
    def api_group_balances(group_id: str, api_state: APIState = Depends(state)) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        entries = list_ledger_entries(api_state.conn, group_id=group_id)
        memberships = list_group_memberships(api_state.conn, group_id=group_id)
        return {
            "group_id": group_id,
            "members": [
                {
                    "client_id": membership.client_id,
                    "display_name": membership.display_name,
                    "role": membership.role.value,
                    "finalized_balance": _decimal(client_balance(entries, group_id, membership.client_id)),
                    "available_balance": _decimal(available_balance(entries, group_id, membership.client_id)),
                }
                for membership in memberships
            ],
        }

    @app.post("/api/groups/{group_id}/mt5-accounts", status_code=status.HTTP_201_CREATED)
    def api_create_mt5_account(
        group_id: str,
        payload: MT5AccountCreateRequest,
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        group = get_group(api_state.conn, group_id)
        if group is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

        try:
            account = create_mt5_account(
                group=group,
                nickname=payload.nickname,
                broker_name=payload.broker_name,
                server=payload.server,
                login=payload.login,
                sync_password=payload.sync_password,
                investor_login=payload.investor_login,
                investor_password=payload.investor_password,
                currency=payload.currency,
                is_cent_account=payload.is_cent_account,
                status=payload.status,
                notes=payload.notes,
            )
            save_mt5_account(api_state.conn, account, secret_cipher=api_state.secret_cipher)
            return mt5_account_to_admin_dict(account)

        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
            
    @app.get("/api/groups/{group_id}/mt5-accounts")
    def api_list_mt5_accounts(group_id: str, api_state: APIState = Depends(state)) -> list[dict[str, Any]]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        accounts = list_mt5_accounts(api_state.conn, group_id=group_id, secret_cipher=api_state.secret_cipher)
        return [mt5_account_to_admin_dict(account) for account in accounts]

    @app.post("/api/mt5-accounts/{account_id}/activate")
    def api_activate_mt5_account(account_id: str, payload: ActivateAccountRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        account = get_mt5_account(api_state.conn, account_id, secret_cipher=api_state.secret_cipher)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MT5 account not found")
        updated = activate_mt5_account(account) if payload.live else account
        save_mt5_account(api_state.conn, updated, secret_cipher=api_state.secret_cipher)
        return mt5_account_to_admin_dict(updated)

    @app.post("/api/mt5-accounts/{account_id}/snapshots", status_code=status.HTTP_201_CREATED)
    def api_create_mt5_snapshot(account_id: str, payload: MT5SnapshotCreateRequest, api_state: APIState = Depends(state)) -> dict[str, Any]:
        account = get_mt5_account(api_state.conn, account_id, secret_cipher=api_state.secret_cipher)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MT5 account not found")
        try:
            snapshot = create_mt5_snapshot(
                account=account,
                broker_server_time=payload.broker_server_time,
                raw_balance=payload.raw_balance,
                raw_equity=payload.raw_equity,
                raw_profit=payload.raw_profit,
                raw_margin=payload.raw_margin,
                raw_free_margin=payload.raw_free_margin,
                broker_server_day=payload.broker_server_day,
            )
            save_mt5_snapshot(api_state.conn, snapshot)
            return snapshot_to_dict(snapshot)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/mt5-accounts/{account_id}/sync", status_code=status.HTTP_201_CREATED)
    def api_sync_mt5_account(account_id: str, payload: MT5SyncRequest | None = None, api_state: APIState = Depends(state)) -> dict[str, Any]:
        account = get_mt5_account(api_state.conn, account_id, secret_cipher=api_state.secret_cipher)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MT5 account not found")
        try:
            result = sync_account_snapshot(
                account,
                terminal_path=payload.terminal_path if payload else None,
            )
            save_mt5_snapshot(api_state.conn, result.snapshot)
            return {
                "account_id": result.account_id,
                "group_id": result.group_id,
                "snapshot": snapshot_to_dict(result.snapshot),
            }
        except MT5TerminalError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/api/groups/{group_id}/mt5-client-view")
    def api_client_visible_mt5_accounts(group_id: str, api_state: APIState = Depends(state)) -> list[dict[str, str]]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        accounts = list_mt5_accounts(api_state.conn, group_id=group_id, secret_cipher=api_state.secret_cipher)
        snapshots = latest_snapshots_by_account(list_mt5_snapshots(api_state.conn, group_id=group_id))
        return [client_visible_mt5_account(account, snapshots.get(account.account_id)) for account in accounts]

    @app.get("/api/groups/{group_id}/mt5-closed-balance")
    def api_group_mt5_closed_balance(group_id: str, api_state: APIState = Depends(state)) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        accounts = list_mt5_accounts(api_state.conn, group_id=group_id, secret_cipher=api_state.secret_cipher)
        snapshots = list_mt5_snapshots(api_state.conn, group_id=group_id)
        return {
            "group_id": group_id,
            "closed_balance": _decimal(latest_group_closed_balance(accounts=accounts, snapshots=snapshots, group_id=group_id)),
        }

    # Protected portal routes used by the real admin/client web UI.
    # The older /api/... routes above remain available for local development and automated tests.

    @app.get("/api/admin/clients")
    def admin_list_clients(
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> list[dict[str, Any]]:
        return [client_to_dict(client) for client in list_client_profiles(api_state.conn)]

    @app.post("/api/admin/clients", status_code=status.HTTP_201_CREATED)
    def admin_create_client(
        payload: ClientCreateRequest,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        try:
            account = create_user_account(
                username=payload.username,
                password=payload.password,
                role=UserRole.CLIENT,
                existing_accounts=list_user_accounts(api_state.conn),
            )
            client = create_client_profile(
                display_name=payload.display_name,
                user_account=account,
                email=payload.email,
                email_reports_opt_in=payload.email_reports_opt_in,
            )
            save_user_account(api_state.conn, account)
            save_client_profile(api_state.conn, client)
            return {"user": user_to_dict(account), "client": client_to_dict(client)}
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/api/admin/clients/{client_id}/dashboard")
    def admin_view_client_dashboard(
        client_id: str,
        reason: str | None = None,
        admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        client = get_client_profile(api_state.conn, client_id)
        if client is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        target_account = get_user_account(api_state.conn, client.user_id)
        record_audit_event(
            api_state,
            event_type="admin_viewed_client_dashboard",
            description="Admin viewed client dashboard",
            actor_user_id=admin.user_id,
            target_user_id=client.user_id,
            target_client_id=client.client_id,
            metadata={"reason": reason or ""},
        )
        return {
            **_client_dashboard_payload(api_state, client),
            "viewed_by_admin": user_to_dict(admin),
            "client_user": user_to_dict(target_account) if target_account else None,
        }

    @app.post("/api/admin/clients/{client_id}/password-reset")
    def admin_reset_client_password(
        client_id: str,
        payload: AdminPasswordResetRequest,
        admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        client = get_client_profile(api_state.conn, client_id)
        if client is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        account = get_user_account(api_state.conn, client.user_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client user account not found")
        updated = reset_password(account, payload.new_password)
        save_user_account(api_state.conn, updated)
        record_audit_event(
            api_state,
            event_type="admin_reset_client_password",
            description="Admin reset client password",
            actor_user_id=admin.user_id,
            target_user_id=client.user_id,
            target_client_id=client.client_id,
        )
        return {"password_reset": True, "client": client_to_dict(client)}

    @app.post("/api/admin/clients/{client_id}/2fa/reset")
    def admin_reset_client_two_factor(
        client_id: str,
        admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        client = get_client_profile(api_state.conn, client_id)
        if client is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        account = get_user_account(api_state.conn, client.user_id)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client user account not found")
        updated = replace(account, two_factor_enabled=False)
        save_user_account(api_state.conn, updated)
        record_audit_event(
            api_state,
            event_type="admin_reset_client_2fa",
            description="Admin disabled/reset client 2FA",
            actor_user_id=admin.user_id,
            target_user_id=client.user_id,
            target_client_id=client.client_id,
        )
        return {"two_factor_enabled": False, "client": client_to_dict(client)}

    @app.get("/api/admin/audit-events")
    def admin_audit_events(
        target_client_id: str | None = None,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> list[dict[str, Any]]:
        return list_audit_events(api_state.conn, target_client_id=target_client_id)

    @app.get("/api/admin/groups")
    def admin_list_groups(
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> list[dict[str, Any]]:
        return [group_to_dict(group) for group in list_groups(api_state.conn)]

    @app.post("/api/admin/groups", status_code=status.HTTP_201_CREATED)
    def admin_create_group(
        payload: GroupCreateRequest,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        try:
            rule = CommissionRule(
                total_rate=payload.total_rate,
                external_rate=payload.external_rate,
                partner_1_rate=payload.partner_1_rate,
                partner_2_rate=payload.partner_2_rate,
                partner_1_client_id=payload.partner_1_client_id,
                partner_2_client_id=payload.partner_2_client_id,
            )
            group = create_group(
                name=payload.name,
                currency=payload.currency,
                commission_rule=rule,
                display_timezone=payload.display_timezone,
            )
            save_group(api_state.conn, group)
            return group_to_dict(group)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/admin/groups/{group_id}/members", status_code=status.HTTP_201_CREATED)
    def admin_add_group_member(
        group_id: str,
        payload: MembershipCreateRequest,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        group = get_group(api_state.conn, group_id)
        client = get_client_profile(api_state.conn, payload.client_id)
        if group is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        if client is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        if list_group_memberships(api_state.conn, group_id=group_id, client_id=payload.client_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Client is already a member of this group")
        try:
            membership = add_client_to_group(
                group=group,
                client=client,
                effective_capital=payload.effective_capital,
                role=payload.role,
                joined_on=payload.joined_on,
                effective_from=payload.effective_from,
            )
            save_group_membership(api_state.conn, membership)
            return membership_to_dict(membership)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/api/admin/groups/{group_id}/members")
    def admin_list_group_members(
        group_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> list[dict[str, Any]]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        return [membership_to_dict(membership) for membership in list_group_memberships(api_state.conn, group_id=group_id)]

    @app.post("/api/admin/groups/{group_id}/deposits/pending", status_code=status.HTTP_201_CREATED)
    def admin_record_deposit_pending(
        group_id: str,
        payload: DepositPendingRequest,
        admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        try:
            entry = record_deposit_pending(
                group_id=group_id,
                client_id=payload.client_id,
                amount=payload.amount,
                effective_date=payload.effective_date,
                description=payload.description or "Deposit pending",
                created_by_user_id=payload.created_by_user_id or admin.user_id,
            )
            save_ledger_entry(api_state.conn, entry)
            return ledger_to_dict(entry)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/admin/ledger/{entry_id}/deposit/effective")
    def admin_make_deposit_effective(
        entry_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        entry = get_ledger_entry(api_state.conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found")
        try:
            effective = make_deposit_effective(entry)
            save_ledger_entry(api_state.conn, effective)
            return ledger_to_dict(effective)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/api/admin/groups/{group_id}/balances")
    def admin_group_balances(
        group_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        entries = list_ledger_entries(api_state.conn, group_id=group_id)
        memberships = list_group_memberships(api_state.conn, group_id=group_id)
        return {
            "group_id": group_id,
            "members": [
                {
                    **membership_to_dict(membership),
                    "finalized_balance": _decimal(client_balance(entries, group_id, membership.client_id)),
                    "available_balance": _decimal(available_balance(entries, group_id, membership.client_id)),
                }
                for membership in memberships
            ],
        }

    @app.post("/api/admin/groups/{group_id}/mt5-accounts", status_code=status.HTTP_201_CREATED)
    def admin_create_mt5_account(
        group_id: str,
        payload: MT5AccountCreateRequest,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        group = get_group(api_state.conn, group_id)
        if group is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        try:
            account = create_mt5_account(
                group=group,
                nickname=payload.nickname,
                broker_name=payload.broker_name,
                server=payload.server,
                login=payload.login,
            
                # Step 28:
                # Optional legacy compatibility only.
                # New UI should not ask for this.
                sync_password=payload.sync_password,
            
                investor_login=payload.investor_login,
                investor_password=payload.investor_password,
                currency=payload.currency,
                is_cent_account=payload.is_cent_account,
                status=payload.status,
                notes=payload.notes,
            )
            save_mt5_account(api_state.conn, account, secret_cipher=api_state.secret_cipher)
            return mt5_account_to_admin_dict(account)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/api/admin/groups/{group_id}/mt5-accounts")
    def admin_list_mt5_accounts(
        group_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> list[dict[str, Any]]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        return [
            mt5_account_to_admin_dict(account)
            for account in list_mt5_accounts(api_state.conn, group_id=group_id, secret_cipher=api_state.secret_cipher)
        ]

    @app.post("/api/admin/mt5-accounts/{account_id}/snapshots", status_code=status.HTTP_201_CREATED)
    def admin_create_mt5_snapshot(
        account_id: str,
        payload: MT5SnapshotCreateRequest,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        account = get_mt5_account(api_state.conn, account_id, secret_cipher=api_state.secret_cipher)
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MT5 account not found")
        try:
            snapshot = create_mt5_snapshot(
                account=account,
                broker_server_time=payload.broker_server_time,
                raw_balance=payload.raw_balance,
                raw_equity=payload.raw_equity,
                raw_profit=payload.raw_profit,
                raw_margin=payload.raw_margin,
                raw_free_margin=payload.raw_free_margin,
                broker_server_day=payload.broker_server_day,
            )
            save_mt5_snapshot(api_state.conn, snapshot)
            return snapshot_to_dict(snapshot)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/api/admin/groups/{group_id}/mt5-client-view")
    def admin_client_visible_mt5_accounts(
        group_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> list[dict[str, str]]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        accounts = list_mt5_accounts(api_state.conn, group_id=group_id, secret_cipher=api_state.secret_cipher)
        snapshots = latest_snapshots_by_account(list_mt5_snapshots(api_state.conn, group_id=group_id))
        return [client_visible_mt5_account(account, snapshots.get(account.account_id)) for account in accounts]

    @app.get("/api/admin/groups/{group_id}/mt5-closed-balance")
    def admin_group_mt5_closed_balance(
        group_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        accounts = list_mt5_accounts(api_state.conn, group_id=group_id, secret_cipher=api_state.secret_cipher)
        snapshots = list_mt5_snapshots(api_state.conn, group_id=group_id)
        return {
            "group_id": group_id,
            "closed_balance": _decimal(latest_group_closed_balance(accounts=accounts, snapshots=snapshots, group_id=group_id)),
        }


    @app.get("/api/admin/groups/{group_id}/ledger")
    def admin_list_group_ledger(
        group_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> list[dict[str, Any]]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        return [ledger_to_dict(entry) for entry in list_ledger_entries(api_state.conn, group_id=group_id)]

    @app.get("/api/admin/groups/{group_id}/ledger/export.csv")
    def admin_export_group_ledger_csv(
        group_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> Response:
        group = get_group(api_state.conn, group_id)
        if group is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        entries = list_ledger_entries(api_state.conn, group_id=group_id)
        safe_name = "".join(ch if ch.isalnum() else "_" for ch in group.name).strip("_").lower() or "group"
        return csv_response(f"{safe_name}_ledger.csv", entries)

    @app.get("/api/admin/groups/{group_id}/workflow-items")
    def admin_group_workflow_items(
        group_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        entries = list_ledger_entries(api_state.conn, group_id=group_id)
        accounts = list_mt5_accounts(api_state.conn, group_id=group_id, secret_cipher=api_state.secret_cipher)
        return workflow_items_to_dict(
            group_id=group_id,
            entries=entries,
            clients=list_client_profiles(api_state.conn),
            accounts=accounts,
            external_payable=external_commission_payable(entries, group_id),
        )

    @app.post("/api/admin/ledger/{entry_id}/withdrawal/approve")
    def admin_approve_withdrawal(
        entry_id: str,
        payload: WithdrawalApproveRequest,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        entry = get_ledger_entry(api_state.conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found")
        try:
            approved = approve_withdrawal(entry, effective_date=payload.effective_date)
            save_ledger_entry(api_state.conn, approved)
            return ledger_to_dict(approved)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/admin/ledger/{entry_id}/withdrawal/reject")
    def admin_reject_withdrawal(
        entry_id: str,
        payload: WithdrawalRejectRequest,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        entry = get_ledger_entry(api_state.conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found")
        try:
            rejected = reject_withdrawal(entry, reason=payload.reason)
            save_ledger_entry(api_state.conn, rejected)
            return ledger_to_dict(rejected)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/admin/ledger/{entry_id}/withdrawal/effective")
    def admin_make_withdrawal_effective(
        entry_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        entry = get_ledger_entry(api_state.conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found")
        try:
            effective = make_withdrawal_effective(entry)
            save_ledger_entry(api_state.conn, effective)
            return ledger_to_dict(effective)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/admin/ledger/{entry_id}/withdrawal/paid")
    def admin_mark_withdrawal_paid(
        entry_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        entry = get_ledger_entry(api_state.conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found")
        try:
            paid = mark_withdrawal_paid(entry)
            save_ledger_entry(api_state.conn, paid)
            return ledger_to_dict(paid)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/admin/groups/{group_id}/expenses/equal/pending", status_code=status.HTTP_201_CREATED)
    def admin_record_equal_expense_pending(
        group_id: str,
        payload: ExpensePendingRequest,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> list[dict[str, Any]]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        members = active_group_members(list_group_memberships(api_state.conn, group_id=group_id), group_id)
        try:
            entries = record_equal_expense_pending(
                group_id=group_id,
                members=members,
                amount=payload.amount,
                effective_date=payload.effective_date,
                description=payload.description,
            )
            for entry in entries:
                save_ledger_entry(api_state.conn, entry)
            return [ledger_to_dict(entry) for entry in entries]
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/admin/ledger/{entry_id}/expense/effective")
    def admin_make_expense_effective(
        entry_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        entry = get_ledger_entry(api_state.conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found")
        try:
            effective = make_expense_effective(entry)
            save_ledger_entry(api_state.conn, effective)
            return ledger_to_dict(effective)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/admin/groups/{group_id}/expenses/{transaction_id}/effective")
    def admin_make_expense_transaction_effective(
        group_id: str,
        transaction_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> list[dict[str, Any]]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        transaction_entries = [
            entry
            for entry in list_ledger_entries(api_state.conn, group_id=group_id)
            if entry.transaction_id == transaction_id
        ]
        if not transaction_entries:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense transaction not found")
        if any(entry.entry_type == LedgerEntryType.EXPENSE_EFFECTIVE for entry in transaction_entries):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Expense transaction is already effective")
        pending_entries = [entry for entry in transaction_entries if entry.entry_type == LedgerEntryType.EXPENSE_PENDING]
        if not pending_entries:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No pending expense entries found for transaction")
        effective_entries = []
        try:
            for entry in pending_entries:
                effective = make_expense_effective(entry)
                save_ledger_entry(api_state.conn, effective)
                effective_entries.append(effective)
            return [ledger_to_dict(entry) for entry in effective_entries]
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/admin/groups/{group_id}/internal-transfers/pending", status_code=status.HTTP_201_CREATED)
    def admin_record_internal_transfer_pending(
        group_id: str,
        payload: InternalTransferPendingRequest,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        if get_mt5_account(api_state.conn, payload.from_mt5_account_id, secret_cipher=api_state.secret_cipher) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MT5 account not found")
        try:
            entry = record_internal_transfer_pending(
                group_id=group_id,
                from_mt5_account_id=payload.from_mt5_account_id,
                amount=payload.amount,
                description=payload.description,
            )
            save_ledger_entry(api_state.conn, entry)
            return ledger_to_dict(entry)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/admin/ledger/{entry_id}/internal-transfer/complete")
    def admin_complete_internal_transfer(
        entry_id: str,
        payload: InternalTransferCompleteRequest,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        entry = get_ledger_entry(api_state.conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger entry not found")
        if get_mt5_account(api_state.conn, payload.to_mt5_account_id, secret_cipher=api_state.secret_cipher) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MT5 account not found")
        try:
            completed = complete_internal_transfer(entry, to_mt5_account_id=payload.to_mt5_account_id)
            save_ledger_entry(api_state.conn, completed)
            return ledger_to_dict(completed)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/admin/groups/{group_id}/daily-close/finalize", status_code=status.HTTP_201_CREATED)
    def admin_finalize_daily_close(
        group_id: str,
        payload: DailyCloseFinalizeRequest,
        admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        group = get_group(api_state.conn, group_id)
        if group is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        if get_daily_group_close(api_state.conn, group_id, payload.broker_server_day) is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Daily close already finalized for this broker server day")

        accounts = list_mt5_accounts(api_state.conn, group_id=group_id, secret_cipher=api_state.secret_cipher)
        snapshots = list_mt5_snapshots(api_state.conn, group_id=group_id)
        memberships = list_group_memberships(api_state.conn, group_id=group_id)
        existing_entries = list_ledger_entries(api_state.conn, group_id=group_id)
        try:
            result = finalize_daily_close(
                group=group,
                broker_server_day=payload.broker_server_day,
                previous_broker_server_day=payload.previous_broker_server_day,
                accounts=accounts,
                snapshots=snapshots,
                memberships=memberships,
                existing_entries=existing_entries,
                manual_profit_loss=payload.manual_profit_loss,
                override_reason=payload.override_reason,
                created_by_user_id=payload.created_by_user_id or admin.user_id,
            )
            save_daily_group_close(api_state.conn, result.close)
            for entry in result.ledger_entries:
                save_ledger_entry(api_state.conn, entry)
            return {
                "daily_close": daily_close_to_dict(result.close),
                "entries": [ledger_to_dict(entry) for entry in result.ledger_entries],
            }
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/api/admin/groups/{group_id}/daily-closes")
    def admin_list_daily_closes(
        group_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> list[dict[str, Any]]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        return [daily_close_to_dict(close) for close in list_daily_group_closes(api_state.conn, group_id=group_id)]

    @app.get("/api/admin/groups/{group_id}/commission/external/payable")
    def admin_external_commission_payable(
        group_id: str,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        entries = list_ledger_entries(api_state.conn, group_id=group_id)
        return {"group_id": group_id, "external_commission_payable": _decimal(external_commission_payable(entries, group_id))}

    @app.post("/api/admin/groups/{group_id}/commission/withdrawals", status_code=status.HTTP_201_CREATED)
    def admin_record_commission_withdrawal(
        group_id: str,
        payload: CommissionWithdrawalRequest,
        _admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        try:
            entry = record_commission_withdrawal(
                group_id=group_id,
                amount=payload.amount,
                client_id=payload.client_id,
                description=payload.description,
            )
            save_ledger_entry(api_state.conn, entry)
            return ledger_to_dict(entry)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/admin/groups/{group_id}/manual-adjustments", status_code=status.HTTP_201_CREATED)
    def admin_record_manual_adjustment(
        group_id: str,
        payload: ManualAdjustmentRequest,
        admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        try:
            entry = record_manual_adjustment(
                group_id=group_id,
                client_id=payload.client_id,
                amount=payload.amount,
                reason=payload.reason,
                created_by_user_id=payload.created_by_user_id or admin.user_id,
            )
            save_ledger_entry(api_state.conn, entry)
            return ledger_to_dict(entry)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/admin/groups/{group_id}/import-wizard/review")
    def admin_review_import_wizard(
        group_id: str,
        payload: ImportWizardRequest,
        admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        memberships = list_group_memberships(api_state.conn, group_id=group_id)
        decisions = [_classification_request_to_decision(item) for item in payload.classifications]
        try:
            review = review_import_classifications(
                group_id=group_id,
                import_mode=payload.import_mode,
                memberships=memberships,
                decisions=decisions,
                created_by_user_id=admin.user_id,
            )
            return import_review_to_dict(review)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/admin/groups/{group_id}/import-wizard/finalize", status_code=status.HTTP_201_CREATED)
    def admin_finalize_import_wizard(
        group_id: str,
        payload: ImportWizardRequest,
        admin: Any = Depends(require_admin),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        if get_group(api_state.conn, group_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        memberships = list_group_memberships(api_state.conn, group_id=group_id)
        decisions = [_classification_request_to_decision(item) for item in payload.classifications]
        try:
            review = review_import_classifications(
                group_id=group_id,
                import_mode=payload.import_mode,
                memberships=memberships,
                decisions=decisions,
                created_by_user_id=admin.user_id,
            )
            for entry in review.ledger_entries:
                save_ledger_entry(api_state.conn, entry)
            record_audit_event(
                api_state,
                event_type="existing_group_import_finalized",
                description=f"Finalized import wizard for {review.total_detected} detected MT5 money movements.",
                actor_user_id=admin.user_id,
                metadata={
                    "group_id": group_id,
                    "import_mode": review.import_mode.value,
                    "entry_count": str(review.entry_count),
                    "total_classified_amount": str(review.total_classified_amount),
                },
            )
            return import_review_to_dict(review)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/api/client/me/profile")
    def client_self_profile(
        account: Any = Depends(require_client),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        client = client_profile_for_user(api_state, account.user_id)
        return {
            "user": user_to_dict(account),
            "client": client_to_dict(client),
            "email_missing": not bool(client.email),
            "password_reset_available": bool(client.email),
        }

    @app.patch("/api/client/me/profile")
    def client_update_own_profile(
        payload: ClientProfileUpdateRequest,
        account: Any = Depends(require_client),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        client = client_profile_for_user(api_state, account.user_id)
        email = validate_optional_email(payload.email)
        if payload.email_reports_opt_in and not email:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Add an email before enabling email reports")
        updated = replace(client, email=email, email_reports_opt_in=payload.email_reports_opt_in)
        save_client_profile(api_state.conn, updated)
        record_audit_event(
            api_state,
            event_type="client_profile_updated",
            description="Client updated email/report preferences",
            actor_user_id=account.user_id,
            target_user_id=account.user_id,
            target_client_id=client.client_id,
            metadata={"email_reports_opt_in": payload.email_reports_opt_in, "email_present": bool(email)},
        )
        return {
            "user": user_to_dict(account),
            "client": client_to_dict(updated),
            "email_missing": not bool(updated.email),
            "password_reset_available": bool(updated.email),
        }

    @app.post("/api/client/me/password")
    def client_change_own_password(
        payload: PasswordChangeRequest,
        account: Any = Depends(require_client),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        if not verify_password(account, payload.current_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
        updated = reset_password(account, payload.new_password)
        save_user_account(api_state.conn, updated)
        client = client_profile_for_user(api_state, account.user_id)
        record_audit_event(
            api_state,
            event_type="client_password_changed",
            description="Client changed their password while logged in",
            actor_user_id=account.user_id,
            target_user_id=account.user_id,
            target_client_id=client.client_id,
        )
        return {"password_changed": True}

    @app.post("/api/client/me/2fa")
    def client_update_two_factor_preference(
        payload: TwoFactorPreferenceRequest,
        account: Any = Depends(require_client),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        updated = replace(account, two_factor_enabled=payload.enabled)
        save_user_account(api_state.conn, updated)
        client = client_profile_for_user(api_state, account.user_id)
        record_audit_event(
            api_state,
            event_type="client_2fa_preference_updated",
            description="Client updated 2FA preference",
            actor_user_id=account.user_id,
            target_user_id=account.user_id,
            target_client_id=client.client_id,
            metadata={"enabled": payload.enabled},
        )
        return {"two_factor_enabled": updated.two_factor_enabled}

    @app.get("/api/client/me/dashboard")
    def client_self_dashboard(
        account: Any = Depends(require_client),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        client = client_profile_for_user(api_state, account.user_id)
        return _client_dashboard_payload(api_state, client)

    @app.get("/api/client/me/mt5-accounts")
    def client_self_mt5_accounts(
        account: Any = Depends(require_client),
        api_state: APIState = Depends(state),
    ) -> list[dict[str, Any]]:
        client = client_profile_for_user(api_state, account.user_id)
        visible: list[dict[str, Any]] = []
        memberships = list_group_memberships(api_state.conn, client_id=client.client_id)
        for membership in memberships:
            accounts = list_mt5_accounts(api_state.conn, group_id=membership.group_id, secret_cipher=api_state.secret_cipher)
            snapshots = latest_snapshots_by_account(list_mt5_snapshots(api_state.conn, group_id=membership.group_id))
            group = get_group(api_state.conn, membership.group_id)
            for account_record in accounts:
                account_payload = client_visible_mt5_account(account_record, snapshots.get(account_record.account_id))
                account_payload["group_id"] = membership.group_id
                account_payload["group_name"] = group.name if group else membership.group_id
                visible.append(account_payload)
        return visible

    @app.get("/api/client/me/ledger")
    def client_self_ledger(
        account: Any = Depends(require_client),
        api_state: APIState = Depends(state),
    ) -> list[dict[str, Any]]:
        client = client_profile_for_user(api_state, account.user_id)
        entries = list_ledger_entries(api_state.conn, client_id=client.client_id)
        return [ledger_to_dict(entry) for entry in entries]

    @app.get("/api/client/me/ledger/export.csv")
    def client_self_ledger_csv(
        account: Any = Depends(require_client),
        api_state: APIState = Depends(state),
    ) -> Response:
        client = client_profile_for_user(api_state, account.user_id)
        entries = list_ledger_entries(api_state.conn, client_id=client.client_id)
        safe_name = "".join(ch if ch.isalnum() else "_" for ch in client.display_name).strip("_").lower() or "client"
        return csv_response(f"{safe_name}_ledger.csv", entries)

    @app.post("/api/client/groups/{group_id}/withdrawals/request", status_code=status.HTTP_201_CREATED)
    def client_request_own_withdrawal(
        group_id: str,
        payload: WithdrawalRequestRequest,
        account: Any = Depends(require_client),
        api_state: APIState = Depends(state),
    ) -> dict[str, Any]:
        client = client_profile_for_user(api_state, account.user_id)
        ensure_client_in_group(api_state, client.client_id, group_id)
        if payload.client_id != client.client_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Clients can only request withdrawals for themselves")
        entries = list_ledger_entries(api_state.conn, group_id=group_id, client_id=client.client_id)
        try:
            entry = request_withdrawal(
                group_id=group_id,
                client_id=client.client_id,
                amount=payload.amount,
                available_balance_amount=_available_for_client_group(api_state, group_id, client.client_id),
                description=payload.description or "Withdrawal requested by client",
            )
            save_ledger_entry(api_state.conn, entry)
            return ledger_to_dict(entry)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


    return app


def configured_app() -> FastAPI:
    return create_app(
        db_path=resolve_database_path(),
        secret_key=os.getenv("MT5_PORTAL_SECRET_KEY"),
    )


app = configured_app()
