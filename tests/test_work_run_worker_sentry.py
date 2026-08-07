from __future__ import annotations

from unittest.mock import Mock

from app.core.work_run_worker_sentry import initialize_work_run_worker_sentry


def test_worker_sentry_stays_disabled_without_dsn_or_in_local_environment() -> None:
    init = Mock()

    assert not initialize_work_run_worker_sentry(
        dsn="",
        environment="beta_prod_data",
        release="beta-44",
        deployment_channel="beta",
        init=init,
    )
    assert not initialize_work_run_worker_sentry(
        dsn="https://public@example.invalid/1",
        environment="test",
        release="test",
        deployment_channel="test",
        init=init,
    )
    init.assert_not_called()


def test_worker_sentry_enables_metrics_without_pii_or_stack_locals() -> None:
    init = Mock()
    set_tag = Mock()

    assert initialize_work_run_worker_sentry(
        dsn="https://public@example.invalid/1",
        environment="beta_prod_data",
        release="beta-44",
        deployment_channel="beta",
        init=init,
        set_tag=set_tag,
    )

    init.assert_called_once_with(
        dsn="https://public@example.invalid/1",
        environment="beta_prod_data",
        release="beta-44",
        traces_sample_rate=1.0,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        enable_logs=True,
        _experiments={"metrics_aggregator": True},
    )
    assert set_tag.call_args_list == [
        (("deployment_channel", "beta"),),
        (("service", "work-run-worker"),),
    ]
