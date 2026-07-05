from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.models import CommissionRule
from app.domain.mt5 import MT5AccountStatus, SecretValue
from app.services.groups import create_group
from app.services.mt5_accounts import (
    activate_mt5_account,
    admin_visible_mt5_account,
    archive_mt5_account,
    client_visible_mt5_account,
    convert_raw_amount,
    create_mt5_account,
    create_mt5_snapshot,
    deactivate_mt5_account,
    latest_group_closed_balance,
    latest_snapshots_by_account,
    live_accounts_for_group,
)


def commission_rule() -> CommissionRule:
    return CommissionRule(
        partner_1_client_id="partner_1",
        partner_2_client_id="partner_2",
    )


def group(group_id: str = "group_a"):
    return create_group(
        name="Gold Bot Group",
        group_id=group_id,
        commission_rule=commission_rule(),
    )


def test_cent_account_converts_raw_mt5_amounts_to_display_usd():
    assert convert_raw_amount(Decimal("270000"), Decimal("100")) == Decimal("2700")
    assert convert_raw_amount(Decimal("2700"), Decimal("1")) == Decimal("2700")


def test_create_cent_mt5_account_defaults_to_pending_and_divisor_100():
    account = create_mt5_account(
        group=group(),
        nickname="Main Cent Account",
        broker_name="Broker A",
        server="BrokerA-Server",
        login="123456",
        sync_password="master-password",
        investor_login="123456",
        investor_password="investor-password",
    )

    assert account.status == MT5AccountStatus.PENDING
    assert account.display_divisor == Decimal("100")
    assert account.is_cent_account() is True
    assert account.group_id == "group_a"


def test_create_normal_mt5_account_uses_divisor_1():
    account = create_mt5_account(
        group=group(),
        nickname="Normal Account",
        broker_name="Broker B",
        server="BrokerB-Server",
        login="222222",
        sync_password="master-password",
        investor_login="222222",
        investor_password="investor-password",
        is_cent_account=False,
    )

    assert account.display_divisor == Decimal("1")
    assert account.is_cent_account() is False


def test_mt5_account_status_flow():
    account = create_mt5_account(
        group=group(),
        nickname="Pending Account",
        broker_name="Broker A",
        server="BrokerA-Server",
        login="123456",
        sync_password="master-password",
        investor_login="123456",
        investor_password="investor-password",
    )

    live = activate_mt5_account(account)
    inactive = deactivate_mt5_account(live)
    archived = archive_mt5_account(inactive)

    assert live.status == MT5AccountStatus.LIVE
    assert inactive.status == MT5AccountStatus.INACTIVE
    assert archived.status == MT5AccountStatus.ARCHIVED

    with pytest.raises(ValueError, match="Archived MT5 accounts cannot be activated"):
        activate_mt5_account(archived)


def test_snapshot_stores_raw_values_and_converted_display_values():
    account = activate_mt5_account(
        create_mt5_account(
            group=group(),
            nickname="Main Cent Account",
            broker_name="Broker A",
            server="BrokerA-Server",
            login="123456",
            sync_password="master-password",
            investor_login="123456",
            investor_password="investor-password",
        )
    )

    snapshot = create_mt5_snapshot(
        account=account,
        broker_server_time=datetime(2026, 6, 25, 23, 59, tzinfo=timezone.utc),
        raw_balance=Decimal("270000"),
        raw_equity=Decimal("271250"),
        raw_profit=Decimal("1250"),
        raw_margin=Decimal("4000"),
        raw_free_margin=Decimal("267250"),
    )

    assert snapshot.raw_balance == Decimal("270000")
    assert snapshot.display_balance == Decimal("2700")
    assert snapshot.display_equity == Decimal("2712.5")
    assert snapshot.display_profit == Decimal("12.5")
    assert snapshot.display_margin == Decimal("40")
    assert snapshot.display_free_margin == Decimal("2672.5")
    assert snapshot.broker_server_day.isoformat() == "2026-06-25"


