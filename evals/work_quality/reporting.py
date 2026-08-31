from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean

from evals.work_quality.contracts import (
    CaseScore,
    EvalObservation,
    EvalReport,
    EvalSuite,
    HumanAssessment,
)
from evals.work_quality.scoring import score_observation


def build_report(
    suite: EvalSuite,
    observations: list[EvalObservation],
    *,
    environment: str,
    human_assessments: list[HumanAssessment] = (),
) -> EvalReport:
    cases_by_id = {case.id: case for case in suite.cases}
    humans_by_id = {
        assessment.case_id: assessment
        for assessment in human_assessments
        if assessment.reviewed
    }
    scores: list[CaseScore] = []
    for observation in observations:
        case = cases_by_id.get(observation.case_id)
        if case is None:
            continue
        scores.append(
            score_observation(
                case,
                observation,
                human=humans_by_id.get(case.id),
            )
        )

    all_runs = [run for observation in observations for run in observation.runs]
    final_runs = [
        observation.runs[-1] for observation in observations if observation.runs
    ]
    artifacts = [artifact for run in all_runs for artifact in run.artifacts]
    artifact_checks = [
        check
        for score in scores
        for check in score.checks
        if check.id.startswith("artifact_valid_")
    ]
    citation_case_ids = {
        case.id for case in suite.cases if case.expectations.min_citations > 0
    }
    citation_checks = [
        check
        for score in scores
        if score.case_id in citation_case_ids
        for check in score.checks
        if check.id == "citation_integrity"
    ]
    assessed = [score.human for score in scores if score.human is not None]
    metrics: dict[str, float | int | None] = {
        "cases_in_suite": len(suite.cases),
        "cases_observed": len(scores),
        "automated_pass_rate": _rate(
            sum(score.automated_passed for score in scores), len(scores)
        ),
        "system_success_rate": _rate(
            sum(run.status == "succeeded" for run in final_runs), len(final_runs)
        ),
        "artifact_validity_rate": _rate(
            sum(check.passed for check in artifact_checks), len(artifact_checks)
        ),
        "citation_integrity_rate": _rate(
            sum(check.passed for check in citation_checks), len(citation_checks)
        ),
        "average_cost_usd": mean(run.actual_cost_usd for run in all_runs)
        if all_runs
        else None,
        "p90_cost_usd": _percentile([run.actual_cost_usd for run in all_runs], 0.9),
        "average_duration_seconds": mean(
            run.duration_seconds for run in all_runs if run.duration_seconds is not None
        )
        if any(run.duration_seconds is not None for run in all_runs)
        else None,
        "human_assessments": len(assessed),
        "useful_without_correction_rate": _rate(
            sum(
                item.usefulness >= 4 and not item.needed_correction for item in assessed
            ),
            len(assessed),
        ),
        "average_human_score": mean(
            mean(
                [item.usefulness, item.correctness, item.completeness, item.readability]
            )
            for item in assessed
        )
        if assessed
        else None,
        "artifact_count": len(artifacts),
    }
    return EvalReport(
        suite_version=suite.version,
        generated_at=datetime.now(timezone.utc),
        environment=environment,
        case_scores=scores,
        metrics=metrics,
    )


def markdown_report(report: EvalReport, suite: EvalSuite) -> str:
    lines = [
        f"# {suite.name} — {report.environment}",
        "",
        f"Generated: {report.generated_at.isoformat()}",
        "",
        "## Product gates",
        "",
        "| Metric | Result | MVP gate |",
        "| --- | ---: | ---: |",
        f"| Automated case pass | {_format_rate(report.metrics['automated_pass_rate'])} | ≥ 80% |",
        f"| System success | {_format_rate(report.metrics['system_success_rate'])} | ≥ 90% |",
        f"| Valid artifacts | {_format_rate(report.metrics['artifact_validity_rate'])} | 100% |",
        f"| Citation integrity | {_format_rate(report.metrics['citation_integrity_rate'])} | ≥ 95% |",
        f"| Useful without correction | {_format_rate(report.metrics['useful_without_correction_rate'])} | ≥ 70% |",
        "",
        "## Cases",
        "",
        "| Case | Automated | Human | Main failures |",
        "| --- | --- | --- | --- |",
    ]
    for score in report.case_scores:
        failures = [
            check.id for check in score.checks if check.required and not check.passed
        ]
        human = (
            f"{mean([score.human.usefulness, score.human.correctness, score.human.completeness, score.human.readability]):.1f}/5"
            if score.human
            else "pending"
        )
        lines.append(
            f"| `{score.case_id}` | {'pass' if score.automated_passed else 'fail'} "
            f"({score.automated_score:.0%}) | {human} | {', '.join(failures) or '—'} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Automated checks cover system behavior, observable tool use, evidence integrity, "
            "artifact structure, cost, and latency. They do not prove that a fluent answer is "
            "correct or useful. Complete the human rubric before making a product decision.",
            "",
        ]
    )
    return "\n".join(lines)


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _format_rate(value: object) -> str:
    return f"{float(value):.1%}" if value is not None else "pending"


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return ordered[index]
