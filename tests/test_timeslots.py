from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.clock import TASHKENT, utcnow
from app.models import TimeslotWatch
from app.services import uzum_accounts, uzum_timeslots
from app.services.uzum_timeslots import blocker, pick_slot, tashkent_date
from tests.fake_uzum import HOUR_MS, FakeUzum, invoice
from tests.helpers import add_user, logged_client

CONNECT = {"api_token": "api-token-seller-1", "login": "seller@uzum.test", "password": "uzum-pass-1"}


def at(days: int, hour: int) -> int:
    """Toshkent vaqti bo'yicha bugundan `days` kun keyin soat `hour`:00 (ms)."""
    local = datetime.combine(uzum_timeslots.local_today() + timedelta(days=days), datetime.min.time(), TASHKENT)
    return int((local + timedelta(hours=hour)).timestamp() * 1000)


def label(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TASHKENT).strftime("%d.%m.%Y %H:%M")


@pytest.fixture
async def uzum(monkeypatch, database, account):
    fake = FakeUzum().install(monkeypatch)
    db, settings = database
    await uzum_accounts.connect(db, settings, account.id, **CONNECT)
    return fake


async def add_watch(db, user_id, invoice_id=501, days=(3, 4), status="active", shop_id="77"):
    today = uzum_timeslots.local_today()
    async with db.transaction() as session:
        watch = TimeslotWatch(user_id=user_id, shop_id=shop_id, invoice_id=invoice_id, invoice_number=f"N-{invoice_id}",
                              date_from=today + timedelta(days=days[0]), date_to=today + timedelta(days=days[1]),
                              status=status, message="")
        session.add(watch)
        await session.flush()
    return watch.id


async def load(db, watch_id):
    async with db.transaction() as session:
        return await session.get(TimeslotWatch, watch_id)


def test_tashkent_date_uses_utc_plus_five():
    assert tashkent_date(int(datetime(2026, 11, 2, 19, 30, tzinfo=UTC).timestamp() * 1000)) == date(2026, 11, 3)
    assert tashkent_date(int(datetime(2026, 11, 3, 18, 59, tzinfo=UTC).timestamp() * 1000)) == date(2026, 11, 3)


def test_earliest_slot_inside_range_is_picked():
    now = int(datetime(2026, 10, 30, 7, tzinfo=UTC).timestamp() * 1000)
    moment = lambda *args: int(datetime(*args, tzinfo=UTC).timestamp() * 1000)  # noqa: E731
    slots = [{"timeFrom": moment(2026, 11, 2, 18)}, {"timeFrom": moment(2026, 11, 4, 5)},
             {"timeFrom": moment(2026, 11, 3, 9)}, {"timeFrom": moment(2026, 11, 5, 5)}, "broken"]
    window = (date(2026, 11, 3), date(2026, 11, 4))
    assert pick_slot(slots, *window, None, now) == {"timeFrom": moment(2026, 11, 3, 9)}
    assert pick_slot(slots, *window, moment(2026, 11, 3, 9), now) == {"timeFrom": moment(2026, 11, 4, 5)}
    assert pick_slot(slots[3:], *window, None, now) is None
    assert pick_slot([{"timeFrom": "2026-11-03T09:00:00Z"}], *window, None, now) == {"timeFrom": "2026-11-03T09:00:00Z"}


