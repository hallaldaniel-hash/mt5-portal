from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.db.sqlite import (
    connect_database,
    get_client_profile,
    get_group,
    get_group_membership,
    get_ledger_entry,
    get_mt5_account,
    get_user_account,
    get_user_account_by_username,
    init_db,
    list_group_memberships,
    list_ledger_entries,
    list_mt5_accounts,
    list_mt5_snapshots,
    save_client_profile,
    save_group,
    save_group_membership,
    save_ledger_entry,
    save_ledger_entries,
    save_mt5_account,
    save_mt5_snapshot,
    save_user_account,
)
from app.domain.models import CommissionRule, MemberRole
from app.domain.portal import UserRole
from app.services.groups import (
    add_client_to_group,
    create_client_profile,
    create_group,
    create_user_account,
    verify_password,
)
from app.services.ledger import (
    client_balance,
    make_deposit_effective,
    record_deposit_pending,
    record_manual_adjustment,
)
from app.services.mt5_accounts import (
    activate_mt5_account,
    create_mt5_account,
    create_mt5_snapshot,
    latest_group_closed_balance,
)


def fresh_db():
    conn = connect_database()
    init_db(conn)
    return conn


def commission_rule() -> CommissionRule:
    return CommissionRule(
        partner_1_client_id="partner_1",
        partner_2_client_id="partner_2",
    )


def make_client(username: str, display_name: str, client_id: str):
    user = create_user_account(
        username=username,
        password="secret123",
        role=UserRole.CLIENT,
        user_id=f"user_{client_id}",
    )
    client = create_client_profile(
        display_name=display_name,
        user_account=user,
        client_id=client_id,
        email=f"{username}@example.com",
        email_reports_opt_in=True,
    )
    return user, client


def test_user_account_round_trips_with_password_hash():
    conn = fresh_db()
    account = create_user_account(
        username=" ClientA ",
        password="secret123",
        role=UserRole.CLIENT,
        user_id="user_1",
    )

    save_user_account(conn, account)
    loaded = get_user_account(conn, "user_1")
    loaded_by_username = get_user_account_by_username(conn, " CLIENTA ")

    assert loaded == account
    assert loaded_by_username == account
    assert loaded.password_hash != "secret123"
    assert verify_password(loaded, "secret123") is True


def test_sqlite_rejects_duplicate_usernames():
    conn = fresh_db()
    first = create_user_account(
        username="clienta",
        password="secret123",
        role=UserRole.CLIENT,
        user_id="user_1",
    )
    duplicate = create_user_account(
        username="clienta",
        password="secret456",
        role=UserRole.CLIENT,
        user_id="user_2",
    )

    save_user_account(conn, first)
    with pytest.raises(Exception):
        save_user_account(conn, duplicate)


def test_client_group_and_membership_round_trip():
    conn = fresh_db()
    user, client = make_client("clienta", "Client A", "client_a")
    group = create_group(
        name="Gold Bot Group",
        group_id="group_a",
        commission_rule=commission_rule(),
    )
    membership = add_client_to_group(
        group=group,
        client=client,
        effective_capital=Decimal("500"),
        role=MemberRole.NORMAL,
        joined_on=date(2026, 6, 25),
        effective_from=date(2026, 6, 26),
        membership_id="membership_1",
    )

    save_user_account(conn, user)
    save_client_profile(conn, client)
    save_group(conn, group)
    save_group_membership(conn, membership)

    assert get_client_profile(conn, "client_a") == client
    assert get_group(conn, "group_a") == group
    assert get_group_membership(conn, "membership_1") == membership
    assert list_group_memberships(conn, group_id="group_a") == [membership]
    assert list_group_memberships(conn, client_id="client_a") == [membership]


def test_group_specific_commission_settings_persist():
    conn = fresh_db()
    group = create_group(
        name="Custom Commission Group",
        group_id="group_custom",
        commission_rule=CommissionRule(
            total_rate=Decimal("0.20"),
            external_rate=Decimal("0.10"),
            partner_1_rate=Decimal("0.05"),
            partner_2_rate=Decimal("0.05"),
            partner_1_client_id="partner_a",
            partner_2_client_id="partner_b",
        ),
    )

    save_group(conn, group)
    loaded = get_group(conn, "group_custom")

    assert loaded == group
    assert loaded.commission_rule.total_rate == Decimal("0.20")
    assert loaded.commission_rule.partner_1_client_id == "partner_a"


