import json
import re
from pathlib import Path
from typing import Any

from app.schemas import CardContent, ReviewItem, normalize_review_path, overlaps

RULES = json.loads(Path(__file__).with_name("rules.json").read_text(encoding="utf-8"))
RULES_VERSION: str = RULES["version"]
CONTACT = re.compile(r"https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]{2,}|\+998(?:[\s()-]*\d){9}|@[a-z0-9_]{5,}\b|t\.me/", re.I)
STOP_WORDS = re.compile("|".join(RULES["stop_patterns"]), re.I)
CYRILLIC = re.compile(r"[\u0400-\u04ff]")
TITLE_SYMBOLS = re.compile(r"[^\w\s,.:;\-–()/“”\"%*'ʻʼ’]")
REPEATED_PUNCTUATION = re.compile(r"([!?.,])\1{2,}")
Issue = tuple[str, str]


def text_leaves(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(prefix, value)]
    if isinstance(value, dict):
        return [item for key, child in value.items() for item in text_leaves(child, f"{prefix}.{key}".strip("."))
                if key not in {"review_items", "source", "key", "schema_version"}]
    if isinstance(value, list):
        return [item for position, child in enumerate(value) for item in text_leaves(child, f"{prefix}.{position}")]
    return []


def in_scope(path: str, field_path: str | None) -> bool:
    return field_path is None or overlaps(normalize_review_path(path), field_path) or path.startswith(field_path + ".")


def title_issues(title: str, path: str) -> tuple[list[Issue], list[Issue]]:
    errors, warnings = [], []
    if len(title.split()) < 3:
        errors.append((path, "sarlavhada kamida uchta so'z bo'lishi kerak"))
    if any(ord(char) >= 0x1F000 or 0x2600 <= ord(char) <= 0x27BF for char in title):
        errors.append((path, "sarlavhada emoji bo'lmasligi kerak"))
    if title[:1].islower():
        warnings.append((path, "sarlavha katta harf bilan boshlanishi kerak"))
    if title.endswith("."):
        warnings.append((path, "sarlavha oxirida nuqta bo'lmasligi kerak"))
    if symbols := sorted(set(TITLE_SYMBOLS.findall(title))):
        warnings.append((path, "ortiqcha belgilar: " + " ".join(symbols)))
    if any(len(word) >= 4 and word.isupper() for word in re.findall(r"\w+", title)):
        warnings.append((path, "katta harflar faqat brend yoki model nomi uchun ishlatilsin"))
    return errors, warnings


def rule_issues(content: CardContent, field_path: str | None = None) -> tuple[list[Issue], list[Issue]]:
    data = content.model_dump()
    errors: list[Issue] = []
    warnings: list[Issue] = []
    for path, value in text_leaves(data):
        if not in_scope(path, field_path):
            continue
        normalized = value.replace("ʻ", "'").replace("’", "'").replace("ʼ", "'")
        if CONTACT.search(normalized):
            errors.append((path, "aloqa ma'lumoti yoki tashqi havola bo'lmasligi kerak"))
        if match := STOP_WORDS.search(normalized):
            warnings.append((path, f"Uzum stop-so'zi yoki subyektiv ibora: \"{match.group(0)}\""))
        if REPEATED_PUNCTUATION.search(value):
            warnings.append((path, "takroriy tinish belgilari"))
        if (path.endswith(".uz") or ".uz." in path) and CYRILLIC.search(value):
            errors.append((path, "o'zbekcha matn lotin yozuvida bo'lishi kerak"))
    for language in ("uz", "ru"):
        if in_scope(f"title.{language}", field_path):
            title_errors, title_warnings = title_issues(data["title"][language], f"title.{language}")
            errors += title_errors
            warnings += title_warnings
        if in_scope(f"short_description.{language}", field_path) and not data["short_description"][language]:
            errors.append((f"short_description.{language}", "qisqa tavsif bo'sh bo'lmasligi kerak"))
    for path in ("title.ru", "description.ru"):
        if in_scope(path, field_path) and not CYRILLIC.search(value_of(data, path)):
            errors.append((path, "ruscha matn kirill yozuvida bo'lishi kerak"))
    return errors, warnings


def value_of(data: dict[str, Any], path: str) -> str:
    field, language = path.split(".")
    return data[field][language] or ""


def check_business_rules(content: CardContent, field_path: str | None = None) -> None:
    errors, _ = rule_issues(content, field_path)
    if errors:
        raise ValueError("; ".join(f"{path}: {message}" for path, message in errors[:5]))


def rule_warnings(content: CardContent) -> list[dict[str, str]]:
    return [{"path": normalize_review_path(path), "message": message} for path, message in rule_issues(content)[1]]


def prepare_reviews(content: CardContent, field_path: str | None = None) -> CardContent:
    updated = content.model_copy(deep=True)
    for path, reason in (("material", "Materialni mahsulot ma'lumoti bilan tekshiring."),
                         ("suggested_category", "Kategoriyani Uzum kabinetida tekshiring."),
                         ("description", "Tavsifdagi faktlar, kafolat va zarur kategoriya ma'lumotlarini tekshiring.")):
        if field_path and not overlaps(field_path, path):
            continue
        if not any(overlaps(item.path, path) for item in updated.review_items):
            updated.review_items.append(ReviewItem(id=f"check_{path}", path=path, reason=reason, source="unknown"))
    grouped: dict[str, list[str]] = {}
    for path, message in rule_issues(updated, field_path)[1]:
        grouped.setdefault(normalize_review_path(path), []).append(message)
    for path, messages in grouped.items():
        reason = ("Uzum qoidasi: " + "; ".join(dict.fromkeys(messages)))[:500]
        identifier = "rule_" + re.sub(r"[^a-z0-9]+", "_", path.lower())
        if not any(item.id == identifier for item in updated.review_items):
            updated.review_items.append(ReviewItem(id=identifier, path=path, reason=reason, source="inferred"))
    return CardContent.model_validate(updated.model_dump())
