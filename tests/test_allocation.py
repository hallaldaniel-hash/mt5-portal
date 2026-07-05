from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.domain.models import CommissionRule, GroupMember, MemberRole
from app.services.allocation import allocate_daily_profit_loss, allocate_equal_expense


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


def test_profit_day_model_b_commission():
    members = [
        GroupMember("client_a", "Client A", Decimal("1000"), MemberRole.NORMAL),
        GroupMember("client_b", "Client B", Decimal("500"), MemberRole.NORMAL),
        GroupMember("partner_1", "Partner 1", Decimal("1000"), MemberRole.PARTNER),
        GroupMember("partner_2", "Partner 2", Decimal("500"), MemberRole.PARTNER),
    ]

    result = allocate_daily_profit_loss(
        members=members,
        group_profit_loss=Decimal("300"),
        commission_rule=default_rule(),
    )

    by_client = {a.client_id: a for a in result.member_allocations}

    # Ownership: 1000/3000, 500/3000, 1000/3000, 500/3000
    assert money(by_client["client_a"].gross_profit_loss) == Decimal("100.00")
    assert money(by_client["client_b"].gross_profit_loss) == Decimal("50.00")

    # Normal clients pay 34% only on their own gross profit.
    assert money(by_client["client_a"].commission_paid) == Decimal("34.00")
    assert money(by_client["client_b"].commission_paid) == Decimal("17.00")

    assert money(result.total_commission_collected) == Decimal("51.00")

    # Partner commissions are credited to partner balances.
    assert money(by_client["partner_1"].commission_earned) == Decimal("14.25")
    assert money(by_client["partner_2"].commission_earned) == Decimal("14.25")

    assert money(result.external_commission_earned) == Decimal("22.50")

    # Whole group must balance: member net P/L + external commission = group P/L.
    member_net_total = sum(a.net_profit_loss for a in result.member_allocations)
    assert money(member_net_total + result.external_commission_earned) == Decimal("300.00")


def test_loss_day_has_no_commission():
    members = [
        GroupMember("client_a", "Client A", Decimal("1000"), MemberRole.NORMAL),
        GroupMember("partner_1", "Partner 1", Decimal("1000"), MemberRole.PARTNER),
    ]

    result = allocate_daily_profit_loss(
        members=members,
        group_profit_loss=Decimal("-100"),
        commission_rule=default_rule(),
    )

    assert result.total_commission_collected == Decimal("0")
    assert result.external_commission_earned == Decimal("0")
    assert result.partner_1_commission_earned == Decimal("0")
    assert result.partner_2_commission_earned == Decimal("0")

    by_client = {a.client_id: a for a in result.member_allocations}
    assert by_client["client_a"].net_profit_loss == Decimal("-50.0")
    assert by_client["partner_1"].net_profit_loss == Decimal("-50.0")


def test_equal_expense_split():
    members = [
        GroupMember("a", "A", Decimal("1000"), MemberRole.NORMAL),
        GroupMember("b", "B", Decimal("500"), MemberRole.NORMAL),
        GroupMember("c", "C", Decimal("2000"), MemberRole.PARTNER),
    ]

    allocations = allocate_equal_expense(members, Decimal("60"))

    assert len(allocations) == 3
    assert all(a.expense_share == Decimal("20") for a in allocations)


def test_requires_active_capital():
    with pytest.raises(ValueError):
        allocate_daily_profit_loss([], Decimal("10"), default_rule())
