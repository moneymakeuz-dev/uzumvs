import subprocess
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.errors import AppError
from app.models import TimeslotWatch, UzumAccount
from app.services import uzum_accounts, uzum_cabinet, uzum_seller
from tests.fake_uzum import FakeUzum
from tests.helpers import PASSWORD, add_user, client_for, csrf, logged_client

CONNECT = {"api_token": "api-token-seller-1", "login": "seller@uzum.test", "password": "uzum-pass-1"}


@pytest.fixture
def uzum(monkeypatch):
    return FakeUzum().install(monkeypatch)


async def login_location(database, email):
    async with client_for(database) as client:
        token = await csrf(client)
        response = await client.post("/auth/login", data={"email": email, "password": PASSWORD, "csrf_token": token})
        return response.headers["location"]


async def stored(db, user_id) -> UzumAccount | None:
    async with db.transaction() as session:
        return await session.scalar(select(UzumAccount).where(UzumAccount.user_id == user_id))


async def age_shops(db, user_id):
    async with db.transaction() as session:
        account = await session.scalar(select(UzumAccount).where(UzumAccount.user_id == user_id))
        account.shops_checked_at = utcnow() - timedelta(hours=7)


async def test_sign_in_asks_for_uzum_until_connected_and_stores_secrets_encrypted(database, account, uzum):
    db, settings = database
    assert await login_location(database, account.email_normalized) == "/uzum/connect"
    async with logged_client(database, account.email_normalized) as client:
        page = await client.get("/uzum/connect")
        assert page.status_code == 200 and 'id="uzum-connect-form"' in page.text and "Keyinroq" in page.text
        connected = await client.post("/api/uzum/account", json=CONNECT)
        assert connected.status_code == 200, connected.text
        assert connected.json() == {"connected": True, "status": "active", "label": "Ulangan", "message": "",
                                    "login_hint": "se***@uzum.test", "shops": [{"id": "77", "name": "Shop"}]}
        assert (await client.get("/api/uzum/shops")).json() == {"shops": [{"id": "77", "name": "Shop"}]}
        assert "se***@uzum.test" in (await client.get("/profile")).text
    assert await login_location(database, account.email_normalized) == "/cards/new"

    record = await stored(db, account.id)
    secrets = [record.login_secret, record.password_secret, record.api_token_secret, record.access_token_secret]
    assert not any(value in secret for secret in secrets for value in [*CONNECT.values(), uzum.tokens["seller@uzum.test"]])
    assert uzum_accounts.reveal(settings, record.password_secret) == "uzum-pass-1"
    assert "uzum-pass-1" not in settings.audit_log.read_text(encoding="utf-8")


async def test_connect_rejects_bad_token_wrong_password_and_foreign_shop(database, account, uzum):
    db, _ = database
    uzum.add_seller("other@uzum.test", "other-pass-1", "api-token-other-1", [{"id": 88, "name": "Other"}])
    async with logged_client(database, account.email_normalized) as client:
        bad_token = (await client.post("/api/uzum/account", json={**CONNECT, "api_token": "api-token-unknown"})).json()["error"]
        assert (bad_token["code"], list(bad_token["fields"])) == ("uzum_token_invalid", ["api_token"])
        wrong = (await client.post("/api/uzum/account", json={**CONNECT, "password": "nope"})).json()["error"]
        assert (wrong["code"], list(wrong["fields"])) == ("uzum_login_invalid", ["password"])
        foreign = await client.post("/api/uzum/account", json={**CONNECT, "api_token": "api-token-other-1"})
        assert foreign.json()["error"]["code"] == "uzum_account_mismatch"
        assert (await client.post("/api/uzum/account", json=CONNECT)).status_code == 200
        assert (await client.post("/api/uzum/account", json=CONNECT)).status_code == 200
        assert (await client.post("/api/uzum/account", json=CONNECT)).status_code == 429
    assert (await stored(db, account.id)).status == "active"


