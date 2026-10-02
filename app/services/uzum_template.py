import io
import posixpath
import re
import unicodedata
import zipfile
from copy import copy
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from app.errors import AppError

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MAX_TEMPLATE_BYTES = 10 * 1024 * 1024
MAX_TEMPLATE_ENTRIES = 2000
MAX_TEMPLATE_UNCOMPRESSED = 32 * 1024 * 1024
FILTER_START_COLUMN = 31
FILTER_COLUMNS = 51
STOP_WORDS = {"и", "для", "на", "по", "с", "в", "из", "к", "от", "до", "при", "the", "and"}
PRODUCT_COLUMNS = {
    "title_ru": "A",
    "seller_id": "B",
    "title_uz": "C",
    "sku_group": "D",
    "category_path": "E",
    "category_id": "F",
    "brand": "G",
    "model": "H",
    "country": "I",
    "description_ru": "J",
    "description_uz": "K",
    "short_description_ru": "L",
    "short_description_uz": "M",
    "photo_urls": "T",
    "barcode": "U",
    "ikpu": "V",
    "color": "W",
    "size": "X",
    "sale_price": "Y",
    "list_price": "Z",
    "weight_g": "AA",
    "height_mm": "AB",
    "width_mm": "AC",
    "length_mm": "AD",
}


@dataclass(frozen=True)
class TemplateInfo:
    categories: dict[str, tuple[str, str]]
    brands: frozenset[str]
    countries: frozenset[str]
    selected_category_id: str | None
    filters: dict[str, list[dict]] = field(default_factory=dict)

    def to_payload(self) -> dict:
        return {
            "selected_category_id": self.selected_category_id,
            "selected_category_path": self.categories.get(self.selected_category_id or "", ("", ""))[1],
            "filters": self.filters,
            "categories": [
                {"id": category_id, "title": title, "path": path}
                for category_id, (title, path) in sorted(self.categories.items(), key=lambda item: item[1][1].casefold())
            ],
            "countries": sorted(self.countries, key=str.casefold),
        }


def inspect_template(data: bytes) -> TemplateInfo:
    archive = _open_template(data)
    with archive:
        shared_strings = _shared_strings(archive)
        sheet_paths = _sheet_paths(archive)
        if len(sheet_paths) < 3:
            raise AppError("uzum_template_invalid", "Uzum shablonidagi varaqlar topilmadi.")
        product_sheet = ET.fromstring(archive.read(sheet_paths[0]))
        category_sheet = ET.fromstring(archive.read(sheet_paths[1]))
        catalog_sheet = ET.fromstring(archive.read(sheet_paths[2]))
        category_rows = _rows(category_sheet, shared_strings)
        header = _row_values(category_rows.get(1, {}), shared_strings)
        columns = {value: index for index, value in header.items()}
        required = ("category_id", "category_title", "full_path_ru")
        if any(name not in columns for name in required):
            raise AppError("uzum_template_invalid", "Shablonda kategoriya ro'yxati topilmadi.")
        categories = {}
        filters: dict[str, dict[str, dict]] = {}
        for row_number, cells in category_rows.items():
            if row_number == 1:
                continue
            values = _row_values(cells, shared_strings)
            category_id = values.get(columns["category_id"], "")
            title = values.get(columns["category_title"], "")
            path = values.get(columns["full_path_ru"], "")
            if category_id.isdigit() and title and path:
                categories[category_id] = (title, path)
                filter_id = values.get(columns.get("filter_id", 0), "").strip()
                if filter_id:
                    item = filters.setdefault(category_id, {}).setdefault(filter_id, {
                        "id": filter_id, "name": values.get(columns.get("filter_ru", 0), "").strip(),
                        "multiple": values.get(columns.get("customer_type", 0), "").strip() != "SINGLE_CHOICE",
                        "values": [],
                    })
                    for option in values.get(columns.get("filter_value_ru", 0), "").replace("\r", "").replace("\n", "").split(";"):
                        option = option.replace("\xa0", " ").replace("\t", " ").strip()
                        if option and option not in item["values"]:
                            item["values"].append(option)
        catalog_rows = _rows(catalog_sheet, shared_strings)
        catalog_headers = _row_values(catalog_rows.get(1, {}), shared_strings)
        brands = set()
        countries = set()
        for row_number, cells in catalog_rows.items():
            if row_number == 1:
                continue
            values = _row_values(cells, shared_strings)
            if values.get(3):
                brands.add(values[3])
            if values.get(4):
                countries.add(values[4])
        if not categories or not brands or not countries or not catalog_headers:
            raise AppError("uzum_template_invalid", "Uzum shablonidagi ma'lumotnomalar bo'sh.")
        selected = _row_values(_rows(product_sheet, shared_strings).get(1, {}), shared_strings).get(3, "")
        match = re.search(r"\|\s*(\d+)\s*$", selected)
        return TemplateInfo(categories, frozenset(brands), frozenset(countries), match.group(1) if match else None,
                            {key: list(value.values()) for key, value in filters.items()})


