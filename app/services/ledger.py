from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.domain.ledger import (
    BALANCE_AFFECTING_ENTRY_TYPES,
    PENDING_WITHDRAWAL_ENTRY_TYPES,
    WITHDRAWAL_CLOSING_ENTRY_TYPES,
    LedgerEntry,
    LedgerEntryType,
)
from app.domain.models import DailyAllocationResult, GroupMember
from app.services.allocation import allocate_equal_expense

ZERO = Decimal("0")
DEFAULT_MIN_WITHDRAWAL = Decimal("10")


def _require_positive_amount(amount: Decimal, label: str = "amount") -> None:
    if amount <= ZERO:
        raise ValueError(f"{label} must be greater than zero")


def _same_transaction(source: LedgerEntry, entry_type: LedgerEntryType, description: str, amount: Decimal | None = None, **kwargs: object) -> LedgerEntry:
    return LedgerEntry(
        group_id=source.group_id,
        client_id=source.client_id,
        mt5_account_id=source.mt5_account_id,
        transaction_id=source.transaction_id,
        entry_type=entry_type,
        amount=source.amount if amount is None else amount,
        currency=source.currency,
        description=description,
        **kwargs,
    )


def client_balance(entries: list[LedgerEntry], group_id: str, client_id: str) -> Decimal:
    """Return finalized capital for one client inside one group."""

    return sum(
        (
            entry.amount
            for entry in entries
            if entry.group_id == group_id
            and entry.client_id == client_id
            and entry.entry_type in BALANCE_AFFECTING_ENTRY_TYPES
        ),
        ZERO,
    )


def group_client_balances(entries: list[LedgerEntry], group_id: str) -> dict[str, Decimal]:
    balances: dict[str, Decimal] = {}
    for entry in entries:
        if (
            entry.group_id == group_id
            and entry.client_id is not None
            and entry.entry_type in BALANCE_AFFECTING_ENTRY_TYPES
        ):
            balances[entry.client_id] = balances.get(entry.client_id, ZERO) + entry.amount
    return balances


def pending_withdrawal_total(entries: list[LedgerEntry], group_id: str, client_id: str) -> Decimal:
    """Return valid requested/approved withdrawals that are not rejected/effective yet."""

    closed_transaction_ids = {
        entry.transaction_id
        for entry in entries
        if entry.group_id == group_id
        and entry.client_id == client_id
        and entry.entry_type in WITHDRAWAL_CLOSING_ENTRY_TYPES
    }

    return sum(
        (
            abs(entry.amount)
            for entry in entries
            if entry.group_id == group_id
            and entry.client_id == client_id
            and entry.entry_type in PENDING_WITHDRAWAL_ENTRY_TYPES
            and entry.transaction_id not in closed_transaction_ids
        ),
        ZERO,
    )


def available_balance(entries: list[LedgerEntry], group_id: str, client_id: str) -> Decimal:
    return client_balance(entries, group_id, client_id) - pending_withdrawal_total(
        entries, group_id, client_id
    )


def external_commission_payable(entries: list[LedgerEntry], group_id: str) -> Decimal:
    """Track external commission earned minus external commission withdrawn."""

    return sum(
        (
            entry.amount
            for entry in entries
            if entry.group_id == group_id
            and entry.client_id is None
            and entry.entry_type
            in {
                LedgerEntryType.EXTERNAL_COMMISSION_EARNED,
                LedgerEntryType.COMMISSION_WITHDRAWN,
            }
        ),
        ZERO,
    )


def record_deposit_pending(
    *,
    group_id: str,
    client_id: str,
    amount: Decimal,
    effective_date: date,
    created_by_user_id: str | None = None,
    description: str | None = None,
) -> LedgerEntry:
    _require_positive_amount(amount)
    return LedgerEntry(
        group_id=group_id,
        client_id=client_id,
        entry_type=LedgerEntryType.DEPOSIT_PENDING,
        amount=amount,
        effective_date=effective_date,
        created_by_user_id=created_by_user_id,
        description=description or f"Deposit pending for {effective_date.isoformat()}",
    )


