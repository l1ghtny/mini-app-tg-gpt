from __future__ import annotations

import json
from pathlib import Path

from evals.work_quality.contracts import EvalSuite


PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_SUITE_PATH = PACKAGE_ROOT / "suite.json"


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


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
