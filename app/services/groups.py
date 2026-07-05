from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.domain.ledger import LedgerEntry
from app.domain.models import CommissionRule, GroupMember, MemberRole
from app.domain.portal import ClientProfile, Group, GroupMembership, UserAccount, UserRole
from app.services.ledger import client_balance

ZERO = Decimal("0")
MIN_PASSWORD_LENGTH = 6


def _normalize_username(username: str) -> str:
    normalized = username.strip().lower()
    if not normalized:
        raise ValueError("Username is required")
    return normalized


def _validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")


def _hash_password(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        200_000,
    ).hex()


def ensure_unique_username(existing_accounts: list[UserAccount], username: str) -> str:
    normalized = _normalize_username(username)
    existing = {_normalize_username(account.username) for account in existing_accounts}
    if normalized in existing:
        raise ValueError(f"Username already exists: {normalized}")
    return normalized


def create_user_account(
    *,
    username: str,
    password: str,
    role: UserRole,
    existing_accounts: list[UserAccount] | None = None,
    user_id: str | None = None,
) -> UserAccount:
    """Create an admin/client login account with a salted password hash."""

    _validate_password(password)
    normalized_username = (
        ensure_unique_username(existing_accounts, username)
        if existing_accounts is not None
        else _normalize_username(username)
    )
    salt = os.urandom(16).hex()
    return UserAccount(
        user_id=user_id or str(uuid4()),
        username=normalized_username,
        password_hash=_hash_password(password, salt),
        password_salt=salt,
        role=role,
    )


def verify_password(account: UserAccount, password: str) -> bool:
    attempted_hash = _hash_password(password, account.password_salt)
    return hmac.compare_digest(attempted_hash, account.password_hash)


def reset_password(account: UserAccount, new_password: str) -> UserAccount:
    _validate_password(new_password)
    salt = os.urandom(16).hex()
    return replace(
        account,
        password_salt=salt,
        password_hash=_hash_password(new_password, salt),
    )


def create_client_profile(
    *,
    display_name: str,
    user_account: UserAccount,
    email: str | None = None,
    email_reports_opt_in: bool = False,
    client_id: str | None = None,
) -> ClientProfile:
    if user_account.role != UserRole.CLIENT:
        raise ValueError("Client profiles must be linked to a client user account")
    if not display_name.strip():
        raise ValueError("Client display name is required")
    if email_reports_opt_in and not email:
        raise ValueError("Email is required when report emails are enabled")

    return ClientProfile(
        client_id=client_id or str(uuid4()),
        display_name=display_name.strip(),
        user_id=user_account.user_id,
        email=email.strip() if email else None,
        email_reports_opt_in=email_reports_opt_in,
    )


def create_group(
    *,
    name: str,
    commission_rule: CommissionRule,
    currency: str = "USD",
    group_id: str | None = None,
    display_timezone: str = "Asia/Beirut",
) -> Group:
    if not name.strip():
        raise ValueError("Group name is required")
    commission_rule.validate()
    return Group(
        group_id=group_id or str(uuid4()),
        name=name.strip(),
        currency=currency.upper(),
        commission_rule=commission_rule,
        display_timezone=display_timezone,
    )


def add_client_to_group(
    *,
    group: Group,
    client: ClientProfile,
    effective_capital: Decimal,
    role: MemberRole = MemberRole.NORMAL,
    joined_on: date | None = None,
    effective_from: date | None = None,
    membership_id: str | None = None,
) -> GroupMembership:
    if not group.is_active():
        raise ValueError("Cannot add clients to an inactive group")
    if not client.is_active:
        raise ValueError("Cannot add inactive client to group")
    if effective_capital < ZERO:
        raise ValueError("Effective capital cannot be negative")

    return GroupMembership(
        membership_id=membership_id or str(uuid4()),
        group_id=group.group_id,
        client_id=client.client_id,
        display_name=client.display_name,
        effective_capital=effective_capital,
        role=role,
        joined_on=joined_on,
        effective_from=effective_from,
    )


def memberships_for_client(
    memberships: list[GroupMembership], client_id: str
) -> list[GroupMembership]:
    return [membership for membership in memberships if membership.client_id == client_id]


def memberships_for_group(
    memberships: list[GroupMembership], group_id: str) -> list[GroupMembership]:
    return [membership for membership in memberships if membership.group_id == group_id]


def active_group_members(
    memberships: list[GroupMembership], group_id: str
) -> list[GroupMember]:
    return [
        membership.to_group_member()
        for membership in memberships_for_group(memberships, group_id)
        if membership.is_active
    ]


def client_balances_by_group(
    *,
    entries: list[LedgerEntry],
    memberships: list[GroupMembership],
    client_id: str,
) -> dict[str, Decimal]:
    """Return one client's finalized ledger balance in each group they belong to."""

    balances: dict[str, Decimal] = {}
    for membership in memberships_for_client(memberships, client_id):
        balances[membership.group_id] = client_balance(
            entries, membership.group_id, client_id
        )
    return balances


def combined_client_balance(
    *,
    entries: list[LedgerEntry],
    memberships: list[GroupMembership],
    client_id: str,
) -> Decimal:
    return sum(
        client_balances_by_group(
            entries=entries,
            memberships=memberships,
            client_id=client_id,
        ).values(),
        ZERO,
    )