def test_status_limit_and_late_slot_block_the_move():
    now = int(datetime(2026, 10, 30, 7, tzinfo=UTC).timestamp() * 1000)
    current = int(datetime(2026, 11, 14, 4, tzinfo=UTC).timestamp() * 1000)
    window, lead = (date(2026, 11, 3), date(2026, 11, 4)), 48 * HOUR_MS
    info = lambda **fields: {"status": "CREATED", "status_text": None, "time_from": current, "remaining": 2, **fields}  # noqa: E731
    assert blocker(info(status="ACCEPTED", status_text="Принята"), *window, now, lead) == {
        "outcome": "stopped", "reason": "status", "detail": "Принята"}
    assert blocker(info(remaining=0), *window, now, lead) == {"outcome": "stopped", "reason": "limit"}
    assert blocker(info(time_from=now + 47 * HOUR_MS), *window, now, lead) == {"outcome": "stopped", "reason": "too_late"}
    assert blocker(info(time_from=int(datetime(2026, 11, 4, 5, tzinfo=UTC).timestamp() * 1000)), *window, now, lead) == {"outcome": "already"}
    assert blocker(info(), *window, now, lead) is None
    assert blocker(info(time_from=None, remaining=0), *window, now, lead) is None


async def test_watch_api_creates_lists_and_cancels(database, account, uzum):
    day = (uzum_timeslots.local_today() + timedelta(days=4)).isoformat()
    body = {"shop_id": "77", "invoice_id": 501, "invoice_number": "N-501", "stock_title": "Toshkent",
            "date_from": day, "date_to": day}
    async with logged_client(database, account.email_normalized) as client:
        created = await client.post("/api/timeslot-watches", json=body)
        assert created.status_code == 201, created.text
        assert created.json()["status"] == "active"
        assert (await client.post("/api/timeslot-watches", json=body)).json()["error"]["code"] == "timeslot_watch_exists"

        listed = (await client.get("/api/timeslot-watches")).json()["watches"]
        assert [item["invoice_number"] for item in listed] == ["N-501"]
        page = await client.get("/timeslots")
        assert page.status_code == 200 and "Yuk xati N-501" in page.text and 'hx-trigger="every 20s"' in page.text

        cancelled = await client.post(f"/api/timeslot-watches/{created.json()['id']}/cancel")
        assert cancelled.json()["status"] == "cancelled"
        again = await client.post("/api/timeslot-watches", json=body)
        assert again.status_code == 201


async def test_watch_api_validates_dates_shop_and_owner(database, account, uzum):
    today = uzum_timeslots.local_today()
    body = {"shop_id": "77", "invoice_id": 7, "date_from": today.isoformat(), "date_to": today.isoformat()}
    other = await add_user(database, "other@example.com")
    async with logged_client(database, account.email_normalized) as client:
        past = await client.post("/api/timeslot-watches", json={**body, "date_from": (today - timedelta(days=1)).isoformat()})
        assert past.json()["error"]["code"] == "timeslot_date_past"
        reversed_range = await client.post("/api/timeslot-watches", json={**body, "date_to": (today - timedelta(days=1)).isoformat()})
        assert reversed_range.json()["error"]["code"] in {"timeslot_date_past", "timeslot_range_invalid"}
        assert (await client.post("/api/timeslot-watches", json={**body, "shop_id": "99"})).json()["error"]["code"] == "uzum_shop_invalid"
        watch_id = (await client.post("/api/timeslot-watches", json=body)).json()["id"]
    async with logged_client(database, other.email_normalized) as client:
        assert (await client.post(f"/api/timeslot-watches/{watch_id}/cancel")).status_code == 404
        assert (await client.get("/api/timeslot-watches")).json()["watches"] == []


