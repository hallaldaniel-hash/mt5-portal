from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4


class MT5AccountStatus(StrEnum):
    PENDING = "pending"
    LIVE = "live"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class SecretValue:
    """Small wrapper for secrets so repr() never prints raw passwords.

    This is not encryption. Real encrypted storage will be added when we add
    the database/API layer. For now, it prevents accidental password exposure
    in test failures, logs, and object representations.
    """

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("Secret value is required")

    def reveal(self) -> str:
        return self.value

    def masked(self) -> str:
        return "********"

    def __repr__(self) -> str:
        return "SecretValue('********')"


@dataclass(frozen=True)
class MT5Account:
    """One MT5 account tracked inside one independent group/pool."""

    group_id: str
    nickname: str
    broker_name: str
    server: str
    login: str
    sync_password: SecretValue
    investor_login: str
    investor_password: SecretValue
    currency: str = "USD"
    display_divisor: Decimal = Decimal("100")
    status: MT5AccountStatus = MT5AccountStatus.PENDING
    account_id: str = field(default_factory=lambda: str(uuid4()))
    notes: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_live(self) -> bool:
        return self.status == MT5AccountStatus.LIVE

    def is_cent_account(self) -> bool:
        return self.display_divisor == Decimal("100")


@dataclass(frozen=True)
class MT5Snapshot:
    """A raw MT5 account snapshot plus converted portal display amounts."""

    account_id: str
    group_id: str
    broker_server_time: datetime
    broker_server_day: date
    raw_balance: Decimal
    raw_equity: Decimal
    raw_profit: Decimal = Decimal("0")
    raw_margin: Decimal = Decimal("0")
    raw_free_margin: Decimal = Decimal("0")
    currency: str = "USD"
    display_divisor: Decimal = Decimal("100")
    snapshot_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def display_balance(self) -> Decimal:
        return self.raw_balance / self.display_divisor

    @property
    def display_equity(self) -> Decimal:
        return self.raw_equity / self.display_divisor

    @property
    def display_profit(self) -> Decimal:
        return self.raw_profit / self.display_divisor

    @property
    def display_margin(self) -> Decimal:
        return self.raw_margin / self.display_divisor

    @property
    def display_free_margin(self) -> Decimal:
        return self.raw_free_margin / self.display_divisor
