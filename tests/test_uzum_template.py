from html import escape
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.errors import AppError
from app.services.uzum_template import fill_template, inspect_template, recommend_category

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def inline_cell(reference: str, value: str) -> str:
    return f'<c r="{reference}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'


def sheet(cells: str, *, extensions: bool = False) -> bytes:
    extension = (
        '<extLst><ext uri="{test}"><x14:dataValidations xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"/></ext></extLst>'
        if extensions else ""
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:x14ac="http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac" '
        'xmlns:xr="http://schemas.microsoft.com/office/spreadsheetml/2014/revision" '
        'xmlns:xr2="http://schemas.microsoft.com/office/spreadsheetml/2015/revision2" '
        'xmlns:xr3="http://schemas.microsoft.com/office/spreadsheetml/2016/revision3" '
        'mc:Ignorable="x14ac xr xr2 xr3"><sheetData>'
        f"{cells}</sheetData>{extension}</worksheet>"
    )
    return xml.encode()


def template_bytes() -> bytes:
    members = {
        "xl/workbook.xml": (
            b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            b'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            b'<sheet name="Products" sheetId="1" r:id="rId1"/>'
            b'<sheet name="Categories" sheetId="2" r:id="rId2"/>'
            b'<sheet name="Dictionaries" sheetId="3" r:id="rId3"/>'
            b'</sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" Target="worksheets/sheet1.xml" '
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
            b'<Relationship Id="rId2" Target="worksheets/sheet2.xml" '
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
            b'<Relationship Id="rId3" Target="worksheets/sheet3.xml" '
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
            b'</Relationships>'
        ),
        "xl/worksheets/sheet1.xml": sheet(
            f'<row r="1">{inline_cell("C1", "Home > Kitchen > Mugs | 123")}</row>'
            f'<row r="2">{inline_cell("A2", "Product title RU")}</row>'
            '<row r="4"></row>', extensions=True,
        ),
        "xl/worksheets/sheet2.xml": sheet(
            f'<row r="1">{inline_cell("A1", "category_id")}{inline_cell("B1", "category_title")}'
            f'{inline_cell("C1", "full_path_ru")}</row>'
            f'<row r="2"><c r="A2"><v>123</v></c>{inline_cell("B2", "Mugs")}'
            f'{inline_cell("C2", "Home > Kitchen > Mugs")}</row>'
            f'<row r="3"><c r="A3"><v>124</v></c>{inline_cell("B3", "Cups")}'
            f'{inline_cell("C3", "Home > Kitchen > Cups")}</row>'
        ),
        "xl/worksheets/sheet3.xml": sheet(
            f'<row r="1">{inline_cell("A1", "Sizes")}{inline_cell("B1", "Colors")}'
            f'{inline_cell("C1", "Brands")}{inline_cell("D1", "Countries")}</row>'
            f'<row r="2">{inline_cell("C2", "No brand")}{inline_cell("D2", "Uzbekistan")}</row>'
        ),
        "xl/vbaProject.bin": b"synthetic-vba-project",
        "[Content_Types].xml": b"<Types/>",
    }
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    return output.getvalue()


def template_values(**changes):
    values = {
        "content": {
            "title": {"ru": "Кружка керамическая белая", "uz": "Oq keramik krujka"},
            "description": {"ru": "Керамическая кружка.", "uz": "Keramik krujka."},
            "short_description": {"ru": "Белая кружка", "uz": "Oq krujka"},
        },
        "category_id": "123",
        "brand": "No brand",
        "country": "Uzbekistan",
        "sku_group": "KRUJKA1",
        "ikpu": "1234567890123456",
        "photo_urls": ["https://cdn.example.test/mug.jpg"],
        "sale_price": 10000,
        "list_price": 10000,
        "weight_g": 300,
        "height_mm": 100,
        "width_mm": 80,
        "length_mm": 80,
    }
    values.update(changes)
    return values


def cell_text(root, reference: str) -> str:
    cell = root.find(f".//{{{MAIN}}}c[@r='{reference}']")
    if cell is None:
        return ""
    text = "".join(node.text or "" for node in cell.iter(f"{{{MAIN}}}t"))
    return text or (cell.findtext(f"{{{MAIN}}}v") or "")


def test_template_lists_categories_and_fills_one_product_without_losing_vba_or_extensions():
    original = template_bytes()
    info = inspect_template(original)

    assert info.categories["123"] == ("Mugs", "Home > Kitchen > Mugs")
    assert info.selected_category_id == "123"
    assert "No brand" in info.brands
    assert "Uzbekistan" in info.countries

    result = fill_template(original, template_values())
    with ZipFile(BytesIO(original)) as source, ZipFile(BytesIO(result)) as filled:
        assert source.namelist() == filled.namelist()
        assert all(source.read(name) == filled.read(name)
                   for name in source.namelist() if name != "xl/worksheets/sheet1.xml")
        root = ET.fromstring(filled.read("xl/worksheets/sheet1.xml"))
        assert cell_text(root, "C1") == "Home > Kitchen > Mugs | 123"
        assert cell_text(root, "D1") == "123"
        assert cell_text(root, "A4") == "Кружка керамическая белая"
        assert cell_text(root, "C4") == "Oq keramik krujka"
        assert cell_text(root, "E4") == "Mugs"
        assert cell_text(root, "T4") == "https://cdn.example.test/mug.jpg"
        assert cell_text(root, "V4") == "1234567890123456"
        assert cell_text(root, "Y4") == "10000"
        xml = filled.read("xl/worksheets/sheet1.xml")
        assert b'mc:Ignorable="x14ac xr xr2 xr3"' in xml
        assert b"x14:dataValidations" in xml
        assert filled.read("xl/vbaProject.bin") == b"synthetic-vba-project"


@pytest.mark.parametrize("changes,code", [
    ({"category_id": "999"}, "uzum_category_invalid"),
    ({"brand": "Unknown brand"}, "uzum_brand_invalid"),
    ({"country": "Unknown country"}, "uzum_country_invalid"),
    ({"photo_urls": ["http://cdn.example.test/mug.jpg"]}, "uzum_photo_url_invalid"),
    ({"ikpu": "123"}, "uzum_ikpu_invalid"),
])
def test_template_rejects_invalid_seller_data(changes, code):
    with pytest.raises(AppError) as error:
        fill_template(template_bytes(), template_values(**changes))
    assert error.value.code == code


def test_recommend_category_uses_product_title_and_category_hint():
    categories = {
        "123": ("Кружки и чашки", "Товары для дома > Кружки и чашки"),
        "15831": ("Детские кружки", "Детские товары > Детские кружки"),
        "12407": ("Термокружки", "Товары для дома > Термосы и термокружки > Термокружки"),
        "200": ("Чехлы для телефонов", "Электроника > Аксессуары > Чехлы для телефонов"),
    }
    content = {
        "title": {"ru": "Кружка керамическая белая 350 мл"},
        "suggested_category": {"ru": "Кружки и чашки"},
    }

    recommendation = recommend_category(content, "Oq keramik krujka", categories)

    assert recommendation["id"] == "123"
    assert recommendation["path"] == "Товары для дома > Кружки и чашки"


def test_recommend_category_returns_none_when_text_has_no_category_signal():
    categories = {"123": ("Кружки и чашки", "Товары для дома > Кружки и чашки")}

    assert recommend_category({"title": {"ru": "Предмет 350 мл"}}, "", categories) is None


def test_template_rejects_category_different_from_macro_prepared_category():
    with pytest.raises(AppError) as error:
        fill_template(template_bytes(), template_values(category_id="124"))
    assert error.value.code == "uzum_template_category_mismatch"