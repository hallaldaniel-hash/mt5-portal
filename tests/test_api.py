from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.services.mt5_terminal import MT5SyncResult
from app.services.mt5_accounts import create_mt5_snapshot
from app.security.encryption import generate_secret_key


def make_client() -> TestClient:
    return TestClient(create_app(secret_key=generate_secret_key()))


def test_health_endpoint() -> None:
    client = make_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_client_and_login() -> None:
    client = make_client()

    created = client.post(
        "/api/clients",
        json={
            "username": "Ahmad",
            "password": "secret123",
            "display_name": "Ahmad",
            "email": "ahmad@example.com",
            "email_reports_opt_in": True,
        },
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["user"]["username"] == "ahmad"
    assert payload["client"]["display_name"] == "Ahmad"

    login = client.post("/api/auth/login", json={"username": "ahmad", "password": "secret123"})
    assert login.status_code == 200
    assert login.json()["authenticated"] is True
    assert login.json()["user"]["role"] == "client"

    bad_login = client.post("/api/auth/login", json={"username": "ahmad", "password": "wrong"})
    assert bad_login.status_code == 401


def test_group_membership_deposit_and_dashboard_flow() -> None:
    client = make_client()

    client_payload = client.post(
        "/api/clients",
        json={"username": "client1", "password": "secret123", "display_name": "Client One"},
    ).json()
    client_id = client_payload["client"]["client_id"]

    group_payload = client.post("/api/groups", json={"name": "Gold Bot Group"}).json()
    group_id = group_payload["group_id"]

    member_response = client.post(
        f"/api/groups/{group_id}/members",
        json={"client_id": client_id, "effective_capital": "0", "role": "normal"},
    )
    assert member_response.status_code == 201

    pending = client.post(
        f"/api/groups/{group_id}/deposits/pending",
        json={"client_id": client_id, "amount": "500", "effective_date": "2026-06-26"},
    )
    assert pending.status_code == 201
    pending_id = pending.json()["entry_id"]

    before_dashboard = client.get(f"/api/clients/{client_id}/dashboard").json()
    assert before_dashboard["combined_balance"] == "0"
    assert before_dashboard["groups"][0]["finalized_balance"] == "0"

    effective = client.post(f"/api/ledger/{pending_id}/deposit/effective")
    assert effective.status_code == 200
    assert effective.json()["entry_type"] == "deposit_effective"

    after_dashboard = client.get(f"/api/clients/{client_id}/dashboard").json()
    assert after_dashboard["combined_balance"] == "500"
    assert after_dashboard["groups"][0]["available_balance"] == "500"

    group_balances = client.get(f"/api/groups/{group_id}/balances").json()
    assert group_balances["members"][0]["finalized_balance"] == "500"


def test_mt5_account_snapshot_cent_conversion_and_client_view() -> None:
    client = make_client()
    group_id = client.post("/api/groups", json={"name": "Gold Bot Group"}).json()["group_id"]

    account_response = client.post(
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
    assert account_response.status_code == 201
    account = account_response.json()
    account_id = account["account_id"]
    assert "sync_password" not in account
    assert account["investor_password"] == "********"
    assert account["read_only_mode"] is True
    assert account["credential_mode"] == "investor_view_only"
    assert account["master_password_required"] is False

    snapshot_response = client.post(
        f"/api/mt5-accounts/{account_id}/snapshots",
        json={
            "broker_server_time": "2026-06-25T23:59:00",
            "broker_server_day": "2026-06-25",
            "raw_balance": "270000",
            "raw_equity": "270000",
            "raw_profit": "0",
        },
    )
    assert snapshot_response.status_code == 201
    assert snapshot_response.json()["display_balance"] == "2700"

    closed_balance = client.get(f"/api/groups/{group_id}/mt5-closed-balance").json()
    assert closed_balance["closed_balance"] == "2700"

    client_view = client.get(f"/api/groups/{group_id}/mt5-client-view").json()
    assert "investor_password" not in client_view[0]
    assert client_view[0]["read_only_mode"] == "true"
    assert client_view[0]["read_only_notice"].startswith("Read-only MT5 investor access")
    assert "readonly-password" not in str(client_view[0])
    assert "sync_password" not in client_view[0]
    assert client_view[0]["balance"] == "2700"


def _create_client(client: TestClient, username: str, display_name: str | None = None) -> str:
    response = client.post(
        "/api/clients",
        json={"username": username, "password": "secret123", "display_name": display_name or username.title()},
    )
    assert response.status_code == 201
    return response.json()["client"]["client_id"]


def _create_group(client: TestClient, name: str = "Gold Bot Group", **extra: str) -> str:
    response = client.post("/api/groups", json={"name": name, **extra})
    assert response.status_code == 201
    return response.json()["group_id"]


def _add_member(client: TestClient, group_id: str, client_id: str, capital: str, role: str = "normal") -> None:
    response = client.post(
        f"/api/groups/{group_id}/members",
        json={"client_id": client_id, "effective_capital": capital, "role": role},
    )
    assert response.status_code == 201


def _effective_deposit(client: TestClient, group_id: str, client_id: str, amount: str) -> None:
    pending = client.post(
        f"/api/groups/{group_id}/deposits/pending",
        json={"client_id": client_id, "amount": amount, "effective_date": "2026-06-26"},
    )
    assert pending.status_code == 201
    effective = client.post(f"/api/ledger/{pending.json()['entry_id']}/deposit/effective")
    assert effective.status_code == 200


def test_withdrawal_api_flow_updates_available_then_finalized_balance() -> None:
    client = make_client()
    client_id = _create_client(client, "withdrawal_client", "Withdrawal Client")
    group_id = _create_group(client)
    _add_member(client, group_id, client_id, "100")
    _effective_deposit(client, group_id, client_id, "100")

    requested = client.post(
        f"/api/groups/{group_id}/withdrawals/request",
        json={"client_id": client_id, "amount": "50", "description": "Client wants cash out"},
    )
    assert requested.status_code == 201
    assert requested.json()["entry_type"] == "withdrawal_requested"

    dashboard_after_request = client.get(f"/api/clients/{client_id}/dashboard").json()
    assert dashboard_after_request["groups"][0]["finalized_balance"] == "100"
    assert dashboard_after_request["groups"][0]["available_balance"] == "50"

    approved = client.post(
        f"/api/ledger/{requested.json()['entry_id']}/withdrawal/approve",
        json={"effective_date": "2026-06-27"},
    )
    assert approved.status_code == 200
    assert approved.json()["entry_type"] == "withdrawal_approved"

    effective = client.post(f"/api/ledger/{approved.json()['entry_id']}/withdrawal/effective")
    assert effective.status_code == 200
    assert effective.json()["entry_type"] == "withdrawal_effective"

    paid = client.post(f"/api/ledger/{effective.json()['entry_id']}/withdrawal/paid")
    assert paid.status_code == 200
    assert paid.json()["entry_type"] == "withdrawal_paid"

    dashboard_after_effective = client.get(f"/api/clients/{client_id}/dashboard").json()
    assert dashboard_after_effective["groups"][0]["finalized_balance"] == "50"
    assert dashboard_after_effective["groups"][0]["available_balance"] == "50"


def test_withdrawal_request_above_available_balance_is_auto_rejected() -> None:
    client = make_client()
    client_id = _create_client(client, "overdraw_client", "Overdraw Client")
    group_id = _create_group(client)
    _add_member(client, group_id, client_id, "25")
    _effective_deposit(client, group_id, client_id, "25")

    response = client.post(
        f"/api/groups/{group_id}/withdrawals/request",
        json={"client_id": client_id, "amount": "100"},
    )
    assert response.status_code == 201
    assert response.json()["entry_type"] == "withdrawal_rejected"
    assert response.json()["metadata"]["rejection_reason"] == "Amount exceeds available balance"


def test_equal_expense_and_manual_adjustment_api_flow() -> None:
    client = make_client()
    admin = client.post(
        "/api/users",
        json={"username": "admin1", "password": "secret123", "role": "admin"},
    ).json()
    admin_id = admin["user_id"]
    client_1 = _create_client(client, "expense_one", "Expense One")
    client_2 = _create_client(client, "expense_two", "Expense Two")
    group_id = _create_group(client)
    _add_member(client, group_id, client_1, "500")
    _add_member(client, group_id, client_2, "500")
    _effective_deposit(client, group_id, client_1, "500")
    _effective_deposit(client, group_id, client_2, "500")

    pending_expenses = client.post(
        f"/api/groups/{group_id}/expenses/equal/pending",
        json={"amount": "50", "effective_date": "2026-06-27", "description": "VPS"},
    )
    assert pending_expenses.status_code == 201
    entries = pending_expenses.json()
    assert len(entries) == 2
    assert sorted(entry["amount"] for entry in entries) == ["-25", "-25"]

    for entry in entries:
        effective = client.post(f"/api/ledger/{entry['entry_id']}/expense/effective")
        assert effective.status_code == 200
        assert effective.json()["entry_type"] == "expense_effective"

    adjustment = client.post(
        f"/api/groups/{group_id}/manual-adjustments",
        json={
            "client_id": client_1,
            "amount": "5",
            "reason": "Small correction after bank fee review",
            "created_by_user_id": admin_id,
        },
    )
    assert adjustment.status_code == 201
    assert adjustment.json()["entry_type"] == "manual_adjustment"
    assert adjustment.json()["metadata"]["reason"] == "Small correction after bank fee review"

    balances = client.get(f"/api/groups/{group_id}/balances").json()["members"]
    by_client = {row["client_id"]: row["finalized_balance"] for row in balances}
    assert by_client[client_1] == "480"
    assert by_client[client_2] == "475"


def test_daily_allocation_external_commission_and_commission_withdrawal_api() -> None:
    client = make_client()
    normal_1 = _create_client(client, "normal_one", "Normal One")
    normal_2 = _create_client(client, "normal_two", "Normal Two")
    partner_1 = _create_client(client, "partner_one", "Partner One")
    partner_2 = _create_client(client, "partner_two", "Partner Two")
    group_id = _create_group(
        client,
        partner_1_client_id=partner_1,
        partner_2_client_id=partner_2,
    )
    _add_member(client, group_id, normal_1, "1000")
    _add_member(client, group_id, normal_2, "500")
    _add_member(client, group_id, partner_1, "1000", role="partner")
    _add_member(client, group_id, partner_2, "500", role="partner")

    allocation = client.post(
        f"/api/groups/{group_id}/daily-allocations",
        json={"group_profit_loss": "300", "allocation_date": "2026-06-26"},
    )
    assert allocation.status_code == 201
    payload = allocation.json()
    assert payload["total_commission_collected"] == "51"
    assert payload["external_commission_earned"] == "22.5"

    payable = client.get(f"/api/groups/{group_id}/external-commission-payable").json()
    assert payable["payable"] == "22.5"

    withdrawal = client.post(
        f"/api/groups/{group_id}/commissions/withdrawals",
        json={"amount": "10", "description": "Partial external commission withdrawal"},
    )
    assert withdrawal.status_code == 201
    assert withdrawal.json()["entry_type"] == "commission_withdrawn"

    payable_after = client.get(f"/api/groups/{group_id}/external-commission-payable").json()
    assert payable_after["payable"] == "12.5"

    balances = client.get(f"/api/groups/{group_id}/balances").json()["members"]
    by_client = {row["client_id"]: row["finalized_balance"] for row in balances}
    assert by_client[normal_1] == "66"
    assert by_client[normal_2] == "33"
    assert by_client[partner_1] == "114.25"
    assert by_client[partner_2] == "64.25"


def test_api_sync_mt5_account_saves_snapshot(monkeypatch) -> None:
    client = make_client()
    group_id = client.post("/api/groups", json={"name": "Gold Bot Group"}).json()["group_id"]
    account_response = client.post(
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
    account_id = account_response.json()["account_id"]

    def fake_sync(account, terminal_path=None):
        snapshot = create_mt5_snapshot(
            account=account,
            broker_server_time=datetime(2026, 6, 25, 23, 59, tzinfo=timezone.utc),
            raw_balance=Decimal("270000"),
            raw_equity=Decimal("270000"),
        )
        return MT5SyncResult(
            account_id=account.account_id,
            group_id=account.group_id,
            snapshot=snapshot,
            broker_name=account.broker_name,
            server=account.server,
            login=account.login,
        )

    monkeypatch.setattr("app.api.app.sync_account_snapshot", fake_sync)

    response = client.post(f"/api/mt5-accounts/{account_id}/sync", json={})

    assert response.status_code == 201
    assert response.json()["snapshot"]["display_balance"] == "2700"
    closed_balance = client.get(f"/api/groups/{group_id}/mt5-closed-balance").json()
    assert closed_balance["closed_balance"] == "2700"
