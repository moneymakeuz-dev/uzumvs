import io
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from app.clock import utcnow
from app.main import create_app
from app.models import User
from app.security import hash_password
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