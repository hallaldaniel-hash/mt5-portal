from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.daily_close import DailyCloseAdjustment, DailyCloseStatus, DailyGroupClose
from app.domain.ledger import LedgerEntry, LedgerEntryType
from app.domain.models import DailyAllocationResult, GroupMember
from app.domain.mt5 import MT5Account, MT5Snapshot
from app.domain.portal import Group, GroupMembership
from app.services.allocation import allocate_daily_profit_loss
from app.services.ledger import BALANCE_AFFECTING_ENTRY_TYPES, client_balance, record_daily_allocation_entries
from app.services.mt5_accounts import live_accounts_for_group

ZERO = Decimal("0")


@dataclass(frozen=True)
class FinalizedDailyCloseResult:
    close: DailyGroupClose
    allocation: DailyAllocationResult
    ledger_entries: list[LedgerEntry]


def _latest_snapshot_on_or_before_day(
    snapshots: list[MT5Snapshot], *, account_id: str, broker_server_day: date
) -> MT5Snapshot | None:
    candidates = [
        snapshot
        for snapshot in snapshots
        if snapshot.account_id == account_id and snapshot.broker_server_day <= broker_server_day
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda snapshot: snapshot.broker_server_time)


def group_closed_balance_for_day(
    *,
    accounts: list[MT5Account],
    snapshots: list[MT5Snapshot],
    group_id: str,
    broker_server_day: date,
) -> Decimal:
    """Sum the latest display balance up to a broker day for all live group accounts."""

    total = ZERO
    for account in live_accounts_for_group(accounts, group_id):
        snapshot = _latest_snapshot_on_or_before_day(
            snapshots,
            account_id=account.account_id,
            broker_server_day=broker_server_day,
        )
        if snapshot is not None:
            total += snapshot.display_balance
    return total


def _entries_for_effective_day(entries: list[LedgerEntry], *, group_id: str, day: date) -> list[LedgerEntry]:
    return [entry for entry in entries if entry.group_id == group_id and entry.effective_date == day]


def daily_close_adjustments(entries: list[LedgerEntry], *, group_id: str, day: date) -> DailyCloseAdjustment:
    effective_entries = _entries_for_effective_day(entries, group_id=group_id, day=day)
    deposits = sum(
        (entry.amount for entry in effective_entries if entry.entry_type == LedgerEntryType.DEPOSIT_EFFECTIVE),
        ZERO,
    )
    withdrawals = sum(
        (abs(entry.amount) for entry in effective_entries if entry.entry_type == LedgerEntryType.WITHDRAWAL_EFFECTIVE),
        ZERO,
    )
    expenses = sum(
        (abs(entry.amount) for entry in effective_entries if entry.entry_type == LedgerEntryType.EXPENSE_EFFECTIVE),
        ZERO,
    )

    # Internal transfers do not change client capital. If a transfer is pending
    # because funds left one MT5 account but the new MT5 account is not live yet,
    # add the amount back so it is not counted as a trading loss.
    pending_transfer_ids = {
        entry.transaction_id
        for entry in entries
        if entry.group_id == group_id and entry.entry_type == LedgerEntryType.TRANSFER_PENDING
    }
    completed_transfer_ids = {
        entry.transaction_id
        for entry in entries
        if entry.group_id == group_id and entry.entry_type == LedgerEntryType.TRANSFER_COMPLETED
    }
    pending_internal_transfers = sum(
        (
            entry.amount
            for entry in entries
            if entry.group_id == group_id
            and entry.entry_type == LedgerEntryType.TRANSFER_PENDING
            and entry.transaction_id in pending_transfer_ids
            and entry.transaction_id not in completed_transfer_ids
        ),
        ZERO,
    )

    return DailyCloseAdjustment(
        deposits_effective=deposits,
        withdrawals_effective=withdrawals,
        expenses_effective=expenses,
        pending_internal_transfers=pending_internal_transfers,
    )


def calculate_trading_profit_loss(
    *,
    opening_closed_balance: Decimal,
    closing_closed_balance: Decimal,
    adjustments: DailyCloseAdjustment,
) -> Decimal:
    """Calculate true trading P/L from MT5 balance movement.

    Formula:
    close - open - deposits + withdrawals + expenses + pending transfers

    Deposits are removed because they increase MT5 balance without being trading profit.
    Withdrawals/expenses are added back because they reduce MT5 balance without being trading loss.
    Pending internal transfers are added back until the destination account is tracked/live.
    """

    return (
        closing_closed_balance
        - opening_closed_balance
        - adjustments.deposits_effective
        + adjustments.withdrawals_effective
        + adjustments.expenses_effective
        + adjustments.pending_internal_transfers
    )


