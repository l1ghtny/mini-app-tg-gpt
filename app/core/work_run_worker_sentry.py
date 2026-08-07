from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import sentry_sdk


_LOCAL_ENVIRONMENTS = frozenset({"local", "development", "test"})


def initialize_work_run_worker_sentry(
    *,
    dsn: str,
    environment: str,
    release: str,
    deployment_channel: str,
    init: Callable[..., Any] = sentry_sdk.init,
    set_tag: Callable[[str, str], Any] = sentry_sdk.set_tag,
    logger: logging.Logger | None = None,
) -> bool:
    resolved_environment = environment.strip()
    if not dsn.strip() or resolved_environment.lower() in _LOCAL_ENVIRONMENTS:
        return False

    init(
        dsn=dsn,
        environment=resolved_environment,
        release=release,
        traces_sample_rate=(
            0.1
            if resolved_environment in ("production", "production_main_server")
            else 1.0
        ),
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        enable_logs=True,
        _experiments={"metrics_aggregator": True},
    )
    set_tag("deployment_channel", deployment_channel)
    set_tag("service", "work-run-worker")
    if logger is not None:
        logger.info("Initialized Sentry for work-run worker")
    return True
