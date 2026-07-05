from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.security.encryption import generate_secret_key


def make_client() -> TestClient:
    return TestClient(create_app(secret_key=generate_secret_key()))


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_admin_and_login(client: TestClient) -> str:
    setup = client.post("/api/setup/admin", json={"username": "admin", "password": "secret123"})
    assert setup.status_code == 201
    login = client.post("/api/auth/login", json={"username": "admin", "password": "secret123"})
    assert login.status_code == 200
    return login.json()["access_token"]


def test_first_admin_setup_only_once_and_login_returns_session_token() -> None:
    client = make_client()

    created = client.post("/api/setup/admin", json={"username": "Admin", "password": "secret123"})
    assert created.status_code == 201
    assert created.json()["role"] == "admin"

    second = client.post("/api/setup/admin", json={"username": "OtherAdmin", "password": "secret123"})
    assert second.status_code == 400

    login = client.post("/api/auth/login", json={"username": "admin", "password": "secret123"})
    assert login.status_code == 200
    payload = login.json()
    assert payload["authenticated"] is True
    assert payload["access_token"]
    assert payload["token_type"] == "bearer"

    me = client.get("/api/session/me", headers=auth_header(payload["access_token"]))
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "admin"


def test_admin_routes_require_admin_session() -> None:
    client = make_client()

    missing = client.get("/api/admin/clients")
    assert missing.status_code == 401

    admin_token = create_admin_and_login(client)
    authed = client.get("/api/admin/clients", headers=auth_header(admin_token))
    assert authed.status_code == 200
    assert authed.json() == []


