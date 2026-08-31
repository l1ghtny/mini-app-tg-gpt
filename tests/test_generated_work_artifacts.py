from __future__ import annotations

from types import SimpleNamespace

from openpyxl import Workbook
import pytest

from app.services.work_runs.generated_artifacts import (
    GeneratedArtifactReference,
    artifact_contract_error,
    build_generated_spreadsheet_preview,
    download_generated_artifact,
    generated_artifact_references,
    plan_expects_artifacts,
)
from app.services.work_threads.planner import PlannedWork


def _response(*annotations: object) -> SimpleNamespace:
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                content=[SimpleNamespace(annotations=list(annotations))]
            )
        ]
    )


class _ArtifactStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def iter_bytes(self):
        yield b"%PDF-1.7\n"
        yield b"artifact"


def test_generated_artifacts_are_normalized_and_deduplicated() -> None:
    annotation = SimpleNamespace(
        type="container_file_citation",
        container_id="cntr-1",
        file_id="cfile-1",
        filename="../Launch brief.pdf",
    )

    references = generated_artifact_references(_response(annotation, annotation))

    assert references == [
        GeneratedArtifactReference(
            container_id="cntr-1",
            file_id="cfile-1",
            filename="Launch brief.pdf",
            mime_type="application/pdf",
            kind="document",
            preview_kind="pdf",
        )
    ]


def test_generated_artifacts_reject_unsafe_or_unsupported_outputs() -> None:
    response = _response(
        {
            "type": "container_file_citation",
            "container_id": "cntr-1",
            "file_id": "cfile-1",
            "filename": "payload.py",
        },
        {
            "type": "container_file_citation",
            "container_id": "",
            "file_id": "cfile-2",
            "filename": "report.pdf",
        },
    )

    assert generated_artifact_references(response) == []


def test_artifact_execution_is_opt_in_from_the_approved_plan() -> None:
    assert plan_expects_artifacts([{"kind": "artifact"}])
    assert not plan_expects_artifacts([{"kind": "answer"}])


def test_generated_artifact_must_match_the_approved_file_format() -> None:
    payload = {
        "current_request": "Create the actual PDF file.",
        "approved_plan": {
            "expected_outputs": [
                {
                    "kind": "artifact",
                    "label": "PDF brief",
                    "description": "A polished one-page PDF.",
                    "acceptance_criteria": ["The final response cites the PDF."],
                }
            ]
        },
    }
    png_response = _response(
        {
            "type": "container_file_citation",
            "container_id": "cntr-1",
            "file_id": "cfile-1",
            "filename": "preview.png",
        }
    )
    pdf_response = _response(
        {
            "type": "container_file_citation",
            "container_id": "cntr-1",
            "file_id": "cfile-2",
            "filename": "brief.pdf",
        }
    )

    assert "requires a PDF" in artifact_contract_error(png_response, payload)
    assert artifact_contract_error(pdf_response, payload) is None


def test_source_file_format_does_not_become_a_required_output_format() -> None:
    payload = {
        "current_request": (
            "Analyze the attached sales CSV and create an Excel workbook with totals."
        ),
        "approved_plan": {
            "expected_outputs": [
                {
                    "kind": "artifact",
                    "label": "Sales analysis",
                    "description": "A calculated workbook based on the source CSV.",
                    "acceptance_criteria": [
                        "The final response cites the generated XLSX file."
                    ],
                }
            ]
        },
    }
    xlsx_response = _response(
        {
            "type": "container_file_citation",
            "container_id": "cntr-1",
            "file_id": "cfile-xlsx",
            "filename": "sales-analysis.xlsx",
        }
    )

    assert artifact_contract_error(xlsx_response, payload) is None


def test_planner_contract_can_request_a_general_artifact() -> None:
    plan = PlannedWork.model_validate(
        {
            "title": "Launch brief",
            "summary": "Create a concise decision document.",
            "execution_kind": "agentic_task",
            "steps": [
                {"id": "research", "title": "Research", "description": "Find evidence."},
                {"id": "write", "title": "Write", "description": "Create the brief."},
            ],
            "expected_outputs": [
                {
                    "kind": "artifact",
                    "label": "PDF brief",
                    "description": "A readable PDF decision brief.",
                    "acceptance_criteria": ["The final response cites the generated PDF."],
                }
            ],
            "assumptions": [],
        }
    )

    assert plan.expected_outputs[0].kind == "artifact"


def test_generated_spreadsheet_gets_a_readable_bounded_preview(tmp_path) -> None:
    path = tmp_path / "decision.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Option", "Score", "Approved"])
    sheet.append(["A", 9.5, True])
    sheet.append(["B", 7, False])
    workbook.save(path)

    preview = build_generated_spreadsheet_preview(
        path,
        goal="Compare the options",
        source_count=2,
    )

    assert preview["row_count"] == 2
    assert preview["column_count"] == 3
    assert preview["rows"] == [["A", 9.5, True], ["B", 7, False]]
    assert [column["data_type"] for column in preview["columns"]] == [
        "text",
        "number",
        "boolean",
    ]


@pytest.mark.asyncio
async def test_generated_artifact_is_streamed_and_hashed(tmp_path) -> None:
    retrieve_calls: list[tuple[str, str]] = []

    def retrieve(file_id: str, *, container_id: str):
        retrieve_calls.append((file_id, container_id))
        return _ArtifactStream()

    client = SimpleNamespace(
        containers=SimpleNamespace(
            files=SimpleNamespace(
                content=SimpleNamespace(
                    with_streaming_response=SimpleNamespace(retrieve=retrieve)
                )
            )
        )
    )
    reference = GeneratedArtifactReference(
        container_id="cntr-1",
        file_id="cfile-1",
        filename="brief.pdf",
        mime_type="application/pdf",
        kind="document",
        preview_kind="pdf",
    )

    downloaded = await download_generated_artifact(
        client,  # type: ignore[arg-type]
        reference,
        tmp_path / "brief.pdf",
    )

    assert retrieve_calls == [("cfile-1", "cntr-1")]
    assert downloaded.size_bytes == len(b"%PDF-1.7\nartifact")
    assert len(downloaded.sha256) == 64