async def test_check_moves_invoice_into_free_slot_verifies_and_notifies(database, account, uzum):
    db, settings = database
    watch_id = await add_watch(db, account.id)
    uzum.invoices[501] = invoice(501, at(10, 9))
    free = at(3, 10)
    uzum.slots = [{"timeFrom": at(2, 9)}, {"timeFrom": free, "timeTo": free + 2 * HOUR_MS}, {"timeFrom": at(4, 15)}]

    assert await uzum_timeslots.check_due(db, settings) == 1

    (_, path, body), = uzum.requests("/time-slot/set")
    assert path == "/api/seller/shop/77/v2/invoice/time-slot/set"
    assert body == {"timeFrom": free, "invoiceIds": [501], "stockId": 9, "poolSource": "POOL"}
    (_, _, query), = uzum.requests("/time-slot/get")
    assert query["invoiceIds"] == [501] and query["poolSource"] == "POOL"
    assert abs(query["timeFrom"] - (utcnow().timestamp() * 1000 + HOUR_MS)) < 60_000
    watch = await load(db, watch_id)
    assert (watch.status, watch.checks, watch.invoice_number) == ("done", 1, "00501")
    assert watch.new_time_from == watch.current_time_from == datetime.fromtimestamp(free / 1000, UTC)
    assert watch.message == f"Timeslot {label(free)} ga ko'chirildi."
    letters = list(settings.mail_dir.glob("*.eml"))
    assert len(letters) == 1 and "00501" in letters[0].read_text(encoding="utf-8")

    assert await uzum_timeslots.check_due(db, settings) == 0
    assert len(uzum.requests("/time-slot/set")) == 1


async def test_check_keeps_waiting_and_stops_on_uzum_rules(database, account, uzum):
    db, settings = database
    waiting = await add_watch(db, account.id, invoice_id=1)
    limited = await add_watch(db, account.id, invoice_id=2)
    late = await add_watch(db, account.id, invoice_id=3, days=(5, 6))
    missed = await add_watch(db, account.id, invoice_id=4, days=(12, 12))
    accepted = await add_watch(db, account.id, invoice_id=5)
    uzum.invoices = {1: invoice(1, at(10, 9)), 2: invoice(2, at(10, 9), remaining=0),
                     3: invoice(3, int(utcnow().timestamp() * 1000) + 47 * HOUR_MS),
                     4: invoice(4, at(10, 9)), 5: invoice(5, at(10, 9), status="ACCEPTED")}
    uzum.slots = [{"timeFrom": at(12, 9)}, {"timeFrom": at(14, 9)}]
    uzum.set_errors[4] = httpx.Response(400, json={"errors": [{"code": "time-slot-003"}]})

    assert await uzum_timeslots.check_due(db, settings) == 5

    first = await load(db, waiting)
    assert first.status == "active" and first.message.endswith(f"Eng yaqin bo'sh slot: {label(at(12, 9))}.")
    assert first.current_time_from == datetime.fromtimestamp(at(10, 9) / 1000, UTC)
    assert (await load(db, limited)).status == "failed"
    assert "48 soatdan kam" in (await load(db, late)).message
    assert (await load(db, missed)).status == "active" and "boshqa sotuvchi" in (await load(db, missed)).message
    assert "Принята на складе" in (await load(db, accepted)).message
    assert [body["invoiceIds"] for _, _, body in uzum.requests("/time-slot/set")] == [[4]]


async def test_set_rejected_with_401_is_retried_after_relogin_in_same_check(database, account, uzum):
    """Prod'dagi holat: sotuvchining brauzeri tokenni yangiladi, set 401 oldi — endi shu tekshiruvning o'zida ko'chadi."""
    db, settings = database
    watch_id = await add_watch(db, account.id)
    uzum.invoices[501] = invoice(501, at(10, 9))
    uzum.slots = [{"timeFrom": at(3, 10)}]
    original = uzum.cabinet

    def rotate_before_set(method, path, request, payload):
        if path.endswith("/time-slot/set") and len(uzum.logins) == 1:
            uzum.rotate()
            return httpx.Response(401, json={"errors": [{"code": "unauthorized-001"}]})
        return original(method, path, request, payload)

    uzum.cabinet = rotate_before_set
    await uzum_timeslots.check_due(db, settings)

    assert (await load(db, watch_id)).status == "done"
    assert len(uzum.logins) == 2
    assert uzum.invoices[501]["remainingAmountOfUpdates"] == 1


