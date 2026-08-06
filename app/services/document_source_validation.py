from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree


MAX_SPREADSHEET_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 512
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 100
MAX_SHEETS = 50
MAX_ROWS_PER_SHEET = 100_000
MAX_COLUMNS = 256
MAX_CELL_TEXT_LENGTH = 32_767

_CELL_REFERENCE_PATTERN = re.compile(r"^([A-Z]+)")
_REQUIRED_XLSX_ENTRIES = frozenset({"[Content_Types].xml", "xl/workbook.xml"})
_BLOCKED_XLSX_PREFIXES = (
    "xl/activex/",
    "xl/embeddings/",
    "xl/externallinks/",
)


class DocumentSourceValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DocumentSourceInspection:
    extension: str
    row_count: int
    max_column_count: int
    sheet_count: int
    uncompressed_bytes: int


def _reject(code: str, message: str) -> None:
    raise DocumentSourceValidationError(code, message)


def _spreadsheet_size(path: Path) -> int:
    size_bytes = path.stat().st_size
    if size_bytes > MAX_SPREADSHEET_BYTES:
        _reject(
            "spreadsheet_file_too_large",
            f"spreadsheet exceeds {MAX_SPREADSHEET_BYTES} bytes",
        )
    return size_bytes


def _decode_csv(data: bytes) -> str:
    if b"\x00" in data:
        _reject("csv_contains_null_bytes", "CSV contains null bytes")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return data.decode("cp1251")
        except UnicodeDecodeError:
            _reject("csv_encoding_not_supported", "CSV must be UTF-8 or Windows-1251")


def _inspect_csv(path: Path) -> DocumentSourceInspection:
    size_bytes = _spreadsheet_size(path)
    text = _decode_csv(path.read_bytes())
    row_count = 0
    max_column_count = 0
    has_content = False

    try:
        rows = csv.reader(io.StringIO(text, newline=""))
        for row in rows:
            row_count += 1
            if row_count > MAX_ROWS_PER_SHEET:
                _reject("csv_row_limit_exceeded", "CSV contains too many rows")
            if len(row) > MAX_COLUMNS:
                _reject("csv_column_limit_exceeded", "CSV contains too many columns")
            max_column_count = max(max_column_count, len(row))
            for cell in row:
                if len(cell) > MAX_CELL_TEXT_LENGTH:
                    _reject("csv_cell_too_long", "CSV contains an oversized cell")
                has_content = has_content or bool(cell.strip())
    except csv.Error as exc:
        _reject("csv_malformed", f"CSV cannot be parsed: {exc}")

    if not has_content:
        _reject("csv_empty", "CSV contains no data")

    return DocumentSourceInspection(
        extension=".csv",
        row_count=row_count,
        max_column_count=max_column_count,
        sheet_count=1,
        uncompressed_bytes=size_bytes,
    )


def _safe_archive_name(filename: str) -> str:
    normalized = filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        _reject("xlsx_unsafe_archive_path", "XLSX contains an unsafe archive path")
    return normalized


def _column_number(cell_reference: str) -> int:
    match = _CELL_REFERENCE_PATTERN.match(cell_reference.upper())
    if not match:
        return 0
    column_number = 0
    for character in match.group(1):
        column_number = column_number * 26 + ord(character) - ord("A") + 1
    return column_number


def _reject_unsafe_xml_prefix(data: bytes) -> None:
    prefix = data[:4096].lower()
    if b"<!doctype" in prefix or b"<!entity" in prefix:
        _reject("xlsx_unsafe_xml", "XLSX contains a DTD or entity declaration")


def _reject_external_relationship(data: bytes) -> None:
    _reject_unsafe_xml_prefix(data)
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        _reject("xlsx_malformed_xml", f"XLSX relationship file is malformed: {exc}")
    for relationship in root.iter():
        target_mode = next(
            (
                value
                for name, value in relationship.attrib.items()
                if name.rsplit("}", 1)[-1].casefold() == "targetmode"
            ),
            "",
        )
        if target_mode.casefold() == "external":
            _reject(
                "xlsx_external_relationship_not_supported",
                "XLSX contains an external relationship",
            )


