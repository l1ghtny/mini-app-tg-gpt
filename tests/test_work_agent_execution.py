from __future__ import annotations

import json
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


def _cited_response(
    response_id: str,
    output_text: str,
    *,
    title: str,
    url: str,
) -> SimpleNamespace:
    start_index = output_text.index(url)
    end_index = start_index + len(url)
    response = _response(response_id, output_text)
    response.output = [
        SimpleNamespace(type="web_search_call"),
        SimpleNamespace(
            type="message",
            content=[
                SimpleNamespace(
                    annotations=[
                        SimpleNamespace(
                            type="url_citation",
                            title=title,
                            url=url,
                            start_index=start_index,
                            end_index=end_index,
                        )
                    ]
                )
            ],
        ),
    ]
    return response


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
    assert result.generation_count == 2
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
        "sources": [],
        "citations": [],
        "citation_count": 0,
        "generated_artifacts": [],
    }
    assert "tools" not in create.await_args_list[1].kwargs
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
async def test_web_reviewer_rejects_resolving_but_irrelevant_source() -> None:
    irrelevant_url = (
        "https://platform.openai.com/docs/api-reference/evals/deleteRun?lang=python"
    )
    corrected_url = (
        "https://developers.openai.com/api/reference/resources/evals/methods/create"
    )
    first_draft = _cited_response(
        "draft-irrelevant-source",
        (
            "The first practical recommendation is to create task-specific evaluations "
            "with explicit testing criteria, and then compare the model configurations "
            f"for the agent. ([OpenAI Evals]({irrelevant_url}))"
        ),
        title="Evals | OpenAI API Reference",
        url=irrelevant_url,
    )
    failed_review = _response(
        "review-irrelevant-source",
        json.dumps(
            {
                "passes": False,
                "issues": [
                    "The delete-eval endpoint does not support the adjacent advice."
                ],
                "revision_instructions": (
                    "Replace the delete endpoint with a page that documents eval "
                    "criteria and model comparison."
                ),
            }
        ),
        "web_search_call",
    )
    corrected_draft = _cited_response(
        "draft-supported-source",
        (
            "The first practical recommendation is to create task-specific evaluations "
            "with explicit testing criteria, and then compare the model configurations "
            f"for the agent. ([OpenAI Evals]({corrected_url}))"
        ),
        title="Create eval | OpenAI API Reference",
        url=corrected_url,
    )
    passed_review = _response(
        "review-supported-source",
        json.dumps({"passes": True, "issues": [], "revision_instructions": ""}),
        "web_search_call",
    )
    create = AsyncMock(
        side_effect=[first_draft, failed_review, corrected_draft, passed_review]
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    request_payload = _request_payload()
    request_payload["current_request"] = (
        "Using current official OpenAI documentation, recommend how to evaluate agents "
        "and cite the source beside each claim."
    )

    result = await agent_execution._generate_validated_result(
        client=client,  # type: ignore[arg-type]
        request_payload=request_payload,
        tools=[{"type": "web_search"}],
        observe_response=AsyncMock(),
    )

    assert result.content == corrected_draft.output_text
    first_review_kwargs = create.await_args_list[1].kwargs
    assert first_review_kwargs["tools"] == [{"type": "web_search"}]
    assert first_review_kwargs["tool_choice"] == "required"
    assert "must directly support" in first_review_kwargs["input"][0]["content"][0][
        "text"
    ]
    first_review_payload = json.loads(
        first_review_kwargs["input"][1]["content"][0]["text"]
    )
    assert first_review_payload["available_evidence"]["sources"][0]["url"] == (
        irrelevant_url
    )
    assert "create task-specific evaluations" in first_review_payload[
        "available_evidence"
    ]["citations"][0]["cited_context"]
    retry_prompt = create.await_args_list[2].kwargs["input"][0]["content"][0]["text"]
    assert "Replace the delete endpoint" in retry_prompt


@pytest.mark.asyncio
async def test_plain_material_question_is_retried_as_structured_human_input() -> None:
    request_payload = _request_payload()
    request_payload["current_request"] = (
        "Prepare a launch announcement for our new product. Ask one concise question "
        "that obtains the minimum information needed before writing the announcement."
    )
    plain_question = _response(
        "draft-plain-question",
        "What is the product, target audience, and delivery channel?",
    )
    structured_question = SimpleNamespace(
        id="draft-structured-question",
        output_text="",
        output=[
            SimpleNamespace(
                type="function_call",
                name="ask_user",
                call_id="call-question-1",
                arguments=json.dumps(
                    {
                        "question": "What is the product, audience, and channel?",
                        "reason": "Those details materially change the announcement.",
                    }
                ),
            )
        ],
        usage=plain_question.usage,
    )
    create = AsyncMock(side_effect=[plain_question, structured_question])
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    handler = AsyncMock(side_effect=[False, True])

    with pytest.raises(agent_execution.WorkRunAwaitingUser):
        await agent_execution._generate_validated_result(
            client=client,  # type: ignore[arg-type]
            request_payload=request_payload,
            tools=[agent_execution.ASK_USER_TOOL, {"type": "web_search"}],
            observe_response=AsyncMock(),
            handle_human_input=handler,
        )

    assert create.await_count == 2
    retry_kwargs = create.await_args_list[1].kwargs
    assert retry_kwargs["tools"] == [agent_execution.ASK_USER_TOOL]
    assert retry_kwargs["tool_choice"] == "required"
    assert "structured ask_user tool" in retry_kwargs["input"][0]["content"][0]["text"]
    assert handler.await_count == 2


@pytest.mark.parametrize(
    "foreign_text",
    [
        (
            "Bu karar notu için üç öncelik öneriyorum ve her öneri dosya kanıtına "
            "dayanıyor. Sonuç olarak ürün ekibi önce güven sorunlarını çözmelidir."
        ),
        (
            "Definir el propósito de la evaluación y establecer criterios medibles. "
            "Preparar escenarios representativos, asignar responsables y documentar "
            "cómo se analizarán los resultados y las limitaciones del proceso."
        ),
    ],
    ids=["turkish", "spanish"],
)
@pytest.mark.asyncio
async def test_english_request_retries_a_foreign_draft_before_review(
    foreign_text: str,
) -> None:
    request_payload = _request_payload()
    request_payload["current_request"] = (
        "Read both attached files and write a decision memo for the beta. Recommend "
        "the three highest-priority product changes and explain the evidence for each."
    )
    foreign_draft = _response("draft-foreign", foreign_text)
    english_draft = _response(
        "draft-english",
        "The decision memo recommends three priorities supported by the attached "
        "files: evidence links, role-based access, and CSV export. The main risk is "
        "loss of user trust, and the proposed 30-day criterion is measurable.",
    )
    passed_review = _response(
        "review-english",
        json.dumps({"passes": True, "issues": [], "revision_instructions": ""}),
    )
    create = AsyncMock(side_effect=[foreign_draft, english_draft, passed_review])
    client = SimpleNamespace(responses=SimpleNamespace(create=create))

    result = await agent_execution._generate_validated_result(
        client=client,  # type: ignore[arg-type]
        request_payload=request_payload,
        tools=[{"type": "file_search"}],
        observe_response=AsyncMock(),
    )

    assert result.content == english_draft.output_text
    assert result.generation_count == 2
    assert result.review_count == 1
    retry_prompt = create.await_args_list[1].kwargs["input"][0]["content"][0]["text"]
    assert "current English request" in retry_prompt


def test_explicit_other_language_request_bypasses_english_auto_detection() -> None:
    issue = agent_execution._language_contract_error(
        "Bu karar notu üç ürün önceliğini açıklar ve dosya kanıtlarını özetler.",
        {
            "current_request": (
                "Read the attached files and write the decision memo in Turkish."
            )
        },
    )

    assert issue is None


@pytest.mark.asyncio
async def test_active_steering_discards_the_draft_before_review() -> None:
    first_draft = _response("draft-before-steering", "The original result." * 10)
    redirected_draft = _response(
        "draft-after-steering",
        "The redirected result follows the user's latest instruction." * 4,
    )
    passed_review = _response(
        "review-after-steering",
        json.dumps(
            {"passes": True, "issues": [], "revision_instructions": ""}
        ),
    )
    create = AsyncMock(
        side_effect=[first_draft, redirected_draft, passed_review]
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    consume_steering = AsyncMock(
        side_effect=["Focus only on the pricing risks.", None, None]
    )

    result = await agent_execution._generate_validated_result(
        client=client,  # type: ignore[arg-type]
        request_payload=_request_payload(),
        tools=[{"type": "web_search"}],
        observe_response=AsyncMock(),
        consume_steering=consume_steering,
    )

    assert result.content == redirected_draft.output_text
    assert result.steering_restarts == 1
    assert result.generation_count == 2
    assert create.await_count == 3
    redirected_request = create.await_args_list[1].kwargs
    redirected_payload = json.loads(
        redirected_request["input"][1]["content"][0]["text"]
    )
    assert redirected_payload["current_request"] == "Focus only on the pricing risks."
    assert "user redirected" in redirected_request["input"][0]["content"][0][
        "text"
    ]


@pytest.mark.asyncio
async def test_active_steering_after_review_still_replaces_the_result() -> None:
    first_draft = _response("draft-before-late-steering", "The original result." * 10)
    first_review = _response(
        "review-before-late-steering",
        json.dumps({"passes": True, "issues": [], "revision_instructions": ""}),
    )
    redirected_draft = _response(
        "draft-after-late-steering",
        "The new result focuses only on pricing risk." * 5,
    )
    redirected_review = _response(
        "review-after-late-steering",
        json.dumps({"passes": True, "issues": [], "revision_instructions": ""}),
    )
    create = AsyncMock(
        side_effect=[first_draft, first_review, redirected_draft, redirected_review]
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    consume_steering = AsyncMock(
        side_effect=[None, "Focus only on pricing risk.", None, None]
    )
    request_payload = _request_payload()
    request_payload["current_request"] = (
        "Review the current plan and summarize the main operational risk."
    )

    result = await agent_execution._generate_validated_result(
        client=client,  # type: ignore[arg-type]
        request_payload=request_payload,
        tools=[{"type": "web_search"}],
        observe_response=AsyncMock(),
        consume_steering=consume_steering,
    )

    assert result.content == redirected_draft.output_text
    assert result.steering_restarts == 1
    assert result.generation_count == 2
    assert result.review_count == 2


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
    request_payload = _request_payload()
    request_payload["current_request"] = (
        "Recommend the stronger supplier and state the next practical step."
    )

    result = await agent_execution._generate_validated_result(
        client=client,  # type: ignore[arg-type]
        request_payload=request_payload,
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


def test_document_tool_plan_keeps_two_vector_stores_on_file_search() -> None:
    documents = [
        SimpleNamespace(openai_vector_store_id="vs-2"),
        SimpleNamespace(openai_vector_store_id="vs-1"),
    ]

    vector_store_ids, code_interpreter_enabled = (
        agent_execution._document_tool_plan(
            documents,
            artifact_requested=False,
        )
    )

    assert vector_store_ids == ["vs-1", "vs-2"]
    assert code_interpreter_enabled is False


def test_document_tool_plan_preserves_three_documents_via_code_interpreter() -> None:
    documents = [
        SimpleNamespace(openai_vector_store_id="vs-1"),
        SimpleNamespace(openai_vector_store_id="vs-2"),
        SimpleNamespace(openai_vector_store_id="vs-3"),
    ]

    vector_store_ids, code_interpreter_enabled = (
        agent_execution._document_tool_plan(
            documents,
            artifact_requested=False,
        )
    )

    assert vector_store_ids == []
    assert code_interpreter_enabled is True


@pytest.mark.asyncio
async def test_code_interpreter_stages_a_source_without_provider_file_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = uuid.uuid4()
    document = SimpleNamespace(
        id=document_id,
        filename="supplier-offer.csv",
        openai_file_id=None,
        source_bucket="private-documents",
        source_storage_key="documents/supplier-offer.csv",
    )
    download = AsyncMock()

    async def materialize_source(**kwargs: str) -> None:
        with open(kwargs["target_path"], "w", encoding="utf-8") as source:
            source.write("supplier,price\nAcme,125\n")

    download.side_effect = materialize_source
    monkeypatch.setattr(agent_execution, "download_document_source", download)
    container_files_create = AsyncMock(
        return_value=SimpleNamespace(id="container-file-1")
    )
    client = SimpleNamespace(
        containers=SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(id="container-1")),
            files=SimpleNamespace(create=container_files_create),
            delete=AsyncMock(),
        )
    )

    prepared = await agent_execution._create_code_interpreter_container(
        client=client,  # type: ignore[arg-type]
        run=SimpleNamespace(id=uuid.uuid4()),
        documents=[document],
    )

    assert prepared.container_id == "container-1"
    assert prepared.provider_file_document_ids == {
        "container-file-1": str(document_id)
    }
    staged_path = container_files_create.await_args.kwargs["file"]
    assert staged_path.name == document.filename
    download.assert_awaited_once_with(
        bucket=document.source_bucket,
        key=document.source_storage_key,
        target_path=str(staged_path),
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
