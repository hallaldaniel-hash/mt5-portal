from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.domain.import_wizard import (
    ExistingGroupImportMode,
    ImportReviewLine,
    ImportWizardReview,
    MovementClassification,
    MovementClassificationDecision,
)
from app.domain.ledger import LedgerEntry, LedgerEntryType
from app.domain.portal import GroupMembership

ZERO = Decimal("0")


POSITIVE_CLASSIFICATIONS = {
    MovementClassification.CLIENT_DEPOSIT,
    MovementClassification.DEPOSIT_SPLIT_EQUALLY,
    MovementClassification.DEPOSIT_SPLIT_BY_PERCENTAGE,
    MovementClassification.RETURNED_PENDING_TRANSFER,
    MovementClassification.BROKER_CORRECTION,
    MovementClassification.MANUAL_ADJUSTMENT,
    MovementClassification.IGNORE,
}

NEGATIVE_CLASSIFICATIONS = {
    MovementClassification.CLIENT_WITHDRAWAL,
    MovementClassification.SHARED_GROUP_EXPENSE,
    MovementClassification.EXTERNAL_COMMISSION_WITHDRAWAL,
    MovementClassification.PARTNER_COMMISSION_WITHDRAWAL,
    MovementClassification.MIXED_COMMISSION_WITHDRAWAL,
    MovementClassification.TRANSFER_TO_NEW_MT5_ACCOUNT,
    MovementClassification.TRANSFER_TO_EXISTING_MT5_ACCOUNT,
    MovementClassification.BROKER_FEE,
    MovementClassification.MANUAL_ADJUSTMENT,
    MovementClassification.IGNORE,
}


def _active_members(memberships: list[GroupMembership], group_id: str) -> list[GroupMembership]:
    return [membership for membership in memberships if membership.group_id == group_id and membership.is_active]


def _require_client_id(decision: MovementClassificationDecision) -> str:
    if not decision.client_id:
        raise ValueError(f"{decision.classification.value} requires client_id")
    return decision.client_id


def _split_equally(amount: Decimal, members: list[GroupMembership]) -> list[tuple[str, Decimal]]:
    if not members:
        raise ValueError("At least one active group member is required")
    share = amount / Decimal(len(members))
    return [(member.client_id, share) for member in members]


def _split_by_percentage(amount: Decimal, members: list[GroupMembership]) -> list[tuple[str, Decimal]]:
    if not members:
        raise ValueError("At least one active group member is required")
    total_capital = sum((member.effective_capital for member in members), ZERO)
    if total_capital <= ZERO:
        raise ValueError("Percentage split requires positive effective capital")
    return [(member.client_id, amount * (member.effective_capital / total_capital)) for member in members]


def _base_metadata(decision: MovementClassificationDecision) -> dict[str, str]:
    movement = decision.movement
    metadata = {
        "source": "existing_group_import_wizard",
        "movement_id": movement.movement_id,
        "mt5_comment": movement.comment,
        "classification": decision.classification.value,
    }
    if movement.mt5_account_id:
        metadata["source_mt5_account_id"] = movement.mt5_account_id
    return metadata


def _entry(
    *,
    group_id: str,
    decision: MovementClassificationDecision,
    entry_type: LedgerEntryType,
    amount: Decimal,
    client_id: str | None = None,
    mt5_account_id: str | None = None,
    description: str,
    transaction_id: str,
    metadata: dict[str, str] | None = None,
    created_by_user_id: str | None = None,
) -> LedgerEntry:
    return LedgerEntry(
        group_id=group_id,
        client_id=client_id,
        mt5_account_id=mt5_account_id,
        transaction_id=transaction_id,
        entry_type=entry_type,
        amount=amount,
        effective_date=decision.effective_date or decision.movement.occurred_on,
        description=description,
        created_by_user_id=created_by_user_id,
        metadata={**_base_metadata(decision), **(metadata or {})},
    )


