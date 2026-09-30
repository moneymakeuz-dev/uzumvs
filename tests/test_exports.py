import csv
import io

import pytest
from openpyxl import load_workbook

from app.errors import AppError
from app.services.exports import HEADERS, clean_cell, make_csv, make_xlsx


@pytest.mark.parametrize("value", ["=1+1", "+SUM(1,2)", "-1+1", "@test", " \t=1+1", "\r\n+1", "\x00=1", "\ufeff=1"])
def test_spreadsheet_formula_is_text(value):
    row = [value] * len(HEADERS)
    csv_data = list(csv.reader(io.StringIO(make_csv([row]).decode("utf-8-sig"))))
    assert csv_data[1][0].startswith("'")
    workbook = load_workbook(io.BytesIO(make_xlsx([row])), data_only=False)
    assert workbook.active["A2"].data_type == "s"
    workbook.close()


def test_unicode_multiline_csv_roundtrip():
    value = 'O\'zbekcha, "matn"\nРусский текст'
    data = make_csv([[value] * len(HEADERS)])
    assert data.startswith(b"\xef\xbb\xbf")
    assert list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))[1][0] == value


def test_long_cell_rejected_not_silently_truncated():
    with pytest.raises(AppError):
        clean_cell("a" * 32768)