import re
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

FIELD_PATHS = (
    "title.uz", "title.ru", "short_description.uz", "short_description.ru",
    "description.uz", "description.ru", "attributes", "suggested_category",
    "keywords.uz", "keywords.ru", "color", "material",
)
Source = Literal["seller", "visible", "inferred", "unknown"]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=10000)]
REVIEW_ANCHORS = sorted({*FIELD_PATHS, "title", "short_description", "description", "keywords",
                         "suggested_category.uz", "suggested_category.ru", "color.uz", "color.ru",
                         "material.uz", "material.ru"}, key=len, reverse=True)


def normalize_review_path(path: str) -> str:
    for anchor in REVIEW_ANCHORS:
        if path == anchor or path.startswith(anchor + "."):
            return anchor
    return path


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Bilingual(StrictModel):
    uz: Text
    ru: Text


class OptionalBilingual(StrictModel):
    uz: Text | None
    ru: Text | None


class Attribute(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    name: Bilingual
    value: Bilingual
    source: Source


class Keywords(StrictModel):
    uz: list[Annotated[str, StringConstraints(min_length=1, max_length=64)]] = Field(max_length=20)
    ru: list[Annotated[str, StringConstraints(min_length=1, max_length=64)]] = Field(max_length=20)


class ReviewItem(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    path: str = Field(max_length=100)
    reason: str = Field(min_length=1, max_length=500)
    source: Source


def value_at(content: dict[str, Any], path: str) -> Any:
    value: Any = content
    for part in path.split("."):
        value = value[int(part)] if isinstance(value, list) and part.isdigit() else value[part]
    return value


def set_value(content: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    parent = content
    for part in parts[:-1]:
        parent = parent[part]
    parent[parts[-1]] = value


def inspect_text(value: Any) -> None:
    if isinstance(value, str) and (re.search(r"<\s*/?\w+[^>]*>", value) or "\x00" in value):
        raise ValueError("HTML va yashirin null belgilar qabul qilinmaydi")
    if isinstance(value, dict):
        for item in value.values():
            inspect_text(item)
    if isinstance(value, list):
        for item in value:
            inspect_text(item)


class CardContent(StrictModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    title: Bilingual
    short_description: Bilingual
    description: Bilingual
    attributes: list[Attribute] = Field(max_length=30)
    suggested_category: OptionalBilingual
    keywords: Keywords
    color: OptionalBilingual
    material: OptionalBilingual
    review_items: list[ReviewItem] = Field(max_length=50)

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        content = self.model_dump()
        inspect_text(content)
        for field, limit in {"title": 200, "short_description": 1000, "description": 10000,
                             "suggested_category": 200, "color": 100, "material": 150}.items():
            for value in content[field].values():
                if value is not None and len(value) > limit:
                    raise ValueError(f"{field}: eng ko'pi {limit} belgi")
                if field in {"title", "description"} and not value:
                    raise ValueError(f"{field}: bo'sh bo'lmasligi kerak")
        self.validate_collections(content)
        return self

    def validate_collections(self, content: dict[str, Any]) -> None:
        keys = [attribute.key for attribute in self.attributes]
        if len(set(keys)) != len(keys):
            raise ValueError("Takroriy xususiyat kaliti")
        for attribute in self.attributes:
            if any(not value or len(value) > 100 for value in attribute.name.model_dump().values()):
                raise ValueError("Xususiyat nomi 1-100 belgi")
            if any(not value or len(value) > 500 for value in attribute.value.model_dump().values()):
                raise ValueError("Xususiyat qiymati 1-500 belgi")
        for values in (self.keywords.uz, self.keywords.ru):
            if len({value.casefold() for value in values}) != len(values):
                raise ValueError("Takroriy kalit so'z")
        if len({item.id for item in self.review_items}) != len(self.review_items):
            raise ValueError("Takroriy tekshirish identifikatori")
        for item in self.review_items:
            if item.path.split(".")[0] not in set(FIELD_PATHS) | {"title", "short_description", "description", "keywords"}:
                raise ValueError("Noto'g'ri tekshirish maydoni")
            try:
                value_at(content, item.path)
            except (KeyError, IndexError, TypeError):
                raise ValueError("Tekshirish maydoni mavjud emas") from None


class FieldGenerationResult(StrictModel):
    field_path: str
    value: Any
    review_items: list[ReviewItem] = Field(max_length=50)

    def apply(self, original: dict[str, Any], expected: str) -> CardContent:
        if self.field_path != expected or expected not in FIELD_PATHS:
            raise ValueError("So'ralmagan maydon qaytdi")
        content = CardContent.model_validate(original).model_dump()
        set_value(content, expected, self.value)
        content["review_items"] = [item for item in content["review_items"]
                                   if not overlaps(item["path"], expected)]
        for item in self.review_items:
            if not (item.path == expected or item.path.startswith(expected + ".")):
                raise ValueError("Boshqa maydon tekshiruvi qaytdi")
            content["review_items"].append(item.model_dump())
        return CardContent.model_validate(content)


def overlaps(first: str, second: str) -> bool:
    return first == second or first.startswith(second + ".") or second.startswith(first + ".")


def field_schema(path: str) -> dict[str, Any]:
    if path not in FIELD_PATHS:
        raise ValueError("Noto'g'ri maydon")
    value_type = str
    if path.startswith("keywords."):
        value_type = list[str]
    elif path == "attributes":
        value_type = list[Attribute]
    elif path in {"color", "material", "suggested_category"}:
        value_type = OptionalBilingual
    schema = TypeAdapter(value_type).json_schema()
    definitions = schema.pop("$defs", {})
    review_schema = TypeAdapter(list[ReviewItem]).json_schema()
    definitions.update(review_schema.pop("$defs", {}))
    return {"type": "object", "properties": {"field_path": {"type": "string", "enum": [path]},
            "value": schema, "review_items": review_schema}, "$defs": definitions,
            "required": ["field_path", "value", "review_items"], "additionalProperties": False}


class CardPatch(StrictModel):
    expected_version: int = Field(ge=1)
    seller_notes: str | None = Field(default=None, max_length=2000)
    content: dict[str, Any] | None = None
    resolve_review_ids: list[str] = Field(default_factory=list, max_length=50)


class GenerateRequest(StrictModel):
    operation: Literal["initial", "regenerate_all", "regenerate_field"]
    expected_version: int = Field(ge=1)
    field_path: str | None = None

    @model_validator(mode="after")
    def validate_field(self) -> Self:
        if self.operation == "regenerate_field" and self.field_path not in FIELD_PATHS:
            raise ValueError("Qayta yaratiladigan maydonni tanlang")
        if self.operation != "regenerate_field" and self.field_path is not None:
            raise ValueError("Bu amal maydon qabul qilmaydi")
        return self


class ExportRequest(StrictModel):
    card_ids: list[UUID] = Field(min_length=1, max_length=100)
    format: Literal["csv", "xlsx"]

    @model_validator(mode="after")
    def unique_cards(self) -> Self:
        if len(set(self.card_ids)) != len(self.card_ids):
            raise ValueError("Takroriy kartochka tanlangan")
        return self
