from __future__ import annotations

import os
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("R2_BUCKET", "test-public-bucket")
os.environ.setdefault("R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-key")

from app.services.work_runs import service
from app.services.work_runs.comparison import ComparisonColumnSchema, SourceTable
from app.services.work_runs.contracts import WorkRunErrorCode
from app.services.work_runs.normalization import (
    ComparisonNormalizationResult,
    NormalizationUsage,
    serialize_comparison_column_schema,
)


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def first(self) -> object:
        return self.value

    def one(self) -> object:
        return self.value


class _Session:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.added: list[object] = []
        self.commits = 0

    async def exec(self, _statement: object) -> _Result:
        return _Result(self.results.pop(0))

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


def _tables() -> tuple[SourceTable, ...]:
    return (
        SourceTable(
            document_id=uuid.uuid4(),
            filename="a.csv",
            sheet_name="CSV",
            headers=("Product", "Price"),
            rows=(("Tea", 10),),
            first_data_row=2,
        ),
        SourceTable(
            document_id=uuid.uuid4(),
            filename="b.csv",
            sheet_name="CSV",
            headers=("Item", "Cost"),
            rows=(("Tea", 9),),
            first_data_row=2,
        ),
    )


def _schema(tables: tuple[SourceTable, ...]) -> ComparisonColumnSchema:
    first, second = tables
    return ComparisonColumnSchema(
        canonical_headers=("Product", "Price"),
        source_headers={
            (first.document_id, first.sheet_name, "Product"): "Product",
            (first.document_id, first.sheet_name, "Price"): "Price",
            (second.document_id, second.sheet_name, "Item"): "Product",
            (second.document_id, second.sheet_name, "Cost"): "Price",
        },
    )


def _run() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        kind="offer_comparison_xlsx",
        instructions="Compare prices",
        options={"currency": "EUR", "output_language": "en"},
        input_manifest={"document_ids": [str(uuid.uuid4()), str(uuid.uuid4())]},
        estimated_cost_usd=Decimal("0"),
        actual_cost_usd=Decimal("0"),
    )


@pytest.mark.asyncio
async def test_normalization_cost_records_immutable_pricing_snapshot() -> None:
    pricing_id = uuid.uuid4()
    pricing = SimpleNamespace(
        id=pricing_id,
        currency="USD",
        unit_price_input_per_1m=Decimal("1.000000"),
        unit_price_cached_input_per_1m=Decimal("0.100000"),
        unit_price_output_per_1m=Decimal("6.000000"),
        unit_price_reasoning_per_1m=Decimal("6.000000"),
    )

    cost, snapshot = await service._normalization_cost(
        _Session(pricing),  # type: ignore[arg-type]
        model="gpt-5.6-luna",
        usage=NormalizationUsage(1000, 200, 100, 25),
    )

    assert cost == Decimal("0.001570")
    assert snapshot["pricing_id"] == str(pricing_id)
    assert snapshot["unit_price_input_per_1m"] == "1.000000"
    assert snapshot["unit_price_cached_input_per_1m"] == "0.100000"
    assert snapshot["unit_price_output_per_1m"] == "6.000000"
    assert snapshot["unit_price_reasoning_per_1m"] == "6.000000"


@pytest.mark.asyncio
async def test_normalization_operation_persists_reusable_result_and_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tables = _tables()
    schema = _schema(tables)
    run = _run()
    session = _Session(None)
    monkeypatch.setattr(
        service,
        "_reserve_normalization_budget",
        AsyncMock(return_value=(Decimal("0.020000"), {"estimated": True})),
    )
    monkeypatch.setattr(
        service,
        "normalize_comparison_columns",
        AsyncMock(
            return_value=ComparisonNormalizationResult(
                schema=schema,
                model="gpt-5.6-luna",
                provider_response_id="resp_123",
                provider_request_id="req_123",
                usage=NormalizationUsage(100, 0, 50, 0),
                used_model=True,
            )
        ),
    )
    monkeypatch.setattr(
        service,
        "_normalization_cost",
        AsyncMock(return_value=(Decimal("0.001500"), {"tokens": 150})),
    )

    result = await service._normalize_columns_for_run(
        session=session,  # type: ignore[arg-type]
        run=run,
        tables=tables,
    )

    operation = next(
        value
        for value in session.added
        if value.__class__.__name__ == "ProviderOperation"
    )
    assert result == schema
    assert operation.status == "succeeded"
    assert operation.estimated_cost_usd == Decimal("0.020000")
    assert operation.actual_cost_usd == Decimal("0.001500")
    assert operation.provider_response_id == "resp_123"
    assert run.estimated_cost_usd == Decimal("0.020000")
    assert run.actual_cost_usd == Decimal("0.001500")
    assert run.input_manifest["normalization_v1"]["schema"] == (
        serialize_comparison_column_schema(tables, schema)
    )


@pytest.mark.asyncio
async def test_existing_successful_operation_reuses_stored_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tables = _tables()
    schema = _schema(tables)
    run = _run()
    run.input_manifest["normalization_v1"] = {
        "schema": serialize_comparison_column_schema(tables, schema)
    }
    operation = SimpleNamespace(status="succeeded")
    normalize = AsyncMock()
    reserve = AsyncMock()
    monkeypatch.setattr(service, "normalize_comparison_columns", normalize)
    monkeypatch.setattr(service, "_reserve_normalization_budget", reserve)

    result = await service._normalize_columns_for_run(
        session=_Session(operation),  # type: ignore[arg-type]
        run=run,
        tables=tables,
    )

    assert result == schema
    normalize.assert_not_awaited()
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_marks_operation_ambiguous_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class APITimeoutError(Exception):
        pass

    run = _run()
    session = _Session(None)
    monkeypatch.setattr(
        service,
        "_reserve_normalization_budget",
        AsyncMock(return_value=(Decimal("0.020000"), {"estimated": True})),
    )
    monkeypatch.setattr(
        service,
        "normalize_comparison_columns",
        AsyncMock(side_effect=APITimeoutError("provider timed out")),
    )

    with pytest.raises(service.WorkRunExecutionError) as error:
        await service._normalize_columns_for_run(
            session=session,  # type: ignore[arg-type]
            run=run,
            tables=_tables(),
        )

    operation = next(
        value
        for value in session.added
        if value.__class__.__name__ == "ProviderOperation"
    )
    assert error.value.code == WorkRunErrorCode.PROVIDER_AMBIGUOUS
    assert operation.status == "ambiguous"
    assert operation.error_code == WorkRunErrorCode.PROVIDER_AMBIGUOUS.value


@pytest.mark.asyncio
async def test_budget_reservation_rejects_projected_daily_overspend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = SimpleNamespace(
        enabled=True,
        per_run_budget_usd=Decimal("1"),
        global_daily_budget_usd=Decimal("3"),
    )
    session = _Session(
        policy,
        Decimal("1.00"),
        Decimal("1.60"),
        Decimal("0.20"),
    )
    monkeypatch.setattr(
        service,
        "_normalization_cost",
        AsyncMock(return_value=(Decimal("0.25"), {"estimated": True})),
    )

    with pytest.raises(service.WorkRunExecutionError) as error:
        await service._reserve_normalization_budget(
            session=session,  # type: ignore[arg-type]
            run=_run(),
            tables=_tables(),
        )

    assert error.value.code == WorkRunErrorCode.DAILY_BUDGET_EXCEEDED