def test_latest_snapshots_are_selected_by_broker_server_time():
    account = activate_mt5_account(
        create_mt5_account(
            group=group(),
            nickname="Main Cent Account",
            broker_name="Broker A",
            server="BrokerA-Server",
            login="123456",
            sync_password="master-password",
            investor_login="123456",
            investor_password="investor-password",
        )
    )
    old = create_mt5_snapshot(
        account=account,
        broker_server_time=datetime(2026, 6, 25, 20, 0, tzinfo=timezone.utc),
        raw_balance=Decimal("250000"),
        raw_equity=Decimal("250000"),
    )
    new = create_mt5_snapshot(
        account=account,
        broker_server_time=datetime(2026, 6, 25, 23, 59, tzinfo=timezone.utc),
        raw_balance=Decimal("270000"),
        raw_equity=Decimal("270000"),
    )

    latest = latest_snapshots_by_account([old, new])

    assert latest[account.account_id] == new


def test_group_closed_balance_sums_latest_snapshots_for_live_accounts_only():
    group_a = group("group_a")
    live_account = activate_mt5_account(
        create_mt5_account(
            group=group_a,
            nickname="Live Account",
            broker_name="Broker A",
            server="BrokerA-Server",
            login="111111",
            sync_password="master-password",
            investor_login="111111",
            investor_password="investor-password",
        )
    )
    pending_account = create_mt5_account(
        group=group_a,
        nickname="Pending Account",
        broker_name="Broker A",
        server="BrokerA-Server",
        login="222222",
        sync_password="master-password",
        investor_login="222222",
        investor_password="investor-password",
    )

    snapshots = [
        create_mt5_snapshot(
            account=live_account,
            broker_server_time=datetime(2026, 6, 25, 23, 59, tzinfo=timezone.utc),
            raw_balance=Decimal("200000"),
            raw_equity=Decimal("200000"),
        ),
        create_mt5_snapshot(
            account=pending_account,
            broker_server_time=datetime(2026, 6, 25, 23, 59, tzinfo=timezone.utc),
            raw_balance=Decimal("70000"),
            raw_equity=Decimal("70000"),
        ),
    ]

    assert live_accounts_for_group([live_account, pending_account], "group_a") == [
        live_account
    ]
    assert latest_group_closed_balance(
        accounts=[live_account, pending_account],
        snapshots=snapshots,
        group_id="group_a",
    ) == Decimal("2000")


def test_client_visible_account_includes_read_only_info_but_not_master_password():
    account = activate_mt5_account(
        create_mt5_account(
            group=group(),
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
    )

    visible = client_visible_mt5_account(account, snapshot)

    assert visible["nickname"] == "Main Cent Account"
    assert visible["investor_login"] == "123456-investor"
    assert visible["investor_password"] == "investor-password"
    assert visible["balance"] == "2700"
    assert visible["equity"] == "2710"
    assert visible["read_only_notice"].startswith("Read-only access")
    assert "sync_password" not in visible
    assert "master-password" not in str(visible)


def test_admin_visible_account_masks_passwords():
    account = create_mt5_account(
        group=group(),
        nickname="Main Cent Account",
        broker_name="Broker A",
        server="BrokerA-Server",
        login="123456",
        sync_password="master-password",
        investor_login="123456",
        investor_password="investor-password",
    )

    visible = admin_visible_mt5_account(account)

    assert visible["sync_password"] == "********"
    assert visible["investor_password"] == "********"
    assert "master-password" not in str(visible)
    assert "investor-password" not in str(visible)


def test_secret_value_repr_does_not_expose_password():
    secret = SecretValue("very-secret")

    assert secret.reveal() == "very-secret"
    assert "very-secret" not in repr(secret)
    assert secret.masked() == "********"


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError, match="display_divisor"):
        convert_raw_amount(Decimal("100"), Decimal("0"))

    with pytest.raises(ValueError, match="raw_balance cannot be negative"):
        account = create_mt5_account(
            group=group(),
            nickname="Bad Account",
            broker_name="Broker A",
            server="BrokerA-Server",
            login="123456",
            sync_password="master-password",
            investor_login="123456",
            investor_password="investor-password",
        )
        create_mt5_snapshot(
            account=account,
            broker_server_time=datetime(2026, 6, 25, 23, 59, tzinfo=timezone.utc),
            raw_balance=Decimal("-1"),
            raw_equity=Decimal("0"),
        )
