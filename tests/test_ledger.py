from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.domain.ledger import LedgerEntryType
from app.domain.models import CommissionRule, GroupMember, MemberRole
from app.services.allocation import allocate_daily_profit_loss
from app.services.ledger import (
    approve_withdrawal,
    available_balance,
    client_balance,
    complete_internal_transfer,
    external_commission_payable,
    group_client_balances,
    make_deposit_effective,
    make_expense_effective,
    make_withdrawal_effective,
    mark_withdrawal_paid,
    pending_withdrawal_total,
    record_commission_withdrawal,
    record_daily_allocation_entries,
    record_deposit_pending,
    record_equal_expense_pending,
    record_internal_transfer_pending,
    record_manual_adjustment,
    request_withdrawal,
)


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def default_rule() -> CommissionRule:
    return CommissionRule(
        total_rate=Decimal("0.34"),
        external_rate=Decimal("0.15"),
        partner_1_rate=Decimal("0.095"),
        partner_2_rate=Decimal("0.095"),
        partner_1_client_id="partner_1",
        partner_2_client_id="partner_2",
    )


def sample_members() -> list[GroupMember]:
    return [
        GroupMember("client_a", "Client A", Decimal("1000"), MemberRole.NORMAL),
        GroupMember("client_b", "Client B", Decimal("500"), MemberRole.NORMAL),
        GroupMember("partner_1", "Partner 1", Decimal("1000"), MemberRole.PARTNER),
        GroupMember("partner_2", "Partner 2", Decimal("500"), MemberRole.PARTNER),
    ]


def test_deposit_pending_does_not_affect_balance_until_effective():
    entries = []
    pending = record_deposit_pending(
        group_id="group_1",
        client_id="client_a",
        amount=Decimal("500"),
        effective_date=date(2026, 6, 26),
    )
    entries.append(pending)

    assert client_balance(entries, "group_1", "client_a") == Decimal("0")

    effective = make_deposit_effective(pending)
    entries.append(effective)

    assert effective.transaction_id == pending.transaction_id
    assert effective.entry_type == LedgerEntryType.DEPOSIT_EFFECTIVE
    assert client_balance(entries, "group_1", "client_a") == Decimal("500")


def test_withdrawal_lifecycle_affects_available_then_final_balance():
    entries = [
        make_deposit_effective(
            record_deposit_pending(
                group_id="group_1",
                client_id="client_a",
                amount=Decimal("100"),
                effective_date=date(2026, 6, 26),
            )
        )
    ]

    too_large = request_withdrawal(
        group_id="group_1",
        client_id="client_a",
        amount=Decimal("150"),
        available_balance_amount=available_balance(entries, "group_1", "client_a"),
    )
    assert too_large.entry_type == LedgerEntryType.WITHDRAWAL_REJECTED

    request = request_withdrawal(
        group_id="group_1",
        client_id="client_a",
        amount=Decimal("40"),
        available_balance_amount=available_balance(entries, "group_1", "client_a"),
    )
    entries.append(request)

    assert client_balance(entries, "group_1", "client_a") == Decimal("100")
    assert pending_withdrawal_total(entries, "group_1", "client_a") == Decimal("40")
    assert available_balance(entries, "group_1", "client_a") == Decimal("60")

    approved = approve_withdrawal(request, effective_date=date(2026, 6, 27))
    entries.append(approved)
    assert client_balance(entries, "group_1", "client_a") == Decimal("100")

    effective = make_withdrawal_effective(approved)
    entries.append(effective)
    assert client_balance(entries, "group_1", "client_a") == Decimal("60")
    assert pending_withdrawal_total(entries, "group_1", "client_a") == Decimal("0")

    paid = mark_withdrawal_paid(effective)
    entries.append(paid)
    assert client_balance(entries, "group_1", "client_a") == Decimal("60")


def test_withdrawal_minimum_is_enforced():
    with pytest.raises(ValueError, match="Withdrawal minimum"):
        request_withdrawal(
            group_id="group_1",
            client_id="client_a",
            amount=Decimal("9.99"),
            available_balance_amount=Decimal("100"),
        )


