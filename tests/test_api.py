import io
import json
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from uuid import UUID, uuid4

import httpx
import pytest
from PIL import Image

from app.ai.rules import check_business_rules
from app.clock import utcnow
from app.main import create_app
from app.models import Card, User
from app.schemas import CardContent
from app.security import hash_password
from app.services.uzum_template import TemplateInfo
from app.worker import run_once

pytestmark = pytest.mark.integration


class CsrfParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = ""

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta" and values.get("name") == "csrf-token":
            self.token = values["content"]


async def csrf(client, path="/auth/login"):
    response = await client.get(path)
    assert response.status_code == 200
    parser = CsrfParser()
    parser.feed(response.text)
    return parser.token


@asynccontextmanager
async def logged_client(database, account):
    db, settings = database
    application = create_app(settings, db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url=settings.app_base_url) as client:
        token = await csrf(client)
        response = await client.post("/auth/login", data={"email": account.email_normalized, "password": "local-test-password-42", "csrf_token": token})
        assert response.status_code == 303
        client.headers["X-CSRF-Token"] = await csrf(client, "/cards/new")
        yield client


async def test_api_upload_generate_review_export_flow(database, account):
    db, settings = database
    async with logged_client(database, account) as client:
        photo = io.BytesIO()
        Image.new("RGB", (100, 100), "white").save(photo, "PNG")
        response = await client.post("/api/cards", headers={"Idempotency-Key": "upload"},
                                     data={"seller_notes": "Oq chashka", "consent_version": settings.consent_version},
                                     files=[("images[]", ("test.png", photo.getvalue(), "image/png"))])
        assert response.status_code == 201, response.text
        card = response.json()
        card_id = card["id"]
        response = await client.post(f"/api/cards/{card_id}/generations", headers={"Idempotency-Key": "generate"},
                                     json={"operation": "initial", "expected_version": 1})
        assert response.status_code == 202, response.text
        await run_once(db, settings)
        card = (await client.get(f"/api/cards/{card_id}")).json()
        assert card["status"] == "ready"
        rejected = await client.post("/api/exports", json={"card_ids": [card_id], "format": "csv"})
        assert rejected.status_code == 409
        saved = await client.patch(f"/api/cards/{card_id}", json={"expected_version": 2,
                                    "resolve_review_ids": [item["id"] for item in card["unresolved_reviews"]]})
        assert saved.status_code == 200, saved.text
        exported = await client.post("/api/exports", json={"card_ids": [card_id], "format": "xlsx"})
        assert exported.status_code == 200, exported.text
        assert exported.content[:2] == b"PK"
        assert (await client.get(card["images"][0]["url"])).status_code == 200


async def test_csrf_session_and_origin_protection(database, account):
    async with logged_client(database, account) as client:
        client.headers.pop("X-CSRF-Token")
        response = await client.post("/api/exports", json={"card_ids": [str(account.id)], "format": "csv"})
        assert response.status_code == 403
        client.headers["X-CSRF-Token"] = await csrf(client, "/profile")
        response = await client.post("/api/exports", headers={"Origin": "https://other.example"},
                                     json={"card_ids": [str(account.id)], "format": "csv"})
        assert response.status_code == 403
        response = await client.post("/api/exports", headers={"Sec-Fetch-Site": "cross-site"},
                                     json={"card_ids": [str(account.id)], "format": "csv"})
        assert response.status_code == 403
        response = await client.post("/api/exports", headers={"Origin": "null"},
                                     json={"card_ids": [str(account.id)], "format": "csv"})
        assert response.status_code == 404
        response = await client.get("/api/me/usage")
        assert response.json()["initial"]["limit"] == 20
        assert response.headers["x-content-type-options"] == "nosniff"


async def upload(client, settings, key="page-upload"):
    photo = io.BytesIO()
    Image.new("RGB", (80, 120), "white").save(photo, "PNG")
    response = await client.post("/api/cards", headers={"Idempotency-Key": key},
                                 data={"seller_notes": "Oq chashka", "consent_version": settings.consent_version},
                                 files=[("images[]", ("test.png", photo.getvalue(), "image/png"))])
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_card_page_and_status_fragment(database, account):
    db, settings = database
    async with logged_client(database, account) as client:
        card_id = await upload(client, settings)
        page = await client.get(f"/cards/{card_id}")
        assert page.status_code == 200 and 'id="card-data"' in page.text
        status = await client.get(f"/cards/{card_id}/status")
        assert "Kartochka yaratish" in status.text and "every 2s" not in status.text
        await client.post(f"/api/cards/{card_id}/generations", headers={"Idempotency-Key": "page-generate"},
                          json={"operation": "initial", "expected_version": 1})
        assert 'hx-trigger="every 2s"' in (await client.get(f"/cards/{card_id}/status")).text
        await run_once(db, settings)
        finished = (await client.get(f"/cards/{card_id}/status")).text
        assert 'data-status="succeeded"' in finished and "every 2s" not in finished
        missing = await client.get(f"/cards/{uuid4()}/status")
        assert 'data-status="missing"' in missing.text
    async with db.transaction() as session:
        session.add(User(email_normalized="other@example.com", password_hash=hash_password("local-test-password-42"),
                         email_verified_at=utcnow()))
    other = type(account)(email_normalized="other@example.com")
    async with logged_client(database, other) as client:
        assert (await client.get(f"/cards/{card_id}")).status_code == 404
        assert (await client.get(f"/api/cards/{card_id}")).status_code == 404
    application = create_app(settings, db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url=settings.app_base_url) as anonymous:
        response = await anonymous.get(f"/cards/{card_id}/status", headers={"HX-Request": "true"})
        assert response.status_code == 401 and response.headers["hx-redirect"] == "/auth/login"


