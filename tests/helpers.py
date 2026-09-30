import io
from contextlib import asynccontextmanager
from html.parser import HTMLParser

import httpx
from PIL import Image

from app.clock import utcnow
from app.main import create_app
from app.models import User
from app.security import hash_password

PASSWORD = "local-test-password-42"


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
    assert response.status_code == 200, response.status_code
    parser = CsrfParser()
    parser.feed(response.text)
    return parser.token


def client_for(database):
    db, settings = database
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings, db)), base_url=settings.app_base_url)


@asynccontextmanager
async def logged_client(database, email, password=PASSWORD):
    async with client_for(database) as client:
        token = await csrf(client)
        response = await client.post("/auth/login", data={"email": email, "password": password, "csrf_token": token})
        assert response.status_code == 303, response.status_code
        client.headers["X-CSRF-Token"] = await csrf(client, "/profile")
        yield client


async def add_user(database, email, verified=True):
    db, settings = database
    async with db.transaction() as session:
        user = User(email_normalized=email, password_hash=hash_password(PASSWORD),
                    email_verified_at=utcnow() if verified else None,
                    ai_consent_at=utcnow(), consent_version=settings.consent_version)
        session.add(user)
        await session.flush()
    return user


def png(size=(80, 120), mode="RGB", color="white", image_format="PNG", **options):
    stream = io.BytesIO()
    Image.new(mode, size, color).save(stream, image_format, **options)
    return stream.getvalue()


async def upload(client, settings, key="page-upload", files=None):
    files = files or [("images[]", ("test.png", png(), "image/png"))]
    return await client.post("/api/cards", headers={"Idempotency-Key": key}, files=files,
                             data={"seller_notes": "Oq chashka", "consent_version": settings.consent_version})