async def test_phone_login_is_tried_in_both_formats(database, account, uzum):
    uzum.add_seller("+998901234567", "phone-pass-1", "api-token-phone-1", [{"id": 90, "name": "Phone shop"}])
    async with logged_client(database, account.email_normalized) as client:
        response = await client.post("/api/uzum/account", json={
            "api_token": "api-token-phone-1", "login": "+998 (90) 123-45-67", "password": "phone-pass-1"})
    assert response.status_code == 200, response.text
    assert response.json()["login_hint"] == "+99890***67"
    assert uzum.logins == ["998901234567", "+998901234567"]
    assert uzum_cabinet.login_candidates("Seller@Uzum.test ") == ["Seller@Uzum.test"]


async def test_uzum_features_need_connection_and_each_member_uses_own_shops(database, account, uzum):
    other = await add_user(database, "other@example.com")
    uzum.add_seller("other@uzum.test", "other-pass-1", "api-token-other-1", [{"id": 88, "name": "Other"}])
    day = (utcnow() + timedelta(days=10)).date().isoformat()
    watch = {"shop_id": "77", "invoice_id": 5, "date_from": day, "date_to": day}
    async with logged_client(database, other.email_normalized) as client:
        assert (await client.get("/api/uzum/shops")).json()["error"]["code"] == "uzum_not_connected"
        assert (await client.post("/api/timeslot-watches", json=watch)).json()["error"]["code"] == "uzum_not_connected"
        page = await client.get("/timeslots")
        assert page.status_code == 200 and "Uzum'ni ulash" in page.text and 'id="timeslot-form"' not in page.text
        await client.post("/api/uzum/account", json={"api_token": "api-token-other-1", "login": "other@uzum.test",
                                                     "password": "other-pass-1"})
        assert (await client.get("/api/uzum/shops")).json()["shops"] == [{"id": "88", "name": "Other"}]
        assert (await client.post("/api/timeslot-watches", json=watch)).json()["error"]["code"] == "uzum_shop_invalid"
        assert (await client.get("/api/uzum/invoices?shop_id=77")).json()["error"]["code"] == "uzum_shop_invalid"
        assert 'id="timeslot-form"' in (await client.get("/timeslots")).text


async def test_expired_token_is_reported_and_cached_shops_survive_outage(database, account, uzum):
    db, _ = database
    async with logged_client(database, account.email_normalized) as client:
        await client.post("/api/uzum/account", json=CONNECT)
        await age_shops(db, account.id)
        uzum.openapi_down = True
        assert (await client.get("/api/uzum/shops")).json()["shops"] == [{"id": "77", "name": "Shop"}]

        uzum.openapi_down = False
        uzum.expired.add(CONNECT["api_token"])
        failed = (await client.get("/api/uzum/shops")).json()["error"]
        assert failed["code"] == "uzum_token_failed" and "token" in failed["message"]
        assert (await stored(db, account.id)).status == "token_failed"
        assert "Token eskirgan" in (await client.get("/profile")).text
        assert (await client.get("/api/uzum/invoices?shop_id=77")).json()["error"]["code"] == "uzum_token_failed"
    assert await login_location(database, account.email_normalized) == "/uzum/connect"


async def test_cabinet_relogs_in_when_seller_refreshes_token_elsewhere(database, account, uzum):
    db, settings = database
    uzum.invoices[5] = {"id": 5, "invoiceNumber": "005", "invoiceStatus": {"value": "CREATED"},
                        "stock": {"title": "Toshkent"}, "timeSlotReservation": None}
    async with logged_client(database, account.email_normalized) as client:
        await client.post("/api/uzum/account", json=CONNECT)
        uzum.rotate()
        response = await client.get("/api/uzum/invoices?shop_id=77")
        assert response.status_code == 200, response.text
        assert response.json()["invoices"] == [{"id": 5, "number": "005", "stock": "Toshkent", "current_slot": "",
                                                "remaining_updates": None}]
    assert uzum.logins == ["seller@uzum.test", "seller@uzum.test"]
    record = await stored(db, account.id)
    assert uzum_accounts.reveal(settings, record.access_token_secret) == uzum.tokens["seller@uzum.test"]


