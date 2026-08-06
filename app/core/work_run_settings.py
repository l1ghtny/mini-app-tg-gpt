from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Mapping


_TRUE_VALUES = frozenset({"1", "true"})
_FALSE_VALUES = frozenset({"0", "false"})


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be true, false, 1, or 0")


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
class WorkRunDeploymentGate:
    """Deployment gate only; product limits belong to durable policy."""

    master_enabled: bool
    beta_allowed_user_ids: frozenset[uuid.UUID]

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> WorkRunDeploymentGate:
        values = os.environ if environ is None else environ
        return cls(
            master_enabled=_parse_bool(
                "WORK_RUNS_ENABLED",
                values.get("WORK_RUNS_ENABLED", "False"),
            ),
            beta_allowed_user_ids=_parse_user_ids(
                values.get("WORK_RUNS_BETA_ALLOWED_USER_IDS", "")
            ),
        )

    def allows_beta_user(self, user_id: uuid.UUID) -> bool:
        return self.master_enabled and user_id in self.beta_allowed_user_ids