def recommend_category(content: dict, seller_notes: str, categories: dict[str, tuple[str, str]]) -> dict | None:
    title = (content.get("title") or {}).get("ru") or ""
    suggested = ((content.get("suggested_category") or {}).get("ru")) or ""
    if not title.strip() or not categories:
        return None
    target_texts = [(title, 0.68), (suggested, 0.27), (seller_notes, 0.05)] if suggested else [
        (title, 0.88), (seller_notes, 0.12),
    ]
    target_tokens = [(_words(text), weight) for text, weight in target_texts if text.strip()]
    ranked = []
    for category_id, (category_title, path) in categories.items():
        segments = path.split(" > ")
        terms: dict[str, float] = {}
        for index, segment in enumerate(segments):
            weight = 2.0 if index == len(segments) - 1 else 0.45 if index == len(segments) - 2 else 0.12
            for token in _words(segment):
                terms[token] = max(terms.get(token, 0), weight)
        if not terms:
            continue
        source_scores = []
        for tokens, source_weight in target_tokens:
            if not tokens:
                continue
            coverage = sum(
                term_weight * max((_word_similarity(term, token) for token in tokens), default=0)
                for term, term_weight in terms.items()
            ) / sum(terms.values())
            source_scores.append((coverage, source_weight))
        score = sum(value * weight for value, weight in source_scores) / sum(weight for _, weight in source_scores)
        ranked.append((score, category_id, category_title, path))
    if not ranked:
        return None
    score, category_id, category_title, path = max(ranked, key=lambda item: (item[0], -len(item[3])))
    if score < 0.16:
        return None
    return {"id": category_id, "title": category_title, "path": path, "score": round(score, 3)}


def _words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return [word for word in re.findall(r"[\w]+", normalized, flags=re.UNICODE)
            if len(word) > 2 and word not in STOP_WORDS]


def _word_similarity(first: str, second: str) -> float:
    if first == second:
        return 1.0
    if min(len(first), len(second)) >= 4 and (first.startswith(second) or second.startswith(first)):
        return 0.86
    ratio = SequenceMatcher(None, first, second).ratio()
    return ratio * 0.75 if ratio >= 0.72 else 0.0


