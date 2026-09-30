import json

import pytest

from app.ai.provider import ProviderFailure, ProviderResult, generate, mock_result, parse_result
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