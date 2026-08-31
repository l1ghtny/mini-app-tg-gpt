from __future__ import annotations

import re
from pathlib import Path

from evals.work_quality.artifacts import validate_artifact_file
from evals.work_quality.contracts import (
    CaseScore,
    CheckResult,
    EvalCase,
    EvalObservation,
    HumanAssessment,
)


_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_LATIN = re.compile(r"[A-Za-z]")
_TURKISH_SPECIFIC = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
_TURKISH_WORDS = {
    "ancak",
    "bir",
    "bu",
    "değil",
    "dosya",
    "için",
    "ile",
    "olarak",
    "öncelik",
    "öneri",
    "sonuç",
    "ve",
}


def score_observation(
    case: EvalCase,
    observation: EvalObservation,
    *,
    human: HumanAssessment | None = None,
) -> CaseScore:
    checks: list[CheckResult] = []
    runs = observation.runs
    final_run = runs[-1] if runs else None
    successful_runs = sum(run.status == "succeeded" for run in runs)

    _check(
        checks,
        "api_execution",
        not observation.api_errors and bool(runs),
        "product API completed without an orchestration error"
        if not observation.api_errors and runs
        else "; ".join(observation.api_errors) or "no Work run was observed",
    )
    _check(
        checks,
        "terminal_status",
        bool(final_run and final_run.status in case.expectations.terminal_statuses),
        f"final status={final_run.status if final_run else 'missing'}; "
        f"expected={case.expectations.terminal_statuses}",
    )
    _check(
        checks,
        "successful_runs",
        successful_runs >= case.expectations.min_successful_runs,
        f"successful runs={successful_runs}; required={case.expectations.min_successful_runs}",
    )

    result_text = final_run.result_text.strip() if final_run else ""
    _check(
        checks,
        "deliverable_present",
        len(result_text) >= case.expectations.min_result_characters,
        f"result characters={len(result_text)}; required={case.expectations.min_result_characters}",
    )
    if case.expectations.response_language != "any":
        _check(
            checks,
            "response_language",
            _matches_language(result_text, case.expectations.response_language),
            f"expected predominant language={case.expectations.response_language}",
        )

    tool_counts = _aggregate_tool_counts(runs)
    for tool in case.expectations.required_tools:
        _check(
            checks,
            f"tool_required_{tool}",
            tool_counts.get(tool, 0) > 0,
            f"{tool} calls={tool_counts.get(tool, 0)}",
        )
    for tool in case.expectations.forbidden_tools:
        _check(
            checks,
            f"tool_forbidden_{tool}",
            tool_counts.get(tool, 0) == 0,
            f"{tool} calls={tool_counts.get(tool, 0)}",
        )

    clarification_count = sum(run.clarification_count for run in runs)
    clarification_passed = (
        case.expectations.clarification == "allowed"
        or (case.expectations.clarification == "required" and clarification_count > 0)
        or (case.expectations.clarification == "forbidden" and clarification_count == 0)
    )
    _check(
        checks,
        "clarification_behavior",
        clarification_passed,
        f"policy={case.expectations.clarification}; questions={clarification_count}",
    )

    sources = final_run.sources if final_run else []
    citations = final_run.citations if final_run else []
    _check(
        checks,
        "source_count",
        len(sources) >= case.expectations.min_sources,
        f"sources={len(sources)}; required={case.expectations.min_sources}",
    )
    _check(
        checks,
        "citation_count",
        len(citations) >= case.expectations.min_citations,
        f"citations={len(citations)}; required={case.expectations.min_citations}",
    )
    _check(
        checks,
        "citation_integrity",
        _citations_resolve(sources, citations),
        "every citation resolves to a recorded source",
    )

    artifacts = final_run.artifacts if final_run else []
    required_extensions = set(case.expectations.artifact_extensions)
    deliverable_artifacts = (
        [
            artifact
            for artifact in artifacts
            if Path(artifact.filename).suffix.lower() in required_extensions
        ]
        if required_extensions
        else artifacts
    )
    _check(
        checks,
        "artifact_count",
        len(deliverable_artifacts) >= case.expectations.min_artifacts,
        f"matching artifacts={len(deliverable_artifacts)}; "
        f"total={len(artifacts)}; required={case.expectations.min_artifacts}",
    )
    extensions = {
        Path(artifact.filename).suffix.lower() for artifact in deliverable_artifacts
    }
    for extension in case.expectations.artifact_extensions:
        _check(
            checks,
            f"artifact_extension_{extension.removeprefix('.')}",
            extension in extensions,
            f"required={extension}; observed={sorted(extensions)}",
        )
    for artifact in deliverable_artifacts:
        path = Path(artifact.content_path) if artifact.content_path else None
        if artifact.download_error:
            passed, detail = False, artifact.download_error
        elif path is None:
            passed, detail = False, "artifact content was not downloaded"
        else:
            passed, detail = validate_artifact_file(path)
        _check(
            checks,
            f"artifact_valid_{_safe_id(artifact.filename)}",
            passed,
            detail,
        )

    total_cost = sum(run.actual_cost_usd for run in runs)
    if case.expectations.max_cost_usd is not None:
        _check(
            checks,
            "cost_budget",
            total_cost <= case.expectations.max_cost_usd,
            f"actual=${total_cost:.6f}; max=${case.expectations.max_cost_usd:.6f}",
        )
    total_duration = sum(run.duration_seconds or 0 for run in runs)
    if case.expectations.max_duration_seconds is not None:
        _check(
            checks,
            "latency_budget",
            total_duration <= case.expectations.max_duration_seconds,
            f"duration={total_duration:.1f}s; max={case.expectations.max_duration_seconds:.1f}s",
        )

    required = [check for check in checks if check.required]
    passed = sum(check.passed for check in required)
    return CaseScore(
        case_id=case.id,
        automated_passed=passed == len(required),
        automated_score=passed / len(required) if required else 1,
        checks=checks,
        human=human,
    )


def _check(
    checks: list[CheckResult],
    check_id: str,
    passed: bool,
    detail: str,
) -> None:
    checks.append(CheckResult(id=check_id, passed=passed, detail=detail))


def _aggregate_tool_counts(runs) -> dict[str, int]:
    totals: dict[str, int] = {}
    for run in runs:
        for tool, count in run.tool_counts.items():
            totals[tool] = totals.get(tool, 0) + int(count)
    return totals


def _matches_language(text: str, language: str) -> bool:
    cyrillic = len(_CYRILLIC.findall(text))
    latin = len(_LATIN.findall(text))
    if language == "ru":
        return cyrillic > 0 and cyrillic >= latin * 0.25
    words = {word.lower() for word in re.findall(r"[^\W\d_]+", text)}
    looks_turkish = (
        len(_TURKISH_SPECIFIC.findall(text)) >= 2 or len(words & _TURKISH_WORDS) >= 4
    )
    return latin > 0 and latin >= cyrillic * 2 and not looks_turkish


def _citations_resolve(sources: list[dict], citations: list[dict]) -> bool:
    source_ids = {str(source.get("id")) for source in sources if source.get("id")}
    return all(str(citation.get("source_id")) in source_ids for citation in citations)


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:48]
