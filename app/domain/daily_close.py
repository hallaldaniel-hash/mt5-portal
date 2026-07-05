from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4


class DailyCloseStatus(StrEnum):
    FINALIZED = "finalized"
    OVERRIDDEN = "overridden"


@dataclass(frozen=True)
class DailyCloseAdjustment:
    deposits_effective: Decimal = Decimal("0")
    withdrawals_effective: Decimal = Decimal("0")
    expenses_effective: Decimal = Decimal("0")
    pending_internal_transfers: Decimal = Decimal("0")


@dataclass(frozen=True)
class DailyGroupClose:
    """One locked broker-server-day accounting close for one group."""

    group_id: str
    broker_server_day: date
    opening_closed_balance: Decimal
    closing_closed_balance: Decimal
    deposits_effective: Decimal
    withdrawals_effective: Decimal
    expenses_effective: Decimal
    pending_internal_transfers: Decimal
    trading_profit_loss: Decimal
    status: DailyCloseStatus = DailyCloseStatus.FINALIZED
    close_id: str = field(default_factory=lambda: str(uuid4()))
    finalized_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by_user_id: str | None = None
    override_reason: str | None = None

    def validate(self) -> None:
        if self.status == DailyCloseStatus.OVERRIDDEN and not (self.override_reason or "").strip():
            raise ValueError("Override reason is required")
