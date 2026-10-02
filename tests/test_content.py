import io

import pytest
from PIL import Image
from pydantic import ValidationError

from app.errors import AppError
from app.schemas import CardContent, FieldGenerationResult
from app.security import check_password, csrf_matches, hash_password
from app.services.images import prepare_image, required_free_bytes


@pytest.fixture
def content():
    return {
        "schema_version": 1, "title": {"uz": "Oddiy oq chashka", "ru": "Белая керамическая кружка"},
        "short_description": {"uz": "Oq chashka.", "ru": "Белая кружка."},
        "description": {"uz": "Rasmda oq chashka ko'rsatilgan.", "ru": "На фотографии белая кружка."},
        "attributes": [], "suggested_category": {"uz": "Idishlar", "ru": "Посуда"},
        "keywords": {"uz": ["chashka"], "ru": ["кружка"]},
        "color": {"uz": "Oq", "ru": "Белый"}, "material": {"uz": None, "ru": None},
        "review_items": [{"id": "material", "path": "material", "reason": "Materialni tasdiqlang", "source": "unknown"}],
    }


def test_content_rejects_html_and_invalid_reviews(content):
    CardContent.model_validate(content)
    content["title"]["uz"] = "<script>alert(1)</script>"
    with pytest.raises(ValidationError):
        CardContent.model_validate(content)
    content["title"]["uz"] = "Oq chashka"
    content["review_items"][0]["path"] = "secret"
    with pytest.raises(ValidationError):
        CardContent.model_validate(content)


def test_single_field_does_not_overwrite_other_content(content):
    result = FieldGenerationResult(field_path="title.uz", value="Yangi oq chashka", review_items=[])
    applied = result.apply(content, "title.uz").model_dump()
    assert applied["title"]["ru"] == content["title"]["ru"]
    assert applied["description"] == content["description"]
    assert applied["review_items"] == content["review_items"]
    with pytest.raises(ValueError):
        result.apply(content, "description.uz")


def test_password_hash_and_csrf():
    password = "local-test-password-42"
    hashed = hash_password(password)
    assert password not in hashed
    assert check_password(password, hashed)
    assert not check_password("incorrect", hashed)
    assert not check_password(password, None)
    assert csrf_matches("token", "token")
    assert not csrf_matches("token", "wrong")


def test_disk_reserve_scales_with_small_disks_and_is_capped_for_large_ones():
    gib = 1024 ** 3
    assert required_free_bytes(500 * 1024 ** 2) == 100 * 1024 ** 2
    assert required_free_bytes(10 * gib) == gib
    assert required_free_bytes(237 * gib) == 2 * gib


def test_image_is_resized_and_metadata_stripped():
    source = io.BytesIO()
    Image.new("RGBA", (2000, 1000), (0, 255, 0, 80)).save(source, "PNG")
    prepared = prepare_image(source.getvalue())
    assert (prepared.width, prepared.height) == (1024, 512)
    with Image.open(io.BytesIO(prepared.data)) as image:
        assert image.format == "JPEG"
        assert not image.getexif()


def test_fake_image_rejected():
    with pytest.raises(AppError):
        prepare_image(b"not a jpeg")