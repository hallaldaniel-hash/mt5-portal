from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.domain.models import CommissionRule, MemberRole
from app.domain.portal import UserRole
from app.services.allocation import allocate_daily_profit_loss
from app.services.groups import (
    active_group_members,
    add_client_to_group,
    client_balances_by_group,
    combined_client_balance,
    create_client_profile,
    create_group,
    create_user_account,
    memberships_for_client,
    memberships_for_group,
    reset_password,
    verify_password,
)
from app.services.ledger import make_deposit_effective, record_deposit_pending


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def commission_rule(
    *,
    total: str = "0.34",
    external: str = "0.15",
    partner_1: str = "0.095",
    partner_2: str = "0.095",
    partner_1_client_id: str = "partner_1",
    partner_2_client_id: str = "partner_2",
) -> CommissionRule:
    return CommissionRule(
        total_rate=Decimal(total),
        external_rate=Decimal(external),
        partner_1_rate=Decimal(partner_1),
        partner_2_rate=Decimal(partner_2),
        partner_1_client_id=partner_1_client_id,
        partner_2_client_id=partner_2_client_id,
    )


def client_user(username: str):
    return create_user_account(
        username=username,
        password="secret123",
        role=UserRole.CLIENT,
    )


def client_profile(username: str, name: str, client_id: str):
    return create_client_profile(
        display_name=name,
        user_account=client_user(username),
        client_id=client_id,
    )


def test_admin_created_client_user_hashes_and_verifies_password():
    account = create_user_account(
        username=" ClientA ",
        password="secret123",
        role=UserRole.CLIENT,
    )

    assert account.username == "clienta"
    assert account.password_hash != "secret123"
    assert verify_password(account, "secret123") is True
    assert verify_password(account, "wrong-password") is False

    updated = reset_password(account, "newsecret123")
    assert verify_password(updated, "newsecret123") is True
    assert verify_password(updated, "secret123") is False


def test_duplicate_usernames_are_rejected():
    existing = [
        create_user_account(
            username="ahmad",
            password="secret123",
            role=UserRole.CLIENT,
        )
    ]

    with pytest.raises(ValueError, match="Username already exists"):
        create_user_account(
            username=" Ahmad ",
            password="secret456",
            role=UserRole.CLIENT,
            existing_accounts=existing,
        )


def test_email_report_opt_in_requires_email():
    with pytest.raises(ValueError, match="Email is required"):
        create_client_profile(
            display_name="Client A",
            user_account=client_user("client_a"),
            email_reports_opt_in=True,
        )


def test_client_can_belong_to_multiple_groups_and_group_can_have_one_client():
    client = client_profile("client_a", "Client A", "client_a")
    group_a = create_group(
        name="Gold Bot Group A",
        commission_rule=commission_rule(),
        group_id="group_a",
    )
    group_b = create_group(
        name="Single Person Group",
        commission_rule=commission_rule(),
        group_id="group_b",
    )

    memberships = [
        add_client_to_group(
            group=group_a,
            client=client,
            effective_capital=Decimal("500"),
            joined_on=date(2026, 6, 25),
        ),
        add_client_to_group(
            group=group_b,
            client=client,
            effective_capital=Decimal("300"),
            joined_on=date(2026, 6, 25),
        ),
    ]

    assert len(memberships_for_client(memberships, "client_a")) == 2
    assert len(memberships_for_group(memberships, "group_b")) == 1
    assert active_group_members(memberships, "group_b")[0].client_id == "client_a"


def test_group_specific_commission_settings_are_used():
    normal = client_profile("client_a", "Client A", "client_a")
    partner_1 = client_profile("partner_one", "Partner 1", "partner_1")
    partner_2 = client_profile("partner_two", "Partner 2", "partner_2")

    group_a = create_group(
        name="34 Percent Group",
        group_id="group_a",
        commission_rule=commission_rule(),
    )
    group_b = create_group(
        name="20 Percent Group",
        group_id="group_b",
        commission_rule=commission_rule(
            total="0.20",
            external="0.10",
            partner_1="0.05",
            partner_2="0.05",
        ),
    )

    memberships = []
    for group in [group_a, group_b]:
        memberships.extend(
            [
                add_client_to_group(
                    group=group,
                    client=normal,
                    effective_capital=Decimal("1000"),
                    role=MemberRole.NORMAL,
                ),
                add_client_to_group(
                    group=group,
                    client=partner_1,
                    effective_capital=Decimal("1000"),
                    role=MemberRole.PARTNER,
                ),
                add_client_to_group(
                    group=group,
                    client=partner_2,
                    effective_capital=Decimal("1000"),
                    role=MemberRole.PARTNER,
                ),
            ]
        )

    result_a = allocate_daily_profit_loss(
        active_group_members(memberships, "group_a"),
        Decimal("300"),
        group_a.commission_rule,
    )
    result_b = allocate_daily_profit_loss(
        active_group_members(memberships, "group_b"),
        Decimal("300"),
        group_b.commission_rule,
    )

    normal_a = next(a for a in result_a.member_allocations if a.client_id == "client_a")
    normal_b = next(a for a in result_b.member_allocations if a.client_id == "client_a")

    assert money(normal_a.commission_paid) == Decimal("34.00")
    assert money(normal_b.commission_paid) == Decimal("20.00")
    assert money(result_a.external_commission_earned) == Decimal("15.00")
    assert money(result_b.external_commission_earned) == Decimal("10.00")