def _client_has_balance_affecting_entries(entries: list[LedgerEntry], *, group_id: str, client_id: str) -> bool:
    return any(
        entry.group_id == group_id
        and entry.client_id == client_id
        and entry.entry_type in BALANCE_AFFECTING_ENTRY_TYPES
        for entry in entries
    )


def effective_members_for_allocation(
    *,
    memberships: list[GroupMembership],
    entries: list[LedgerEntry],
    group_id: str,
) -> list[GroupMember]:
    """Create allocation members from current finalized ledger balances.

    If a member has no balance-affecting ledger history yet, we fall back to the
    membership's effective_capital so early tests/manual setups still work.
    """

    members: list[GroupMember] = []
    for membership in memberships:
        if membership.group_id != group_id or not membership.is_active:
            continue
        ledger_capital = client_balance(entries, group_id, membership.client_id)
        capital = (
            ledger_capital
            if _client_has_balance_affecting_entries(entries, group_id=group_id, client_id=membership.client_id)
            else membership.effective_capital
        )
        if capital > ZERO:
            members.append(
                GroupMember(
                    client_id=membership.client_id,
                    name=membership.display_name,
                    effective_capital=capital,
                    role=membership.role,
                    is_active=membership.is_active,
                )
            )
    if not members:
        raise ValueError("At least one active member with positive capital is required")
    return members


def finalize_daily_close(
    *,
    group: Group,
    broker_server_day: date,
    previous_broker_server_day: date,
    accounts: list[MT5Account],
    snapshots: list[MT5Snapshot],
    memberships: list[GroupMembership],
    existing_entries: list[LedgerEntry],
    manual_profit_loss: Decimal | None = None,
    override_reason: str | None = None,
    created_by_user_id: str | None = None,
) -> FinalizedDailyCloseResult:
    opening_balance = group_closed_balance_for_day(
        accounts=accounts,
        snapshots=snapshots,
        group_id=group.group_id,
        broker_server_day=previous_broker_server_day,
    )
    closing_balance = group_closed_balance_for_day(
        accounts=accounts,
        snapshots=snapshots,
        group_id=group.group_id,
        broker_server_day=broker_server_day,
    )
    adjustments = daily_close_adjustments(existing_entries, group_id=group.group_id, day=broker_server_day)
    calculated_profit_loss = calculate_trading_profit_loss(
        opening_closed_balance=opening_balance,
        closing_closed_balance=closing_balance,
        adjustments=adjustments,
    )

    status = DailyCloseStatus.FINALIZED
    trading_profit_loss = calculated_profit_loss
    clean_reason = override_reason.strip() if override_reason else None
    if manual_profit_loss is not None:
        if not clean_reason:
            raise ValueError("Override reason is required when manual_profit_loss is provided")
        trading_profit_loss = manual_profit_loss
        status = DailyCloseStatus.OVERRIDDEN

    members = effective_members_for_allocation(
        memberships=memberships,
        entries=existing_entries,
        group_id=group.group_id,
    )
    allocation = allocate_daily_profit_loss(
        members=members,
        group_profit_loss=trading_profit_loss,
        commission_rule=group.commission_rule,
    )
    ledger_entries = record_daily_allocation_entries(
        group_id=group.group_id,
        allocation_result=allocation,
        allocation_date=broker_server_day,
    )
    close = DailyGroupClose(
        group_id=group.group_id,
        broker_server_day=broker_server_day,
        opening_closed_balance=opening_balance,
        closing_closed_balance=closing_balance,
        deposits_effective=adjustments.deposits_effective,
        withdrawals_effective=adjustments.withdrawals_effective,
        expenses_effective=adjustments.expenses_effective,
        pending_internal_transfers=adjustments.pending_internal_transfers,
        trading_profit_loss=trading_profit_loss,
        status=status,
        created_by_user_id=created_by_user_id,
        override_reason=clean_reason,
    )
    close.validate()
    return FinalizedDailyCloseResult(close=close, allocation=allocation, ledger_entries=ledger_entries)