def test_client_can_have_memberships_in_multiple_persisted_groups():
    conn = fresh_db()
    user, client = make_client("clienta", "Client A", "client_a")
    group_a = create_group(name="Group A", group_id="group_a", commission_rule=commission_rule())
    group_b = create_group(name="Group B", group_id="group_b", commission_rule=commission_rule())
    membership_a = add_client_to_group(
        group=group_a,
        client=client,
        effective_capital=Decimal("500"),
        membership_id="membership_a",
    )
    membership_b = add_client_to_group(
        group=group_b,
        client=client,
        effective_capital=Decimal("300"),
        membership_id="membership_b",
    )

    save_user_account(conn, user)
    save_client_profile(conn, client)
    save_group(conn, group_a)
    save_group(conn, group_b)
    save_group_membership(conn, membership_a)
    save_group_membership(conn, membership_b)

    memberships = list_group_memberships(conn, client_id="client_a")

    assert {membership.group_id for membership in memberships} == {"group_a", "group_b"}


def test_mt5_account_and_snapshots_persist_with_cent_conversion():
    conn = fresh_db()
    group = create_group(name="Gold Group", group_id="group_a", commission_rule=commission_rule())
    account = activate_mt5_account(
        create_mt5_account(
            group=group,
            account_id="account_1",
            nickname="Main Cent Account",
            broker_name="Broker A",
            server="BrokerA-Server",
            login="123456",
            sync_password="master-password",
            investor_login="123456-investor",
            investor_password="investor-password",
        )
    )
    snapshot = create_mt5_snapshot(
        account=account,
        broker_server_time=datetime(2026, 6, 25, 23, 59, tzinfo=timezone.utc),
        raw_balance=Decimal("270000"),
        raw_equity=Decimal("271000"),
        raw_profit=Decimal("1000"),
    )

    save_group(conn, group)
    save_mt5_account(conn, account)
    save_mt5_snapshot(conn, snapshot)

    loaded_account = get_mt5_account(conn, "account_1")
    loaded_snapshots = list_mt5_snapshots(conn, account_id="account_1")

    assert loaded_account == account
    assert loaded_snapshots == [snapshot]
    assert loaded_snapshots[0].display_balance == Decimal("2700")
    assert latest_group_closed_balance(
        accounts=list_mt5_accounts(conn, group_id="group_a"),
        snapshots=list_mt5_snapshots(conn, group_id="group_a"),
        group_id="group_a",
    ) == Decimal("2700")


def test_ledger_entries_persist_and_still_calculate_balances():
    conn = fresh_db()
    group = create_group(name="Gold Group", group_id="group_a", commission_rule=commission_rule())
    user, client = make_client("clienta", "Client A", "client_a")
    deposit_pending = record_deposit_pending(
        group_id="group_a",
        client_id="client_a",
        amount=Decimal("500"),
        effective_date=date(2026, 6, 26),
        created_by_user_id="admin_1",
    )
    deposit_effective = make_deposit_effective(deposit_pending)
    adjustment = record_manual_adjustment(
        group_id="group_a",
        client_id="client_a",
        amount=Decimal("12.34"),
        reason="Broker correction",
        created_by_user_id="admin_1",
    )

    save_user_account(conn, user)
    save_client_profile(conn, client)
    save_group(conn, group)
    save_ledger_entries(conn, [deposit_pending, deposit_effective, adjustment])

    entries = list_ledger_entries(conn, group_id="group_a", client_id="client_a")

    assert get_ledger_entry(conn, deposit_pending.entry_id) == deposit_pending
    assert len(entries) == 3
    assert client_balance(entries, "group_a", "client_a") == Decimal("512.34")
    assert entries[-1].metadata["reason"] == "Broker correction"


def test_database_can_be_created_on_disk(tmp_path):
    db_path = tmp_path / "portal.sqlite3"
    conn = connect_database(db_path)
    init_db(conn)
    group = create_group(name="Disk Group", group_id="group_a", commission_rule=commission_rule())
    save_group(conn, group)
    conn.close()

    reopened = connect_database(db_path)
    init_db(reopened)

    assert get_group(reopened, "group_a") == group
