"""Uzum FBO yuk xati timeslotini kuzatish: tanlangan sanalarda slot bo'shasa, yuk xatini o'sha slotga ko'chiradi."""
import asyncio
import json
import logging
import tempfile
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import TASHKENT, utcnow
from app.config import ROOT, Settings
from app.db import Database
from app.errors import AppError, not_found
from app.models import TimeslotWatch, User
from app.schemas import TimeslotWatchRequest
from app.services import mail, uzum_seller

SCRIPT_TIMEOUT_SECONDS = 240
MAX_RANGE_DAYS = 60
MAX_ACTIVE_WATCHES = 20
MIN_CHECK_GAP_SECONDS = 30
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


async def ensure_shop(settings: Settings, shop_id: str) -> None:
    if shop_id not in {shop["id"] for shop in await uzum_seller.list_shops(settings)}:
        raise AppError("uzum_shop_invalid", "Tanlangan do'kon API ro'yxatida topilmadi.", 422)


async def _run(settings: Settings, mode: str, shop_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    runtime = ROOT / "var"
    runtime.mkdir(parents=True, exist_ok=True)
    extra = {"UZUM_DRY_RUN": "1"} if settings.uzum_timeslot_dry_run else {}
    with tempfile.TemporaryDirectory(prefix="uzum-timeslots-", dir=runtime) as temporary:
        tasks = Path(temporary) / "tasks.json"
        tasks.write_text(json.dumps(payload), encoding="utf-8")
        returncode, result = await uzum_seller.run_script(settings, mode, tasks, shop_id, SCRIPT_TIMEOUT_SECONDS, extra)
    if returncode != 0 or result.get("status") != "ok":
        if result.get("code") == "auth_timeout":
            raise AppError("uzum_auth_timeout", "Uzum kirishi, CAPTCHA yoki SMS tasdig'i vaqtida tugamadi.", 408)
        raise AppError("uzum_invoices_unavailable", "Uzum kabinetidan yuk xatlari ma'lumotini olib bo'lmadi.", 502)
    return result


async def list_invoices(settings: Settings, shop_id: str) -> list[dict[str, Any]]:
    await ensure_shop(settings, shop_id)
    result = await _run(settings, "invoices", shop_id, {})
    invoices = []
    for item in result.get("invoices") or []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            continue
        start = from_ms(item.get("timeFrom"))
        remaining = item.get("remainingUpdates")
        invoices.append({"id": item["id"], "number": str(item.get("number") or item["id"])[:64],
                         "stock": str(item.get("stock") or "")[:200], "current_slot": slot_label(start),
                         "remaining_updates": remaining if isinstance(remaining, int) else None})
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
    """Faol kuzatuvlarni do'konlar bo'yicha bitta brauzer sessiyasida tekshiradi; tekshirilganlar sonini qaytaradi."""
    async with db.transaction() as session:
        expired = (await session.scalars(update(TimeslotWatch).where(
            TimeslotWatch.status == "active", TimeslotWatch.date_to < local_today(),
        ).values(status="failed", message="Tanlangan sanalar o'tib ketdi, mos slot bo'shamadi.",
                 updated_at=utcnow()).returning(TimeslotWatch))).all()
        watches = (await session.scalars(select(TimeslotWatch).where(TimeslotWatch.status == "active")
                                         .order_by(TimeslotWatch.last_checked_at.asc().nulls_first())
                                         .limit(MAX_ACTIVE_WATCHES * 2))).all()
    by_shop: dict[str, list[TimeslotWatch]] = defaultdict(list)
    for watch in watches:
        by_shop[watch.shop_id].append(watch)
    finished = list(expired)
    for shop_id, group in by_shop.items():
        tasks = [{"watchId": str(watch.id), "invoiceId": watch.invoice_id, "dateFrom": watch.date_from.isoformat(),
                  "dateTo": watch.date_to.isoformat()} for watch in group]
        try:
            result = await _run(settings, "timeslots", shop_id,
                                {"tasks": tasks, "minLeadHours": settings.uzum_timeslot_min_lead_hours})
            outcomes = {str(item.get("watchId")): item for item in result.get("results") or [] if isinstance(item, dict)}
            failure = "Uzum javobida bu yuk xati topilmadi."
        except AppError as error:
            outcomes, failure = {}, error.message
        finished += await _apply(db, settings, [watch.id for watch in group], outcomes, failure)
    await _notify(db, settings, finished)
    return len(watches)


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
                watch.current_time_from = from_ms(invoice.get("timeFrom"))
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
    """Web jarayonida ishlaydi: Uzum brauzer profili va _browser_lock shu jarayonda."""
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
