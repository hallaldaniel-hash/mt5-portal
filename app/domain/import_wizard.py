from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from app.domain.ledger import LedgerEntry


class ExistingGroupImportMode(StrEnum):
    """How an already-running group is being brought into the portal."""

    PERCENTAGE_IMPORT = "percentage_import"
    CURRENT_BALANCE_IMPORT = "current_balance_import"
    HISTORICAL_RECONSTRUCTION = "historical_reconstruction"


class MovementClassification(StrEnum):
    """Admin meaning assigned to a non-trading MT5 money movement."""

    CLIENT_DEPOSIT = "client_deposit"
    DEPOSIT_SPLIT_EQUALLY = "deposit_split_equally"
    DEPOSIT_SPLIT_BY_PERCENTAGE = "deposit_split_by_percentage"
    RETURNED_PENDING_TRANSFER = "returned_pending_transfer"
    BROKER_CORRECTION = "broker_correction"

    CLIENT_WITHDRAWAL = "client_withdrawal"
    SHARED_GROUP_EXPENSE = "shared_group_expense"
    EXTERNAL_COMMISSION_WITHDRAWAL = "external_commission_withdrawal"
    PARTNER_COMMISSION_WITHDRAWAL = "partner_commission_withdrawal"
    MIXED_COMMISSION_WITHDRAWAL = "mixed_commission_withdrawal"
    TRANSFER_TO_NEW_MT5_ACCOUNT = "transfer_to_new_mt5_account"
    TRANSFER_TO_EXISTING_MT5_ACCOUNT = "transfer_to_existing_mt5_account"
    BROKER_FEE = "broker_fee"

    MANUAL_ADJUSTMENT = "manual_adjustment"
    IGNORE = "ignore"


@dataclass(frozen=True)
class DetectedMoneyMovement:
    """One MT5 non-trading money movement found during import."""

    amount: Decimal
    occurred_on: date
    comment: str = ""
    mt5_account_id: str | None = None
    movement_id: str = field(default_factory=lambda: str(uuid4()))
    currency: str = "USD"

    @property
    def is_addition(self) -> bool:
        return self.amount > Decimal("0")

    @property
    def absolute_amount(self) -> Decimal:
        return abs(self.amount)


@dataclass(frozen=True)
class MovementClassificationDecision:
    movement: DetectedMoneyMovement
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


@dataclass(frozen=True)
class ImportReviewLine:
    movement_id: str
    classification: MovementClassification
    amount: Decimal
    generated_entry_count: int
    description: str


@dataclass(frozen=True)
class ImportWizardReview:
    group_id: str
    import_mode: ExistingGroupImportMode
    total_detected: int
    total_classified_amount: Decimal
    total_ignored_amount: Decimal
    ledger_entries: list[LedgerEntry]
    lines: list[ImportReviewLine]

    @property
    def entry_count(self) -> int:
        return len(self.ledger_entries)
