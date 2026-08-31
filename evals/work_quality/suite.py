from __future__ import annotations

import json
from pathlib import Path

from evals.work_quality.contracts import EvalCase, EvalSuite


PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_SUITE_PATH = PACKAGE_ROOT / "suite.json"
WORK_EVAL_PROFILES: dict[str, tuple[str, ...]] = {
    "mvp-core": (
        "web_openai_agent_evals",
        "docs_product_decision_memo",
        "sheet_sales_analysis",
        "artifact_action_plan_docx",
        "clarify_material_audience",
        "recovery_cancel_then_redirect",
    ),
}


def load_suite(path: Path = DEFAULT_SUITE_PATH) -> EvalSuite:
    return EvalSuite.model_validate_json(path.read_text(encoding="utf-8"))


def validate_suite_files(suite: EvalSuite, suite_path: Path) -> list[str]:
    errors: list[str] = []
    suite_root = suite_path.resolve().parent
    for case in suite.cases:
        for attachment in case.attachments:
            path = suite_root / attachment.path
            if not path.is_file():
                errors.append(
                    f"{case.id}: attachment does not exist: {attachment.path}"
                )
    return errors


def select_cases(
    suite: EvalSuite,
    *,
    case_ids: list[str] | None = None,
    profile: str | None = None,
) -> list[EvalCase]:
    if case_ids and profile:
        raise ValueError("use either case ids or a profile, not both")
    if profile is not None and profile not in WORK_EVAL_PROFILES:
        raise ValueError(f"unknown evaluation profile: {profile}")

    selected_ids = set(case_ids or WORK_EVAL_PROFILES.get(profile or "", ()))
    unknown = selected_ids - {case.id for case in suite.cases}
    if unknown:
        raise ValueError(f"unknown case ids: {sorted(unknown)}")
    return [case for case in suite.cases if not selected_ids or case.id in selected_ids]


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
