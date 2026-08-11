from __future__ import annotations

import json
import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("R2_BUCKET", "test-public-bucket")
os.environ.setdefault("R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-key")

from app.services.work_runs import agent_execution, service
from app.services.work_runs.contracts import WorkRunErrorCode


def _response(
    response_id: str,
    output_text: str,
    *output_types: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=response_id,
        output_text=output_text,
        output=[SimpleNamespace(type=output_type) for output_type in output_types],
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=50,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=10),
        ),
    )


def _request_payload() -> dict[str, object]:
    return {
        "original_goal": "Подготовь краткий чек-лист из 5 пунктов.",
        "current_request": (
            "Подготовь краткий чек-лист из 5 пунктов для оценки нового продукта "
            "перед beta-запуском. Используй только общие знания, без веб-поиска."
        ),
        "work_history": [],
        "approved_plan": {
            "summary": "Подготовить практический чек-лист.",
            "steps": [],
            "expected_outputs": [
                {
                    "kind": "answer",
                    "label": "Чек-лист",
                    "description": "Пять практических пунктов.",
                    "acceptance_criteria": [
                        "Ровно пять проверяемых пунктов.",
                        "Пункты сформулированы как действия или вопросы, а не как уже выполненные проверки.",
                    ],
                }
            ],
            "assumptions": ["Нет данных о конкретном продукте."],
        },
        "source_files": [],
        "searchable_source_files": [],
        "output_language": "ru",
    }


@pytest.mark.asyncio
async def test_agent_retries_a_status_report_and_returns_the_deliverable() -> None:
    first_draft = _response(
        "draft-1",
        "Ценность продукта: подтверждено, какую проблему он решает.",
    )
    failed_review = _response(
        "review-1",
        json.dumps(
            {
                "passes": False,
                "issues": ["The draft claims verification without evidence."],
                "revision_instructions": (
                    "Return exactly five forward-looking checklist questions and do not "
                    "claim that any check already passed."
                ),
            }
        ),
    )
    corrected_draft = _response(
        "draft-2",
        "\n".join(
            [
                "## Чек-лист перед beta-запуском",
                "- [ ] Понятно ли, какую проблему решает продукт?",
                "- [ ] Определена ли основная аудитория?",
                "- [ ] Протестированы ли критические сценарии?",
                "- [ ] Настроены ли обратная связь и аналитика?",
                "- [ ] Заданы ли измеримые критерии успеха?",
            ]
        ),
    )
    passed_review = _response(
        "review-2",
        json.dumps(
            {
                "passes": True,
                "issues": [],
                "revision_instructions": "",
            }
        ),
    )
    create = AsyncMock(
        side_effect=[first_draft, failed_review, corrected_draft, passed_review]
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    observer = AsyncMock()
    phase_observer = AsyncMock()

    result = await agent_execution._generate_validated_result(
        client=client,  # type: ignore[arg-type]
        request_payload=_request_payload(),
        tools=[{"type": "web_search"}],
        observe_response=observer,
        observe_phase=phase_observer,
    )

    assert result.content == corrected_draft.output_text
    assert result.attempt_count == 2
    assert result.review_count == 2
    assert result.validation_passed is True
    assert result.validation_issues == ()
    assert create.await_count == 4
    first_prompt = create.await_args_list[0].kwargs["input"][0]["content"][0]["text"]
    assert "deliverable itself" in first_prompt
    assert "Never claim" in first_prompt
    retry_prompt = create.await_args_list[2].kwargs["input"][0]["content"][0]["text"]
    assert "exactly five forward-looking checklist questions" in retry_prompt
    review_payload = json.loads(
        create.await_args_list[1].kwargs["input"][1]["content"][0]["text"]
    )
    assert review_payload["available_evidence"] == {
        "searchable_source_files": [],
        "web_search_calls": 0,
        "file_search_calls": 0,
        "generated_artifact_count": 0,
    }
    assert (
        review_payload["approved_plan"]["expected_outputs"][0]["acceptance_criteria"][1]
        == "Пункты сформулированы как действия или вопросы, а не как уже выполненные проверки."
    )
    review_schema = create.await_args_list[1].kwargs["text"]["format"]["schema"]
    assert set(review_schema["required"]) == {
        "passes",
        "issues",
        "revision_instructions",
    }
    assert [call.args[0] for call in observer.await_args_list] == [
        "draft_1",
        "review_1",
        "draft_2",
        "review_2",
    ]
    assert [call.args for call in phase_observer.await_args_list] == [
        ("drafting", 1),
        ("reviewing", 1),
        ("revising", 2),
        ("reviewing", 2),
    ]


@pytest.mark.asyncio
async def test_agent_returns_the_best_draft_after_one_corrective_retry() -> None:
    failed_review = json.dumps(
        {
            "passes": False,
            "issues": ["The requested deliverable is still missing."],
            "revision_instructions": "Return the requested deliverable.",
        }
    )
    useful_draft = (
        "Supplier A is the stronger option because its delivery record is more "
        "consistent and its support terms reduce operational risk. Confirm the "
        "renewal clause before signing, then run a two-week implementation pilot."
    )
    create = AsyncMock(
        side_effect=[
            _response("draft-1", "Work completed."),
            _response("review-1", failed_review),
            _response("draft-2", useful_draft),
            _response("review-2", failed_review),
        ]
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=create))

    result = await agent_execution._generate_validated_result(
        client=client,  # type: ignore[arg-type]
        request_payload=_request_payload(),
        tools=[{"type": "web_search"}],
        observe_response=AsyncMock(),
    )

    assert result.content == useful_draft
    assert result.validation_passed is False
    assert result.validation_issues == ("The requested deliverable is still missing.",)
    assert create.await_count == 4