def _inspect_worksheet(archive: zipfile.ZipFile, filename: str) -> tuple[int, int]:
    with archive.open(filename) as worksheet:
        prefix = worksheet.read(4096)
        _reject_unsafe_xml_prefix(prefix)
        worksheet.seek(0)

        row_count = 0
        max_column_count = 0
        cells_in_row = 0
        try:
            for _, element in ElementTree.iterparse(worksheet, events=("end",)):
                tag = element.tag.rsplit("}", 1)[-1]
                if tag == "c":
                    cells_in_row += 1
                    column_number = _column_number(element.attrib.get("r", ""))
                    max_column_count = max(
                        max_column_count,
                        column_number or cells_in_row,
                    )
                    if max_column_count > MAX_COLUMNS:
                        _reject(
                            "xlsx_column_limit_exceeded",
                            "XLSX contains too many columns",
                        )
                elif tag == "row":
                    row_count += 1
                    cells_in_row = 0
                    if row_count > MAX_ROWS_PER_SHEET:
                        _reject("xlsx_row_limit_exceeded", "XLSX contains too many rows")
                elif tag in {"t", "v"} and element.text:
                    if len(element.text) > MAX_CELL_TEXT_LENGTH:
                        _reject("xlsx_cell_too_long", "XLSX contains an oversized cell")
                element.clear()
        except ElementTree.ParseError as exc:
            _reject("xlsx_malformed_xml", f"XLSX worksheet is malformed: {exc}")
    return row_count, max_column_count


def _inspect_xlsx(path: Path) -> DocumentSourceInspection:
    _spreadsheet_size(path)
    if not zipfile.is_zipfile(path):
        _reject("xlsx_not_zip_archive", "XLSX is not a valid ZIP archive")

    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                _reject("xlsx_entry_limit_exceeded", "XLSX contains too many files")

            normalized_names: set[str] = set()
            total_uncompressed = 0
            total_compressed = 0
            worksheet_names: list[str] = []
            relationship_names: list[str] = []

            for entry in entries:
                name = _safe_archive_name(entry.filename)
                lower_name = name.lower()
                if name in normalized_names:
                    _reject("xlsx_duplicate_archive_path", "XLSX contains duplicate paths")
                normalized_names.add(name)
                total_uncompressed += entry.file_size
                total_compressed += entry.compress_size

                if entry.flag_bits & 0x1:
                    _reject("xlsx_encrypted", "Encrypted XLSX files are not supported")
                if lower_name.endswith("vbaproject.bin") or lower_name.startswith(
                    _BLOCKED_XLSX_PREFIXES
                ):
                    _reject(
                        "xlsx_active_content_not_supported",
                        "XLSX contains active or externally linked content",
                    )
                if lower_name.startswith("xl/worksheets/") and lower_name.endswith(
                    ".xml"
                ):
                    worksheet_names.append(name)
                if lower_name.endswith(".rels"):
                    relationship_names.append(name)

            if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                _reject(
                    "xlsx_uncompressed_size_limit_exceeded",
                    "XLSX expands beyond the safe size limit",
                )
            if (
                total_uncompressed > 10 * 1024 * 1024
                and total_uncompressed
                > max(total_compressed, 1) * MAX_ARCHIVE_COMPRESSION_RATIO
            ):
                _reject(
                    "xlsx_compression_ratio_limit_exceeded",
                    "XLSX compression ratio is unsafe",
                )
            if not _REQUIRED_XLSX_ENTRIES.issubset(normalized_names):
                _reject("xlsx_required_parts_missing", "XLSX is missing required parts")
            if not worksheet_names:
                _reject("xlsx_has_no_worksheets", "XLSX contains no worksheets")
            if len(worksheet_names) > MAX_SHEETS:
                _reject("xlsx_sheet_limit_exceeded", "XLSX contains too many sheets")

            content_types = archive.read("[Content_Types].xml")
            _reject_unsafe_xml_prefix(content_types)
            if b"macroenabled" in content_types.lower():
                _reject("xlsx_macros_not_supported", "Macro-enabled workbooks are not supported")

            for entry_name in normalized_names:
                if entry_name.lower().endswith(".xml"):
                    with archive.open(entry_name) as xml_file:
                        _reject_unsafe_xml_prefix(xml_file.read(4096))
            for relationship_name in relationship_names:
                _reject_external_relationship(archive.read(relationship_name))

            total_rows = 0
            max_columns = 0
            for worksheet_name in worksheet_names:
                row_count, column_count = _inspect_worksheet(archive, worksheet_name)
                total_rows += row_count
                max_columns = max(max_columns, column_count)

            return DocumentSourceInspection(
                extension=".xlsx",
                row_count=total_rows,
                max_column_count=max_columns,
                sheet_count=len(worksheet_names),
                uncompressed_bytes=total_uncompressed,
            )
    except zipfile.BadZipFile as exc:
        _reject("xlsx_malformed_archive", f"XLSX archive is malformed: {exc}")


def inspect_spreadsheet_source(
    path: str,
    filename: str,
) -> DocumentSourceInspection | None:
    extension = Path(filename).suffix.lower()
    source_path = Path(path)
    if extension == ".csv":
        return _inspect_csv(source_path)
    if extension == ".xlsx":
        return _inspect_xlsx(source_path)
    return None