def test_equal_expense_pending_then_effective():
    entries = record_equal_expense_pending(
        group_id="group_1",
        members=sample_members(),
        amount=Decimal("80"),
        effective_date=date(2026, 6, 26),
        description="VPS expense",
    )

    assert len(entries) == 4
    assert all(entry.entry_type == LedgerEntryType.EXPENSE_PENDING for entry in entries)
    assert client_balance(entries, "group_1", "client_a") == Decimal("0")

    effective_entries = [make_expense_effective(entry) for entry in entries]
    all_entries = entries + effective_entries

    assert client_balance(all_entries, "group_1", "client_a") == Decimal("-20")
    assert client_balance(all_entries, "group_1", "partner_1") == Decimal("-20")


def test_internal_transfer_never_changes_client_balances():
    entries = [
        make_deposit_effective(
            record_deposit_pending(
                group_id="group_1",
                client_id="client_a",
                amount=Decimal("500"),
                effective_date=date(2026, 6, 26),
            )
        )
    ]

    pending_transfer = record_internal_transfer_pending(
        group_id="group_1",
        from_mt5_account_id="account_1",
        amount=Decimal("650"),
        description="Move funds to open Account 2",
    )
    completed_transfer = complete_internal_transfer(
        pending_transfer, to_mt5_account_id="account_2"
    )
    entries.extend([pending_transfer, completed_transfer])

    assert client_balance(entries, "group_1", "client_a") == Decimal("500")


def test_daily_allocation_entries_match_accounting_result():
    result = allocate_daily_profit_loss(
        members=sample_members(),
        group_profit_loss=Decimal("300"),
        commission_rule=default_rule(),
    )

    entries = record_daily_allocation_entries(
        group_id="group_1",
        allocation_result=result,
        allocation_date=date(2026, 6, 26),
    )

    balances = group_client_balances(entries, "group_1")

    by_client = {allocation.client_id: allocation for allocation in result.member_allocations}
    for client_id, allocation in by_client.items():
        assert money(balances[client_id]) == money(allocation.net_profit_loss)

    assert money(external_commission_payable(entries, "group_1")) == money(
        result.external_commission_earned
    )

    total_client_net = sum(balances.values())
    assert money(total_client_net + external_commission_payable(entries, "group_1")) == Decimal(
        "300.00"
    )


def test_commission_withdrawal_reduces_partner_balance_or_external_payable():
    result = allocate_daily_profit_loss(
        members=sample_members(),
        group_profit_loss=Decimal("300"),
        commission_rule=default_rule(),
    )
    entries = record_daily_allocation_entries(
        group_id="group_1",
        allocation_result=result,
        allocation_date=date(2026, 6, 26),
    )

    partner_withdrawal = record_commission_withdrawal(
        group_id="group_1",
        client_id="partner_1",
        amount=Decimal("5"),
        description="Partner 1 commission withdrawn",
    )
    external_withdrawal = record_commission_withdrawal(
        group_id="group_1",
        client_id=None,
        amount=Decimal("10"),
        description="External commission withdrawn",
    )
    entries.extend([partner_withdrawal, external_withdrawal])

    partner_balance_after = client_balance(entries, "group_1", "partner_1")
    partner_balance_before = next(
        allocation.net_profit_loss
        for allocation in result.member_allocations
        if allocation.client_id == "partner_1"
    )

    assert money(partner_balance_after) == money(partner_balance_before - Decimal("5"))
    assert money(external_commission_payable(entries, "group_1")) == money(
        result.external_commission_earned - Decimal("10")
    )


def test_manual_adjustment_requires_reason_and_affects_balance():
    with pytest.raises(ValueError, match="requires a reason"):
        record_manual_adjustment(
            group_id="group_1",
            client_id="client_a",
            amount=Decimal("10"),
            reason="",
            created_by_user_id="admin_1",
        )

    adjustment = record_manual_adjustment(
        group_id="group_1",
        client_id="client_a",
        amount=Decimal("10"),
        reason="Broker correction",
        created_by_user_id="admin_1",
    )
    assert adjustment.entry_type == LedgerEntryType.MANUAL_ADJUSTMENT
    assert client_balance([adjustment], "group_1", "client_a") == Decimal("10")