def make_deposit_effective(pending_deposit: LedgerEntry) -> LedgerEntry:
    if pending_deposit.entry_type != LedgerEntryType.DEPOSIT_PENDING:
        raise ValueError("Only a pending deposit can be made effective")
    return _same_transaction(
        pending_deposit,
        LedgerEntryType.DEPOSIT_EFFECTIVE,
        "Deposit became effective",
        effective_date=pending_deposit.effective_date,
    )


def request_withdrawal(
    *,
    group_id: str,
    client_id: str,
    amount: Decimal,
    available_balance_amount: Decimal,
    minimum_amount: Decimal = DEFAULT_MIN_WITHDRAWAL,
    description: str | None = None,
) -> LedgerEntry:
    _require_positive_amount(amount)
    if amount < minimum_amount:
        raise ValueError(f"Withdrawal minimum is {minimum_amount}")

    entry_type = LedgerEntryType.WITHDRAWAL_REQUESTED
    metadata: dict[str, str] = {}
    final_description = description or "Withdrawal requested"

    if amount > available_balance_amount:
        entry_type = LedgerEntryType.WITHDRAWAL_REJECTED
        metadata["rejection_reason"] = "Amount exceeds available balance"
        final_description = "Withdrawal rejected automatically: amount exceeds available balance"

    return LedgerEntry(
        group_id=group_id,
        client_id=client_id,
        entry_type=entry_type,
        amount=-amount,
        description=final_description,
        metadata=metadata,
    )


def approve_withdrawal(request_entry: LedgerEntry, *, effective_date: date) -> LedgerEntry:
    if request_entry.entry_type != LedgerEntryType.WITHDRAWAL_REQUESTED:
        raise ValueError("Only requested withdrawals can be approved")
    return _same_transaction(
        request_entry,
        LedgerEntryType.WITHDRAWAL_APPROVED,
        "Withdrawal approved",
        effective_date=effective_date,
    )


def reject_withdrawal(request_entry: LedgerEntry, *, reason: str) -> LedgerEntry:
    if request_entry.entry_type != LedgerEntryType.WITHDRAWAL_REQUESTED:
        raise ValueError("Only requested withdrawals can be rejected")
    return _same_transaction(
        request_entry,
        LedgerEntryType.WITHDRAWAL_REJECTED,
        f"Withdrawal rejected: {reason}",
        metadata={"rejection_reason": reason},
    )


def make_withdrawal_effective(approved_entry: LedgerEntry) -> LedgerEntry:
    if approved_entry.entry_type != LedgerEntryType.WITHDRAWAL_APPROVED:
        raise ValueError("Only approved withdrawals can be made effective")
    return _same_transaction(
        approved_entry,
        LedgerEntryType.WITHDRAWAL_EFFECTIVE,
        "Withdrawal became effective",
        effective_date=approved_entry.effective_date,
    )


def mark_withdrawal_paid(effective_entry: LedgerEntry) -> LedgerEntry:
    if effective_entry.entry_type != LedgerEntryType.WITHDRAWAL_EFFECTIVE:
        raise ValueError("Only effective withdrawals can be marked paid")
    return _same_transaction(
        effective_entry,
        LedgerEntryType.WITHDRAWAL_PAID,
        "Withdrawal paid to client",
    )


def record_equal_expense_pending(
    *,
    group_id: str,
    members: list[GroupMember],
    amount: Decimal,
    effective_date: date,
    description: str,
) -> list[LedgerEntry]:
    _require_positive_amount(amount)
    transaction_id = str(uuid4())
    allocations = allocate_equal_expense(members, amount)
    return [
        LedgerEntry(
            group_id=group_id,
            client_id=allocation.client_id,
            transaction_id=transaction_id,
            entry_type=LedgerEntryType.EXPENSE_PENDING,
            amount=-allocation.expense_share,
            effective_date=effective_date,
            description=description,
        )
        for allocation in allocations
    ]


def make_expense_effective(pending_expense_entry: LedgerEntry) -> LedgerEntry:
    if pending_expense_entry.entry_type != LedgerEntryType.EXPENSE_PENDING:
        raise ValueError("Only a pending expense can be made effective")
    return _same_transaction(
        pending_expense_entry,
        LedgerEntryType.EXPENSE_EFFECTIVE,
        "Expense became effective",
        effective_date=pending_expense_entry.effective_date,
    )