def test_partners_are_defined_per_group():
    same_person = client_profile("sam", "Sam", "sam")
    partner_1 = client_profile("partner_one", "Partner 1", "partner_1")
    partner_2 = client_profile("partner_two", "Partner 2", "partner_2")

    group_normal = create_group(
        name="Sam Normal Group",
        group_id="group_normal",
        commission_rule=commission_rule(),
    )
    group_partner = create_group(
        name="Sam Partner Group",
        group_id="group_partner",
        commission_rule=commission_rule(partner_1_client_id="sam"),
    )

    memberships = [
        add_client_to_group(
            group=group_normal,
            client=same_person,
            effective_capital=Decimal("1000"),
            role=MemberRole.NORMAL,
        ),
        add_client_to_group(
            group=group_normal,
            client=partner_1,
            effective_capital=Decimal("1000"),
            role=MemberRole.PARTNER,
        ),
        add_client_to_group(
            group=group_normal,
            client=partner_2,
            effective_capital=Decimal("1000"),
            role=MemberRole.PARTNER,
        ),
        add_client_to_group(
            group=group_partner,
            client=same_person,
            effective_capital=Decimal("1000"),
            role=MemberRole.PARTNER,
        ),
        add_client_to_group(
            group=group_partner,
            client=partner_2,
            effective_capital=Decimal("1000"),
            role=MemberRole.PARTNER,
        ),
    ]

    normal_group_result = allocate_daily_profit_loss(
        active_group_members(memberships, "group_normal"),
        Decimal("300"),
        group_normal.commission_rule,
    )
    partner_group_result = allocate_daily_profit_loss(
        active_group_members(memberships, "group_partner"),
        Decimal("200"),
        group_partner.commission_rule,
    )

    sam_normal = next(
        a for a in normal_group_result.member_allocations if a.client_id == "sam"
    )
    sam_partner = next(
        a for a in partner_group_result.member_allocations if a.client_id == "sam"
    )

    assert sam_normal.role == MemberRole.NORMAL
    assert money(sam_normal.commission_paid) == Decimal("34.00")
    assert sam_partner.role == MemberRole.PARTNER
    assert money(sam_partner.commission_paid) == Decimal("0.00")


def test_client_dashboard_can_show_combined_and_per_group_balances():
    client = client_profile("client_a", "Client A", "client_a")
    group_a = create_group(
        name="Group A",
        group_id="group_a",
        commission_rule=commission_rule(),
    )
    group_b = create_group(
        name="Group B",
        group_id="group_b",
        commission_rule=commission_rule(),
    )
    memberships = [
        add_client_to_group(
            group=group_a,
            client=client,
            effective_capital=Decimal("500"),
        ),
        add_client_to_group(
            group=group_b,
            client=client,
            effective_capital=Decimal("300"),
        ),
    ]

    deposit_a = make_deposit_effective(
        record_deposit_pending(
            group_id="group_a",
            client_id="client_a",
            amount=Decimal("500"),
            effective_date=date(2026, 6, 26),
        )
    )
    deposit_b = make_deposit_effective(
        record_deposit_pending(
            group_id="group_b",
            client_id="client_a",
            amount=Decimal("300"),
            effective_date=date(2026, 6, 26),
        )
    )
    entries = [deposit_a, deposit_b]

    assert client_balances_by_group(
        entries=entries,
        memberships=memberships,
        client_id="client_a",
    ) == {"group_a": Decimal("500"), "group_b": Decimal("300")}
    assert combined_client_balance(
        entries=entries,
        memberships=memberships,
        client_id="client_a",
    ) == Decimal("800")


def test_negative_effective_capital_is_rejected():
    client = client_profile("client_a", "Client A", "client_a")
    group = create_group(
        name="Group A",
        group_id="group_a",
        commission_rule=commission_rule(),
    )

    with pytest.raises(ValueError, match="cannot be negative"):
        add_client_to_group(
            group=group,
            client=client,
            effective_capital=Decimal("-1"),
        )