def fill_template(data: bytes, values: dict) -> bytes:
    info = inspect_template(data)
    category_id = str(values.get("category_id", ""))
    if category_id not in info.categories:
        raise AppError("uzum_category_invalid", "Kategoriya ID shablonda topilmadi.")
    category_title, category_path = info.categories[category_id]
    category_filters = info.filters.get(category_id, [])
    chosen_filters = values.get("filters") or {}
    if not isinstance(chosen_filters, dict) or set(chosen_filters) - {item["id"] for item in category_filters}:
        raise AppError("uzum_filter_invalid", "Kategoriyaga tegishli bo'lmagan filter yuborildi.")
    for item in category_filters:
        selected = chosen_filters.get(item["id"]) or []
        if (not isinstance(selected, list) or not all(isinstance(option, str) for option in selected)
                or any(option not in item["values"] for option in selected)
                or (not item["multiple"] and len(selected) > 1)):
            raise AppError("uzum_filter_invalid", f"“{item['name']}” filteri uchun ro'yxatdagi qiymatni tanlang.")
    brand, country = str(values.get("brand", "")), str(values.get("country", ""))
    if brand not in info.brands:
        raise AppError("uzum_brand_invalid", "Brendni Uzum shablonidagi ro'yxatdan tanlang.")
    if country not in info.countries:
        raise AppError("uzum_country_invalid", "Ishlab chiqarilgan mamlakatni shablon ro'yxatidan tanlang.")
    content = values.get("content") or {}
    title = content.get("title") or {}
    description = content.get("description") or {}
    short_description = content.get("short_description") or {}
    required_text = (
        title.get("ru"), title.get("uz"), values.get("sku_group"), description.get("ru"),
        description.get("uz"), brand, country,
    )
    if any(not isinstance(value, str) or not value.strip() for value in required_text):
        raise AppError("uzum_required_field", "Uzum uchun nom, tavsif, SKU guruhi, brend va mamlakatni to'ldiring.")
    if len(title["ru"]) > 90 or len(title["uz"]) > 90 or len(values["sku_group"]) > 100:
        raise AppError("uzum_text_limit", "Uzum nomi 90, SKU guruhi 100 belgidan oshmasligi kerak.")
    ikpu = str(values.get("ikpu", ""))
    if not re.fullmatch(r"\d{16}", ikpu):
        raise AppError("uzum_ikpu_invalid", "IKPU 16 ta raqamdan iborat bo'lishi kerak.")
    photo_urls = values.get("photo_urls") or []
    if not 1 <= len(photo_urls) <= 5 or any(not _is_public_https_url(url) for url in photo_urls):
        raise AppError("uzum_photo_url_invalid", "1-5 ta hammaga ochiq HTTPS rasm havolasini kiriting.")
    sale_price = _positive_decimal(values.get("sale_price"), "Sotuv narxi")
    list_price = _positive_decimal(values.get("list_price"), "Eski narx")
    if list_price < sale_price:
        raise AppError("uzum_price_invalid", "Eski narx sotuv narxidan kichik bo'lmasligi kerak.")
    numbers = {name: _positive_integer(values.get(name), label) for name, label in (
        ("weight_g", "Vazn"), ("height_mm", "Balandlik"), ("width_mm", "En"), ("length_mm", "Uzunlik"),
    )}
    fields = {
        "title_ru": title["ru"], "seller_id": str(values.get("seller_id", "")),
        "title_uz": title["uz"], "sku_group": values["sku_group"],
        "category_path": category_title, "category_id": category_id, "brand": brand,
        "model": str(values.get("model", "")), "country": country,
        "description_ru": description["ru"], "description_uz": description["uz"],
        "short_description_ru": str(short_description.get("ru") or ""),
        "short_description_uz": str(short_description.get("uz") or ""),
        "photo_urls": "\n".join(photo_urls), "barcode": str(values.get("barcode", "")),
        "ikpu": ikpu, "color": str(values.get("color", "")), "size": str(values.get("size", "")),
        "sale_price": sale_price, "list_price": list_price, **numbers,
    }
    archive = _open_template(data)
    with archive:
        infos = archive.infolist()
        parts = {item.filename: archive.read(item.filename) for item in infos}
        sheet_paths = _sheet_paths(archive)
        main_path = sheet_paths[0]
        main_sheet = ET.fromstring(parts[main_path])
        _set_cell(main_sheet, "C1", f"{category_path} | {category_id}")
        _set_cell(main_sheet, "D1", int(category_id))
        _clear_cells(main_sheet, (2, 3, 4), FILTER_START_COLUMN, FILTER_START_COLUMN + FILTER_COLUMNS - 1)
        for offset, item in enumerate(category_filters):
            column = _column_letters(FILTER_START_COLUMN + offset)
            _set_cell(main_sheet, f"{column}2", item["name"])
            _set_cell(main_sheet, f"{column}3", "выбор нескольких" if item["multiple"] else "выбор одного")
            selected = chosen_filters.get(item["id"]) or []
            if selected:
                _set_cell(main_sheet, f"{column}4", ";".join(selected))
        for name, value in fields.items():
            _set_cell(main_sheet, f"{PRODUCT_COLUMNS[name]}4", value)
        ET.register_namespace("", MAIN_NS)
        for prefix, uri in _namespace_declarations(parts[main_path]):
            ET.register_namespace(prefix or "", uri)
        updated_sheet = ET.tostring(main_sheet, encoding="utf-8", xml_declaration=True,
                                    short_empty_elements=True)
        result = io.BytesIO()
        with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as output:
            for item in infos:
                output.writestr(copy(item), updated_sheet if item.filename == main_path else parts[item.filename])
        return result.getvalue()


