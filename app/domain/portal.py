from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from app.domain.models import CommissionRule, GroupMember, MemberRole


class UserRole(StrEnum):
    ADMIN = "admin"
    CLIENT = "client"


class GroupStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class UserAccount:
    """Login account for either an admin or a client."""

    username: str
    password_hash: str
    password_salt: str
    role: UserRole
    user_id: str = field(default_factory=lambda: str(uuid4()))
    is_active: bool = True
    two_factor_enabled: bool = False


@dataclass(frozen=True)
class ClientProfile:
    """Client identity shown in the portal.

    A client can belong to multiple independent groups/pools.
    """

    display_name: str
    user_id: str
    client_id: str = field(default_factory=lambda: str(uuid4()))
    email: str | None = None
    email_reports_opt_in: bool = False
    is_active: bool = True


@dataclass(frozen=True)
class Group:
    """Independent pool of clients and MT5 accounts."""

    name: str
    commission_rule: CommissionRule
    group_id: str = field(default_factory=lambda: str(uuid4()))
    currency: str = "USD"
    status: GroupStatus = GroupStatus.ACTIVE
    use_broker_server_day_close: bool = True
    display_timezone: str = "Asia/Beirut"

    def is_active(self) -> bool:
        return self.status == GroupStatus.ACTIVE


@dataclass(frozen=True)
class GroupMembership:
    """A client's role and effective capital inside one group."""

    group_id: str
    client_id: str
    display_name: str
    effective_capital: Decimal
    role: MemberRole = MemberRole.NORMAL
    membership_id: str = field(default_factory=lambda: str(uuid4()))
    joined_on: date | None = None
    effective_from: date | None = None
    is_active: bool = True

    def to_group_member(self) -> GroupMember:
        return GroupMember(
            client_id=self.client_id,
            name=self.display_name,
            effective_capital=self.effective_capital,
            role=self.role,
            is_active=self.is_active,
        )
