import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models import TimeslotWatch
from app.services import uzum_seller, uzum_timeslots
from tests.helpers import add_user, logged_client

SLOT = datetime(2026, 11, 3, 5, 0, tzinfo=UTC)


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def read_tasks(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def shops(monkeypatch):
    async def listed(_settings):
        return [{"id": "77", "name": "Shop"}]

    monkeypatch.setattr(uzum_seller, "list_shops", listed)


@pytest.fixture
def cabinet(monkeypatch):
    """Brauzer skripti o'rniga: har chaqiruvda vazifalarni yozib, berilgan natijalarni qaytaradi."""
    state = {"calls": [], "results": {}, "error": None}

    async def run_script(_settings, mode, path, shop_id, _limit, _extra=None):
        payload = read_tasks(path)
        state["calls"].append((mode, shop_id, payload))
        if state["error"]:
            raise state["error"]
        results = [{"watchId": task["watchId"], **state["results"][task["invoiceId"]]}
                   for task in payload["tasks"] if task["invoiceId"] in state["results"]]
        return 0, {"status": "ok", "results": results}

    monkeypatch.setattr(uzum_seller, "run_script", run_script)
    return state


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


async def test_watch_api_creates_lists_and_cancels(database, account, shops):
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


async def test_watch_api_validates_dates_shop_and_owner(database, account, shops):
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


async def test_timeslot_pages_respect_uzum_allowlist(database, account):
    db, settings = database
    database = (db, settings.model_copy(update={"uzum_allowed_emails": "owner@example.com"}))
    async with logged_client(database, account.email_normalized) as client:
        assert (await client.get("/timeslots")).status_code == 403
        assert (await client.get("/api/timeslot-watches")).status_code == 403
        assert 'href="/timeslots"' not in (await client.get("/profile")).text


async def test_check_moves_invoice_into_free_slot_and_notifies(database, account, cabinet):
    db, settings = database
    watch_id = await add_watch(db, account.id)
    cabinet["results"][501] = {"outcome": "changed", "slot": ms(SLOT),
                               "invoice": {"number": "0042", "stock": "Toshkent", "timeFrom": ms(SLOT)}}

    assert await uzum_timeslots.check_due(db, settings) == 1

    mode, shop_id, payload = cabinet["calls"][0]
    assert (mode, shop_id, payload["minLeadHours"]) == ("timeslots", "77", 48)
    assert payload["tasks"][0]["invoiceId"] == 501
    watch = await load(db, watch_id)
    assert (watch.status, watch.new_time_from, watch.current_time_from) == ("done", SLOT, SLOT)
    assert watch.invoice_number == "0042" and watch.checks == 1
    assert watch.message == "Timeslot 03.11.2026 10:00 ga ko'chirildi."
    letters = list(settings.mail_dir.glob("*.eml"))
    assert len(letters) == 1 and "0042" in letters[0].read_text(encoding="utf-8")

    assert await uzum_timeslots.check_due(db, settings) == 0
    assert len(cabinet["calls"]) == 1


async def test_check_keeps_waiting_and_stops_on_uzum_limits(database, account, cabinet):
    db, settings = database
    waiting = await add_watch(db, account.id, invoice_id=1)
    limited = await add_watch(db, account.id, invoice_id=2)
    late = await add_watch(db, account.id, invoice_id=3)
    missed = await add_watch(db, account.id, invoice_id=4)
    cabinet["results"] = {1: {"outcome": "waiting", "nearest": ms(datetime(2026, 11, 14, 4, tzinfo=UTC))},
                          2: {"outcome": "stopped", "reason": "limit"},
                          3: {"outcome": "stopped", "reason": "too_late"},
                          4: {"outcome": "missed"}}

    assert await uzum_timeslots.check_due(db, settings) == 4

    assert len(cabinet["calls"]) == 1
    first = await load(db, waiting)
    assert first.status == "active" and first.message.endswith("Eng yaqin bo'sh slot: 14.11.2026 09:00.")
    assert (await load(db, limited)).status == "failed"
    assert "48 soatdan kam" in (await load(db, late)).message
    assert (await load(db, missed)).status == "active"


async def test_check_failure_keeps_watch_and_expired_dates_close_it(database, account, cabinet):
    db, settings = database
    active = await add_watch(db, account.id, invoice_id=10)
    expired = await add_watch(db, account.id, invoice_id=11, days=(-3, -1))
    cabinet["error"] = AppError("uzum_auth_timeout", "Uzum kirishi vaqtida tugamadi.", 408)

    assert await uzum_timeslots.check_due(db, settings) == 1

    assert [task["invoiceId"] for task in cabinet["calls"][0][2]["tasks"]] == [10]
    watch = await load(db, active)
    assert (watch.status, watch.message, watch.checks) == ("active", "Uzum kirishi vaqtida tugamadi.", 1)
    assert (await load(db, expired)).status == "failed"


async def test_cancel_during_check_is_kept_unless_slot_was_already_changed(database, account, cabinet, monkeypatch):
    db, settings = database
    stays = await add_watch(db, account.id, invoice_id=20)
    changed = await add_watch(db, account.id, invoice_id=21)
    cabinet["results"] = {20: {"outcome": "waiting"}, 21: {"outcome": "changed", "slot": ms(SLOT), "invoice": {}}}
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
