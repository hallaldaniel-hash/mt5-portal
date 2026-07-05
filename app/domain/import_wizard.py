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


class MovementDirection(StrEnum):
    """Step 28 simplified admin grouping for MT5 money movements."""

    ADDITION = "addition"
    WITHDRAWAL = "withdrawal"
    NEUTRAL = "neutral"


class MovementClassification(StrEnum):
    """Admin meaning assigned to a non-trading MT5 money movement.

    Step 28 note:
    We keep the detailed internal classifications for accounting correctness,
    but the UI should group them into simple sections:
    - Additions
    - Withdrawals
    - Neutral / Ignore
    """

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


ADDITION_CLASSIFICATIONS = {
    MovementClassification.CLIENT_DEPOSIT,
    MovementClassification.DEPOSIT_SPLIT_EQUALLY,
    MovementClassification.DEPOSIT_SPLIT_BY_PERCENTAGE,
    MovementClassification.RETURNED_PENDING_TRANSFER,
    MovementClassification.BROKER_CORRECTION,
}

WITHDRAWAL_CLASSIFICATIONS = {
    MovementClassification.CLIENT_WITHDRAWAL,
    MovementClassification.SHARED_GROUP_EXPENSE,
    MovementClassification.EXTERNAL_COMMISSION_WITHDRAWAL,
    MovementClassification.PARTNER_COMMISSION_WITHDRAWAL,
    MovementClassification.MIXED_COMMISSION_WITHDRAWAL,
    MovementClassification.TRANSFER_TO_NEW_MT5_ACCOUNT,
    MovementClassification.TRANSFER_TO_EXISTING_MT5_ACCOUNT,
    MovementClassification.BROKER_FEE,
}

NEUTRAL_CLASSIFICATIONS = {
    MovementClassification.MANUAL_ADJUSTMENT,
    MovementClassification.IGNORE,
}


MOVEMENT_CLASSIFICATION_LABELS = {
    MovementClassification.CLIENT_DEPOSIT: "Client deposit",
    MovementClassification.DEPOSIT_SPLIT_EQUALLY: "Deposit split equally",
    MovementClassification.DEPOSIT_SPLIT_BY_PERCENTAGE: "Deposit split by percentage",
    MovementClassification.RETURNED_PENDING_TRANSFER: "Returned pending transfer",
    MovementClassification.BROKER_CORRECTION: "Broker correction",
    MovementClassification.CLIENT_WITHDRAWAL: "Client withdrawal",
    MovementClassification.SHARED_GROUP_EXPENSE: "Shared group expense",
    MovementClassification.EXTERNAL_COMMISSION_WITHDRAWAL: "External commission withdrawal",
    MovementClassification.PARTNER_COMMISSION_WITHDRAWAL: "Partner commission withdrawal",
    MovementClassification.MIXED_COMMISSION_WITHDRAWAL: "Mixed commission withdrawal",
    MovementClassification.TRANSFER_TO_NEW_MT5_ACCOUNT: "Transfer to new MT5 account",
    MovementClassification.TRANSFER_TO_EXISTING_MT5_ACCOUNT: "Transfer to existing MT5 account",
    MovementClassification.BROKER_FEE: "Broker fee",
    MovementClassification.MANUAL_ADJUSTMENT: "Manual adjustment",
    MovementClassification.IGNORE: "Ignore",
}


def movement_classification_direction(
    classification: MovementClassification,
) -> MovementDirection:
    if classification in ADDITION_CLASSIFICATIONS:
        return MovementDirection.ADDITION

    if classification in WITHDRAWAL_CLASSIFICATIONS:
        return MovementDirection.WITHDRAWAL

    return MovementDirection.NEUTRAL


def movement_classification_label(classification: MovementClassification) -> str:
    return MOVEMENT_CLASSIFICATION_LABELS[classification]


def movement_classification_admin_bucket(classification: MovementClassification) -> str:
    direction = movement_classification_direction(classification)

    if direction == MovementDirection.ADDITION:
        return "Additions"

    if direction == MovementDirection.WITHDRAWAL:
        return "Withdrawals"

    return "Neutral / Ignore"


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
    def is_withdrawal(self) -> bool:
        return self.amount < Decimal("0")

    @property
    def direction(self) -> MovementDirection:
        if self.is_addition:
            return MovementDirection.ADDITION

        if self.is_withdrawal:
            return MovementDirection.WITHDRAWAL

        return MovementDirection.NEUTRAL

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

    # Step 28 future-ready field.
    # This lets us support Partner 3, Partner 4, etc. later without breaking
    # the old partner_1 / partner_2 workflow.
    dynamic_partner_amounts: dict[str, Decimal] = field(default_factory=dict)

    @property
    def direction(self) -> MovementDirection:
        return movement_classification_direction(self.classification)

    @property
    def label(self) -> str:
        return movement_classification_label(self.classification)

    @property
    def admin_bucket(self) -> str:
        return movement_classification_admin_bucket(self.classification)

    @property
    def effective_on(self) -> date:
        return self.effective_date or self.movement.occurred_on

    def is_addition_classification(self) -> bool:
        return self.direction == MovementDirection.ADDITION

    def is_withdrawal_classification(self) -> bool:
        return self.direction == MovementDirection.WITHDRAWAL


@dataclass(frozen=True)
class ImportReviewLine:
    movement_id: str
    classification: MovementClassification
    amount: Decimal
    generated_entry_count: int
    description: str

    @property
    def label(self) -> str:
        return movement_classification_label(self.classification)

    @property
    def admin_bucket(self) -> str:
        return movement_classification_admin_bucket(self.classification)


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

    @property
    def addition_lines(self) -> list[ImportReviewLine]:
        return [
            line
            for line in self.lines
            if movement_classification_direction(line.classification)
            == MovementDirection.ADDITION
        ]

    @property
    def withdrawal_lines(self) -> list[ImportReviewLine]:
        return [
            line
            for line in self.lines
            if movement_classification_direction(line.classification)
            == MovementDirection.WITHDRAWAL
        ]

    @property
    def neutral_lines(self) -> list[ImportReviewLine]:
        return [
            line
            for line in self.lines
            if movement_classification_direction(line.classification)
            == MovementDirection.NEUTRAL
        ]