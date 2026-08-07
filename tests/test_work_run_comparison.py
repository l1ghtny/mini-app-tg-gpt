from __future__ import annotations

import uuid

from openpyxl import load_workbook

from app.services.work_runs.comparison import (
    load_source_tables,
    render_comparison_workbook,
    validate_rendered_workbook,
)


def test_comparison_renderer_merges_columns_and_preserves_sources(tmp_path) -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    first_path = tmp_path / "offer-a.csv"
    second_path = tmp_path / "offer-b.csv"
    first_path.write_text(
        'Товар;Цена;Комментарий\nЧай;120;=HYPERLINK("https://bad")\nКофе;350;Опт\n',
        encoding="utf-8",
    )
    second_path.write_text(
        "товар;Цена;Срок поставки\nЧай;110;3 дня\n",
        encoding="utf-8",
    )

    tables = (
        *load_source_tables(
            document_id=first_id, filename="offer-a.csv", path=first_path
        ),
        *load_source_tables(
            document_id=second_id, filename="offer-b.csv", path=second_path
        ),
    )
    target_path = tmp_path / "offer-comparison.xlsx"
    rendered = render_comparison_workbook(
        tables=tables,
        target_path=target_path,
        language="ru",
        currency="RUB",
        instructions="Сравнить цены и сроки поставки",
    )
    validate_rendered_workbook(target_path)

    assert rendered.row_count == 3
    assert rendered.column_count == 4
    assert len(rendered.sources) == 2
    workbook = load_workbook(target_path, data_only=False)
    try:
        assert workbook.sheetnames == ["Сводка", "Сравнение", "Источники"]
        comparison = workbook["Сравнение"]
        headers = [cell.value for cell in comparison[1]]
        assert headers == [
            "Файл",
            "Лист",
            "Строка в источнике",
            "Товар",
            "Цена",
            "Комментарий",
            "Срок поставки",
        ]
        assert comparison[2][5].value == '\'=HYPERLINK("https://bad")'
        assert comparison[4][2].value == 2
        sources = workbook["Источники"]
        assert sources.max_row == 3
        assert sources.column_dimensions["A"].width == 36
        assert sources["A2"].alignment.wrap_text is True
    finally:
        workbook.close()


def test_comparison_reader_accepts_windows_1251_csv(tmp_path) -> None:
    path = tmp_path / "price.csv"
    path.write_bytes("Товар;Цена\nЧай;100\n".encode("cp1251"))

    tables = load_source_tables(
        document_id=uuid.uuid4(), filename="price.csv", path=path
    )

    assert tables[0].headers == ("Товар", "Цена")
    assert tables[0].rows == (("Чай", "100"),)