def record_internal_transfer_pending(
    *,
    group_id: str,
    from_mt5_account_id: str,
    amount: Decimal,
    description: str,
) -> LedgerEntry:
    _require_positive_amount(amount)
    return LedgerEntry(
        group_id=group_id,
        mt5_account_id=from_mt5_account_id,
        entry_type=LedgerEntryType.TRANSFER_PENDING,
        amount=amount,
        description=description,
    )


def complete_internal_transfer(pending_transfer: LedgerEntry, *, to_mt5_account_id: str) -> LedgerEntry:
    if pending_transfer.entry_type != LedgerEntryType.TRANSFER_PENDING:
        raise ValueError("Only a pending transfer can be completed")
    return _same_transaction(
        pending_transfer,
        LedgerEntryType.TRANSFER_COMPLETED,
        "Internal transfer completed",
        metadata={"to_mt5_account_id": to_mt5_account_id},
    )


def record_daily_allocation_entries(
    *,
    group_id: str,
    allocation_result: DailyAllocationResult,
    allocation_date: date,
) -> list[LedgerEntry]:
    """Convert a calculated daily allocation into immutable ledger entries."""

    transaction_id = str(uuid4())
    entries: list[LedgerEntry] = []

    for allocation in allocation_result.member_allocations:
        if allocation.gross_profit_loss != ZERO:
            entries.append(
                LedgerEntry(
                    group_id=group_id,
                    client_id=allocation.client_id,
                    transaction_id=transaction_id,
                    entry_type=(
                        LedgerEntryType.DAILY_PROFIT_ALLOCATED
                        if allocation.gross_profit_loss > ZERO
                        else LedgerEntryType.DAILY_LOSS_ALLOCATED
                    ),
                    amount=allocation.gross_profit_loss,
                    effective_date=allocation_date,
                    description="Daily gross trading profit/loss allocated",
                    metadata={"ownership_percent": str(allocation.ownership_percent)},
                )
            )

        if allocation.commission_paid != ZERO:
            entries.append(
                LedgerEntry(
                    group_id=group_id,
                    client_id=allocation.client_id,
                    transaction_id=transaction_id,
                    entry_type=LedgerEntryType.COMMISSION_PAID,
                    amount=-allocation.commission_paid,
                    effective_date=allocation_date,
                    description="Daily commission paid by normal client",
                )
            )

        if allocation.commission_earned != ZERO:
            entries.append(
                LedgerEntry(
                    group_id=group_id,
                    client_id=allocation.client_id,
                    transaction_id=transaction_id,
                    entry_type=LedgerEntryType.COMMISSION_EARNED,
                    amount=allocation.commission_earned,
                    effective_date=allocation_date,
                    description="Daily partner commission earned",
                )
            )

    if allocation_result.external_commission_earned != ZERO:
        entries.append(
            LedgerEntry(
                group_id=group_id,
                client_id=None,
                transaction_id=transaction_id,
                entry_type=LedgerEntryType.EXTERNAL_COMMISSION_EARNED,
                amount=allocation_result.external_commission_earned,
                effective_date=allocation_date,
                description="Daily external commission earned",
            )
        )

    return entries


def record_commission_withdrawal(
    *,
    group_id: str,
    amount: Decimal,
    client_id: str | None,
    description: str,
) -> LedgerEntry:
    """Record commission being withdrawn.

    client_id=None means this withdrawal belongs to the external commission payable.
    client_id=<partner id> means it reduces that partner's portal balance.
    """

    _require_positive_amount(amount)
    return LedgerEntry(
        group_id=group_id,
        client_id=client_id,
        entry_type=LedgerEntryType.COMMISSION_WITHDRAWN,
        amount=-amount,
        description=description,
    )


def record_manual_adjustment(
    *,
    group_id: str,
    client_id: str,
    amount: Decimal,
    reason: str,
    created_by_user_id: str,
) -> LedgerEntry:
    if amount == ZERO:
        raise ValueError("Manual adjustment amount cannot be zero")
    if not reason.strip():
        raise ValueError("Manual adjustment requires a reason")
    return LedgerEntry(
        group_id=group_id,
        client_id=client_id,
        entry_type=LedgerEntryType.MANUAL_ADJUSTMENT,
        amount=amount,
        description=f"Manual adjustment: {reason}",
        created_by_user_id=created_by_user_id,
        metadata={"reason": reason},
    )
