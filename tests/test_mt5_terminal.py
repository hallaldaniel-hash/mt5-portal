from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.models import CommissionRule
from app.services.groups import create_group
from app.services.mt5_accounts import activate_mt5_account, create_mt5_account
from app.services.mt5_terminal import (
    MT5LoginError,
    MT5ReadError,
    connect_and_read_account_info,
    snapshot_from_account_info,
    sync_account_snapshot,
)


@dataclass
class FakeAccountInfo:
    balance: float
    equity: float
    profit: float
    margin: float
    margin_free: float


class FakeMT5Module:
    def __init__(self, *, initialize_ok: bool = True, account_info_value=None) -> None:
        self.initialize_ok = initialize_ok
        self.account_info_value = account_info_value or FakeAccountInfo(
            balance=270000.0,
            equity=271250.0,
            profit=1250.0,
            margin=4000.0,
            margin_free=267250.0,
        )
        self.initialize_kwargs = None
        self.shutdown_called = False

    def initialize(self, **kwargs):
        self.initialize_kwargs = kwargs
        return self.initialize_ok

    def account_info(self):
        return self.account_info_value

    def shutdown(self):
        self.shutdown_called = True

    def last_error(self):
        return (1, "fake error")


def make_account():
    group = create_group(name="Gold Bot Group", group_id="group_a", commission_rule=CommissionRule())
    return activate_mt5_account(
        create_mt5_account(
            group=group,
            nickname="Main Cent Account",
            broker_name="Broker A",
            server="BrokerA-Server",
            login="123456",
            sync_password="master-password",
            investor_login="123456-investor",
            investor_password="investor-password",
        )
    )


def test_connect_and_read_account_info_uses_account_credentials_and_shutdowns():
    account = make_account()
    fake_mt5 = FakeMT5Module()

    info = connect_and_read_account_info(
        account,
        mt5_module=fake_mt5,
        terminal_path="C:/Program Files/MetaTrader 5/terminal64.exe",
    )

    assert info.balance == 270000.0
    assert fake_mt5.initialize_kwargs == {
        "login": 123456,
        "password": "master-password",
        "server": "BrokerA-Server",
        "path": "C:/Program Files/MetaTrader 5/terminal64.exe",
    }
    assert fake_mt5.shutdown_called is True


def test_connect_raises_when_mt5_initialize_fails():
    account = make_account()
    fake_mt5 = FakeMT5Module(initialize_ok=False)

    with pytest.raises(MT5LoginError, match="initialize/login failed"):
        connect_and_read_account_info(account, mt5_module=fake_mt5)


def test_connect_raises_when_account_info_is_missing():
    account = make_account()
    fake_mt5 = FakeMT5Module(account_info_value=None)
    fake_mt5.account_info_value = None

    with pytest.raises(MT5ReadError, match="account_info returned no data"):
        connect_and_read_account_info(account, mt5_module=fake_mt5)


def test_snapshot_from_account_info_converts_live_mt5_values():
    account = make_account()
    info = FakeAccountInfo(
        balance=270000.0,
        equity=271250.0,
        profit=1250.0,
        margin=4000.0,
        margin_free=267250.0,
    )

    snapshot = snapshot_from_account_info(
        account,
        info,
        broker_server_time=datetime(2026, 6, 25, 23, 59, tzinfo=timezone.utc),
    )

    assert snapshot.raw_balance == Decimal("270000.0")
    assert snapshot.display_balance == Decimal("2700.0")
    assert snapshot.display_equity == Decimal("2712.5")
    assert snapshot.display_profit == Decimal("12.5")
    assert snapshot.display_margin == Decimal("40.0")
    assert snapshot.display_free_margin == Decimal("2672.5")
    assert snapshot.broker_server_day.isoformat() == "2026-06-25"


def test_sync_account_snapshot_returns_result():
    account = make_account()
    fake_mt5 = FakeMT5Module()

    result = sync_account_snapshot(
        account,
        mt5_module=fake_mt5,
        broker_server_time=datetime(2026, 6, 25, 23, 59, tzinfo=timezone.utc),
    )

    assert result.account_id == account.account_id
    assert result.group_id == "group_a"
    assert result.snapshot.display_balance == Decimal("2700.0")
    assert result.server == "BrokerA-Server"
