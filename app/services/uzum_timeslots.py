"""Uzum FBO yuk xati timeslotini kuzatish: tanlangan sanalarda slot bo'shasa, yuk xatini o'sha slotga ko'chiradi.

Uzum qoidalari: timeslot faqat «Создана» (CREATED) holatida, ko'pi bilan 2 marta (remainingAmountOfUpdates)
o'zgaradi; slotgacha 48 soatdan kam qolganda ko'chirish jarimaga tushishi mumkin.
"""
import asyncio
import logging
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import TASHKENT, utcnow
from app.config import Settings
from app.db import Database
from app.errors import AppError, not_found
from app.models import TimeslotWatch, User
from app.schemas import TimeslotWatchRequest
from app.services import mail, uzum_accounts, uzum_cabinet

HOUR_MS = 3_600_000
MAX_RANGE_DAYS = 60
MAX_ACTIVE_WATCHES = 20
MIN_CHECK_GAP_SECONDS = 30
# Bu xatolarda a'zoning qolgan yuk xatlari ham tekshirilmaydi (kirish imkoni yo'q).
LOGIN_ERRORS = {"uzum_login_invalid", "uzum_login_throttled"}
logger = logging.getLogger(__name__)


def local_today() -> date:
    return utcnow().astimezone(TASHKENT).date()


def from_ms(value: Any) -> datetime | None:
    return datetime.fromtimestamp(value / 1000, UTC) if isinstance(value, int | float) else None


def slot_label(moment: datetime | None) -> str:
    return moment.astimezone(TASHKENT).strftime("%d.%m.%Y %H:%M") if moment else ""


def range_label(start: date, end: date) -> str:
    return start.strftime("%d.%m.%Y") if start == end else f"{start:%d.%m.%Y} – {end:%d.%m.%Y}"


def watch_payload(watch: TimeslotWatch) -> dict[str, Any]:
    return {"id": str(watch.id), "shop_id": watch.shop_id, "invoice_id": watch.invoice_id,
            "invoice_number": watch.invoice_number, "stock_title": watch.stock_title,
            "date_from": watch.date_from.isoformat(), "date_to": watch.date_to.isoformat(),
            "dates": range_label(watch.date_from, watch.date_to), "status": watch.status,
            "message": watch.message, "checks": watch.checks,
            "current_slot": slot_label(watch.current_time_from), "new_slot": slot_label(watch.new_time_from),
            "last_checked_at": slot_label(watch.last_checked_at), "created_at": watch.created_at.isoformat()}


