from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from openpyxl import Workbook

from evals.work_quality.artifacts import validate_artifact_file
from evals.work_quality.cli import _read_bearer_token, _read_secret_file
from evals.work_quality.live import WorkEvalClient
from evals.work_quality.contracts import (
    ArtifactObservation,
    EvalObservation,
    HumanAssessment,
    RunObservation,
)
from evals.work_quality.reporting import build_report, markdown_report
from evals.work_quality.scoring import score_observation
from evals.work_quality.suite import (
    DEFAULT_SUITE_PATH,
    load_suite,
    select_cases,
    validate_suite_files,
)


def test_work_quality_suite_has_the_approved_product_coverage() -> None:
    suite = load_suite()

    assert suite.version == 1
    assert len(suite.cases) == 15
    assert validate_suite_files(suite, DEFAULT_SUITE_PATH) == []
    categories = {case.category for case in suite.cases}
    assert categories == {
        "artifact",
        "clarification",
        "documents",
        "recovery",
        "revision",
        "spreadsheet",
        "web_research",
    }


def test_work_quality_suite_covers_positive_and_negative_clarification() -> None:
    suite = load_suite()
    cases = {case.id: case for case in suite.cases}

    required = cases["clarify_material_audience"]
    assert required.expectations.clarification == "required"
    assert [interaction.action for interaction in required.interactions] == ["answer"]
    assert required.interactions[0].wait_for == "waiting_for_user"

    forbidden = cases["clarify_not_needed"]
    assert forbidden.expectations.clarification == "forbidden"
    assert forbidden.interactions == []


def test_mvp_core_profile_covers_the_product_acceptance_boundary() -> None:
    suite = load_suite()

    cases = select_cases(suite, profile="mvp-core")

    assert [case.id for case in cases] == [
        "web_openai_agent_evals",
        "docs_product_decision_memo",
        "sheet_sales_analysis",
        "artifact_action_plan_docx",
        "clarify_material_audience",
        "recovery_cancel_then_redirect",
    ]
    assert {case.category for case in cases} == {
        "artifact",
        "clarification",
        "documents",
        "recovery",
        "spreadsheet",
        "web_research",
    }
    assert any(case.expectations.clarification == "required" for case in cases)
    assert {
        extension
        for case in cases
        for extension in case.expectations.artifact_extensions
    } == {
        ".docx",
        ".xlsx",
    }


def test_case_selection_rejects_unknown_case_ids() -> None:
    suite = load_suite()

    try:
        select_cases(suite, case_ids=["not_a_real_case"])
    except ValueError as exc:
        assert "unknown case ids" in str(exc)
    else:
        raise AssertionError("unknown case ids should be rejected")


def test_scoring_passes_observable_research_contract() -> None:
    suite = load_suite()
    case = next(case for case in suite.cases if case.id == "web_openai_agent_evals")
    now = datetime.now(timezone.utc)
    observation = EvalObservation(
        suite_version=suite.version,
        case_id=case.id,
        environment="test",
        started_at=now,
        completed_at=now + timedelta(seconds=12),
        runs=[
            RunObservation(
                id="run-1",
                status="succeeded",
                result_text=(
                    "Use task-specific evaluations built from real use patterns. "
                    "Combine deterministic checks with calibrated human review. "
                    "Repeat the evaluation on every meaningful change."
                ),
                actual_cost_usd=0.08,
                duration_seconds=12,
                tool_counts={"web_search": 2},
                sources=[{"id": "s1"}, {"id": "s2"}],
                citations=[{"source_id": "s1"}, {"source_id": "s2"}],
            )
        ],
    )

    score = score_observation(case, observation)

    assert score.automated_passed is True
    assert score.automated_score == 1


def test_scoring_rejects_missing_downloaded_artifact() -> None:
    suite = load_suite()
    case = next(case for case in suite.cases if case.id == "artifact_launch_pdf")
    now = datetime.now(timezone.utc)
    observation = EvalObservation(
        suite_version=suite.version,
        case_id=case.id,
        environment="test",
        started_at=now,
        completed_at=now,
        runs=[
            RunObservation(
                id="run-1",
                status="succeeded",
                result_text="A detailed inline summary of the generated invitation. "
                * 4,
                tool_counts={"file_search": 1, "code_interpreter": 1},
                sources=[{"id": "source"}],
                citations=[{"source_id": "source"}],
                artifacts=[
                    ArtifactObservation(
                        id="artifact-1",
                        filename="invitation.pdf",
                        status="ready",
                        size_bytes=4000,
                    )
                ],
            )
        ],
    )

    score = score_observation(case, observation)

    artifact_check = next(
        check for check in score.checks if check.id.startswith("artifact_valid_")
    )
    assert artifact_check.passed is False
    assert "not downloaded" in artifact_check.detail
    assert score.automated_passed is False


def test_artifact_validator_opens_real_workbook(tmp_path: Path) -> None:
    path = tmp_path / "result.xlsx"
    workbook = Workbook()
    workbook.active.append(["Metric", "Value"])
    workbook.active.append(["Revenue", 42])
    workbook.save(path)

    passed, detail = validate_artifact_file(path)

    assert passed is True
    assert detail == "artifact is structurally valid"


def test_eval_token_can_be_loaded_from_private_file(tmp_path: Path) -> None:
    token_file = tmp_path / "eval.jwt"
    token_file.write_text("synthetic-token\n", encoding="utf-8")

    assert _read_bearer_token("UNSET_TEST_TOKEN", token_file) == "synthetic-token"


def test_eval_session_cookie_can_be_loaded_from_private_file(tmp_path: Path) -> None:
    cookie_file = tmp_path / "eval.session"
    cookie_file.write_text("synthetic-session\n", encoding="utf-8")

    assert _read_secret_file(cookie_file) == "synthetic-session"


def test_eval_client_requires_exactly_one_authentication_method() -> None:
    client = WorkEvalClient(
        base_url="https://example.test",
        session_cookie="synthetic-session",
    )
    assert client.client.headers["Origin"] == "https://example.test"
    assert client.client.headers["Referer"] == "https://example.test/"
    client.close()

    try:
        WorkEvalClient(base_url="https://example.test")
    except ValueError as exc:
        assert "exactly one" in str(exc)
    else:
        raise AssertionError("missing authentication should be rejected")


def test_report_keeps_human_usefulness_separate_from_automation() -> None:
    suite = load_suite()
    case = next(case for case in suite.cases if case.id == "clarify_not_needed")
    now = datetime.now(timezone.utc)
    observation = EvalObservation(
        suite_version=suite.version,
        case_id=case.id,
        environment="test",
        started_at=now,
        completed_at=now,
        runs=[
            RunObservation(
                status="succeeded",
                result_text="A practical private-beta invitation written for operations leads. "
                * 4,
            )
        ],
    )
    human = HumanAssessment(
        case_id=case.id,
        reviewed=True,
        usefulness=4,
        correctness=5,
        completeness=4,
        readability=5,
        needed_correction=False,
        notes="Ready to use.",
    )

    report = build_report(
        suite,
        [observation],
        environment="test",
        human_assessments=[human],
    )

    assert report.metrics["human_assessments"] == 1
    assert report.metrics["useful_without_correction_rate"] == 1
    assert report.case_scores[0].human == human
    assert "Useful without correction" in markdown_report(report, suite)
