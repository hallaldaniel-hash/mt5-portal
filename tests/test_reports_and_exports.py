from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.security.encryption import generate_secret_key


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_client() -> TestClient:
    return TestClient(create_app(secret_key=generate_secret_key()))


def setup_two_clients_with_group(client: TestClient) -> tuple[dict[str, str], dict[str, str], str, str, str]:
    client.post("/api/setup/admin", json={"username": "admin", "password": "secret123"})
    admin_token = client.post("/api/auth/login", json={"username": "admin", "password": "secret123"}).json()["access_token"]
    admin_headers = auth_header(admin_token)

    one = client.post(
        "/api/admin/clients",
        headers=admin_headers,
        json={"username": "client1", "password": "secret123", "display_name": "Client One"},
    ).json()["client"]
    two = client.post(
        "/api/admin/clients",
        headers=admin_headers,
        json={"username": "client2", "password": "secret123", "display_name": "Client Two"},
    ).json()["client"]
    group_id = client.post("/api/admin/groups", headers=admin_headers, json={"name": "Gold Bot Group"}).json()["group_id"]
    for profile in (one, two):
        client.post(
            f"/api/admin/groups/{group_id}/members",
            headers=admin_headers,
            json={"client_id": profile["client_id"], "effective_capital": "0", "role": "normal"},
        )
        pending = client.post(
            f"/api/admin/groups/{group_id}/deposits/pending",
            headers=admin_headers,
            json={"client_id": profile["client_id"], "amount": "100", "effective_date": "2026-06-26"},
        ).json()
        client.post(f"/api/admin/ledger/{pending['entry_id']}/deposit/effective", headers=admin_headers)
    return admin_headers, one, two, group_id, admin_token


def test_admin_can_download_group_ledger_csv() -> None:
    client = make_client()
    admin_headers, _one, _two, group_id, _token = setup_two_clients_with_group(client)

    response = client.get(f"/api/admin/groups/{group_id}/ledger/export.csv", headers=admin_headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert "entry_id,transaction_id,group_id,client_id" in response.text
    assert "deposit_effective" in response.text


def test_client_transaction_history_is_limited_to_own_entries() -> None:
    client = make_client()
    _admin_headers, one, two, _group_id, _token = setup_two_clients_with_group(client)
    login = client.post("/api/auth/login", json={"username": "client1", "password": "secret123"}).json()
    client_headers = auth_header(login["access_token"])

    ledger = client.get("/api/client/me/ledger", headers=client_headers)
    csv_response = client.get("/api/client/me/ledger/export.csv", headers=client_headers)

    assert ledger.status_code == 200
    assert {entry["client_id"] for entry in ledger.json()} == {one["client_id"]}
    assert two["client_id"] not in csv_response.text
    assert one["client_id"] in csv_response.text