def to_ms(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return int((moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp() * 1000)
    return None


def tashkent_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, TASHKENT).date()


def summary(invoice: dict[str, Any]) -> dict[str, Any]:
    status = invoice.get("invoiceStatus") if isinstance(invoice.get("invoiceStatus"), dict) else {}
    stock = invoice.get("stock") if isinstance(invoice.get("stock"), dict) else {}
    slot = invoice.get("timeSlotReservation") if isinstance(invoice.get("timeSlotReservation"), dict) else {}
    remaining = invoice.get("remainingAmountOfUpdates")
    return {"id": invoice.get("id"), "number": str(invoice.get("invoiceNumber") or invoice.get("id"))[:64],
            "status": status.get("value"), "status_text": status.get("text"), "stock": str(stock.get("title") or "")[:200],
            "time_from": to_ms(slot.get("timeFrom")),
            "remaining": remaining if isinstance(remaining, int) and not isinstance(remaining, bool) else None}


def blocker(info: dict[str, Any], date_from: date, date_to: date, now: int, min_lead_ms: int) -> dict[str, Any] | None:
    """Ko'chirishga to'siq bo'lsa yakuniy natijani, aks holda None qaytaradi."""
    if info["status"] != "CREATED":
        return {"outcome": "stopped", "reason": "status", "detail": info["status_text"] or info["status"]}
    if info["time_from"] is None:
        return None
    if date_from <= tashkent_date(info["time_from"]) <= date_to:
        return {"outcome": "already"}
    if info["remaining"] == 0:
        return {"outcome": "stopped", "reason": "limit"}
    if info["time_from"] - now < min_lead_ms:
        return {"outcome": "stopped", "reason": "too_late"}
    return None


def slot_times(slots: list[Any]) -> list[tuple[int, dict[str, Any]]]:
    return [(ms, slot) for slot in slots if isinstance(slot, dict) and (ms := to_ms(slot.get("timeFrom"))) is not None]


def pick_slot(slots: list[Any], date_from: date, date_to: date, current_from: int | None, now: int) -> dict[str, Any] | None:
    """Oraliqdagi (Toshkent sanasi bo'yicha) eng erta bo'sh slot; joriy slot va o'tib ketganlar hisobga olinmaydi."""
    candidates = [(ms, slot) for ms, slot in slot_times(slots)
                  if ms > now and ms != current_from and date_from <= tashkent_date(ms) <= date_to]
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


async def check_invoice(cabinet: uzum_cabinet.Cabinet, watch: TimeslotWatch, settings: Settings, now: int) -> dict[str, Any]:
    base = f"/seller/shop/{watch.shop_id}"

    async def fetch_invoice() -> tuple[int, Any]:
        response = await cabinet.call("GET", f"{base}/invoice/{watch.invoice_id}")
        return response.status_code, uzum_cabinet.body(response)

    status, invoice = await fetch_invoice()
    if status != 200 or not isinstance(invoice, dict) or not invoice.get("id"):
        return {"outcome": "error", "detail": f"invoice_http_{status}"}
    info = summary(invoice)
    stop = blocker(info, watch.date_from, watch.date_to, now, settings.uzum_timeslot_min_lead_hours * HOUR_MS)
    if stop:
        return {**stop, "invoice": info}
    stock = invoice.get("stock") if isinstance(invoice.get("stock"), dict) else {}
    pool = stock.get("poolSource")
    response = await cabinet.call("POST", f"{base}/v2/invoice/time-slot/get",
                                  json={"invoiceIds": [invoice["id"]], "poolSource": pool, "timeFrom": now + HOUR_MS})
    if not response.is_success:
        return {"outcome": "error", "invoice": info, "detail": f"slots_http_{response.status_code}"}
    payload = uzum_cabinet.body(response)
    inner = payload.get("payload") if isinstance(payload, dict) else None
    slots = inner.get("timeSlots") if isinstance(inner, dict) else None
    slots = slots if isinstance(slots, list) else []
    nearest = min((ms for ms, _ in slot_times(slots)), default=None)
    slot = pick_slot(slots, watch.date_from, watch.date_to, info["time_from"], now)
    if slot is None:
        return {"outcome": "waiting", "invoice": info, "nearest": nearest}
    target = to_ms(slot["timeFrom"])
    if settings.uzum_timeslot_dry_run:
        return {"outcome": "dry_run", "invoice": info, "nearest": nearest, "slot": target}
    response = await cabinet.call("POST", f"{base}/v2/invoice/time-slot/set", json={
        "timeFrom": slot["timeFrom"], "invoiceIds": [invoice["id"]], "stockId": stock.get("id"), "poolSource": pool})
    if not response.is_success:
        code = uzum_cabinet.error_code(response)
        # time-slot-003: slotni boshqa sotuvchi oldinroq band qildi.
        return {"outcome": "missed" if code == "time-slot-003" else "error", "invoice": info, "nearest": nearest,
                "detail": f"set_http_{response.status_code}" + (f" {code}" if code else "")}
    status, after = await fetch_invoice()
    if status != 200 or not isinstance(after, dict):
        return {"outcome": "unverified", "invoice": info, "slot": target}
    info = summary(after)
    return {"outcome": "changed" if info["time_from"] == target else "unverified", "invoice": info, "slot": target}


async def list_invoices(db: Database, settings: Settings, user_id: UUID, shop_id: str) -> list[dict[str, Any]]:
    await uzum_accounts.ensure_shop(db, settings, user_id, shop_id)
    async with uzum_accounts.cabinet(db, settings, user_id) as cabinet:
        raw = await uzum_cabinet.list_invoices(cabinet, shop_id)
    invoices = []
    for info in map(summary, raw):
        if isinstance(info["id"], int) and not isinstance(info["id"], bool):
            invoices.append({"id": info["id"], "number": info["number"], "stock": info["stock"],
                             "current_slot": slot_label(from_ms(info["time_from"])), "remaining_updates": info["remaining"]})
    return invoices


async def create_watch(session: AsyncSession, user_id: UUID, payload: TimeslotWatchRequest) -> dict[str, Any]:
    if payload.date_from < local_today():
        raise AppError("timeslot_date_past", "Sana bugundan oldin bo'lmasin.", fields={"date_from": "Sanani tekshiring."})
    if payload.date_to < payload.date_from:
        raise AppError("timeslot_range_invalid", "Oraliq oxiri boshidan oldin bo'lmasin.", fields={"date_to": "Sanani tekshiring."})
    if (payload.date_to - payload.date_from).days > MAX_RANGE_DAYS:
        raise AppError("timeslot_range_invalid", f"Oraliq {MAX_RANGE_DAYS} kundan oshmasin.", fields={"date_to": "Sanani tekshiring."})
    active = await session.scalar(select(func.count()).select_from(TimeslotWatch).where(
        TimeslotWatch.user_id == user_id, TimeslotWatch.status == "active"))
    if active >= MAX_ACTIVE_WATCHES:
        raise AppError("timeslot_watch_limit", f"Bir vaqtda ko'pi bilan {MAX_ACTIVE_WATCHES} ta yuk xati kuzatiladi.", 409)
    watch = TimeslotWatch(user_id=user_id, shop_id=payload.shop_id, invoice_id=payload.invoice_id,
                          invoice_number=payload.invoice_number or str(payload.invoice_id),
                          stock_title=payload.stock_title, date_from=payload.date_from, date_to=payload.date_to,
                          message="Birinchi tekshiruv kutilmoqda.")
    try:
        async with session.begin_nested():
            session.add(watch)
    except IntegrityError:
        raise AppError("timeslot_watch_exists", "Bu yuk xati allaqachon kuzatilmoqda.", 409) from None
    return watch_payload(watch)


async def list_watches(session: AsyncSession, user_id: UUID) -> list[dict[str, Any]]:
    watches = await session.scalars(select(TimeslotWatch).where(TimeslotWatch.user_id == user_id)
                                    .order_by(TimeslotWatch.created_at.desc()).limit(50))
    return [watch_payload(watch) for watch in watches]


async def cancel_watch(session: AsyncSession, user_id: UUID, watch_id: UUID) -> dict[str, Any]:
    watch = await session.scalar(select(TimeslotWatch).where(
        TimeslotWatch.id == watch_id, TimeslotWatch.user_id == user_id).with_for_update())
    if not watch:
        raise not_found()
    if watch.status == "active":
        watch.status, watch.message, watch.updated_at = "cancelled", "Kuzatuv bekor qilindi.", utcnow()
    return watch_payload(watch)


def outcome_state(outcome: dict[str, Any], settings: Settings) -> tuple[str, str]:
    """Skript natijasini (holat, xabar) juftligiga aylantiradi; 'active' — kuzatuv davom etadi."""
    kind = outcome.get("outcome")
    slot = slot_label(from_ms(outcome.get("slot")))
    if kind == "changed":
        return "done", f"Timeslot {slot} ga ko'chirildi."
    if kind == "already":
        return "done", "Joriy timeslot tanlangan sanalar ichida, ko'chirish kerak emas."
    if kind == "unverified":
        return "failed", "Uzum so'rovni qabul qildi, lekin yangi timeslot tasdiqlanmadi. Kabinetni tekshiring."
    if kind == "stopped":
        reason = outcome.get("reason")
        if reason == "limit":
            return "failed", "Uzum'da bu yuk xati timeslotini ko'chirish limiti tugagan (ko'pi bilan 2 marta)."
        if reason == "too_late":
            hours = settings.uzum_timeslot_min_lead_hours
            return "failed", f"Joriy timeslotgacha {hours} soatdan kam qoldi: ko'chirish jarimaga tushishi mumkin, kuzatuv to'xtatildi."
        status = str(outcome.get("detail") or "noma'lum")[:40]
        return "failed", f"Yuk xati holati «{status}»: timeslot faqat «Создана» holatida o'zgaradi."
    if kind == "missed":
        return "active", "Bo'shagan slotni boshqa sotuvchi oldinroq band qildi, kuzatuv davom etmoqda."
    if kind == "dry_run":
        return "active", f"Sinov rejimi: yuk xati {slot} slotiga ko'chirilardi."
    if kind == "waiting":
        nearest = slot_label(from_ms(outcome.get("nearest")))
        return "active", "Tanlangan sanalarda bo'sh slot yo'q." + (f" Eng yaqin bo'sh slot: {nearest}." if nearest else "")
    detail = str(outcome.get("detail") or "noma'lum")[:120]
    return "active", f"Uzum xatosi ({detail}), keyingi tekshiruvda qayta uriniladi."


async def check_due(db: Database, settings: Settings) -> int:
    """Faol kuzatuvlarni har bir a'zoning o'z Uzum sessiyasida tekshiradi; tekshirilganlar sonini qaytaradi."""
    async with db.transaction() as session:
        expired = (await session.scalars(update(TimeslotWatch).where(
            TimeslotWatch.status == "active", TimeslotWatch.date_to < local_today(),
        ).values(status="failed", message="Tanlangan sanalar o'tib ketdi, mos slot bo'shamadi.",
                 updated_at=utcnow()).returning(TimeslotWatch))).all()
        watches = (await session.scalars(select(TimeslotWatch).where(TimeslotWatch.status == "active")
                                         .order_by(TimeslotWatch.last_checked_at.asc().nulls_first())
                                         .limit(MAX_ACTIVE_WATCHES * 2))).all()
    by_user: dict[UUID, list[TimeslotWatch]] = defaultdict(list)
    for watch in watches:
        by_user[watch.user_id].append(watch)
    finished = list(expired)
    for user_id, group in by_user.items():
        outcomes, failure = await _check_user(db, settings, user_id, group)
        finished += await _apply(db, settings, [watch.id for watch in group], outcomes, failure)
    await _notify(db, settings, finished)
    return len(watches)


async def _check_user(db: Database, settings: Settings, user_id: UUID,
                      group: list[TimeslotWatch]) -> tuple[dict[str, dict[str, Any]], str]:
    """A'zoning yuk xatlari uning o'z kabinet sessiyasida; kirib bo'lmasa olingan natijalar saqlanib, qolganiga sabab yoziladi."""
    outcomes: dict[str, dict[str, Any]] = {}
    try:
        async with uzum_accounts.cabinet(db, settings, user_id, background=True) as cabinet:
            for watch in group:
                try:
                    outcomes[str(watch.id)] = await check_invoice(cabinet, watch, settings, int(utcnow().timestamp() * 1000))
                except AppError as error:
                    if error.code in LOGIN_ERRORS:
                        raise
                    outcomes[str(watch.id)] = {"outcome": "error", "detail": error.code}
    except AppError as error:
        return outcomes, error.message
    return outcomes, "Tekshiruv natijasi olinmadi, keyingi tekshiruvda qayta uriniladi."


async def _apply(db: Database, settings: Settings, watch_ids: list[UUID], outcomes: dict[str, dict],
                 failure: str) -> list[TimeslotWatch]:
    finished, now = [], utcnow()
    async with db.transaction() as session:
        for watch_id in watch_ids:
            watch = await session.get(TimeslotWatch, watch_id, with_for_update=True)
            outcome = outcomes.get(str(watch_id))
            changed = bool(outcome) and outcome.get("outcome") == "changed"
            # Tekshiruv paytida bekor qilingan kuzatuv: faqat haqiqatda bajarilgan ko'chirish yoziladi.
            if not watch or (watch.status != "active" and not changed):
                continue
            watch.checks += 1
            watch.last_checked_at = watch.updated_at = now
            if not outcome:
                watch.message = failure[:300]
                continue
            invoice = outcome.get("invoice") if isinstance(outcome.get("invoice"), dict) else {}
            if invoice:
                watch.current_time_from = from_ms(invoice.get("time_from"))
                watch.invoice_number = str(invoice.get("number") or watch.invoice_number)[:64]
                watch.stock_title = str(invoice.get("stock") or watch.stock_title)[:200]
            status, message = outcome_state(outcome, settings)
            watch.message = message[:300]
            if status != "active":
                watch.status = status
                watch.new_time_from = from_ms(outcome.get("slot")) if changed else None
                finished.append(watch)
    return finished


async def _notify(db: Database, settings: Settings, finished: list[TimeslotWatch]) -> None:
    if not finished:
        return
    async with db.transaction() as session:
        emails = dict((await session.execute(select(User.id, User.email_normalized).where(
            User.id.in_({watch.user_id for watch in finished})))).all())
    for watch in finished:
        if watch.user_id not in emails:
            continue
        subject = f"yuk xati {watch.invoice_number} — " + (
            "timeslot ko'chirildi" if watch.new_time_from else "kuzatuv yakunlandi")
        body = f"{watch.message}\n\nKuzatuvlar: {settings.app_base_url.rstrip('/')}/timeslots"
        try:
            await mail.send_notice(settings, emails[watch.user_id], subject, body)
        except AppError:
            logger.warning("timeslot_notice_failed watch=%s", watch.id)


async def watch_loop(db: Database, settings: Settings, wake: asyncio.Event) -> None:
    """Web jarayonida ishlaydi: 'Hozir tekshirish' shu jarayondagi `wake` hodisasi orqali uyg'otadi."""
    loop = asyncio.get_running_loop()
    while True:
        wake.clear()
        started = loop.time()
        if not settings.maintenance_mode:
            try:
                await check_due(db, settings)
            except Exception as error:
                logger.error("timeslot_watch_failed type=%s", type(error).__name__)
        try:
            await asyncio.wait_for(wake.wait(), timeout=settings.uzum_timeslot_interval_seconds)
        except TimeoutError:
            pass
        await asyncio.sleep(max(0.0, MIN_CHECK_GAP_SECONDS - (loop.time() - started)))