async def test_changed_password_stops_logins_until_reconnected(database, account, uzum):
    db, _ = database
    async with logged_client(database, account.email_normalized) as client:
        await client.post("/api/uzum/account", json=CONNECT)
        uzum.passwords["seller@uzum.test"] = "changed-pass-2"
        uzum.rotate()
        failed = (await client.get("/api/uzum/invoices?shop_id=77")).json()["error"]
        assert failed["code"] == "uzum_login_failed"
        attempts = len(uzum.logins)
        assert (await client.get("/api/uzum/invoices?shop_id=77")).json()["error"]["code"] == "uzum_login_failed"
        assert len(uzum.logins) == attempts
        assert (await stored(db, account.id)).status == "login_failed"
        reconnected = await client.post("/api/uzum/account", json={**CONNECT, "password": "changed-pass-2"})
        assert reconnected.json()["status"] == "active"


async def test_disconnect_and_account_deletion_wipe_uzum_credentials(database, account, uzum):
    db, _ = database
    other = await add_user(database, "other@example.com")
    uzum.add_seller("other@uzum.test", "other-pass-1", "api-token-other-1", [{"id": 88, "name": "Other"}])
    today = utcnow().date()
    async with logged_client(database, account.email_normalized) as client:
        await client.post("/api/uzum/account", json=CONNECT)
        created = await client.post("/api/timeslot-watches", json={
            "shop_id": "77", "invoice_id": 5, "date_from": (today + timedelta(days=3)).isoformat(),
            "date_to": (today + timedelta(days=4)).isoformat()})
        assert created.status_code == 201, created.text
        assert (await client.delete("/api/uzum/account")).status_code == 204
        assert "Uzum'ni ulash" in (await client.get("/timeslots")).text
    assert await stored(db, account.id) is None
    async with db.transaction() as session:
        watch = (await session.scalars(select(TimeslotWatch))).one()
    assert (watch.status, watch.message) == ("cancelled", "Uzum ulanishi o'chirildi, kuzatuv to'xtatildi.")

    async with logged_client(database, other.email_normalized) as client:
        await client.post("/api/uzum/account", json={"api_token": "api-token-other-1", "login": "other@uzum.test",
                                                     "password": "other-pass-1"})
        deleted = await client.request("DELETE", "/api/me", json={"current_password": PASSWORD, "confirm": True})
        assert deleted.status_code == 202, deleted.text
    assert await stored(db, other.id) is None


async def test_browser_runs_with_members_own_login_and_profile(database, account, uzum, monkeypatch):
    db, settings = database
    captured = {}

    def fake_run(command, **options):
        captured.update(command=command, env=options["env"])
        return subprocess.CompletedProcess(command, 0, b'{"status": "accepted"}\n', b"")

    monkeypatch.setattr(uzum_seller.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(uzum_seller.subprocess, "run", fake_run)
    monkeypatch.setattr(uzum_seller, "ROOT", settings.upload_dir.parent)
    (settings.upload_dir.parent / "scripts").mkdir(parents=True)
    (settings.upload_dir.parent / "scripts" / "uzum_upload.mjs").write_text("", encoding="utf-8")
    await uzum_accounts.connect(db, settings, account.id, **CONNECT)

    result = await uzum_seller.upload_template(db, settings, account.id, b"xlsm", "77")

    assert result["status"] == "accepted"
    assert captured["env"]["UZUM_SELLER_EMAIL"] == "seller@uzum.test"
    assert captured["env"]["UZUM_SELLER_PASSWORD"] == "uzum-pass-1"
    assert captured["env"]["UZUM_BROWSER_PROFILE"] == str(uzum_accounts.profile_dir(account.id))
    assert account.id.hex in captured["env"]["UZUM_BROWSER_PROFILE"]
    with pytest.raises(AppError) as error:
        await uzum_seller.upload_template(db, settings, account.id, b"xlsm", "88")
    assert error.value.code == "uzum_shop_invalid"
