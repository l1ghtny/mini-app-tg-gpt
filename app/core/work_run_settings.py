from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping


_TRUE_VALUES = frozenset({"1", "true"})


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in _TRUE_VALUES


def _parse_non_negative_int(name: str, value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed


def _parse_non_negative_decimal(name: str, value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{name} must be a finite, non-negative decimal")
    return parsed


def _parse_user_ids(value: str) -> frozenset[uuid.UUID]:
    user_ids: set[uuid.UUID] = set()
    for raw_user_id in value.split(","):
        raw_user_id = raw_user_id.strip()
        if not raw_user_id:
            continue
        try:
            user_ids.add(uuid.UUID(raw_user_id))
        except ValueError as exc:
            raise ValueError(
                "WORK_RUNS_BETA_ALLOWED_USER_IDS must contain UUIDs"
            ) from exc
    return frozenset(user_ids)


@dataclass(frozen=True)
class WorkRunSettings:
    enabled: bool
    beta_allowed_user_ids: frozenset[uuid.UUID]
    max_active_per_user: int
    monthly_allowance_per_user: int
    per_run_budget_usd: Decimal
    global_daily_budget_usd: Decimal

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> WorkRunSettings:
        values = os.environ if environ is None else environ
        return cls(
            enabled=_parse_bool(values.get("WORK_RUNS_ENABLED", "False")),
            beta_allowed_user_ids=_parse_user_ids(
                values.get("WORK_RUNS_BETA_ALLOWED_USER_IDS", "")
            ),
            max_active_per_user=_parse_non_negative_int(
                "WORK_RUNS_MAX_ACTIVE_PER_USER",
                values.get("WORK_RUNS_MAX_ACTIVE_PER_USER", "1"),
            ),
            monthly_allowance_per_user=_parse_non_negative_int(
                "WORK_RUNS_MONTHLY_ALLOWANCE_PER_USER",
                values.get("WORK_RUNS_MONTHLY_ALLOWANCE_PER_USER", "0"),
            ),
            per_run_budget_usd=_parse_non_negative_decimal(
                "WORK_RUNS_PER_RUN_BUDGET_USD",
                values.get("WORK_RUNS_PER_RUN_BUDGET_USD", "0"),
            ),
            global_daily_budget_usd=_parse_non_negative_decimal(
                "WORK_RUNS_GLOBAL_DAILY_BUDGET_USD",
                values.get("WORK_RUNS_GLOBAL_DAILY_BUDGET_USD", "0"),
            ),
        )

    @property
    def execution_ready(self) -> bool:
        return (
            self.enabled
            and bool(self.beta_allowed_user_ids)
            and self.max_active_per_user > 0
            and self.monthly_allowance_per_user > 0
            and self.per_run_budget_usd > 0
            and self.global_daily_budget_usd > 0
        )

    def allows_user(self, user_id: uuid.UUID) -> bool:
        return self.execution_ready and user_id in self.beta_allowed_user_ids

