import json
from types import SimpleNamespace

import pytest
from google.genai import types

from app.ai.provider import (
    ProviderFailure,
    ProviderResult,
    _gemini_schema,
    _thinking_config,
    generate,
    generate_gemini,
    mock_result,
    parse_result,
)
from app.ai.rules import check_business_rules, prepare_reviews
from app.config import Settings
from app.schemas import CardContent, field_schema


async def test_disabled_provider_fails_without_external_call():
    with pytest.raises(ProviderFailure) as error:
        await generate(Settings(_env_file=None), {"seller_notes": "", "images": []})
    assert not error.value.called


async def test_mock_is_labeled_and_schema_valid():
    settings = Settings(_env_file=None, app_env="test", ai_provider="mock")
    snapshot = {"seller_notes": "Oq chashka", "images": [], "content": None, "field_path": None}
    result = await generate(settings, snapshot)
    content = parse_result(result, snapshot)
    assert "Sinov" in content.title.uz
    check_business_rules(content)
    reviewed = prepare_reviews(content)
    assert {item.path for item in reviewed.review_items} == {"material", "suggested_category", "description"}
    snapshot["content"] = reviewed.model_dump()
    snapshot["field_path"] = "title.uz"
    changed = parse_result(await generate(settings, snapshot), snapshot)
    assert changed.title.ru == reviewed.title.ru
    assert changed.description == reviewed.description
    assert changed.title.uz != reviewed.title.uz


def test_field_schema_is_specific():
    schema = field_schema("keywords.uz")
    assert schema["properties"]["value"]["type"] == "array"
    with pytest.raises(ValueError):
        field_schema("password")


def test_full_response_schema_is_gemini_compatible_json_schema():
    field = CardContent.model_json_schema()["properties"]["schema_version"]
    assert field["type"] == "integer"
    assert field["minimum"] == field["maximum"] == 1
    schema = _gemini_schema(CardContent.model_json_schema())
    assert schema["properties"]["schema_version"]["minimum"] == 1
    assert schema["properties"]["schema_version"]["maximum"] == 1
    assert schema["properties"]["title"]["properties"]["uz"]["type"] == "string"
    assert schema["properties"]["title"]["additionalProperties"] is False
    assert set(schema["required"]) == set(CardContent.model_json_schema()["required"])
    text = json.dumps(schema)
    for key in ("$ref", "$defs", "maxItems", "minItems", "additional_properties"):
        assert key not in text
    assert "maxItems" not in json.dumps(_gemini_schema(field_schema("keywords.uz")))


@pytest.mark.parametrize("schema", [
    CardContent,
    {"type": "object", "properties": {"value": {"type": "string"}}},
])
async def test_generate_gemini_sends_sdk_schema(schema):
    class FakeModels:
        async def count_tokens(self, **kwargs):
            return SimpleNamespace(total_tokens=1)

        async def generate_content(self, **kwargs):
            self.config = kwargs["config"]
            return SimpleNamespace(text="{}", usage_metadata=None, response_id="synthetic")

    models = FakeModels()
    settings = Settings(_env_file=None, app_env="test", ai_provider="mock")
    await generate_gemini(SimpleNamespace(models=models), settings, [], schema)
    assert isinstance(models.config.response_json_schema, dict)
    assert models.config.response_schema is None


@pytest.mark.parametrize("model,thinking_level,thinking_budget", [
    ("gemini-2.5-flash", None, 0),
    ("gemini-3.8-flash", types.ThinkingLevel.LOW, None),
    ("other-model", None, None),
])
def test_thinking_config_matches_model(model, thinking_level, thinking_budget):
    config = _thinking_config(model)
    if thinking_level is None and thinking_budget is None:
        assert config is None
    else:
        assert config.thinking_level == thinking_level
        assert config.thinking_budget == thinking_budget


def provider_result(**changes):
    result = json.loads(mock_result({"content": None, "field_path": None}).text)
    result.update(changes)
    return ProviderResult(json.dumps(result, ensure_ascii=False))


def ready_content(**changes):
    content = parse_result(provider_result(), {"field_path": None}).model_dump()
    content.update(changes)
    return CardContent.model_validate(content)


def test_contacts_block_but_stop_words_only_warn():
    content = ready_content(title={"uz": "Oq keramik chashka aksiya", "ru": "Белая кружка, лучший выбор"})
    check_business_rules(content)
    reasons = {item.path: item.reason for item in prepare_reviews(content).review_items}
    assert "stop-so'zi" in reasons["title.uz"]
    assert "stop-so'zi" in reasons["title.ru"]
    contact = {"uz": "Buyurtma: t.me/shop_seller", "ru": "Пишите нам"}
    with pytest.raises(ValueError, match="aloqa"):
        check_business_rules(ready_content(description=contact))


def test_regenerated_field_is_checked_without_blaming_seller_edits():
    content = ready_content(title={"uz": "Chashka", "ru": "Кружка"})
    check_business_rules(content, "description.uz")
    with pytest.raises(ValueError, match="title.uz"):
        check_business_rules(content, "title.uz")


def test_review_ids_and_paths_are_server_owned():
    reviews = [{"id": "same", "path": "keywords.uz.7", "reason": "Tekshiring", "source": "unknown"},
               {"id": "same", "path": "attributes.9.value.uz", "reason": "Tekshiring", "source": "inferred"}]
    content = parse_result(provider_result(review_items=reviews), {"field_path": None})
    assert [(item.id, item.path) for item in content.review_items] == [
        ("review_1", "keywords.uz"), ("review_2", "attributes")]