from io import BytesIO
from zipfile import ZipFile
import pytest
from pydantic import ValidationError
from app.schemas.chat_documents import DocumentSpec
from app.services.chat_document_renderer import render_document


def sample_spec():
    return DocumentSpec(title="План запуска Lightny", blocks=[
        {"kind":"paragraph", "text":"Документ описывает подготовку платежей и документов к запуску. Последняя строка сохранена полностью.", "items":[], "rows":[]},
        {"kind":"heading", "text":"Проверки перед запуском", "items":[], "rows":[]},
        {"kind":"bullet_list", "text":"", "items":["Проверить оплату и возврат", "Открыть DOCX и PDF на телефоне"], "rows":[]},
        {"kind":"table", "text":"", "items":[], "rows":[["Задача", "Результат"],["Документы", "Скачиваемый файл с русским текстом"],["Стоимость", "490 ₽"]]},
        {"kind":"paragraph", "text":"Источник https://example.invalid/launch?item=1&lang=ru", "items":[], "rows":[]},
    ])


def test_docx_preserves_text_tables_and_safe_xml():
    data = render_document(sample_spec(), "docx")
    with ZipFile(BytesIO(data)) as archive:
        xml = archive.read("word/document.xml").decode()
        assert "Последняя строка сохранена полностью." in xml
        assert "490 ₽" in xml and "<w:tbl>" in xml
        assert "item=1&amp;lang=ru" in xml
        assert "vbaProject" not in " ".join(archive.namelist())


def test_pdf_embeds_unicode_fonts_and_has_real_pdf_bytes():
    data = render_document(sample_spec(), "pdf")
    assert data.startswith(b"%PDF-") and b"/FontFile2" in data
    assert b"/JS" not in data and b"/JavaScript" not in data


def test_pdf_rejects_unsupported_glyphs_instead_of_silently_losing_content():
    spec = DocumentSpec(title="Chinese text", blocks=[{"kind":"paragraph", "text":"中文", "items":[], "rows":[]}])
    with pytest.raises(ValueError, match="use DOCX"):
        render_document(spec, "pdf")
    with ZipFile(BytesIO(render_document(spec, "docx"))) as archive:
        assert "中文" in archive.read("word/document.xml").decode()


@pytest.mark.parametrize("change", [
    {"title":"bad\x00title"},
    {"blocks":[{"kind":"paragraph", "text":"bad\x00text", "items":[], "rows":[]}]},
    {"blocks":[{"kind":"table", "text":"", "items":[], "rows":[["one"],["two","three"]]}]},
    {"blocks":[{"kind":"paragraph", "text":"Visible", "items":["silently lost"], "rows":[]}]},
    {"blocks":[{"kind":"page_break", "text":"", "items":[], "rows":[]}]},
])
def test_rejects_invalid_or_silently_discarded_content(change):
    with pytest.raises(ValidationError):
        DocumentSpec.model_validate({**sample_spec().model_dump(), **change})


def test_large_table_splits_across_pages_without_dropping_last_cell():
    spec = DocumentSpec(title="Long table", blocks=[{"kind":"table", "text":"", "items":[], "rows":[["Item", "Detail"], *[[str(i), "Detailed description " * 20] for i in range(70)]]}])
    assert render_document(spec, "pdf").startswith(b"%PDF-")
    with ZipFile(BytesIO(render_document(spec, "docx"))) as archive:
        assert ">69<" in archive.read("word/document.xml").decode()


def test_oversized_pdf_table_cell_reports_limit_without_a_corrupt_file():
    spec = DocumentSpec(title="Large cell", blocks=[{"kind":"table", "text":"", "items":[], "rows":[["Header"], ["Длинный текст. "*500]]}])
    with pytest.raises(ValueError, match="use DOCX"):
        render_document(spec, "pdf")
