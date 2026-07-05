from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.domain.import_wizard import (
    DetectedMoneyMovement,
    ExistingGroupImportMode,
    MovementClassification,
    MovementClassificationDecision,
)
from app.domain.models import MemberRole
from app.services.groups import create_group, create_client_profile, create_user_account, add_client_to_group
from app.domain.models import CommissionRule
from app.domain.portal import UserRole
from app.services.import_wizard import review_import_classifications
from app.security.encryption import generate_secret_key


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_import_wizard_classifies_mt5_money_movements_into_ledger_entries() -> None:
    user_a = create_user_account(username="a", password="secret123", role=UserRole.CLIENT)
    user_b = create_user_account(username="b", password="secret123", role=UserRole.CLIENT)
    client_a = create_client_profile(display_name="Client A", user_account=user_a)
    client_b = create_client_profile(display_name="Client B", user_account=user_b)
    group = create_group(name="Imported Group", commission_rule=CommissionRule())
    memberships = [
        add_client_to_group(group=group, client=client_a, effective_capital=Decimal("1000"), role=MemberRole.NORMAL),
        add_client_to_group(group=group, client=client_b, effective_capital=Decimal("500"), role=MemberRole.NORMAL),
    ]

    review = review_import_classifications(
        group_id=group.group_id,
        import_mode=ExistingGroupImportMode.PERCENTAGE_IMPORT,
        memberships=memberships,
        decisions=[
            MovementClassificationDecision(
                movement=DetectedMoneyMovement(movement_id="d1", amount=Decimal("300"), occurred_on=date(2026, 6, 1), comment="Deposit"),
                classification=MovementClassification.DEPOSIT_SPLIT_BY_PERCENTAGE,
                description="Historical shared deposit",
            ),
            MovementClassificationDecision(
                movement=DetectedMoneyMovement(movement_id="w1", amount=Decimal("-700"), occurred_on=date(2026, 6, 2), comment="Withdrawal"),
                classification=MovementClassification.TRANSFER_TO_NEW_MT5_ACCOUNT,
                description="Pending new MT5 account funding",
            ),
            MovementClassificationDecision(
                movement=DetectedMoneyMovement(movement_id="c1", amount=Decimal("-150"), occurred_on=date(2026, 6, 3), comment="Withdrawal"),
                classification=MovementClassification.EXTERNAL_COMMISSION_WITHDRAWAL,
                description="External 15% commission withdrawn",
            ),
        ],
    )

    assert review.total_detected == 3
    assert review.entry_count == 4
    entries = review.ledger_entries
    assert [entry.entry_type.value for entry in entries].count("deposit_effective") == 2
    assert sum(entry.amount for entry in entries if entry.entry_type.value == "deposit_effective") == Decimal("300")
    assert any(entry.entry_type.value == "transfer_pending" and entry.amount == Decimal("700") for entry in entries)
    assert any(entry.entry_type.value == "commission_withdrawn" and entry.amount == Decimal("-150") and entry.client_id is None for entry in entries)


def test_import_wizard_api_review_and_finalize_records_entries() -> None:
    client = TestClient(create_app(secret_key=generate_secret_key()))
    client.post("/api/setup/admin", json={"username": "admin", "password": "secret123"})
    token = client.post("/api/auth/login", json={"username": "admin", "password": "secret123"}).json()["access_token"]
    headers = _auth_header(token)

    client_a = client.post(
        "/api/admin/clients",
        headers=headers,
        json={"username": "a", "password": "secret123", "display_name": "Client A"},
    ).json()["client"]
    client_b = client.post(
        "/api/admin/clients",
        headers=headers,
        json={"username": "b", "password": "secret123", "display_name": "Client B"},
    ).json()["client"]
    group_id = client.post("/api/admin/groups", headers=headers, json={"name": "Existing Gold Group"}).json()["group_id"]
    client.post(f"/api/admin/groups/{group_id}/members", headers=headers, json={"client_id": client_a["client_id"], "effective_capital": "1000", "role": "normal"})
    client.post(f"/api/admin/groups/{group_id}/members", headers=headers, json={"client_id": client_b["client_id"], "effective_capital": "500", "role": "normal"})

    payload = {
        "import_mode": "percentage_import",
        "classifications": [
            {
                "movement": {"movement_id": "dep1", "amount": "300", "occurred_on": "2026-06-01", "comment": "Deposit"},
                "classification": "deposit_split_by_percentage",
                "description": "Historical shared deposit",
            },
            {
                "movement": {"movement_id": "fee1", "amount": "-60", "occurred_on": "2026-06-02", "comment": "Withdrawal"},
                "classification": "shared_group_expense",
                "description": "Historical VPS expense",
            },
            {
                "movement": {"movement_id": "com1", "amount": "-30", "occurred_on": "2026-06-03", "comment": "Withdrawal"},
                "classification": "external_commission_withdrawal",
                "description": "External commission withdrawn",
            },
        ],
    }
    review = client.post(f"/api/admin/groups/{group_id}/import-wizard/review", headers=headers, json=payload)
    assert review.status_code == 200
    assert review.json()["entry_count"] == 5

    finalized = client.post(f"/api/admin/groups/{group_id}/import-wizard/finalize", headers=headers, json=payload)
    assert finalized.status_code == 201
    assert finalized.json()["entry_count"] == 5

    ledger = client.get(f"/api/admin/groups/{group_id}/ledger", headers=headers).json()
    assert len(ledger) == 5
    assert any(entry["metadata"]["source"] == "existing_group_import_wizard" for entry in ledger)
