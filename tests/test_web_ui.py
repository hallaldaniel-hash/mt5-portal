from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app


def test_web_index_is_served() -> None:
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert "MT5 Client Portal" in response.text
    assert "Admin financial workflows" in response.text
    assert "/static/app.js" in response.text


def test_static_assets_are_served() -> None:
    client = TestClient(create_app())

    css = client.get("/static/styles.css")
    js = client.get("/static/app.js")

    assert css.status_code == 200
    assert "topbar" in css.text
    assert js.status_code == 200
    assert "refreshLists" in js.text
    assert "adminWithdrawalActionForm" in js.text


def test_step19_admin_command_center_assets_are_present() -> None:
    client = TestClient(create_app())

    index = client.get("/")
    css = client.get("/static/styles.css")
    js = client.get("/static/app.js")

    assert index.status_code == 200
    assert css.status_code == 200
    assert js.status_code == 200
    assert "Admin financial workflows" in index.text
    assert "command-center" in css.text
    assert "collapsible-card" in css.text
    assert "setupCollapsibleAdminCards" in js.text
    assert "adminToolSearch" in js.text
    assert "toastBox" in js.text


def test_step21_client_portfolio_assets_are_present() -> None:
    client = TestClient(create_app())

    css = client.get("/static/styles.css")
    js = client.get("/static/app.js")

    assert css.status_code == 200
    assert js.status_code == 200
    assert "client-group-card" in css.text
    assert "renderClientDashboard" in js.text
    assert "group_name" in js.text


def test_step22_client_profile_security_assets_are_present() -> None:
    client = TestClient(create_app())

    index = client.get("/")
    css = client.get("/static/styles.css")
    js = client.get("/static/app.js")

    assert index.status_code == 200
    assert css.status_code == 200
    assert js.status_code == 200
    assert "About / Profile" in index.text
    assert "client-sidebar" in css.text
    assert "clientProfileForm" in index.text
    assert "clientPasswordForm" in index.text
    assert "passwordResetRequestForm" in index.text
    assert "renderClientProfile" in js.text
    assert "/api/client/me/profile" in js.text


def test_step24_admin_workspace_assets_are_present() -> None:
    client = TestClient(create_app())

    index = client.get("/")
    css = client.get("/static/styles.css")
    js = client.get("/static/app.js")

    assert index.status_code == 200
    assert css.status_code == 200
    assert js.status_code == 200
    assert "wizard-explainer" in index.text
    assert "admin-workspace" in css.text
    assert "admin-sidebar" in css.text
    assert "setupThemeToggle" in js.text
    assert "setupAdminCommandCenter" in js.text
    assert "fieldHints" in js.text


def test_step26_floating_tooltip_assets_are_present() -> None:
    client = TestClient(create_app())

    css = client.get("/static/styles.css")
    js = client.get("/static/app.js")

    assert css.status_code == 200
    assert js.status_code == 200
    assert "floating-tooltip" in css.text
    assert "2147483647" in css.text
    assert "setupFloatingTooltips" in js.text
    assert "floatingTooltip" in js.text


def test_step27_persistence_and_dark_hover_assets_are_present() -> None:
    client = TestClient(create_app())

    css = client.get("/static/styles.css")
    js = client.get("/static/app.js")

    assert css.status_code == 200
    assert js.status_code == 200
    assert "persistence-note" in css.text
    assert "shipping-grade persistence" in css.text
    assert "state.storage" in js.text
    assert "/api/system/storage" in js.text
