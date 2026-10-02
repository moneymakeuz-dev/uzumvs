import pytest

from app.services import ikpu


@pytest.fixture
def fake_search(monkeypatch):
    async def fake_get(_client, endpoint, params):
        text = params.get("search_text") or params.get("text")
        if text != "Кружка керамическая":
            return []
        return [
            {"mxikCode": "06912001043000000", "subPositionName": "Кружка керамическая"},
            {"mxikCode": "06912001043000123", "subPositionName": "Кружка керамическая", "brandName": "X"},
            {"mxikCode": "06911001003000000", "subPositionName": "Кружки, чашки и аналогичные изделие из фарфора"},
        ]

    monkeypatch.setattr(ikpu, "_get", fake_get)


async def test_suggest_ikpu_returns_generic_codes_ranked_by_product_match(fake_search):
    candidates = await ikpu.suggest_ikpu(["Кружка керамическая"], "Кружка керамическая белая 350 мл")

    assert [item["code"] for item in candidates] == ["06912001043000000", "06911001003000000"]
    assert ikpu.recommended(candidates) == "06912001043000000"


async def test_suggest_ikpu_skips_auto_pick_when_match_is_weak(fake_search):
    candidates = await ikpu.suggest_ikpu(["Кружка керамическая"], "Подставка для телефона")

    assert ikpu.recommended(candidates) is None


async def test_suggest_ikpu_is_empty_without_results(fake_search):
    assert await ikpu.suggest_ikpu(["ничего"], "ничего") == []
    assert await ikpu.suggest_ikpu([], "") == []
