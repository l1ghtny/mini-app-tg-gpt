from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.services.document_source_validation import inspect_spreadsheet_source


_WHITESPACE = re.compile(r"\s+")
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_MAX_OUTPUT_ROWS = 250_000


class ComparisonInputError(ValueError):
    pass


@dataclass(frozen=True)
class SourceTable:
    document_id: uuid.UUID
    filename: str
    sheet_name: str
    headers: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    first_data_row: int


@dataclass(frozen=True)
class ComparisonSource:
    document_id: uuid.UUID
    filename: str
    sheet_name: str
    row_start: int
    row_end: int
    output_row_start: int
    output_row_end: int


@dataclass(frozen=True)
class RenderedComparison:
    path: Path
    row_count: int
    column_count: int
    sources: tuple[ComparisonSource, ...]


@dataclass(frozen=True)
class ComparisonColumnSchema:
    canonical_headers: tuple[str, ...]
    source_headers: Mapping[tuple[uuid.UUID, str, str], str]

    def canonical_header(self, table: SourceTable, source_header: str) -> str:
        key = (table.document_id, table.sheet_name, source_header)
        try:
            return self.source_headers[key]
        except KeyError as exc:
            raise ComparisonInputError(
                "comparison schema does not map every source column"
            ) from exc


def _decode_csv(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251")


def _clean_header(value: Any, ordinal: int, seen: set[str]) -> str:
    text = _WHITESPACE.sub(" ", str(value or "").strip()) or f"Column {ordinal}"
    candidate = text
    suffix = 2
    while candidate.casefold() in seen:
        candidate = f"{text} ({suffix})"
        suffix += 1
    seen.add(candidate.casefold())
    return candidate


def _trim_row(values: Iterable[Any]) -> tuple[Any, ...]:
    row = list(values)
    while row and row[-1] is None:
        row.pop()
    return tuple(row)


def _table_from_rows(
    *,
    document_id: uuid.UUID,
    filename: str,
    sheet_name: str,
    rows: Iterable[Iterable[Any]],
) -> SourceTable | None:
    materialized = [_trim_row(row) for row in rows]
    materialized = [
        row for row in materialized if any(value not in (None, "") for value in row)
    ]
    if not materialized:
        return None
    seen: set[str] = set()
    headers = tuple(
        _clean_header(value, index, seen)
        for index, value in enumerate(materialized[0], start=1)
    )
    if not headers:
        return None
    data_rows = tuple(materialized[1:])
    return SourceTable(
        document_id=document_id,
        filename=filename,
        sheet_name=sheet_name,
        headers=headers,
        rows=data_rows,
        first_data_row=2,
    )


def load_source_tables(
    *,
    document_id: uuid.UUID,
    filename: str,
    path: Path,
) -> tuple[SourceTable, ...]:
    inspect_spreadsheet_source(str(path), filename)
    extension = path.suffix.lower()
    tables: list[SourceTable] = []
    if extension == ".csv":
        text = _decode_csv(path.read_bytes())
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        table = _table_from_rows(
            document_id=document_id,
            filename=filename,
            sheet_name="CSV",
            rows=csv.reader(io.StringIO(text, newline=""), dialect=dialect),
        )
        if table:
            tables.append(table)
    elif extension == ".xlsx":
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            for worksheet in workbook.worksheets:
                table = _table_from_rows(
                    document_id=document_id,
                    filename=filename,
                    sheet_name=worksheet.title,
                    rows=worksheet.iter_rows(values_only=True),
                )
                if table:
                    tables.append(table)
        finally:
            workbook.close()
    else:
        raise ComparisonInputError(f"unsupported comparison source: {extension}")

    if not tables:
        raise ComparisonInputError(f"{filename} contains no tabular data")
    return tuple(tables)


def _safe_cell(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def _localized_labels(language: str) -> dict[str, str]:
    if language == "ru":
        return {
            "summary": "Сводка",
            "comparison": "Сравнение",
            "sources": "Источники",
            "generated": "Создано",
            "instructions": "Что сравнить",
            "currency": "Валюта",
            "documents": "Файлов",
            "rows": "Строк данных",
            "document": "Файл",
            "sheet": "Лист",
            "source_row": "Строка в источнике",
            "output_row": "Строка в сравнении",
            "data_rows": "Строк данных",
            "columns": "Столбцов",
        }
    return {
        "summary": "Summary",
        "comparison": "Comparison",
        "sources": "Sources",
        "generated": "Generated",
        "instructions": "Comparison instructions",
        "currency": "Currency",
        "documents": "Documents",
        "rows": "Data rows",
        "document": "Document",
        "sheet": "Sheet",
        "source_row": "Source row",
        "output_row": "Comparison row",
        "data_rows": "Data rows",
        "columns": "Columns",
    }


def render_comparison_workbook(
    *,
    tables: tuple[SourceTable, ...],
    target_path: Path,
    language: str,
    currency: str | None,
    instructions: str | None,
    column_schema: ComparisonColumnSchema | None = None,
) -> RenderedComparison:
    if not tables:
        raise ComparisonInputError("at least one source table is required")
    total_rows = sum(len(table.rows) for table in tables)
    if total_rows > _MAX_OUTPUT_ROWS:
        raise ComparisonInputError("combined workbook contains too many data rows")

    labels = _localized_labels(language)
    ordered_headers: list[str] = []
    if column_schema is None:
        canonical_headers: dict[str, str] = {}
        for table in tables:
            for header in table.headers:
                key = header.casefold()
                if key not in canonical_headers:
                    canonical_headers[key] = header
                    ordered_headers.append(header)
    else:
        ordered_headers.extend(column_schema.canonical_headers)
        seen_headers: set[str] = set()
        for header in ordered_headers:
            normalized = header.casefold()
            if not header.strip() or normalized in seen_headers:
                raise ComparisonInputError(
                    "comparison schema contains invalid canonical columns"
                )
            seen_headers.add(normalized)
        for table in tables:
            for source_header in table.headers:
                canonical_header = column_schema.canonical_header(table, source_header)
                if canonical_header.casefold() not in seen_headers:
                    raise ComparisonInputError(
                        "comparison schema references an unknown canonical column"
                    )

    workbook = Workbook()
    summary = workbook.active
    summary.title = labels["summary"]
    comparison = workbook.create_sheet(labels["comparison"])
    sources_sheet = workbook.create_sheet(labels["sources"])

    accent = "5B5BD6"
    header_fill = PatternFill("solid", fgColor=accent)
    header_font = Font(color="FFFFFF", bold=True)
    subtle_fill = PatternFill("solid", fgColor="F2F3F8")

    summary_rows = [
        (
            labels["generated"],
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        ),
        (labels["instructions"], instructions or ""),
        (labels["currency"], currency or ""),
        (labels["documents"], len({table.document_id for table in tables})),
        (labels["rows"], total_rows),
    ]
    for row in summary_rows:
        summary.append(row)
    for cell in summary[1]:
        cell.fill = subtle_fill
    summary.column_dimensions["A"].width = 26
    summary.column_dimensions["B"].width = 80
    summary.freeze_panes = "A2"

    fixed_headers = [labels["document"], labels["sheet"], labels["source_row"]]
    comparison.append(
        [*fixed_headers, *(_safe_cell(value) for value in ordered_headers)]
    )
    for cell in comparison[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    source_records: list[ComparisonSource] = []
    output_row = 2
    for table in tables:
        if column_schema is None:
            header_positions = {
                header.casefold(): index for index, header in enumerate(table.headers)
            }
        else:
            header_positions: dict[str, int] = {}
            for index, header in enumerate(table.headers):
                canonical_key = column_schema.canonical_header(table, header).casefold()
                if canonical_key in header_positions:
                    raise ComparisonInputError(
                        "comparison schema merges columns within one source table"
                    )
                header_positions[canonical_key] = index
        first_output_row = output_row
        for source_offset, row in enumerate(table.rows):
            normalized_values = []
            for header in ordered_headers:
                position = header_positions.get(header.casefold())
                value = (
                    row[position]
                    if position is not None and position < len(row)
                    else None
                )
                normalized_values.append(_safe_cell(value))
            comparison.append(
                [
                    _safe_cell(table.filename),
                    _safe_cell(table.sheet_name),
                    table.first_data_row + source_offset,
                    *normalized_values,
                ]
            )
            output_row += 1
        if table.rows:
            source_records.append(
                ComparisonSource(
                    document_id=table.document_id,
                    filename=table.filename,
                    sheet_name=table.sheet_name,
                    row_start=table.first_data_row,
                    row_end=table.first_data_row + len(table.rows) - 1,
                    output_row_start=first_output_row,
                    output_row_end=output_row - 1,
                )
            )

    comparison.freeze_panes = "D2"
    comparison.auto_filter.ref = comparison.dimensions
    comparison.column_dimensions["A"].width = 28
    comparison.column_dimensions["B"].width = 22
    comparison.column_dimensions["C"].width = 18
    for index in range(4, comparison.max_column + 1):
        comparison.column_dimensions[get_column_letter(index)].width = 20
    for row in comparison.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    source_headers = [
        labels["document"],
        labels["sheet"],
        labels["data_rows"],
        labels["columns"],
        labels["source_row"],
        labels["output_row"],
    ]
    sources_sheet.append(source_headers)
    for cell in sources_sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
    for table, source in zip(
        (table for table in tables if table.rows), source_records, strict=True
    ):
        sources_sheet.append(
            [
                _safe_cell(table.filename),
                _safe_cell(table.sheet_name),
                len(table.rows),
                len(table.headers),
                f"{source.row_start}-{source.row_end}",
                f"{source.output_row_start}-{source.output_row_end}",
            ]
        )
    sources_sheet.freeze_panes = "A2"
    sources_sheet.auto_filter.ref = sources_sheet.dimensions
    for index, width in enumerate((36, 22, 16, 14, 22, 24), start=1):
        sources_sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sources_sheet.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    workbook.save(target_path)
    workbook.close()
    return RenderedComparison(
        path=target_path,
        row_count=total_rows,
        column_count=len(ordered_headers),
        sources=tuple(source_records),
    )


def validate_rendered_workbook(path: Path) -> None:
    inspect_spreadsheet_source(str(path), path.name)
    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        if len(workbook.sheetnames) != 3:
            raise ComparisonInputError("comparison workbook must contain three sheets")
        if workbook[workbook.sheetnames[1]].max_row < 1:
            raise ComparisonInputError("comparison workbook has no header row")
    finally:
        workbook.close()
