from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.domain.models import CommissionRule, MemberRole
from app.security.encryption import generate_secret_key
from app.services.daily_close import DailyCloseAdjustment, calculate_trading_profit_loss


def test_trading_profit_loss_adjusts_non_trading_movements() -> None:
    profit_loss = calculate_trading_profit_loss(
        opening_closed_balance=Decimal("1000"),
        closing_closed_balance=Decimal("1150"),
        adjustments=DailyCloseAdjustment(
            deposits_effective=Decimal("100"),
            withdrawals_effective=Decimal("20"),
            expenses_effective=Decimal("10"),
            pending_internal_transfers=Decimal("30"),
        ),
    )

    assert profit_loss == Decimal("110")


def make_client() -> TestClient:
    return TestClient(create_app(secret_key=generate_secret_key()))


def _create_client(client: TestClient, username: str, display_name: str | None = None) -> str:
    response = client.post(
        "/api/clients",
        json={"username": username, "password": "secret123", "display_name": display_name or username.title()},
    )
    assert response.status_code == 201
    return response.json()["client"]["client_id"]


def _create_group(client: TestClient, *, partner_1: str, partner_2: str) -> str:
    response = client.post(
        "/api/groups",
        json={
            "name": "Gold Bot Group",
            "partner_1_client_id": partner_1,
            "partner_2_client_id": partner_2,
        },
    )
    assert response.status_code == 201
    return response.json()["group_id"]


def _add_member(client: TestClient, group_id: str, client_id: str, capital: str, role: str = "normal") -> None:
    response = client.post(
        f"/api/groups/{group_id}/members",
        json={"client_id": client_id, "effective_capital": capital, "role": role},
    )
    assert response.status_code == 201


def _effective_deposit(client: TestClient, group_id: str, client_id: str, amount: str, effective_date: str) -> None:
    pending = client.post(
        f"/api/groups/{group_id}/deposits/pending",
        json={"client_id": client_id, "amount": amount, "effective_date": effective_date},
    )
    assert pending.status_code == 201
    effective = client.post(f"/api/ledger/{pending.json()['entry_id']}/deposit/effective")
    assert effective.status_code == 200


def _create_live_mt5_account(client: TestClient, group_id: str) -> str:
    response = client.post(
        f"/api/groups/{group_id}/mt5-accounts",
        json={
            "nickname": "Main Cent Account",
            "broker_name": "Test Broker",
            "server": "TestBroker-Demo",
            "login": "123456",
            "sync_password": "master-password",
            "investor_login": "123456ro",
            "investor_password": "readonly-password",
            "is_cent_account": True,
            "status": "live",
        },
    )
    assert response.status_code == 201
    return response.json()["account_id"]


def _snapshot(client: TestClient, account_id: str, day: str, raw_balance: str) -> None:
    response = client.post(
        f"/api/mt5-accounts/{account_id}/snapshots",
        json={
            "broker_server_time": f"{day}T23:59:00+00:00",
            "broker_server_day": day,
            "raw_balance": raw_balance,
            "raw_equity": raw_balance,
            "raw_profit": "0",
        },
    )
    assert response.status_code == 201


def test_api_finalize_daily_close_allocates_from_mt5_balance_movement() -> None:
    client = make_client()
    normal_1 = _create_client(client, "normalclose1", "Normal Close One")
    normal_2 = _create_client(client, "normalclose2", "Normal Close Two")
    partner_1 = _create_client(client, "partnerclose1", "Partner Close One")
    partner_2 = _create_client(client, "partnerclose2", "Partner Close Two")
    group_id = _create_group(client, partner_1=partner_1, partner_2=partner_2)

    _add_member(client, group_id, normal_1, "1000")
    _add_member(client, group_id, normal_2, "500")
    _add_member(client, group_id, partner_1, "1000", role="partner")
    _add_member(client, group_id, partner_2, "500", role="partner")

    _effective_deposit(client, group_id, normal_1, "1000", "2026-06-25")
    _effective_deposit(client, group_id, normal_2, "500", "2026-06-25")
    _effective_deposit(client, group_id, partner_1, "1000", "2026-06-25")
    _effective_deposit(client, group_id, partner_2, "500", "2026-06-25")

    account_id = _create_live_mt5_account(client, group_id)
    _snapshot(client, account_id, "2026-06-25", "300000")
    _snapshot(client, account_id, "2026-06-26", "330000")

    response = client.post(
        f"/api/groups/{group_id}/daily-close/finalize",
        json={
            "broker_server_day": "2026-06-26",
            "previous_broker_server_day": "2026-06-25",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["close"]["opening_closed_balance"] == "3000"
    assert payload["close"]["closing_closed_balance"] == "3300"
    assert payload["close"]["trading_profit_loss"] == "300"
    assert payload["allocation"]["total_commission_collected"] == "51"
    assert payload["allocation"]["external_commission_earned"] == "22.5"

    balances = client.get(f"/api/groups/{group_id}/balances").json()["members"]
    by_client = {row["client_id"]: row["finalized_balance"] for row in balances}
    assert by_client[normal_1] == "1066"
    assert by_client[normal_2] == "533"
    assert by_client[partner_1] == "1114.25"
    assert by_client[partner_2] == "564.25"

    duplicate = client.post(
        f"/api/groups/{group_id}/daily-close/finalize",
        json={
            "broker_server_day": "2026-06-26",
            "previous_broker_server_day": "2026-06-25",
        },
    )
    assert duplicate.status_code == 400

    closes = client.get(f"/api/groups/{group_id}/daily-closes")
    assert closes.status_code == 200
    assert len(closes.json()) == 1


def test_daily_close_manual_override_requires_reason() -> None:
    client = make_client()
    normal = _create_client(client, "override_normal", "Override Normal")
    partner_1 = _create_client(client, "override_partner1", "Override Partner One")
    partner_2 = _create_client(client, "override_partner2", "Override Partner Two")
    group_id = _create_group(client, partner_1=partner_1, partner_2=partner_2)
    _add_member(client, group_id, normal, "1000")
    _effective_deposit(client, group_id, normal, "1000", "2026-06-25")
    account_id = _create_live_mt5_account(client, group_id)
    _snapshot(client, account_id, "2026-06-25", "100000")
    _snapshot(client, account_id, "2026-06-26", "100000")

    missing_reason = client.post(
        f"/api/groups/{group_id}/daily-close/finalize",
        json={
            "broker_server_day": "2026-06-26",
            "previous_broker_server_day": "2026-06-25",
            "manual_profit_loss": "5",
        },
    )
    assert missing_reason.status_code == 400

    overridden = client.post(
        f"/api/groups/{group_id}/daily-close/finalize",
        json={
            "broker_server_day": "2026-06-26",
            "previous_broker_server_day": "2026-06-25",
            "manual_profit_loss": "5",
            "override_reason": "Broker made a small adjustment after close",
        },
    )
    assert overridden.status_code == 201
    assert overridden.json()["close"]["status"] == "overridden"
    assert overridden.json()["close"]["trading_profit_loss"] == "5"
    assert overridden.json()["close"]["override_reason"] == "Broker made a small adjustment after close"