@pytest.mark.asyncio
async def test_agent_still_fails_when_every_draft_is_empty() -> None:
    create = AsyncMock(
        side_effect=[
            _response("draft-1", ""),
            _response("draft-2", ""),
        ]
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=create))

    with pytest.raises(service.WorkRunExecutionError) as error:
        await agent_execution._generate_validated_result(
            client=client,  # type: ignore[arg-type]
            request_payload=_request_payload(),
            tools=[{"type": "web_search"}],
            observe_response=AsyncMock(),
        )

    assert error.value.code == WorkRunErrorCode.VALIDATION_FAILED


@pytest.mark.asyncio
async def test_agent_does_not_return_a_rejected_status_line_as_the_result() -> None:
    failed_review = json.dumps(
        {
            "passes": False,
            "issues": ["The requested deliverable is still missing."],
            "revision_instructions": "Return the deliverable itself.",
        }
    )
    create = AsyncMock(
        side_effect=[
            _response("draft-1", "Work completed."),
            _response("review-1", failed_review),
            _response("draft-2", "All checks passed."),
            _response("review-2", failed_review),
        ]
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=create))

    with pytest.raises(service.WorkRunExecutionError) as error:
        await agent_execution._generate_validated_result(
            client=client,  # type: ignore[arg-type]
            request_payload=_request_payload(),
            tools=[{"type": "web_search"}],
            observe_response=AsyncMock(),
        )

    assert error.value.code == WorkRunErrorCode.VALIDATION_FAILED


def test_web_search_annotations_become_visible_source_links() -> None:
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(
                        annotations=[
                            SimpleNamespace(
                                type="url_citation",
                                title="Example source",
                                url="https://example.com/source",
                            )
                        ]
                    )
                ],
            )
        ]
    )

    result = agent_execution._attach_source_links("Useful result.", response)

    assert result.endswith(
        "### Sources\n- [Example source](https://example.com/source)"
    )


class _Session:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_each_generation_and_review_call_is_costed_durably(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    run = SimpleNamespace(actual_cost_usd=Decimal("0"))
    operation = SimpleNamespace(
        usage={"estimate": {"total_cost_usd": "0.25"}, "calls": []},
        actual_cost_usd=Decimal("0"),
        provider_response_id=None,
        provider_request_id=None,
    )
    cost = AsyncMock(
        side_effect=[
            (Decimal("0.010000"), {"tokens": 150}),
            (Decimal("0.001000"), {"tokens": 70}),
        ]
    )
    monkeypatch.setattr(service, "_normalization_cost", cost)

    await agent_execution._record_provider_response(
        session=session,  # type: ignore[arg-type]
        run=run,  # type: ignore[arg-type]
        operation=operation,  # type: ignore[arg-type]
        phase="draft_1",
        response=_response(
            "draft-1",
            "Draft",
            "web_search_call",
            "file_search_call",
        ),
    )
    await agent_execution._record_provider_response(
        session=session,  # type: ignore[arg-type]
        run=run,  # type: ignore[arg-type]
        operation=operation,  # type: ignore[arg-type]
        phase="review_1",
        response=_response("review-1", "{}"),
    )

    assert operation.actual_cost_usd == Decimal("0.013500")
    assert run.actual_cost_usd == Decimal("0.013500")
    assert operation.usage["total_cost_usd"] == "0.013500"
    assert [call["phase"] for call in operation.usage["calls"]] == [
        "draft_1",
        "review_1",
    ]
    assert operation.usage["calls"][0]["usage"]["file_search_calls"] == 1
    assert session.commits == 2