def entries_for_classification(
    *,
    group_id: str,
    memberships: list[GroupMembership],
    decision: MovementClassificationDecision,
    created_by_user_id: str | None = None,
) -> list[LedgerEntry]:
    movement = decision.movement
    if movement.amount == ZERO:
        raise ValueError("Detected movement amount cannot be zero")
    if movement.is_addition and decision.classification not in POSITIVE_CLASSIFICATIONS:
        raise ValueError("Positive MT5 movement needs an addition classification")
    if not movement.is_addition and decision.classification not in NEGATIVE_CLASSIFICATIONS:
        raise ValueError("Negative MT5 movement needs a withdrawal classification")

    amount = movement.absolute_amount
    members = _active_members(memberships, group_id)
    transaction_id = str(uuid4())
    description = decision.description or movement.comment or f"Imported {decision.classification.value}"

    if decision.classification == MovementClassification.IGNORE:
        return []

    if decision.classification == MovementClassification.CLIENT_DEPOSIT:
        return [
            _entry(
                group_id=group_id,
                decision=decision,
                entry_type=LedgerEntryType.DEPOSIT_EFFECTIVE,
                amount=amount,
                client_id=_require_client_id(decision),
                description=description,
                transaction_id=transaction_id,
                created_by_user_id=created_by_user_id,
            )
        ]

    if decision.classification == MovementClassification.DEPOSIT_SPLIT_EQUALLY:
        return [
            _entry(
                group_id=group_id,
                decision=decision,
                entry_type=LedgerEntryType.DEPOSIT_EFFECTIVE,
                amount=share,
                client_id=client_id,
                description=description,
                transaction_id=transaction_id,
                created_by_user_id=created_by_user_id,
            )
            for client_id, share in _split_equally(amount, members)
        ]

    if decision.classification == MovementClassification.DEPOSIT_SPLIT_BY_PERCENTAGE:
        return [
            _entry(
                group_id=group_id,
                decision=decision,
                entry_type=LedgerEntryType.DEPOSIT_EFFECTIVE,
                amount=share,
                client_id=client_id,
                description=description,
                transaction_id=transaction_id,
                created_by_user_id=created_by_user_id,
            )
            for client_id, share in _split_by_percentage(amount, members)
        ]

    if decision.classification == MovementClassification.CLIENT_WITHDRAWAL:
        return [
            _entry(
                group_id=group_id,
                decision=decision,
                entry_type=LedgerEntryType.WITHDRAWAL_EFFECTIVE,
                amount=-amount,
                client_id=_require_client_id(decision),
                description=description,
                transaction_id=transaction_id,
                created_by_user_id=created_by_user_id,
            )
        ]

    if decision.classification in {MovementClassification.SHARED_GROUP_EXPENSE, MovementClassification.BROKER_FEE}:
        return [
            _entry(
                group_id=group_id,
                decision=decision,
                entry_type=LedgerEntryType.EXPENSE_EFFECTIVE,
                amount=-share,
                client_id=client_id,
                description=description,
                transaction_id=transaction_id,
                created_by_user_id=created_by_user_id,
            )
            for client_id, share in _split_equally(amount, members)
        ]

    if decision.classification == MovementClassification.EXTERNAL_COMMISSION_WITHDRAWAL:
        return [
            _entry(
                group_id=group_id,
                decision=decision,
                entry_type=LedgerEntryType.COMMISSION_WITHDRAWN,
                amount=-amount,
                client_id=None,
                description=description,
                transaction_id=transaction_id,
                created_by_user_id=created_by_user_id,
            )
        ]

    if decision.classification == MovementClassification.PARTNER_COMMISSION_WITHDRAWAL:
        return [
            _entry(
                group_id=group_id,
                decision=decision,
                entry_type=LedgerEntryType.COMMISSION_WITHDRAWN,
                amount=-amount,
                client_id=_require_client_id(decision),
                description=description,
                transaction_id=transaction_id,
                created_by_user_id=created_by_user_id,
            )
        ]

    if decision.classification == MovementClassification.MIXED_COMMISSION_WITHDRAWAL:
        entries: list[LedgerEntry] = []
        remaining = amount
        if decision.external_amount and decision.external_amount > ZERO:
            entries.append(
                _entry(
                    group_id=group_id,
                    decision=decision,
                    entry_type=LedgerEntryType.COMMISSION_WITHDRAWN,
                    amount=-decision.external_amount,
                    client_id=None,
                    description=f"{description} - external commission",
                    transaction_id=transaction_id,
                    created_by_user_id=created_by_user_id,
                )
            )
            remaining -= decision.external_amount
        for label, client_id, split_amount in (
            ("partner 1 commission", decision.partner_1_client_id, decision.partner_1_amount),
            ("partner 2 commission", decision.partner_2_client_id, decision.partner_2_amount),
        ):
            if split_amount and split_amount > ZERO:
                if not client_id:
                    raise ValueError(f"{label} amount requires a client id")
                entries.append(
                    _entry(
                        group_id=group_id,
                        decision=decision,
                        entry_type=LedgerEntryType.COMMISSION_WITHDRAWN,
                        amount=-split_amount,
                        client_id=client_id,
                        description=f"{description} - {label}",
                        transaction_id=transaction_id,
                        created_by_user_id=created_by_user_id,
                    )
                )
                remaining -= split_amount
        if remaining != ZERO:
            raise ValueError("Mixed commission split amounts must equal the MT5 withdrawal amount")
        return entries

    if decision.classification == MovementClassification.TRANSFER_TO_NEW_MT5_ACCOUNT:
        return [
            _entry(
                group_id=group_id,
                decision=decision,
                entry_type=LedgerEntryType.TRANSFER_PENDING,
                amount=amount,
                client_id=None,
                mt5_account_id=movement.mt5_account_id,
                description=description,
                transaction_id=transaction_id,
                created_by_user_id=created_by_user_id,
            )
        ]

    if decision.classification == MovementClassification.TRANSFER_TO_EXISTING_MT5_ACCOUNT:
        pending = _entry(
            group_id=group_id,
            decision=decision,
            entry_type=LedgerEntryType.TRANSFER_PENDING,
            amount=amount,
            client_id=None,
            mt5_account_id=movement.mt5_account_id,
            description=description,
            transaction_id=transaction_id,
            created_by_user_id=created_by_user_id,
        )
        if not decision.to_mt5_account_id:
            return [pending]
        completed = LedgerEntry(
            group_id=group_id,
            client_id=None,
            mt5_account_id=movement.mt5_account_id,
            transaction_id=transaction_id,
            entry_type=LedgerEntryType.TRANSFER_COMPLETED,
            amount=amount,
            effective_date=decision.effective_date or movement.occurred_on,
            description="Imported internal transfer completed",
            created_by_user_id=created_by_user_id,
            metadata={**_base_metadata(decision), "to_mt5_account_id": decision.to_mt5_account_id},
        )
        return [pending, completed]

    if decision.classification in {
        MovementClassification.MANUAL_ADJUSTMENT,
        MovementClassification.BROKER_CORRECTION,
        MovementClassification.RETURNED_PENDING_TRANSFER,
    }:
        # Returned transfer/broker correction usually needs an admin-chosen client until
        # we add group-level cash-reserve accounting. Keeping it client-linked makes the
        # result auditable and visible in the ledger.
        signed_amount = movement.amount
        return [
            _entry(
                group_id=group_id,
                decision=decision,
                entry_type=LedgerEntryType.MANUAL_ADJUSTMENT,
                amount=signed_amount,
                client_id=_require_client_id(decision),
                description=description,
                transaction_id=transaction_id,
                created_by_user_id=created_by_user_id,
            )
        ]

    raise ValueError(f"Unsupported classification: {decision.classification}")


