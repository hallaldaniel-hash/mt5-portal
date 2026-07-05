from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4


class LedgerEntryType(StrEnum):
    DEPOSIT_PENDING = "deposit_pending"
    DEPOSIT_EFFECTIVE = "deposit_effective"

    WITHDRAWAL_REQUESTED = "withdrawal_requested"
    WITHDRAWAL_APPROVED = "withdrawal_approved"
    WITHDRAWAL_REJECTED = "withdrawal_rejected"
    WITHDRAWAL_EFFECTIVE = "withdrawal_effective"
    WITHDRAWAL_PAID = "withdrawal_paid"

    EXPENSE_PENDING = "expense_pending"
    EXPENSE_EFFECTIVE = "expense_effective"

    TRANSFER_PENDING = "transfer_pending"
    TRANSFER_COMPLETED = "transfer_completed"

    DAILY_PROFIT_ALLOCATED = "daily_profit_allocated"
    DAILY_LOSS_ALLOCATED = "daily_loss_allocated"

    COMMISSION_PAID = "commission_paid"
    COMMISSION_EARNED = "commission_earned"
    EXTERNAL_COMMISSION_EARNED = "external_commission_earned"
    COMMISSION_WITHDRAWN = "commission_withdrawn"

    MANUAL_ADJUSTMENT = "manual_adjustment"


BALANCE_AFFECTING_ENTRY_TYPES = {
    LedgerEntryType.DEPOSIT_EFFECTIVE,
    LedgerEntryType.WITHDRAWAL_EFFECTIVE,
    LedgerEntryType.EXPENSE_EFFECTIVE,
    LedgerEntryType.DAILY_PROFIT_ALLOCATED,
    LedgerEntryType.DAILY_LOSS_ALLOCATED,
    LedgerEntryType.COMMISSION_PAID,
    LedgerEntryType.COMMISSION_EARNED,
    LedgerEntryType.COMMISSION_WITHDRAWN,
    LedgerEntryType.MANUAL_ADJUSTMENT,
}

PENDING_WITHDRAWAL_ENTRY_TYPES = {
    LedgerEntryType.WITHDRAWAL_REQUESTED,
    LedgerEntryType.WITHDRAWAL_APPROVED,
}

WITHDRAWAL_CLOSING_ENTRY_TYPES = {
    LedgerEntryType.WITHDRAWAL_REJECTED,
    LedgerEntryType.WITHDRAWAL_EFFECTIVE,
}


@dataclass(frozen=True)
class LedgerEntry:
    """One immutable financial history record.

    Amounts are signed from the perspective of the client/group ledger:
    - Positive entries increase capital/payable balance.
    - Negative entries decrease capital/payable balance.

    Pending/requested entries do not affect capital until an effective entry is created.
    Entries that belong to the same workflow share the same transaction_id.
    """

    group_id: str
    entry_type: LedgerEntryType
    amount: Decimal
    description: str
    currency: str = "USD"
    client_id: str | None = None
    mt5_account_id: str | None = None
    transaction_id: str = field(default_factory=lambda: str(uuid4()))
    entry_id: str = field(default_factory=lambda: str(uuid4()))
    effective_date: date | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by_user_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def affects_client_balance(self) -> bool:
        return self.client_id is not None and self.entry_type in BALANCE_AFFECTING_ENTRY_TYPES

    def affects_external_payable(self) -> bool:
        return self.client_id is None and self.entry_type in {
            LedgerEntryType.EXTERNAL_COMMISSION_EARNED,
            LedgerEntryType.COMMISSION_WITHDRAWN,
        }