async def test_login_failure_keeps_watches_and_expired_dates_close(database, account, uzum):
    db, settings = database
    active = await add_watch(db, account.id, invoice_id=10)
    expired = await add_watch(db, account.id, invoice_id=11, days=(-3, -1))
    uzum.passwords["seller@uzum.test"] = "changed-pass-2"
    uzum.rotate()

    assert await uzum_timeslots.check_due(db, settings) == 1
    attempts = len(uzum.logins)
    assert await uzum_timeslots.check_due(db, settings) == 1

    watch = await load(db, active)
    assert (watch.status, watch.checks) == ("active", 2)
    assert watch.message == uzum_accounts.LOGIN_FAILED
    assert len(uzum.logins) == attempts
    assert (await load(db, expired)).status == "failed"


async def test_expired_api_token_does_not_stop_background_checks(database, account, uzum):
    db, settings = database
    watch_id = await add_watch(db, account.id)
    uzum.invoices[501] = invoice(501, at(10, 9))
    uzum.slots = [{"timeFrom": at(3, 10)}]
    await uzum_accounts.mark(db, account.id, "token_failed", uzum_accounts.TOKEN_FAILED)

    await uzum_timeslots.check_due(db, settings)

    assert (await load(db, watch_id)).status == "done"


async def test_each_member_is_checked_with_own_login_and_errors_stay_per_invoice(database, account, uzum):
    db, settings = database
    other = await add_user(database, "other@example.com")
    uzum.add_seller("other@uzum.test", "other-pass-1", "api-token-other-1", [{"id": 88, "name": "Other"}])
    await uzum_accounts.connect(db, settings, other.id, "api-token-other-1", "other@uzum.test", "other-pass-1")
    missing = await add_watch(db, account.id, invoice_id=404)
    mine = await add_watch(db, account.id, invoice_id=501)
    foreign = await add_watch(db, other.id, invoice_id=601, shop_id="88")
    uzum.invoices = {501: invoice(501, at(10, 9)), 601: invoice(601, at(10, 9))}

    await uzum_timeslots.check_due(db, settings)

    assert "invoice_http_404" in (await load(db, missing)).message
    assert (await load(db, mine)).message.startswith("Tanlangan sanalarda bo'sh slot yo'q.")
    assert (await load(db, foreign)).message.startswith("Tanlangan sanalarda bo'sh slot yo'q.")
    assert {path.split("/")[4] for _, path, _ in uzum.requests("/time-slot/get")} == {"77", "88"}


async def test_dry_run_reports_slot_without_moving(database, account, uzum):
    db, settings = database
    watch_id = await add_watch(db, account.id)
    uzum.invoices[501] = invoice(501, at(10, 9))
    uzum.slots = [{"timeFrom": at(3, 10)}]

    await uzum_timeslots.check_due(db, settings.model_copy(update={"uzum_timeslot_dry_run": True}))

    watch = await load(db, watch_id)
    assert watch.status == "active" and watch.message == f"Sinov rejimi: yuk xati {label(at(3, 10))} slotiga ko'chirilardi."
    assert uzum.requests("/time-slot/set") == []


async def test_cancel_during_check_is_kept_unless_slot_was_already_changed(database, account, uzum, monkeypatch):
    db, settings = database
    stays = await add_watch(db, account.id, invoice_id=20, days=(20, 20))
    changed = await add_watch(db, account.id, invoice_id=21)
    uzum.invoices = {20: invoice(20, at(10, 9)), 21: invoice(21, at(10, 9))}
    uzum.slots = [{"timeFrom": at(3, 10)}]
    original = uzum_timeslots._apply

    async def cancel_then_apply(*args):
        async with db.transaction() as session:
            for watch in (await session.scalars(select(TimeslotWatch))).all():
                watch.status = "cancelled"
        return await original(*args)

    monkeypatch.setattr(uzum_timeslots, "_apply", cancel_then_apply)
    await uzum_timeslots.check_due(db, settings)

    assert (await load(db, stays)).status == "cancelled"
    assert (await load(db, changed)).status == "done"
