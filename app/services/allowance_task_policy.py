"""Durable policies separate customer charging from funded supplier execution."""

from app.core.config import settings

NO_HOLD = "no-hold-v3"
ITERATIVE = "iterative-v3"
ACTIVE_TASKS = 2
LEASE_SECONDS = 300
PROVIDER_SECONDS = 240


def limits(account):
    loss = min(
        settings.SHARED_ALLOWANCE_USER_LOSS_MAX_UNITS,
        max(
            settings.SHARED_ALLOWANCE_USER_LOSS_MIN_UNITS,
            account.granted * settings.SHARED_ALLOWANCE_USER_LOSS_PERCENT // 100,
        ),
    )
    return dict(
        version=NO_HOLD,
        active_tasks=ACTIVE_TASKS,
        lease_seconds=LEASE_SECONDS,
        provider_seconds=PROVIDER_SECONDS,
        task_seconds=settings.SHARED_ALLOWANCE_REQUEST_SECONDS,
        supplier_max=max(1, settings.SHARED_ALLOWANCE_TASK_MAX_UNITS),
        grace_units=max(0, min(settings.SHARED_ALLOWANCE_TASK_GRACE_UNITS, loss)),
        user_loss_units=max(0, loss),
        loss_window_days=30,
        tool_executions=max(1, min(12, settings.SHARED_ALLOWANCE_TOOL_EXECUTIONS)),
        planning_turns=max(1, min(12, settings.SHARED_ALLOWANCE_TOOL_TURNS)),
        parallelism=max(1, min(2, settings.SHARED_ALLOWANCE_TOOL_PARALLELISM)),
        research_seconds=max(1, settings.SHARED_ALLOWANCE_RESEARCH_SECONDS),
        image_operations=1,
        tool_result_tokens=2048,
        context_tokens=max(32_000, settings.SHARED_ALLOWANCE_LOOP_CONTEXT_TOKENS),
    )
