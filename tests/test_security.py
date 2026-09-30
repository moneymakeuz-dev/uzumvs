import os
import uuid

import pytest

from app.worker import run_once
from tests.helpers import add_user, client_for, csrf, logged_client, png, upload

pytestmark = pytest.mark.integration


async def ready_card(client, database, key):
    db, settings = database
    response = await upload(client, settings, key)
    card_id = response.json()["id"]
    job = await client.post(f"/api/cards/{card_id}/generations", headers={"Idempotency-Key": f"{key}-job"},
                            json={"operation": "initial", "expected_version": 1})
    await run_once(db, settings)
    card = (await client.get(f"/api/cards/{card_id}")).json()
    await client.patch(f"/api/cards/{card_id}", json={"expected_version": card["version"],
                       "resolve_review_ids": [item["id"] for item in card["unresolved_reviews"]]})
    return card_id, job.json()["id"], card["images"][0]["url"]


async def test_other_seller_cannot_touch_any_owned_object(database):
    await add_user(database, "owner@example.com")
    await add_user(database, "intruder@example.com")
    async with logged_client(database, "owner@example.com") as owner:
        card_id, job_id, image_url = await ready_card(owner, database, "owner")
    async with logged_client(database, "intruder@example.com") as intruder:
        own_card, _, _ = await ready_card(intruder, database, "intruder")
        checks = [intruder.get(f"/api/cards/{card_id}"), intruder.get(image_url), intruder.get(f"/api/jobs/{job_id}"),
                  intruder.post(f"/api/jobs/{job_id}/cancel"), intruder.patch(f"/api/cards/{card_id}", json={"expected_version": 1}),
                  intruder.delete(f"/api/cards/{card_id}?expected_version=1"), intruder.get(f"/cards/{card_id}"),
                  intruder.post(f"/api/cards/{card_id}/generations", headers={"Idempotency-Key": "steal"},
                                json={"operation": "regenerate_all", "expected_version": 3})]
        for request in checks:
            assert (await request).status_code == 404
        mixed = await intruder.post("/api/exports", json={"card_ids": [own_card, card_id], "format": "csv"})
        assert mixed.status_code == 404
        assert (await intruder.post("/api/exports", json={"card_ids": [own_card], "format": "csv"})).status_code == 200


async def test_export_boundaries(database):
    await add_user(database, "bounds@example.com")
    async with logged_client(database, "bounds@example.com") as client:
        ids = [str(uuid.uuid4()) for _ in range(101)]
        assert (await client.post("/api/exports", json={"card_ids": ids, "format": "csv"})).status_code == 422
        assert (await client.post("/api/exports", json={"card_ids": [ids[0], ids[0]], "format": "csv"})).status_code == 422
        assert (await client.post("/api/exports", json={"card_ids": ids[:100], "format": "xlsx"})).status_code == 404


async def test_unverified_email_cannot_upload(database):
    _, settings = database
    await add_user(database, "new@example.com", verified=False)
    async with logged_client(database, "new@example.com") as client:
        response = await upload(client, settings)
        assert response.status_code == 403 and response.json()["error"]["code"] == "email_unverified"


async def test_upload_rejects_unsafe_files(database):
    _, settings = database
    await add_user(database, "files@example.com")
    frames = [png((20, 20), color=color) for color in ("white", "black")]
    from io import BytesIO

    from PIL import Image

    animated = BytesIO()
    Image.open(BytesIO(frames[0])).save(animated, "PNG", save_all=True, append_images=[Image.open(BytesIO(frames[1]))])
    cases = {
        "six": ([("images[]", (f"{i}.png", png(), "image/png")) for i in range(6)], 422),
        "fake": ([("images[]", ("photo.png", b"<?php echo 1; ?>", "image/png"))], 415),
        "svg": ([("images[]", ("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"))], 415),
        "animated": ([("images[]", ("a.png", animated.getvalue(), "image/png"))], 415),
        "bomb": ([("images[]", ("b.png", png((8000, 6000), mode="1", color=0), "image/png"))], 415),
        "large": ([("images[]", ("l.png", os.urandom(10 * 1024 * 1024 + 1), "image/png"))], 413),
    }
    async with logged_client(database, "files@example.com") as client:
        for key, (files, status) in cases.items():
            response = await upload(client, settings, key, files)
            assert response.status_code == status, (key, response.text)
    assert not list(settings.upload_dir.glob("*.jpg"))


async def test_login_throttling_and_generic_errors(database):
    await add_user(database, "throttle@example.com")
    async with client_for(database) as client:
        token = await csrf(client)
        statuses = []
        for _ in range(6):
            response = await client.post("/auth/login", data={"email": "throttle@example.com", "password": "wrong-password",
                                                             "csrf_token": token})
            statuses.append(response.status_code)
        assert statuses == [401] * 5 + [429]
        unknown = await client.post("/auth/login", data={"email": "nobody@example.com", "password": "wrong-password",
                                                         "csrf_token": token})
        assert unknown.status_code == 401 and "Email yoki parol noto" in unknown.text


async def test_spoofed_forwarded_for_does_not_reset_ip_limit(database):
    async with client_for(database) as client:
        token = await csrf(client, "/auth/forgot-password")
        statuses = []
        for index in range(11):
            response = await client.post("/auth/forgot-password", headers={"X-Forwarded-For": f"203.0.113.{index}"},
                                         data={"email": f"person{index}@example.com", "csrf_token": token})
            statuses.append(response.status_code)
        assert statuses == [303] * 10 + [429]


async def test_password_change_revokes_existing_sessions(database):
    await add_user(database, "rotate@example.com")
    async with logged_client(database, "rotate@example.com") as client:
        old_cookie = client.cookies.get("karto_session")
        response = await client.post("/api/me/password", json={"current_password": "local-test-password-42",
                                                                "new_password": "brand-new-password-42"})
        assert response.status_code == 204
    async with client_for(database) as stale:
        stale.cookies.set("karto_session", old_cookie)
        assert (await stale.get("/api/me/usage")).status_code == 401
    async with logged_client(database, "rotate@example.com", "brand-new-password-42") as fresh:
        assert (await fresh.get("/api/me/usage")).status_code == 200


async def test_account_deletion_cancels_queue_and_blocks_access(database):
    db, settings = database
    await add_user(database, "leaving@example.com")
    async with logged_client(database, "leaving@example.com") as client:
        card_id = (await upload(client, settings, "leaving")).json()["id"]
        await client.post(f"/api/cards/{card_id}/generations", headers={"Idempotency-Key": "leaving-job"},
                          json={"operation": "initial", "expected_version": 1})
        wrong = await client.request("DELETE", "/api/me", json={"current_password": "incorrect", "confirm": True})
        assert wrong.status_code == 403
        response = await client.request("DELETE", "/api/me", json={"current_password": "local-test-password-42", "confirm": True})
        assert response.status_code == 202
        assert (await client.get("/api/me/usage")).status_code == 401
    async with db.engine.connect() as connection:
        usage = (await connection.exec_driver_sql("SELECT initial_reserved, initial_used FROM monthly_usage")).first()
    assert tuple(usage) == (0, 0)
    async with client_for(database) as client:
        token = await csrf(client)
        response = await client.post("/auth/login", data={"email": "leaving@example.com",
                                                          "password": "local-test-password-42", "csrf_token": token})
        assert response.status_code == 401
