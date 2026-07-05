from __future__ import annotations

from decimal import Decimal

from app.domain.models import (
    CommissionRule,
    DailyAllocationResult,
    ExpenseAllocation,
    GroupMember,
    MemberAllocation,
    MemberRole,
)

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


def _active_members(members: list[GroupMember]) -> list[GroupMember]:
    active = [m for m in members if m.is_active]
    if not active:
        raise ValueError("At least one active member is required")
    return active


def calculate_ownership_percentages(
    members: list[GroupMember],
) -> dict[str, Decimal]:
    active = _active_members(members)
    total_capital = sum((m.effective_capital for m in active), ZERO)

    if total_capital <= ZERO:
        raise ValueError("Total effective capital must be greater than zero")

    return {
        m.client_id: (m.effective_capital / total_capital) * ONE_HUNDRED
        for m in active
    }


def allocate_daily_profit_loss(
    members: list[GroupMember],
    group_profit_loss: Decimal,
    commission_rule: CommissionRule,
) -> DailyAllocationResult:
    """
    Allocate one finalized daily group profit/loss across members.

    Rules implemented:
    - Ownership is based on each active member's effective capital.
    - Positive days: normal clients pay commission on their own gross profit.
    - Positive days: partner clients do not pay commission on their own gross profit.
    - Collected commission is split according to the commission rule.
    - Loss days: no commission is charged. Loss is split by ownership.
    """

    commission_rule.validate()
    active = _active_members(members)
    total_capital = sum((m.effective_capital for m in active), ZERO)

    if total_capital <= ZERO:
        raise ValueError("Total effective capital must be greater than zero")

    raw_allocations: dict[str, dict[str, Decimal]] = {}
    total_commission_collected = ZERO

    for member in active:
        ownership_ratio = member.effective_capital / total_capital
        gross_pl = group_profit_loss * ownership_ratio

        commission_paid = ZERO
        if group_profit_loss > ZERO and member.role == MemberRole.NORMAL:
            commission_paid = gross_pl * commission_rule.total_rate
            total_commission_collected += commission_paid

        raw_allocations[member.client_id] = {
            "ownership_percent": ownership_ratio * ONE_HUNDRED,
            "gross_pl": gross_pl,
            "commission_paid": commission_paid,
            "commission_earned": ZERO,
        }

    external_commission = total_commission_collected * (
        commission_rule.external_rate / commission_rule.total_rate
    )
    partner_1_commission = total_commission_collected * (
        commission_rule.partner_1_rate / commission_rule.total_rate
    )
    partner_2_commission = total_commission_collected * (
        commission_rule.partner_2_rate / commission_rule.total_rate
    )

    if commission_rule.partner_1_client_id in raw_allocations:
        raw_allocations[commission_rule.partner_1_client_id][
            "commission_earned"
        ] += partner_1_commission

    if commission_rule.partner_2_client_id in raw_allocations:
        raw_allocations[commission_rule.partner_2_client_id][
            "commission_earned"
        ] += partner_2_commission

    member_allocations: list[MemberAllocation] = []
    for member in active:
        raw = raw_allocations[member.client_id]
        net_pl = raw["gross_pl"] - raw["commission_paid"] + raw["commission_earned"]
        closing_capital = member.effective_capital + net_pl

        member_allocations.append(
            MemberAllocation(
                client_id=member.client_id,
                name=member.name,
                role=member.role,
                opening_capital=member.effective_capital,
                ownership_percent=raw["ownership_percent"],
                gross_profit_loss=raw["gross_pl"],
                commission_paid=raw["commission_paid"],
                commission_earned=raw["commission_earned"],
                net_profit_loss=net_pl,
                closing_capital=closing_capital,
            )
        )

    return DailyAllocationResult(
        group_profit_loss=group_profit_loss,
        total_effective_capital=total_capital,
        total_commission_collected=total_commission_collected,
        external_commission_earned=external_commission,
        partner_1_commission_earned=partner_1_commission,
        partner_2_commission_earned=partner_2_commission,
        member_allocations=member_allocations,
    )


def allocate_equal_expense(
    members: list[GroupMember], amount: Decimal
) -> list[ExpenseAllocation]:
    """Split one group expense equally across all active group members."""

    if amount <= ZERO:
        raise ValueError("Expense amount must be greater than zero")

    active = _active_members(members)
    share = amount / Decimal(len(active))

    return [
        ExpenseAllocation(
            client_id=member.client_id,
            name=member.name,
            expense_share=share,
        )
        for member in active
    ]