def test_client_cannot_access_admin_routes_and_can_view_only_self_dashboard() -> None:
    client = make_client()
    admin_token = create_admin_and_login(client)
    admin_headers = auth_header(admin_token)

    created = client.post(
        "/api/admin/clients",
        headers=admin_headers,
        json={"username": "client1", "password": "secret123", "display_name": "Client One"},
    )
    assert created.status_code == 201
    client_id = created.json()["client"]["client_id"]

    group = client.post("/api/admin/groups", headers=admin_headers, json={"name": "Gold Bot Group"})
    group_id = group.json()["group_id"]
    member = client.post(
        f"/api/admin/groups/{group_id}/members",
        headers=admin_headers,
        json={"client_id": client_id, "effective_capital": "0", "role": "normal"},
    )
    assert member.status_code == 201
    pending = client.post(
        f"/api/admin/groups/{group_id}/deposits/pending",
        headers=admin_headers,
        json={"client_id": client_id, "amount": "100", "effective_date": "2026-06-26"},
    )
    effective = client.post(
        f"/api/admin/ledger/{pending.json()['entry_id']}/deposit/effective",
        headers=admin_headers,
    )
    assert effective.status_code == 200

    client_login = client.post("/api/auth/login", json={"username": "client1", "password": "secret123"})
    client_headers = auth_header(client_login.json()["access_token"])

    denied = client.get("/api/admin/clients", headers=client_headers)
    assert denied.status_code == 403

    dashboard = client.get("/api/client/me/dashboard", headers=client_headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["client"]["client_id"] == client_id
    assert dashboard.json()["combined_balance"] == "100"


def test_client_withdrawal_route_forces_own_client_id() -> None:
    client = make_client()
    admin_token = create_admin_and_login(client)
    admin_headers = auth_header(admin_token)

    client_one = client.post(
        "/api/admin/clients",
        headers=admin_headers,
        json={"username": "client1", "password": "secret123", "display_name": "Client One"},
    ).json()["client"]
    client_two = client.post(
        "/api/admin/clients",
        headers=admin_headers,
        json={"username": "client2", "password": "secret123", "display_name": "Client Two"},
    ).json()["client"]
    group_id = client.post("/api/admin/groups", headers=admin_headers, json={"name": "Gold Bot Group"}).json()["group_id"]
    for client_payload in (client_one, client_two):
        client.post(
            f"/api/admin/groups/{group_id}/members",
            headers=admin_headers,
            json={"client_id": client_payload["client_id"], "effective_capital": "0", "role": "normal"},
        )
        pending = client.post(
            f"/api/admin/groups/{group_id}/deposits/pending",
            headers=admin_headers,
            json={"client_id": client_payload["client_id"], "amount": "100", "effective_date": "2026-06-26"},
        )
        client.post(f"/api/admin/ledger/{pending.json()['entry_id']}/deposit/effective", headers=admin_headers)

    client_one_login = client.post("/api/auth/login", json={"username": "client1", "password": "secret123"})
    client_one_headers = auth_header(client_one_login.json()["access_token"])

    wrong_client_id = client.post(
        f"/api/client/groups/{group_id}/withdrawals/request",
        headers=client_one_headers,
        json={"client_id": client_two["client_id"], "amount": "10"},
    )
    assert wrong_client_id.status_code == 403

    own_request = client.post(
        f"/api/client/groups/{group_id}/withdrawals/request",
        headers=client_one_headers,
        json={"client_id": client_one["client_id"], "amount": "10"},
    )
    assert own_request.status_code == 201
    assert own_request.json()["entry_type"] == "withdrawal_requested"


def test_admin_can_create_mt5_account_through_protected_route() -> None:
    client = make_client()
    admin_token = create_admin_and_login(client)
    headers = auth_header(admin_token)

    group_response = client.post("/api/admin/groups", headers=headers, json={"name": "Gold Bot Group"})
    assert group_response.status_code == 201
    group_id = group_response.json()["group_id"]

    response = client.post(
        f"/api/admin/groups/{group_id}/mt5-accounts",
        headers=headers,
        json={
            "nickname": "Main XAUUSD Cent",
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
    payload = response.json()
    assert payload["group_id"] == group_id
    assert payload["nickname"] == "Main XAUUSD Cent"
    assert payload["display_divisor"] == "100"
    assert "sync_password" not in payload
    assert payload["investor_password"] == "********"
    assert payload["read_only_mode"] is True
    assert payload["credential_mode"] == "investor_view_only"
    assert payload["master_password_required"] is False

    listed = client.get(f"/api/admin/groups/{group_id}/mt5-accounts", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_admin_finalize_daily_close_protected_route_allocates_balance_movement() -> None:
    client = make_client()
    client.post("/api/setup/admin", json={"username": "adminclose", "password": "secret123"})
    token = client.post("/api/auth/login", json={"username": "adminclose", "password": "secret123"}).json()["access_token"]
    headers = auth_header(token)

    client_payload = client.post(
        "/api/admin/clients",
        headers=headers,
        json={"username": "closeclient", "password": "secret123", "display_name": "Close Client"},
    ).json()["client"]
    client_id = client_payload["client_id"]

    group = client.post("/api/admin/groups", headers=headers, json={"name": "Protected Close Group"}).json()
    group_id = group["group_id"]

    member = client.post(
        f"/api/admin/groups/{group_id}/members",
        headers=headers,
        json={"client_id": client_id, "effective_capital": "0", "role": "normal"},
    )
    assert member.status_code == 201

    pending = client.post(
        f"/api/admin/groups/{group_id}/deposits/pending",
        headers=headers,
        json={"client_id": client_id, "amount": "1000", "effective_date": "2026-06-25"},
    ).json()
    effective = client.post(f"/api/admin/ledger/{pending['entry_id']}/deposit/effective", headers=headers)
    assert effective.status_code == 200

    account = client.post(
        f"/api/admin/groups/{group_id}/mt5-accounts",
        headers=headers,
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
    assert account.status_code == 201
    account_id = account.json()["account_id"]

    first_snapshot = client.post(
        f"/api/admin/mt5-accounts/{account_id}/snapshots",
        headers=headers,
        json={
            "broker_server_time": "2026-06-25T23:59:00+00:00",
            "broker_server_day": "2026-06-25",
            "raw_balance": "100000",
            "raw_equity": "100000",
            "raw_profit": "0",
        },
    )
    assert first_snapshot.status_code == 201
    second_snapshot = client.post(
        f"/api/admin/mt5-accounts/{account_id}/snapshots",
        headers=headers,
        json={
            "broker_server_time": "2026-06-26T23:59:00+00:00",
            "broker_server_day": "2026-06-26",
            "raw_balance": "110000",
            "raw_equity": "110000",
            "raw_profit": "0",
        },
    )
    assert second_snapshot.status_code == 201

    close = client.post(
        f"/api/admin/groups/{group_id}/daily-close/finalize",
        headers=headers,
        json={
            "broker_server_day": "2026-06-26",
            "previous_broker_server_day": "2026-06-25",
        },
    )
    assert close.status_code == 201
    assert close.json()["daily_close"]["opening_closed_balance"] == "1000"
    assert close.json()["daily_close"]["closing_closed_balance"] == "1100"
    assert close.json()["daily_close"]["trading_profit_loss"] == "100"

    duplicate = client.post(
        f"/api/admin/groups/{group_id}/daily-close/finalize",
        headers=headers,
        json={
            "broker_server_day": "2026-06-26",
            "previous_broker_server_day": "2026-06-25",
        },
    )
    assert duplicate.status_code == 400


def test_admin_cannot_add_same_client_to_same_group_twice() -> None:
    client = make_client()
    admin_token = create_admin_and_login(client)
    headers = auth_header(admin_token)

    created = client.post(
        "/api/admin/clients",
        headers=headers,
        json={"username": "uniqueclient", "password": "secret123", "display_name": "Unique Client"},
    )
    assert created.status_code == 201
    client_id = created.json()["client"]["client_id"]
    group_id = client.post("/api/admin/groups", headers=headers, json={"name": "Unique Group"}).json()["group_id"]

    first = client.post(
        f"/api/admin/groups/{group_id}/members",
        headers=headers,
        json={"client_id": client_id, "effective_capital": "500", "role": "normal"},
    )
    second = client.post(
        f"/api/admin/groups/{group_id}/members",
        headers=headers,
        json={"client_id": client_id, "effective_capital": "500", "role": "normal"},
    )

    assert first.status_code == 201
    assert second.status_code == 400
    assert "already a member" in second.json()["detail"]


def test_client_can_update_profile_change_password_and_reset_by_email() -> None:
    client = make_client()
    admin_token = create_admin_and_login(client)
    admin_headers = auth_header(admin_token)

    created = client.post(
        "/api/admin/clients",
        headers=admin_headers,
        json={"username": "profileclient", "password": "secret123", "display_name": "Profile Client"},
    )
    assert created.status_code == 201

    login = client.post("/api/auth/login", json={"username": "profileclient", "password": "secret123"})
    client_headers = auth_header(login.json()["access_token"])

    profile = client.get("/api/client/me/profile", headers=client_headers)
    assert profile.status_code == 200
    assert profile.json()["email_missing"] is True
    assert profile.json()["password_reset_available"] is False

    updated = client.patch(
        "/api/client/me/profile",
        headers=client_headers,
        json={"email": "client@example.com", "email_reports_opt_in": True},
    )
    assert updated.status_code == 200
    assert updated.json()["client"]["email"] == "client@example.com"
    assert updated.json()["client"]["email_reports_opt_in"] is True
    assert updated.json()["password_reset_available"] is True

    changed = client.post(
        "/api/client/me/password",
        headers=client_headers,
        json={"current_password": "secret123", "new_password": "changed123"},
    )
    assert changed.status_code == 200
    assert client.post("/api/auth/login", json={"username": "profileclient", "password": "secret123"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "profileclient", "password": "changed123"}).status_code == 200

    reset_request = client.post("/api/auth/password-reset/request", json={"identifier": "client@example.com"})
    assert reset_request.status_code == 200
    token = reset_request.json()["development_only_reset_token"]
    assert token
    confirmed = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": token, "new_password": "emailreset123"},
    )
    assert confirmed.status_code == 200
    assert client.post("/api/auth/login", json={"username": "profileclient", "password": "emailreset123"}).status_code == 200


def test_admin_can_view_client_dashboard_reset_password_and_reset_2fa_with_audit() -> None:
    client = make_client()
    admin_token = create_admin_and_login(client)
    admin_headers = auth_header(admin_token)

    created = client.post(
        "/api/admin/clients",
        headers=admin_headers,
        json={"username": "secureclient", "password": "secret123", "display_name": "Secure Client", "email": "secure@example.com"},
    ).json()
    client_id = created["client"]["client_id"]

    client_login = client.post("/api/auth/login", json={"username": "secureclient", "password": "secret123"})
    client_headers = auth_header(client_login.json()["access_token"])
    twofa = client.post("/api/client/me/2fa", headers=client_headers, json={"enabled": True})
    assert twofa.status_code == 200
    assert twofa.json()["two_factor_enabled"] is True

    dashboard = client.get(f"/api/admin/clients/{client_id}/dashboard?reason=support", headers=admin_headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["client"]["client_id"] == client_id
    assert dashboard.json()["viewed_by_admin"]["username"] == "admin"

    reset_2fa = client.post(f"/api/admin/clients/{client_id}/2fa/reset", headers=admin_headers)
    assert reset_2fa.status_code == 200
    assert reset_2fa.json()["two_factor_enabled"] is False

    reset_password_response = client.post(
        f"/api/admin/clients/{client_id}/password-reset",
        headers=admin_headers,
        json={"new_password": "adminreset123"},
    )
    assert reset_password_response.status_code == 200
    assert client.post("/api/auth/login", json={"username": "secureclient", "password": "adminreset123"}).status_code == 200

    audits = client.get(f"/api/admin/audit-events?target_client_id={client_id}", headers=admin_headers)
    assert audits.status_code == 200
    event_types = {event["event_type"] for event in audits.json()}
    assert "admin_viewed_client_dashboard" in event_types
    assert "admin_reset_client_password" in event_types
    assert "admin_reset_client_2fa" in event_types
