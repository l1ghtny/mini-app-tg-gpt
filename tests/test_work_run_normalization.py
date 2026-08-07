from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.work_runs.comparison import ComparisonInputError, SourceTable
from app.services.work_runs.normalization import normalize_comparison_columns


def _table(
    *,
    filename: str,
    headers: tuple[str, ...],
    rows: tuple[tuple[object, ...], ...],
) -> SourceTable:
    return SourceTable(
        document_id=uuid.uuid4(),
        filename=filename,
        sheet_name="CSV",
        headers=headers,
        rows=rows,
        first_data_row=2,
    )


def _client(payload: dict[str, object]) -> tuple[SimpleNamespace, AsyncMock]:
    response = SimpleNamespace(
        id="resp_123",
        _request_id="req_123",
        output_text=json.dumps(payload, ensure_ascii=False),
        usage=SimpleNamespace(
            input_tokens=120,
            output_tokens=80,
            input_tokens_details=SimpleNamespace(cached_tokens=20),
            output_tokens_details=SimpleNamespace(reasoning_tokens=10),
        ),
    )
    create = AsyncMock(return_value=response)
    return SimpleNamespace(responses=SimpleNamespace(create=create)), create


@pytest.mark.asyncio
async def test_normalization_skips_provider_for_matching_columns() -> None:
    tables = (
        _table(filename="a.csv", headers=("Product", "Price"), rows=(("Tea", 10),)),
        _table(filename="b.csv", headers=("product", "price"), rows=(("Tea", 9),)),
    )
    client, create = _client({})

    result = await normalize_comparison_columns(
        tables=tables,
        instructions=None,
        currency="EUR",
        language="en",
        client=client,
    )

    assert result.used_model is False
    assert result.schema.canonical_headers == ("Product", "Price")
    assert result.usage.input_tokens == 0
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_normalization_aligns_semantically_equivalent_columns() -> None:
    first = _table(
        filename="a.csv",
        headers=("Наименование", "Цена"),
        rows=(("Чай", 120),),
    )
    second = _table(
        filename="b.csv",
        headers=("Товар", "Стоимость", "Срок"),
        rows=(("Чай", 110, "3 дня"),),
    )
    client, create = _client(
        {
            "schema_version": 1,
            "canonical_headers": ["Товар", "Цена", "Срок"],
            "assignments": [
                {
                    "table_id": "table_1",
                    "source_header": "Наименование",
                    "canonical_header": "Товар",
                },
                {
                    "table_id": "table_1",
                    "source_header": "Цена",
                    "canonical_header": "Цена",
                },
                {
                    "table_id": "table_2",
                    "source_header": "Товар",
                    "canonical_header": "Товар",
                },
                {
                    "table_id": "table_2",
                    "source_header": "Стоимость",
                    "canonical_header": "Цена",
                },
                {
                    "table_id": "table_2",
                    "source_header": "Срок",
                    "canonical_header": "Срок",
                },
            ],
        }
    )

    result = await normalize_comparison_columns(
        tables=(first, second),
        instructions="Сравнить цены и сроки",
        currency="RUB",
        language="ru",
        client=client,
    )

    assert result.used_model is True
    assert result.schema.canonical_headers == ("Товар", "Цена", "Срок")
    assert result.schema.canonical_header(first, "Наименование") == "Товар"
    assert result.schema.canonical_header(second, "Стоимость") == "Цена"
    assert result.provider_response_id == "resp_123"
    assert result.provider_request_id == "req_123"
    assert result.usage.input_tokens == 120
    assert result.usage.cached_input_tokens == 20
    assert result.usage.output_tokens == 80
    assert result.usage.reasoning_tokens == 10
    request = create.await_args.kwargs
    assert request["model"] == "gpt-5.6-luna"
    assert request["reasoning"] == {"effort": "low"}
    assert request["store"] is True
    assert request["text"]["format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_normalization_rejects_incomplete_source_mapping() -> None:
    tables = (
        _table(filename="a.csv", headers=("Product", "Price"), rows=()),
        _table(filename="b.csv", headers=("Item", "Cost"), rows=()),
    )
    client, _ = _client(
        {
            "schema_version": 1,
            "canonical_headers": ["Product", "Price"],
            "assignments": [
                {
                    "table_id": "table_1",
                    "source_header": "Product",
                    "canonical_header": "Product",
                }
            ],
        }
    )

    with pytest.raises(ComparisonInputError, match="did not map every"):
        await normalize_comparison_columns(
            tables=tables,
            instructions=None,
            currency=None,
            language="en",
            client=client,
        )


@pytest.mark.asyncio
async def test_normalization_rejects_merging_columns_within_one_table() -> None:
    tables = (
        _table(filename="a.csv", headers=("Price", "Unit price"), rows=()),
        _table(filename="b.csv", headers=("Cost",), rows=()),
    )
    client, _ = _client(
        {
            "schema_version": 1,
            "canonical_headers": ["Price"],
            "assignments": [
                {
                    "table_id": "table_1",
                    "source_header": "Price",
                    "canonical_header": "Price",
                },
                {
                    "table_id": "table_1",
                    "source_header": "Unit price",
                    "canonical_header": "Price",
                },
                {
                    "table_id": "table_2",
                    "source_header": "Cost",
                    "canonical_header": "Price",
                },
            ],
        }
    )

    with pytest.raises(ComparisonInputError, match="within one source table"):
        await normalize_comparison_columns(
            tables=tables,
            instructions=None,
            currency=None,
            language="en",
            client=client,
        )