def review_import_classifications(
    *,
    group_id: str,
    import_mode: ExistingGroupImportMode,
    memberships: list[GroupMembership],
    decisions: list[MovementClassificationDecision],
    created_by_user_id: str | None = None,
) -> ImportWizardReview:
    ledger_entries: list[LedgerEntry] = []
    lines: list[ImportReviewLine] = []
    total_classified = ZERO
    total_ignored = ZERO
    for decision in decisions:
        entries = entries_for_classification(
            group_id=group_id,
            memberships=memberships,
            decision=decision,
            created_by_user_id=created_by_user_id,
        )
        if decision.classification == MovementClassification.IGNORE:
            total_ignored += decision.movement.absolute_amount
        else:
            total_classified += decision.movement.absolute_amount
        ledger_entries.extend(entries)
        lines.append(
            ImportReviewLine(
                movement_id=decision.movement.movement_id,
                classification=decision.classification,
                amount=decision.movement.amount,
                generated_entry_count=len(entries),
                description=decision.description or decision.movement.comment or decision.classification.value,
            )
        )
    return ImportWizardReview(
        group_id=group_id,
        import_mode=import_mode,
        total_detected=len(decisions),
        total_classified_amount=total_classified,
        total_ignored_amount=total_ignored,
        ledger_entries=ledger_entries,
        lines=lines,
    )