def _open_template(data: bytes) -> zipfile.ZipFile:
    if not data or len(data) > MAX_TEMPLATE_BYTES:
        raise AppError("uzum_template_size", "Uzum shabloni 10 MiB dan kichik bo'lishi kerak.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
        if (len(infos) > MAX_TEMPLATE_ENTRIES
                or sum(item.file_size for item in infos) > MAX_TEMPLATE_UNCOMPRESSED
                or "xl/vbaProject.bin" not in archive.namelist()
                or "xl/workbook.xml" not in archive.namelist()):
            archive.close()
            raise AppError("uzum_template_invalid", "Yangi rasmiy Uzum .xlsm shablonini yuklang.")
        return archive
    except (zipfile.BadZipFile, OSError):
        raise AppError("uzum_template_invalid", "Yangi rasmiy Uzum .xlsm shablonini yuklang.") from None


def _sheet_paths(archive: zipfile.ZipFile) -> list[str]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {item.get("Id"): item.get("Target") for item in relationships.findall(f"{{{PKG_REL_NS}}}Relationship")}
    paths = []
    for sheet in workbook.findall(f"{{{MAIN_NS}}}sheets/{{{MAIN_NS}}}sheet"):
        target = targets.get(sheet.get(f"{{{DOC_REL_NS}}}id"))
        if not target:
            continue
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        if path in archive.namelist():
            paths.append(path)
    return paths


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t"))
            for item in root.findall(f"{{{MAIN_NS}}}si")]


def _rows(root: ET.Element, shared_strings: list[str]) -> dict[int, dict[int, ET.Element]]:
    result = {}
    for row in root.findall(f"{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
        row_number = int(row.get("r", "0"))
        result[row_number] = {_column_index(cell.get("r", "")): cell for cell in row.findall(f"{{{MAIN_NS}}}c")}
    return result


def _row_values(cells: dict[int, ET.Element], shared_strings: list[str]) -> dict[int, str]:
    return {column: _cell_value(cell, shared_strings) for column, cell in cells.items()}


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    if cell.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t"))
    value = cell.find(f"{{{MAIN_NS}}}v")
    if value is None or value.text is None:
        return ""
    if cell.get("t") == "s":
        try:
            return shared_strings[int(value.text)]
        except (IndexError, ValueError):
            return ""
    return value.text


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference)
    if not letters:
        return 0
    result = 0
    for char in letters.group(0):
        result = result * 26 + ord(char) - ord("A") + 1
    return result


def _column_letters(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _clear_cells(root: ET.Element, row_numbers: tuple[int, ...], first: int, last: int) -> None:
    main = f"{{{MAIN_NS}}}"
    for row in root.findall(f"{main}sheetData/{main}row"):
        if int(row.get("r", "0")) not in row_numbers:
            continue
        for cell in row.findall(main + "c"):
            if first <= _column_index(cell.get("r", "")) <= last:
                for child in list(cell):
                    cell.remove(child)
                cell.attrib.pop("t", None)


def _set_cell(root: ET.Element, reference: str, value: str | int | Decimal) -> None:
    main = f"{{{MAIN_NS}}}"
    sheet_data = root.find(main + "sheetData")
    if sheet_data is None:
        raise AppError("uzum_template_invalid", "Shablondagi mahsulot varag'i noto'g'ri.")
    row_number = int(re.search(r"\d+$", reference).group())
    row = sheet_data.find(f"{main}row[@r='{row_number}']")
    if row is None:
        row = ET.SubElement(sheet_data, main + "row", {"r": str(row_number)})
    cell = row.find(f"{main}c[@r='{reference}']")
    if cell is None:
        cell = ET.Element(main + "c", {"r": reference})
        column = _column_index(reference)
        cells = list(row.findall(main + "c"))
        following = next((item for item in cells if _column_index(item.get("r", "")) > column), None)
        if following is None:
            row.append(cell)
        else:
            row.insert(list(row).index(following), cell)
    for child in list(cell):
        if child.tag in {main + "v", main + "is", main + "f"}:
            cell.remove(child)
    if isinstance(value, (int, Decimal)):
        cell.attrib.pop("t", None)
        node = ET.SubElement(cell, main + "v")
        node.text = str(value)
    else:
        cell.set("t", "inlineStr")
        inline = ET.SubElement(cell, main + "is")
        text = ET.SubElement(inline, main + "t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
        text.text = str(value)


def _namespace_declarations(xml: bytes) -> list[tuple[str | None, str]]:
    return [(prefix or None, uri) for _, (prefix, uri) in ET.iterparse(io.BytesIO(xml), events=("start-ns",))]


def _positive_integer(value, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    if number <= 0:
        raise AppError("uzum_required_field", f"{label} musbat butun son bo'lishi kerak.")
    return number


def _positive_decimal(value, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        number = Decimal(0)
    if not number.is_finite() or number <= 0:
        raise AppError("uzum_required_field", f"{label} musbat son bo'lishi kerak.")
    return number


def _is_public_https_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        host = parsed.hostname or ""
        return (parsed.scheme == "https" and bool(host) and parsed.username is None and parsed.password is None
                and host != "localhost" and not host.endswith((".local", ".internal"))
                and not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host))
    except ValueError:
        return False