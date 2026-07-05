from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.security.encryption import generate_secret_key


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_client() -> TestClient:
    return TestClient(create_app(secret_key=generate_secret_key()))


def setup_admin_client_group(client: TestClient) -> tuple[dict[str, str], str, str]:
    client.post("/api/setup/admin", json={"username": "admin", "password": "secret123"})
    token = client.post("/api/auth/login", json={"username": "admin", "password": "secret123"}).json()["access_token"]
    headers = auth_header(token)
    client_payload = client.post(
        "/api/admin/clients",
        headers=headers,
        json={"username": "client1", "password": "secret123", "display_name": "Client One"},
    ).json()["client"]
    group_id = client.post("/api/admin/groups", headers=headers, json={"name": "Gold Bot Group"}).json()["group_id"]
    client.post(
        f"/api/admin/groups/{group_id}/members",
        headers=headers,
        json={"client_id": client_payload["client_id"], "effective_capital": "0", "role": "normal"},
    )
    return headers, client_payload["client_id"], group_id


def test_admin_can_list_group_ledger_and_process_withdrawal() -> None:
    client = make_client()
    headers, client_id, group_id = setup_admin_client_group(client)

    pending = client.post(
        f"/api/admin/groups/{group_id}/deposits/pending",
        headers=headers,
        json={"client_id": client_id, "amount": "100", "effective_date": "2026-06-26"},
    ).json()
    client.post(f"/api/admin/ledger/{pending['entry_id']}/deposit/effective", headers=headers)

    login = client.post("/api/auth/login", json={"username": "client1", "password": "secret123"}).json()
    client_headers = auth_header(login["access_token"])
    withdrawal = client.post(
        f"/api/client/groups/{group_id}/withdrawals/request",
        headers=client_headers,
        json={"client_id": client_id, "amount": "10"},
    ).json()

    ledger = client.get(f"/api/admin/groups/{group_id}/ledger", headers=headers)
    assert ledger.status_code == 200
    assert any(entry["entry_type"] == "withdrawal_requested" for entry in ledger.json())

    approved = client.post(
        f"/api/admin/ledger/{withdrawal['entry_id']}/withdrawal/approve",
        headers=headers,
        json={"effective_date": "2026-06-27"},
    )
    assert approved.status_code == 200
    assert approved.json()["entry_type"] == "withdrawal_approved"

    effective = client.post(f"/api/admin/ledger/{approved.json()['entry_id']}/withdrawal/effective", headers=headers)
    assert effective.status_code == 200
    assert effective.json()["entry_type"] == "withdrawal_effective"

    paid = client.post(f"/api/admin/ledger/{effective.json()['entry_id']}/withdrawal/paid", headers=headers)
    assert paid.status_code == 200
    assert paid.json()["entry_type"] == "withdrawal_paid"


def test_admin_can_record_expense_external_commission_and_manual_adjustment() -> None:
    client = make_client()
    headers, client_id, group_id = setup_admin_client_group(client)

    expense = client.post(
        f"/api/admin/groups/{group_id}/expenses/equal/pending",
        headers=headers,
        json={"amount": "20", "effective_date": "2026-06-26", "description": "VPS"},
    )
    assert expense.status_code == 201
    assert expense.json()[0]["entry_type"] == "expense_pending"

    effective = client.post(f"/api/admin/ledger/{expense.json()[0]['entry_id']}/expense/effective", headers=headers)
    assert effective.status_code == 200
    assert effective.json()["entry_type"] == "expense_effective"

    adjustment = client.post(
        f"/api/admin/groups/{group_id}/manual-adjustments",
        headers=headers,
        json={"client_id": client_id, "amount": "5", "reason": "test correction"},
    )
    assert adjustment.status_code == 201
    assert adjustment.json()["entry_type"] == "manual_adjustment"

    withdrawal = client.post(
        f"/api/admin/groups/{group_id}/commission/withdrawals",
        headers=headers,
        json={"amount": "3", "description": "external commission withdrawn"},
    )
    assert withdrawal.status_code == 201
    assert withdrawal.json()["entry_type"] == "commission_withdrawn"

    payable = client.get(f"/api/admin/groups/{group_id}/commission/external/payable", headers=headers)
    assert payable.status_code == 200
    assert payable.json()["external_commission_payable"] == "-3"


def test_admin_workflow_items_show_pending_and_next_actions() -> None:
    client = make_client()
    headers, client_id, group_id = setup_admin_client_group(client)

    pending_deposit = client.post(
        f"/api/admin/groups/{group_id}/deposits/pending",
        headers=headers,
        json={"client_id": client_id, "amount": "100", "effective_date": "2026-06-26"},
    ).json()
    workflow = client.get(f"/api/admin/groups/{group_id}/workflow-items", headers=headers)

    assert workflow.status_code == 200
    assert workflow.json()["pending_deposits"][0]["entry_id"] == pending_deposit["entry_id"]

    client.post(f"/api/admin/ledger/{pending_deposit['entry_id']}/deposit/effective", headers=headers)
    login = client.post("/api/auth/login", json={"username": "client1", "password": "secret123"}).json()
    client_headers = auth_header(login["access_token"])
    withdrawal = client.post(
        f"/api/client/groups/{group_id}/withdrawals/request",
        headers=client_headers,
        json={"client_id": client_id, "amount": "10"},
    ).json()

    workflow = client.get(f"/api/admin/groups/{group_id}/workflow-items", headers=headers).json()
    assert workflow["withdrawal_requests"][0]["entry_id"] == withdrawal["entry_id"]
    assert workflow["withdrawal_requests"][0]["client_name"] == "Client One"

    approved = client.post(
        f"/api/admin/ledger/{withdrawal['entry_id']}/withdrawal/approve",
        headers=headers,
        json={"effective_date": "2026-06-27"},
    ).json()
    workflow = client.get(f"/api/admin/groups/{group_id}/workflow-items", headers=headers).json()
    assert workflow["withdrawal_requests"] == []
    assert workflow["approved_withdrawals"][0]["entry_id"] == approved["entry_id"]


def test_admin_can_make_whole_expense_transaction_effective_from_workflow() -> None:
    client = make_client()
    headers, _client_id, group_id = setup_admin_client_group(client)

    pending_expense = client.post(
        f"/api/admin/groups/{group_id}/expenses/equal/pending",
        headers=headers,
        json={"amount": "20", "effective_date": "2026-06-26", "description": "VPS"},
    ).json()
    transaction_id = pending_expense[0]["transaction_id"]

    workflow = client.get(f"/api/admin/groups/{group_id}/workflow-items", headers=headers).json()
    assert workflow["pending_expenses"][0]["transaction_id"] == transaction_id
    assert workflow["pending_expenses"][0]["total_amount"] == "20"

    effective = client.post(
        f"/api/admin/groups/{group_id}/expenses/{transaction_id}/effective",
        headers=headers,
    )
    assert effective.status_code == 200
    assert effective.json()[0]["entry_type"] == "expense_effective"

    workflow = client.get(f"/api/admin/groups/{group_id}/workflow-items", headers=headers).json()
    assert workflow["pending_expenses"] == []
