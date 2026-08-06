import zipfile
from pathlib import Path

import pytest

from app.services import document_source_validation as validation
from app.services.document_source_validation import (
    DocumentSourceValidationError,
    inspect_spreadsheet_source,
)


_CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
</Types>
"""
_WORKBOOK = b"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheets><sheet name="Sheet1" sheetId="1"/></sheets>
</workbook>
"""
_WORKSHEET = b"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="inlineStr"><is><t>Price</t></is></c></row>
    <row r="2"><c r="A2"><v>100</v></c></row>
  </sheetData>
</worksheet>
"""


def _write_xlsx(
    path: Path,
    *,
    worksheet: bytes = _WORKSHEET,
    extra_entries: dict[str, bytes] | None = None,
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("xl/workbook.xml", _WORKBOOK)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        for name, data in (extra_entries or {}).items():
            archive.writestr(name, data)


def test_inspects_bounded_utf8_and_cp1251_csv(tmp_path: Path) -> None:
    utf8_path = tmp_path / "offers.csv"
    utf8_path.write_text("name,price\nTea,100\n", encoding="utf-8")
    cp1251_path = tmp_path / "offers-cp1251.csv"
    cp1251_path.write_bytes("товар,цена\nЧай,100\n".encode("cp1251"))

    utf8_result = inspect_spreadsheet_source(str(utf8_path), utf8_path.name)
    cp1251_result = inspect_spreadsheet_source(str(cp1251_path), cp1251_path.name)

    assert utf8_result is not None and utf8_result.row_count == 2
    assert utf8_result.max_column_count == 2
    assert cp1251_result is not None and cp1251_result.row_count == 2


@pytest.mark.parametrize(
    ("data", "error_code"),
    [
        (b"name,price\nTea,\x00\n", "csv_contains_null_bytes"),
        (b"  \n", "csv_empty"),
    ],
)
def test_rejects_unsafe_or_empty_csv(
    tmp_path: Path,
    data: bytes,
    error_code: str,
) -> None:
    path = tmp_path / "offers.csv"
    path.write_bytes(data)

    with pytest.raises(DocumentSourceValidationError) as exc_info:
        inspect_spreadsheet_source(str(path), path.name)

    assert exc_info.value.code == error_code


def test_inspects_minimal_xlsx(tmp_path: Path) -> None:
    path = tmp_path / "offers.xlsx"
    _write_xlsx(path)

    result = inspect_spreadsheet_source(str(path), path.name)

    assert result is not None
    assert result.sheet_count == 1
    assert result.row_count == 2
    assert result.max_column_count == 1


@pytest.mark.parametrize(
    ("extra_entries", "error_code"),
    [
        ({"xl/vbaProject.bin": b"macro"}, "xlsx_active_content_not_supported"),
        ({"xl/embeddings/oleObject1.bin": b"ole"}, "xlsx_active_content_not_supported"),
        ({"../outside.xml": b"unsafe"}, "xlsx_unsafe_archive_path"),
        (
            {
                "xl/_rels/workbook.xml.rels": (
                    b"<Relationships><Relationship TargetMode='External'/></Relationships>"
                )
            },
            "xlsx_external_relationship_not_supported",
        ),
        (
            {"docProps/custom.xml": b"<!DOCTYPE x [<!ENTITY y SYSTEM 'file:///etc/passwd'>]><x/>"},
            "xlsx_unsafe_xml",
        ),
    ],
)
def test_rejects_active_external_or_unsafe_xlsx_content(
    tmp_path: Path,
    extra_entries: dict[str, bytes],
    error_code: str,
) -> None:
    path = tmp_path / "offers.xlsx"
    _write_xlsx(path, extra_entries=extra_entries)

    with pytest.raises(DocumentSourceValidationError) as exc_info:
        inspect_spreadsheet_source(str(path), path.name)

    assert exc_info.value.code == error_code


def test_rejects_xlsx_column_over_the_hard_limit(tmp_path: Path) -> None:
    path = tmp_path / "offers.xlsx"
    worksheet = b"""<worksheet><sheetData><row><c r="IW1"><v>1</v></c></row></sheetData></worksheet>"""
    _write_xlsx(path, worksheet=worksheet)

    with pytest.raises(DocumentSourceValidationError) as exc_info:
        inspect_spreadsheet_source(str(path), path.name)

    assert exc_info.value.code == "xlsx_column_limit_exceeded"


def test_rejects_xlsx_that_expands_beyond_limit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(validation, "MAX_ARCHIVE_UNCOMPRESSED_BYTES", 32)
    path = tmp_path / "offers.xlsx"
    _write_xlsx(path)

    with pytest.raises(DocumentSourceValidationError) as exc_info:
        inspect_spreadsheet_source(str(path), path.name)

    assert exc_info.value.code == "xlsx_uncompressed_size_limit_exceeded"
