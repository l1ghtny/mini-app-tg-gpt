from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from evals.work_quality.contracts import EvalObservation, HumanAssessment
from evals.work_quality.live import WorkEvalClient
from evals.work_quality.reporting import build_report, markdown_report
from evals.work_quality.suite import (
    DEFAULT_SUITE_PATH,
    WORK_EVAL_PROFILES,
    dump_json,
    load_suite,
    select_cases,
    validate_suite_files,
)


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        exit_code = args.handler(args)
    except (OSError, ValidationError, ValueError) as exc:
        parser.error(str(exc))
    raise SystemExit(exit_code or 0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.work_quality",
        description="Run and score the versioned Lightny Work product eval suite.",
    )
    parser.add_argument(
        "--suite",
        type=Path,
        default=DEFAULT_SUITE_PATH,
        help="Path to suite.json.",
    )
    subparsers = parser.add_subparsers(required=True)

    validate = subparsers.add_parser(
        "validate", help="Validate cases and fixtures offline."
    )
    validate.set_defaults(handler=_validate)

    run = subparsers.add_parser(
        "run", help="Run cases through a live Lightny Work API."
    )
    run.add_argument("--base-url", default="https://beta.app.lightny.ru")
    run.add_argument("--environment", default="beta")
    run.add_argument("--token-env", default="LIGHTNY_EVAL_BEARER_TOKEN")
    run.add_argument(
        "--token-file",
        type=Path,
        help="Read the bearer token from a local file without printing it.",
    )
    run.add_argument(
        "--session-cookie-file",
        type=Path,
        help="Read the web session cookie value from a local file.",
    )
    run.add_argument(
        "--session-cookie-name",
        default="lightny_beta_session",
    )
    selection = run.add_mutually_exclusive_group()
    selection.add_argument("--case", action="append", dest="case_ids")
    selection.add_argument(
        "--profile",
        choices=sorted(WORK_EVAL_PROFILES),
        help="Run a named product acceptance profile.",
    )
    run.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".eval-results/work/observations"),
    )
    run.add_argument("--timeout-seconds", type=float, default=900)
    run.set_defaults(handler=_run)

    score = subparsers.add_parser("score", help="Score saved observations.")
    score.add_argument("--observations", type=Path, required=True)
    score.add_argument("--human", type=Path)
    score.add_argument("--environment", default="beta")
    score.add_argument(
        "--output-dir", type=Path, default=Path(".eval-results/work/report")
    )
    score.set_defaults(handler=_score)

    template = subparsers.add_parser(
        "human-template",
        help="Create the human-review worksheet for the suite.",
    )
    template.add_argument("--output", type=Path, required=True)
    template.set_defaults(handler=_human_template)
    return parser


def _validate(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    errors = validate_suite_files(suite, args.suite)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    categories: dict[str, int] = {}
    for case in suite.cases:
        categories[case.category] = categories.get(case.category, 0) + 1
    print(f"Valid suite v{suite.version}: {len(suite.cases)} cases")
    for category, count in sorted(categories.items()):
        print(f"  {category}: {count}")
    return 0


def _run(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    errors = validate_suite_files(suite, args.suite)
    if errors:
        raise ValueError("; ".join(errors))
    if args.token_file is not None and args.session_cookie_file is not None:
        raise ValueError("use either --token-file or --session-cookie-file, not both")
    session_cookie = (
        _read_secret_file(args.session_cookie_file)
        if args.session_cookie_file is not None
        else None
    )
    token = (
        None if session_cookie else _read_bearer_token(args.token_env, args.token_file)
    )
    if not token and not session_cookie:
        raise ValueError(
            f"{args.token_env} is not set and no credential file was provided; "
            "supply a short-lived bearer token or web session cookie"
        )
    cases = select_cases(suite, case_ids=args.case_ids, profile=args.profile)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = WorkEvalClient(
        base_url=args.base_url,
        bearer_token=token,
        session_cookie=session_cookie,
        session_cookie_name=args.session_cookie_name,
        timeout_seconds=args.timeout_seconds,
    )
    failures = 0
    try:
        for index, case in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case.id}: {case.title}")
            observation = client.run_case(
                suite,
                case,
                suite_root=args.suite.resolve().parent,
                output_dir=args.output_dir,
                environment=args.environment,
            )
            dump_json(args.output_dir / f"{case.id}.json", observation)
            if observation.api_errors:
                failures += 1
                print(f"  API failure: {observation.api_errors[-1]}")
            elif observation.runs:
                print(
                    f"  {observation.runs[-1].status}; "
                    f"cost=${sum(run.actual_cost_usd for run in observation.runs):.6f}"
                )
    finally:
        client.close()
    print(f"Saved {len(cases)} observations to {args.output_dir}")
    return 1 if failures else 0


def _score(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    observations = _load_observations(args.observations)
    human = _load_human(args.human) if args.human else []
    report = build_report(
        suite,
        observations,
        environment=args.environment,
        human_assessments=human,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    dump_json(args.output_dir / "report.json", report)
    (args.output_dir / "report.md").write_text(
        markdown_report(report, suite),
        encoding="utf-8",
    )
    print(f"Scored {len(report.case_scores)} cases")
    print(f"Report: {(args.output_dir / 'report.md').resolve()}")
    return 0 if all(score.automated_passed for score in report.case_scores) else 1


def _human_template(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    template = [
        {
            "case_id": case.id,
            "reviewed": False,
            "usefulness": 3,
            "correctness": 3,
            "completeness": 3,
            "readability": 3,
            "needed_correction": True,
            "notes": "Review prompts: " + " | ".join(case.human_rubric),
        }
        for case in suite.cases
    ]
    dump_json(args.output, template)
    print(f"Human review template: {args.output.resolve()}")
    return 0


def _load_observations(path: Path) -> list[EvalObservation]:
    adapter = TypeAdapter(list[EvalObservation])
    if path.is_file():
        payload = path.read_text(encoding="utf-8")
        if payload.lstrip().startswith("["):
            return adapter.validate_json(payload)
        return [EvalObservation.model_validate_json(payload)]
    return [
        EvalObservation.model_validate_json(item.read_text(encoding="utf-8"))
        for item in sorted(path.glob("*.json"))
        if item.name != "report.json"
    ]


def _load_human(path: Path) -> list[HumanAssessment]:
    return TypeAdapter(list[HumanAssessment]).validate_json(
        path.read_text(encoding="utf-8")
    )


def _read_bearer_token(token_env: str, token_file: Path | None) -> str | None:
    token = (
        _read_secret_file(token_file)
        if token_file is not None
        else os.environ.get(token_env, "").strip()
    )
    if token and any(character.isspace() for character in token):
        raise ValueError("bearer token must not contain whitespace")
    return token or None


def _read_secret_file(path: Path) -> str:
    secret = path.read_text(encoding="utf-8").strip()
    if not secret:
        raise ValueError(f"credential file is empty: {path}")
    if any(character.isspace() for character in secret):
        raise ValueError("credential file must contain only the credential value")
    return secret
