from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class MemberRole(StrEnum):
    NORMAL = "normal"
    PARTNER = "partner"


@dataclass(frozen=True)
class GroupMember:
    """A client's effective position inside one group/pool."""

    client_id: str
    name: str
    effective_capital: Decimal
    role: MemberRole = MemberRole.NORMAL
    is_active: bool = True


@dataclass(frozen=True)
class CommissionRule:
    """
    Commission model B:
    - Normal clients pay commission only on their own positive profit.
    - Partners do not pay commission on their own investor profit.
    - Collected commission is split between the external recipient and partner recipients.
    """

    total_rate: Decimal = Decimal("0.34")
    external_rate: Decimal = Decimal("0.15")
    partner_1_rate: Decimal = Decimal("0.095")
    partner_2_rate: Decimal = Decimal("0.095")
    partner_1_client_id: str | None = None
    partner_2_client_id: str | None = None

    def validate(self) -> None:
        if self.total_rate <= 0:
            raise ValueError("total_rate must be greater than zero")

        component_sum = self.external_rate + self.partner_1_rate + self.partner_2_rate
        if component_sum != self.total_rate:
            raise ValueError(
                "external_rate + partner_1_rate + partner_2_rate must equal total_rate"
            )


@dataclass(frozen=True)
class MemberAllocation:
    client_id: str
    name: str
    role: MemberRole
    opening_capital: Decimal
    ownership_percent: Decimal
    gross_profit_loss: Decimal
    commission_paid: Decimal
    commission_earned: Decimal
    net_profit_loss: Decimal
    closing_capital: Decimal


@dataclass(frozen=True)
class DailyAllocationResult:
    group_profit_loss: Decimal
    total_effective_capital: Decimal
    total_commission_collected: Decimal
    external_commission_earned: Decimal
    partner_1_commission_earned: Decimal
    partner_2_commission_earned: Decimal
    member_allocations: list[MemberAllocation]


@dataclass(frozen=True)
class ExpenseAllocation:
    client_id: str
    name: str
    expense_share: Decimal