async def test_uzum_template_catalog_and_card_download(database, account, monkeypatch):
    db, settings = database
    from app.routes import cards as card_routes

    monkeypatch.setattr(
        card_routes, "inspect_template",
        lambda _: TemplateInfo({"123": ("Кружки и чашки", "Товары для дома > Кружки и чашки")},
                       frozenset({"No brand"}), frozenset({"Uzbekistan"}), "123"),
    )
    monkeypatch.setattr(card_routes, "fill_template", lambda _template, _values: b"filled-xlsm")

    async def fake_shops(_settings):
        return [{"id": "15895", "name": "Test shop"}]

    async def fake_upload(_settings, _data, shop_id):
        return {"status": "accepted", "shop_id": shop_id, "message": "Test accepted"}

    monkeypatch.setattr(card_routes, "list_shops", fake_shops)
    monkeypatch.setattr(card_routes, "upload_template", fake_upload)
    async with logged_client(database, account) as client:
        card_id = await upload(client, settings, "uzum-template")
        content = CardContent.model_validate({
            "title": {"ru": "Кружка керамическая белая", "uz": "Oq keramik krujka"},
            "short_description": {"ru": "Белая керамическая кружка", "uz": "Oq keramik krujka"},
            "description": {"ru": "Белая керамическая кружка.", "uz": "Oq keramik krujka."},
            "attributes": [], "suggested_category": {"ru": "Чашки", "uz": "Krujkalar"},
            "keywords": {"ru": [], "uz": []}, "color": {"ru": None, "uz": None},
            "material": {"ru": None, "uz": None}, "review_items": [],
        })
        check_business_rules(content)
        async with db.transaction() as session:
            card = await session.get(Card, UUID(card_id))
            card.content_json = content.model_dump()
            card.status = "ready"
            await session.flush()
        catalog = await client.post(
            f"/api/cards/{card_id}/uzum-template/catalog",
            files={"template": ("official.xlsm", b"template", "application/octet-stream")},
        )
        assert catalog.status_code == 200, catalog.text
        assert catalog.json()["categories"] == [{
            "id": "123", "title": "Кружки и чашки", "path": "Товары для дома > Кружки и чашки",
        }]
        assert catalog.json()["countries"] == ["Uzbekistan"]
        assert catalog.json()["recommended_category"]["id"] == "123"
        assert catalog.json()["shops"] == [{"id": "15895", "name": "Test shop"}]
        assert "brands" not in catalog.json()
        payload = {
            "expected_version": 1, "category_id": 123, "sku_group": "MUG-1",
            "brand": "No brand", "country": "Uzbekistan", "ikpu": "1234567890123456",
            "photo_urls": ["https://cdn.example.test/mug.jpg"], "sale_price": 10000,
            "list_price": 10000, "weight_g": 300, "height_mm": 100,
            "width_mm": 80, "length_mm": 80,
        }
        response = await client.post(
            f"/api/cards/{card_id}/uzum-import-file",
            data={"payload": json.dumps(payload)},
            files={"template": ("official.xlsm", b"template", "application/octet-stream")},
        )
        assert response.status_code == 200, response.text
        assert response.content == b"filled-xlsm"
        assert response.headers["content-type"] == "application/vnd.ms-excel.sheet.macroEnabled.12"
        assert response.headers["cache-control"] == "no-store"

        publish_payload = {**payload, "expected_version": 1, "shop_id": "15895"}
        published = await client.post(
            f"/api/cards/{card_id}/uzum-publish",
            data={"payload": json.dumps(publish_payload)},
            files={"template": ("official.xlsm", b"template", "application/octet-stream")},
        )
        assert published.status_code == 200, published.text
        assert published.json() == {"status": "accepted", "shop_id": "15895", "message": "Test accepted"}

        payload["expected_version"] = 999
        stale = await client.post(
            f"/api/cards/{card_id}/uzum-import-file",
            data={"payload": json.dumps(payload)},
            files={"template": ("official.xlsm", b"template", "application/octet-stream")},
        )
        assert stale.status_code == 409