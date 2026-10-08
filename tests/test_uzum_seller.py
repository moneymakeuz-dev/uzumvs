import pytest

from app.config import Settings
from app.errors import AppError
from app.services import uzum_seller


@pytest.fixture
def api_down(monkeypatch, tmp_path):
    async def failing(_settings):
        raise AppError("uzum_api_unavailable", "down", 502)

    monkeypatch.setattr(uzum_seller, "_fetch_shops", failing)
    monkeypatch.setattr(uzum_seller, "SHOPS_CACHE", tmp_path / "shops.json")


async def test_list_shops_falls_back_to_configured_shop_when_api_is_down(api_down):
    shops = await uzum_seller.list_shops(Settings(_env_file=None, uzum_shop_id="15895"))

    assert shops == [{"id": "15895", "name": "Do'kon 15895"}]


async def test_list_shops_uses_last_successful_list_when_api_is_down(api_down, monkeypatch):
    uzum_seller.SHOPS_CACHE.write_text('[{"id": "1", "name": "Cached"}]', encoding="utf-8")

    assert await uzum_seller.list_shops(Settings(_env_file=None)) == [{"id": "1", "name": "Cached"}]


async def test_list_shops_raises_without_api_cache_or_configured_shop(api_down):
    with pytest.raises(AppError) as error:
        await uzum_seller.list_shops(Settings(_env_file=None))
    assert error.value.code == "uzum_api_unavailable"
