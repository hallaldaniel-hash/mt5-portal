from __future__ import annotations

from dataclasses import dataclass, field
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
class PartnerCommissionShare:
    """Step 28 dynamic partner commission recipient.

    This allows the portal to support Partner 3, Partner 4, etc. later
    without destroying the current partner_1 / partner_2 compatibility.
    """

    client_id: str
    name: str
    rate: Decimal

    @classmethod
    def from_percentage(cls, client_id: str, name: str, percentage: Decimal) -> "PartnerCommissionShare":
        """Allow admin UI to send 34 instead of 0.34."""
        return cls(
            client_id=client_id,
            name=name,
            rate=percentage_to_rate(percentage),
        )


def percentage_to_rate(value: Decimal) -> Decimal:
    """Convert admin-entered percentage into internal decimal rate.

    Example:
    - UI/admin enters 34
    - backend stores Decimal("0.34")

    If the value already looks like a decimal rate between 0 and 1,
    it is returned unchanged to avoid breaking existing code/tests.
    """

    if value > Decimal("1"):
        return value / Decimal("100")

    return value


@dataclass(frozen=True)
class CommissionRule:
    """
    Commission model B:
    - Normal clients pay commission only on their own positive profit.
    - Partners do not pay commission on their own investor profit.
    - Collected commission is split between the external recipient and partner recipients.

    Step 28 additions:
    - Admin UI may input percentages as whole numbers, e.g. 34 means 0.34.
    - Dynamic partner recipients are supported through dynamic_partners.
    - Old partner_1 / partner_2 fields remain for backward compatibility.
    """

    total_rate: Decimal = Decimal("0.34")
    external_rate: Decimal = Decimal("0.15")
    partner_1_rate: Decimal = Decimal("0.095")
    partner_2_rate: Decimal = Decimal("0.095")
    partner_1_client_id: str | None = None
    partner_2_client_id: str | None = None

    # Step 28 future-ready dynamic partner list.
    # Current services may still use partner_1/partner_2.
    # Later UI can switch to this list for Partner 3, Partner 4, etc.
    dynamic_partners: tuple[PartnerCommissionShare, ...] = field(default_factory=tuple)

    @classmethod
    def from_admin_percentages(
        cls,
        total_rate: Decimal,
        external_rate: Decimal,
        partner_1_rate: Decimal = Decimal("0"),
        partner_2_rate: Decimal = Decimal("0"),
        partner_1_client_id: str | None = None,
        partner_2_client_id: str | None = None,
        dynamic_partners: tuple[PartnerCommissionShare, ...] = (),
    ) -> "CommissionRule":
        """Create a CommissionRule from admin-friendly percent inputs.

        Example:
        CommissionRule.from_admin_percentages(
            total_rate=Decimal("34"),
            external_rate=Decimal("15"),
            partner_1_rate=Decimal("9.5"),
            partner_2_rate=Decimal("9.5"),
        )

        Internally becomes:
        total_rate=Decimal("0.34")
        external_rate=Decimal("0.15")
        partner_1_rate=Decimal("0.095")
        partner_2_rate=Decimal("0.095")
        """

        return cls(
            total_rate=percentage_to_rate(total_rate),
            external_rate=percentage_to_rate(external_rate),
            partner_1_rate=percentage_to_rate(partner_1_rate),
            partner_2_rate=percentage_to_rate(partner_2_rate),
            partner_1_client_id=partner_1_client_id,
            partner_2_client_id=partner_2_client_id,
            dynamic_partners=dynamic_partners,
        )

    def uses_dynamic_partners(self) -> bool:
        return len(self.dynamic_partners) > 0

    def partner_commission_total(self) -> Decimal:
        if self.uses_dynamic_partners():
            return sum((partner.rate for partner in self.dynamic_partners), Decimal("0"))

        return self.partner_1_rate + self.partner_2_rate

    def component_total(self) -> Decimal:
        return self.external_rate + self.partner_commission_total()

    def validate(self) -> None:
        if self.total_rate <= 0:
            raise ValueError("total_rate must be greater than zero")

        if self.external_rate < 0:
            raise ValueError("external_rate cannot be negative")

        if self.partner_1_rate < 0 or self.partner_2_rate < 0:
            raise ValueError("partner rates cannot be negative")

        for partner in self.dynamic_partners:
            if partner.rate < 0:
                raise ValueError("dynamic partner rates cannot be negative")

        component_sum = self.component_total()

        if component_sum != self.total_rate:
            raise ValueError(
                "external_rate + partner commission rates must equal total_rate"
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