"""Uzum sharhlariga javob: har bir sharhga AI (ijodiy) yoki shablon orqali; avtomatik rejimda fon tekshiruvi o'zi yozadi.

Uzum javoblarni moderatsiyadan o'tkazadi (CREATED → PUBLISHED yoki DECLINED). Mijoz ismi va sharh matni
bazada saqlanmaydi — ular har safar kabinetdan olinadi; saqlanadigani faqat yozilgan javob.
"""
import asyncio
import logging
import re
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import replies
from app.ai.provider import ProviderFailure
from app.clock import utcnow
from app.config import Settings
from app.db import Database
from app.errors import AppError, not_found
from app.models import ReviewReply, ReviewSettings, ReviewShopSettings, ReviewTemplate
from app.schemas import ReviewSettingsRequest, ReviewTemplateRequest
from app.services import budget, quotas, uzum_accounts, uzum_cabinet
from app.services.uzum_timeslots import from_ms, slot_label

MAX_REPLY_CHARS = replies.MAX_REPLY_CHARS
MAX_TEMPLATES = 30
# Avtomatik rejim har tekshiruvda ko'pi bilan 100 ta javobsiz sharhni ko'radi va 20 tasiga javob yozadi.
AUTO_PAGES = 5
MAX_AUTO_PER_RUN = 20
MAX_ATTEMPTS = 3
# A'zo qo'lda tayyorlayotgan javobga avtomatik rejim shu vaqt tegmaydi.
DRAFT_HOLD = timedelta(hours=6)
# "Yuborilmoqda" holatida qolib ketgan javob (jarayon uzilgan) shundan keyin qayta yuborilishi mumkin.
SENDING_STALE = timedelta(minutes=10)
MODERATION_DAYS = 14
LOGIN_ERRORS = {"uzum_login_invalid", "uzum_login_throttled", "uzum_login_failed"}
PLACEHOLDER = re.compile(r"\{(ism|mahsulot|dokon)\}")
ANY_PLACEHOLDER = re.compile(r"\{[^{}]*\}")
UZ_CYRILLIC = set("ўқғҳЎҚҒҲ")
# Uzum ismi yo'q mijozni «Безымянный», anonimni «***» deb qaytaradi — bunday "ism" bilan murojaat qilinmaydi.
NO_NAMES = {"безымянный", "безымянная", "без имени", "аноним", "anonim", "anonymous", "noma'lum"}
# Ism bo'lmasa {ism} vergul va murojaat so'zi bilan birga olib tashlanadi: «Assalomu alaykum, {ism}!» → «Assalomu alaykum!».
NO_NAME = re.compile(r"\s*,\s*\{ism\}|(?:\b(?:hurmatli|qadrli|уважаем\w*|дорог\w*)\s+)?\{ism\}\s*,?\s*", re.IGNORECASE)
METHOD_LABELS = {"ai": "AI", "template": "Shablon", "manual": "Qo'lda"}
# Avtomatik rejim usullari (umumiy va do'kon sozlamasi uchun).
AUTO_METHOD_LABELS = {"ai": "AI", "template": "Shablon", "skip": "Javob yozilmasin"}
STATUS_LABELS = {"draft": "Loyiha", "sending": "Yuborilmoqda", "sent": "Yuborildi", "failed": "Xato"}
MODERATION_LABELS = {"CREATED": "Moderatsiyada", "EDITED": "Moderatsiyada", "PUBLISHED": "E'lon qilindi",
                     "DECLINED": "Uzum rad etdi"}
DEFAULT_TEMPLATES = [
    ("Ijobiy sharh", 4, 5,
     "Assalomu alaykum, {ism}! Yuqori bahoingiz uchun katta rahmat. Xaridingiz sizga yoqqanidan xursandmiz — yana kutib qolamiz!",
     "Здравствуйте, {ism}! Большое спасибо за высокую оценку. Рады, что покупка вам понравилась — будем ждать вас снова!"),
    ("Ijobiy sharh (2)", 4, 5,
     "Rahmat, {ism}! Bizni tanlaganingiz va fikr qoldirganingiz biz uchun muhim. Hurmat bilan, «{dokon}» jamoasi.",
     "Спасибо, {ism}! Нам очень приятно, что вы выбрали нас и оставили отзыв. С уважением, команда «{dokon}»."),
    ("O'rtacha sharh", 3, 3,
     "Assalomu alaykum, {ism}! Fikringiz uchun rahmat. Xaridingiz to'liq kutganingizdek bo'lmaganidan afsusdamiz — mulohazangizni albatta inobatga olamiz.",
     "Здравствуйте, {ism}! Спасибо за отзыв. Жаль, что покупка не полностью оправдала ожидания — мы обязательно учтём ваше мнение."),
    ("Salbiy sharh", 1, 2,
     "Assalomu alaykum, {ism}! Noqulaylik uchun uzr so'raymiz. Fikringiz biz uchun juda muhim — sifatni yaxshilash ustida albatta ishlaymiz.",
     "Здравствуйте, {ism}! Приносим извинения за неудобства. Ваш отзыв очень важен для нас — мы обязательно поработаем над качеством."),
]
logger = logging.getLogger(__name__)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def review_text(review: dict[str, Any]) -> str:
    return "\n".join(str(review.get(key) or "").strip() for key in ("content", "pros", "cons")).strip()


def review_language(review: dict[str, Any]) -> str:
    """Sharh tili: kirillcha va o'zbekcha harflarsiz bo'lsa rus, aks holda o'zbek (matnsiz sharh ham o'zbekcha)."""
    text = review_text(review)
    letters = [char for char in text if char.isalpha()]
    cyrillic = sum("Ѐ" <= char <= "ӿ" for char in letters)
    if not letters or cyrillic * 2 < len(letters) or UZ_CYRILLIC & set(text):
        return "uz"
    return "ru"


def customer_name(review: dict[str, Any]) -> str:
    name = " ".join(str(review.get("customerName") or "").split())[:40]
    if review.get("anonymous") or name.casefold() in NO_NAMES or not any(char.isalpha() for char in name):
        return ""
    return name


def shop_of(review: dict[str, Any]) -> str:
    return str(_dict(review.get("shop")).get("id") or "")


def rating_of(review: dict[str, Any]) -> int:
    rating = review.get("rating")
    return rating if isinstance(rating, int) and not isinstance(rating, bool) and 1 <= rating <= 5 else 0


def render(template: ReviewTemplate, review: dict[str, Any], language: str) -> str:
    values = {"ism": customer_name(review),
              "mahsulot": str(_dict(review.get("product")).get("productTitle") or "").strip()[:120],
              "dokon": str(_dict(review.get("shop")).get("title") or "").strip()[:80]}
    text = template.text_ru if language == "ru" else template.text_uz
    if not values["ism"]:
        text = NO_NAME.sub("", text)
    text = " ".join(PLACEHOLDER.sub(lambda match: values[match.group(1)], text).split())
    text = re.sub(r"\s+([!?.,])", r"\1", text).lstrip(" !?.,")
    return (text[:1].upper() + text[1:])[:MAX_REPLY_CHARS]


def pick_template(templates: list[ReviewTemplate], rating: int, review_id: int) -> ReviewTemplate | None:
    """Reytingga mos shablonlardan biri; bir xil javoblar ketma-ket chiqmasligi uchun sharh raqami bo'yicha almashadi."""
    matching = sorted((item for item in templates if item.rating_min <= rating <= item.rating_max),
                      key=lambda item: item.created_at)
    return matching[review_id % len(matching)] if matching else None


def template_view(template: ReviewTemplate) -> dict[str, Any]:
    stars = (f"{template.rating_min}★" if template.rating_min == template.rating_max
             else f"{template.rating_min}–{template.rating_max}★")
    return {"id": str(template.id), "name": template.name, "rating_min": template.rating_min,
            "rating_max": template.rating_max, "stars": stars, "text_uz": template.text_uz, "text_ru": template.text_ru}


def settings_view(settings: ReviewSettings) -> dict[str, Any]:
    return {"auto_reply": settings.auto_reply, "text_method": settings.text_method,
            # Eski sharhlarga javob faqat a'zo o'zi belgilasa: avtomatik rejim hali yoqilmagan bo'lsa belgi o'chiq.
            "empty_method": settings.empty_method, "backlog": settings.auto_reply and settings.since is None,
            "since": slot_label(settings.since), "last_run_at": slot_label(settings.last_run_at),
            "last_message": settings.last_message}


def shops_view(shops: list[dict[str, str]], rules: dict[str, ReviewShopSettings]) -> list[dict[str, Any]]:
    """A'zoning do'konlari va har biriga tanlangan usul ("default" — umumiy sozlama)."""
    views = []
    for shop in shops:
        rule = rules.get(shop["id"])
        text = (rule.text_method if rule else None) or "default"
        empty = (rule.empty_method if rule else None) or "default"
        views.append({"id": shop["id"], "name": shop["name"], "text_method": text, "empty_method": empty,
                      "custom": text != "default" or empty != "default"})
    return views


def method_for(current: ReviewSettings, rules: dict[str, ReviewShopSettings], review: dict[str, Any]) -> str:
    """Do'kon uchun alohida usul tanlangan bo'lsa o'sha, aks holda umumiy sozlama."""
    rule = rules.get(shop_of(review))
    if review_text(review):
        return (rule.text_method if rule else None) or current.text_method
    return (rule.empty_method if rule else None) or current.empty_method


def reply_view(row: ReviewReply) -> dict[str, Any]:
    return {"id": str(row.id), "review_id": row.review_id, "product": row.product_title, "shop": row.shop_title,
            "rating": row.rating, "language": row.language, "method": row.method,
            "method_label": METHOD_LABELS[row.method], "text": row.text, "status": row.status,
            "status_label": STATUS_LABELS[row.status], "source": row.source, "moderation": row.moderation,
            "moderation_label": MODERATION_LABELS.get(row.moderation, ""), "error": row.error,
            "updated_at": slot_label(row.updated_at), "sent_at": slot_label(row.sent_at)}


def review_view(review: dict[str, Any], row: ReviewReply | None) -> dict[str, Any]:
    product, shop = _dict(review.get("product")), _dict(review.get("shop"))
    photos = review.get("photos")
    return {"id": review["reviewId"], "date": slot_label(from_ms(review.get("dateCreated"))),
            "rating": rating_of(review), "customer": customer_name(review) or "Anonim xaridor",
            "product": str(product.get("productTitle") or "")[:300], "sku": str(product.get("skuTitle") or "")[:120],
            "shop": str(shop.get("title") or "")[:200], "content": str(review.get("content") or "")[:3000],
            "pros": str(review.get("pros") or "")[:1500], "cons": str(review.get("cons") or "")[:1500],
            "photos": len(photos) if isinstance(photos, list) else 0, "language": review_language(review),
            "reply": reply_view(row) if row else None}


def check_placeholders(payload: ReviewTemplateRequest) -> None:
    for field in ("text_uz", "text_ru"):
        unknown = [item for item in ANY_PLACEHOLDER.findall(getattr(payload, field)) if not PLACEHOLDER.fullmatch(item)]
        if unknown:
            raise AppError("review_template_invalid", f"Noma'lum o'rinbosar: {unknown[0][:30]}. Faqat {{ism}}, {{mahsulot}}, {{dokon}}.",
                           fields={field: "O'rinbosarlarni tekshiring."})


async def ensure_settings(session: AsyncSession, user_id: UUID) -> ReviewSettings:
    """Birinchi murojaatda sozlama yaratiladi va namunaviy shablonlar qo'shiladi."""
    created = await session.scalar(insert(ReviewSettings).values(user_id=user_id).on_conflict_do_nothing()
                                   .returning(ReviewSettings.user_id))
    if created:
        now = utcnow()
        for index, (name, low, high, text_uz, text_ru) in enumerate(DEFAULT_TEMPLATES):
            session.add(ReviewTemplate(user_id=user_id, name=name, rating_min=low, rating_max=high, text_uz=text_uz,
                                       text_ru=text_ru, created_at=now + timedelta(microseconds=index)))
        await session.flush()
    return await session.get(ReviewSettings, user_id, populate_existing=True)


async def templates_of(session: AsyncSession, user_id: UUID) -> list[ReviewTemplate]:
    return list(await session.scalars(select(ReviewTemplate).where(ReviewTemplate.user_id == user_id)
                                      .order_by(ReviewTemplate.rating_max.desc(), ReviewTemplate.created_at)))


async def shop_rules(session: AsyncSession, user_id: UUID) -> dict[str, ReviewShopSettings]:
    return {rule.shop_id: rule for rule in await session.scalars(
        select(ReviewShopSettings).where(ReviewShopSettings.user_id == user_id))}


async def history(session: AsyncSession, user_id: UUID) -> list[dict[str, Any]]:
    rows = await session.scalars(select(ReviewReply).where(ReviewReply.user_id == user_id, ReviewReply.status != "draft")
                                 .order_by(ReviewReply.updated_at.desc()).limit(50))
    return [reply_view(row) for row in rows]


async def update_shop_rules(session: AsyncSession, user_id: UUID, payload: ReviewSettingsRequest) -> None:
    """Ro'yxatdagi do'konlar: "default" bo'lsa alohida sozlama o'chiriladi, aks holda saqlanadi."""
    account = await uzum_accounts.find(session, user_id)
    known = {shop["id"] for shop in account.shops_json} if account else set()
    if any(item.shop_id not in known for item in payload.shops):
        raise AppError("uzum_shop_invalid", "Tanlangan do'kon sizning Uzum do'konlaringiz orasida yo'q.", 422)
    custom = [item for item in payload.shops if item.text_method != "default" or item.empty_method != "default"]
    defaults = [item.shop_id for item in payload.shops if item.text_method == item.empty_method == "default"]
    if defaults:
        await session.execute(delete(ReviewShopSettings).where(
            ReviewShopSettings.user_id == user_id, ReviewShopSettings.shop_id.in_(defaults)))
    for item in custom:
        values = {"text_method": None if item.text_method == "default" else item.text_method,
                  "empty_method": None if item.empty_method == "default" else item.empty_method, "updated_at": utcnow()}
        await session.execute(insert(ReviewShopSettings).values(user_id=user_id, shop_id=item.shop_id, **values)
                              .on_conflict_do_update(index_elements=["user_id", "shop_id"], set_=values))


async def update_settings(session: AsyncSession, user_id: UUID, payload: ReviewSettingsRequest) -> dict[str, Any]:
    current = await ensure_settings(session, user_id)
    await update_shop_rules(session, user_id, payload)
    if payload.auto_reply:
        # Yoqilgan paytdan keyingi sharhlar; "eski sharhlar ham" tanlansa — chegara yo'q.
        current.since = None if payload.backlog else (current.since if current.auto_reply and current.since else utcnow())
    current.auto_reply, current.text_method, current.empty_method = payload.auto_reply, payload.text_method, payload.empty_method
    current.updated_at = utcnow()
    return settings_view(current)


async def create_template(session: AsyncSession, user_id: UUID, payload: ReviewTemplateRequest) -> dict[str, Any]:
    check_placeholders(payload)
    count = await session.scalar(select(func.count()).select_from(ReviewTemplate).where(ReviewTemplate.user_id == user_id))
    if count >= MAX_TEMPLATES:
        raise AppError("review_template_limit", f"Ko'pi bilan {MAX_TEMPLATES} ta shablon saqlanadi.", 409)
    template = ReviewTemplate(user_id=user_id, **payload.model_dump())
    session.add(template)
    await session.flush()
    return template_view(template)


async def owned_template(session: AsyncSession, user_id: UUID, template_id: UUID) -> ReviewTemplate:
    template = await session.scalar(select(ReviewTemplate).where(
        ReviewTemplate.id == template_id, ReviewTemplate.user_id == user_id).with_for_update())
    if not template:
        raise not_found()
    return template


async def update_template(session: AsyncSession, user_id: UUID, template_id: UUID,
                          payload: ReviewTemplateRequest) -> dict[str, Any]:
    check_placeholders(payload)
    template = await owned_template(session, user_id, template_id)
    for key, value in payload.model_dump().items():
        setattr(template, key, value)
    template.updated_at = utcnow()
    return template_view(template)


async def delete_template(session: AsyncSession, user_id: UUID, template_id: UUID) -> None:
    await session.delete(await owned_template(session, user_id, template_id))


async def list_pending(db: Database, settings: Settings, user_id: UUID, page: int) -> dict[str, Any]:
    """Kabinetdagi javobsiz sharhlar (jonli) va ular uchun tayyorlangan javoblar."""
    async with uzum_accounts.cabinet(db, settings, user_id) as cabinet:
        items = await uzum_cabinet.list_reviews(cabinet, page)
    items = [item for item in items if isinstance(item.get("reviewId"), int)]
    async with db.transaction() as session:
        rows = {row.review_id: row for row in await session.scalars(select(ReviewReply).where(
            ReviewReply.user_id == user_id, ReviewReply.review_id.in_([item["reviewId"] for item in items])))}
        templates = [template_view(item) for item in await templates_of(session, user_id)]
    return {"reviews": [review_view(item, rows.get(item["reviewId"])) for item in items], "templates": templates,
            "page": page, "has_more": len(items) == uzum_cabinet.REVIEW_PAGE_SIZE}


async def ai_reply(db: Database, settings: Settings, user_id: UUID, review: dict[str, Any], language: str) -> str:
    """AI javobi: a'zoning kunlik limiti va umumiy kunlik budjet zaxirasi bilan; chaqirilmagan urinish limitni qaytaradi."""
    if settings.ai_provider == "disabled":
        raise AppError("ai_not_configured", "AI xizmati ulanmagan. Shablondan foydalaning.", 503)
    async with db.transaction() as session:
        _, daily = await quotas.rows(session, user_id, settings)
        if daily.reply_ai_calls >= settings.review_ai_daily_limit:
            raise AppError("review_ai_limit", f"Bugungi AI javoblar limiti ({settings.review_ai_daily_limit} ta) tugadi. "
                                              "Shablondan foydalaning.", 429)
        daily.reply_ai_calls += 1
        day = await budget.reserve_reply(session, settings)
    result, called = None, True
    try:
        result = await replies.generate(settings, review, language)
        return replies.parse(result)
    except ProviderFailure as failure:
        called = failure.called
        raise AppError("review_ai_failed", "AI javob yoza olmadi. Qayta urinib ko'ring yoki shablon tanlang.", 502) from None
    except ValueError:
        raise AppError("review_ai_failed", "AI javobi talablarga mos kelmadi. Qayta urinib ko'ring yoki shablon tanlang.", 502) from None
    finally:
        async with db.transaction() as session:
            await budget.settle_reply(session, settings, day, result, called)
            if not called:
                _, daily = await quotas.rows(session, user_id, settings)
                daily.reply_ai_calls = max(0, daily.reply_ai_calls - 1)


async def compose(db: Database, settings: Settings, user_id: UUID, review: dict[str, Any], method: str,
                  template_id: UUID | None = None) -> tuple[str, str, ReviewTemplate | None]:
    """(matn, til, shablon) — shablon tanlanmagan bo'lsa reytingga mosi olinadi."""
    language = review_language(review)
    if method == "ai":
        return await ai_reply(db, settings, user_id, review, language), language, None
    async with db.transaction() as session:
        if template_id:
            template = await session.scalar(select(ReviewTemplate).where(
                ReviewTemplate.id == template_id, ReviewTemplate.user_id == user_id))
        else:
            template = pick_template(await templates_of(session, user_id), rating_of(review), review["reviewId"])
    if not template:
        raise AppError("review_template_missing", f"{rating_of(review)}★ sharhga mos shablon yo'q. Shablon qo'shing.", 409)
    return render(template, review, language), language, template


def _meta(row: ReviewReply, review: dict[str, Any]) -> None:
    row.product_title = str(_dict(review.get("product")).get("productTitle") or "")[:300]
    row.shop_title = str(_dict(review.get("shop")).get("title") or "")[:200]
    row.rating = rating_of(review)


def busy(row: ReviewReply | None) -> bool:
    return bool(row) and (row.status == "sent" or (row.status == "sending" and utcnow() - row.updated_at < SENDING_STALE))


async def _locked(session: AsyncSession, user_id: UUID, review_id: int) -> ReviewReply | None:
    return await session.scalar(select(ReviewReply).where(
        ReviewReply.user_id == user_id, ReviewReply.review_id == review_id).with_for_update())


async def _upsert(db: Database, user_id: UUID, review: dict[str, Any], update: dict[str, Any]) -> ReviewReply:
    """Javob qatorini qulflab yangilaydi; javob allaqachon yuborilgan yoki yuborilayotgan bo'lsa 409."""
    try:
        async with db.transaction() as session:
            row = await _locked(session, user_id, review["reviewId"])
            if busy(row):
                raise AppError("review_already_replied", "Bu sharhga javob allaqachon yuborilgan.", 409)
            if row is None:
                row = ReviewReply(user_id=user_id, review_id=review["reviewId"], attempts=0)
                session.add(row)
            _meta(row, review)
            row.updated_at, row.error = utcnow(), ""
            for key, value in update.items():
                setattr(row, key, value)
            if row.status in {"sending", "failed"}:
                row.attempts = (row.attempts or 0) + 1
            await session.flush()
            return row
    except IntegrityError:
        raise AppError("review_already_replied", "Bu sharhga javob allaqachon yuborilmoqda.", 409) from None


async def _finish(db: Database, row_id: UUID, error: AppError | None = None) -> ReviewReply:
    async with db.transaction() as session:
        row = await session.get(ReviewReply, row_id, with_for_update=True)
        row.status, row.updated_at = ("failed" if error else "sent"), utcnow()
        row.error = error.message[:300] if error else ""
        if not error:
            row.sent_at, row.moderation = row.updated_at, "CREATED"
        return row


async def _send(db: Database, cabinet: uzum_cabinet.Cabinet, user_id: UUID, review: dict[str, Any],
                update: dict[str, Any]) -> ReviewReply:
    row = await _upsert(db, user_id, review, {**update, "status": "sending"})
    try:
        await uzum_cabinet.create_reply(cabinet, review["reviewId"], update["text"])
    except AppError as error:
        await _finish(db, row.id, error)
        raise
    return await _finish(db, row.id)


async def fetch_unanswered(cabinet: uzum_cabinet.Cabinet, review_id: int) -> dict[str, Any]:
    review = await uzum_cabinet.get_review(cabinet, review_id)
    if review.get("reply"):
        raise AppError("review_already_replied", "Bu sharhga Uzum'da javob berilgan.", 409)
    return review


async def draft(db: Database, settings: Settings, user_id: UUID, review_id: int, method: str,
                template_id: UUID | None) -> dict[str, Any]:
    async with uzum_accounts.cabinet(db, settings, user_id) as cabinet:
        review = await fetch_unanswered(cabinet, review_id)
    text, language, template = await compose(db, settings, user_id, review, method, template_id)
    row = await _upsert(db, user_id, review, {"text": text, "language": language, "method": method,
                                              "template_id": template.id if template else None,
                                              "status": "draft", "source": "manual"})
    return reply_view(row)


async def send(db: Database, settings: Settings, user_id: UUID, review_id: int, text: str) -> dict[str, Any]:
    """Qo'lda yuborish: loyiha bo'lsa uning usuli saqlanadi (tahrirlangan bo'lsa ham), bo'lmasa 'manual'."""
    text = " ".join(text.split())
    async with uzum_accounts.cabinet(db, settings, user_id) as cabinet:
        review = await fetch_unanswered(cabinet, review_id)
        async with db.transaction() as session:
            existing = await session.scalar(select(ReviewReply).where(
                ReviewReply.user_id == user_id, ReviewReply.review_id == review_id))
        method = existing.method if existing else "manual"
        row = await _send(db, cabinet, user_id, review, {
            "text": text, "language": existing.language if existing else review_language(review), "method": method,
            "template_id": existing.template_id if existing else None, "source": "manual"})
    return reply_view(row)


def eligible(row: ReviewReply | None) -> bool:
    if row is None:
        return True
    if row.status == "draft":
        return utcnow() - row.updated_at > DRAFT_HOLD
    if row.status == "sent":
        return False
    return row.attempts < MAX_ATTEMPTS and not busy(row)


async def _auto_compose(db: Database, settings: Settings, user_id: UUID, review: dict[str, Any],
                        method: str) -> tuple[str, str, str, ReviewTemplate | None]:
    """Avtomatik rejimda AI ishlamasa (limit, budjet, xato) javob mos shablondan yoziladi."""
    if method == "ai":
        try:
            text, language, _ = await compose(db, settings, user_id, review, "ai")
            return text, language, "ai", None
        except AppError as error:
            logger.info("review_ai_fallback code=%s", error.code)
    text, language, template = await compose(db, settings, user_id, review, "template")
    return text, language, "template", template


async def _sync_moderation(db: Database, cabinet: uzum_cabinet.Cabinet, user_id: UUID) -> None:
    """Yuborilgan javoblarning Uzum moderatsiyasi holatini «javobli» ro'yxatdan yangilaydi."""
    async with db.transaction() as session:
        pending = set(await session.scalars(select(ReviewReply.review_id).where(
            ReviewReply.user_id == user_id, ReviewReply.status == "sent",
            ReviewReply.moderation.in_(["", "CREATED", "EDITED"]),
            ReviewReply.sent_at > utcnow() - timedelta(days=MODERATION_DAYS))))
    if not pending:
        return
    found: dict[int, str] = {}
    for page in range(3):
        items = await uzum_cabinet.list_reviews(cabinet, page, "WITH_REPLY")
        found |= {item["reviewId"]: item["replyStatus"] for item in items
                  if item.get("reviewId") in pending and item.get("replyStatus") in MODERATION_LABELS}
        if len(items) < uzum_cabinet.REVIEW_PAGE_SIZE or pending <= set(found):
            break
    if found:
        async with db.transaction() as session:
            for row in await session.scalars(select(ReviewReply).where(
                    ReviewReply.user_id == user_id, ReviewReply.review_id.in_(found)).with_for_update()):
                row.moderation = found[row.review_id]


async def _run_user(db: Database, settings: Settings, user_id: UUID) -> str:
    async with db.transaction() as session:
        current = await session.get(ReviewSettings, user_id)
        rules = await shop_rules(session, user_id)
    if not current or not current.auto_reply:
        return ""
    since_ms = int(current.since.timestamp() * 1000) if current.since else None
    sent = failed = skipped = 0
    async with uzum_accounts.cabinet(db, settings, user_id, background=True) as cabinet:
        reviews: list[dict[str, Any]] = []
        for page in range(AUTO_PAGES):
            items = await uzum_cabinet.list_reviews(cabinet, page)
            reviews += [item for item in items if isinstance(item.get("reviewId"), int) and rating_of(item)]
            if len(items) < uzum_cabinet.REVIEW_PAGE_SIZE:
                break
        if since_ms is not None:
            reviews = [item for item in reviews if (item.get("dateCreated") or 0) >= since_ms]
        async with db.transaction() as session:
            rows = {row.review_id: row for row in await session.scalars(select(ReviewReply).where(
                ReviewReply.user_id == user_id, ReviewReply.review_id.in_([item["reviewId"] for item in reviews])))}
        queue = []
        for review in reviews:
            method = method_for(current, rules, review)
            if not eligible(rows.get(review["reviewId"])):
                continue
            if method == "skip":
                skipped += 1
            else:
                queue.append((review, method))
        for review, method in queue[:MAX_AUTO_PER_RUN]:
            try:
                text, language, used, template = await _auto_compose(db, settings, user_id, review, method)
            except AppError as error:
                # Javob yozilmadi: xato saqlanadi, aks holda har tekshiruvda AI qayta chaqirilardi.
                failed += 1
                await _upsert(db, user_id, review, {"text": "", "language": review_language(review), "method": "template",
                                                    "template_id": None, "status": "failed", "source": "auto",
                                                    "error": error.message[:300]})
                continue
            try:
                await _send(db, cabinet, user_id, review, {"text": text, "language": language, "method": used,
                                                           "template_id": template.id if template else None,
                                                           "source": "auto"})
                sent += 1
            except AppError as error:
                if error.code in LOGIN_ERRORS:
                    raise
                failed += 1
                logger.warning("review_auto_reply_failed code=%s", error.code)
        await _sync_moderation(db, cabinet, user_id)
    parts = [f"{sent} ta javob yuborildi"] + ([f"{failed} tasida xato"] if failed else []) + (
        [f"{skipped} ta sharhga sozlama bo'yicha javob yozilmadi"] if skipped else [])
    return ", ".join(parts) + "."


async def run_due(db: Database, settings: Settings) -> int:
    """Avtomatik rejim yoqilgan a'zolar; har biri o'z Uzum sessiyasida. Ishlangan a'zolar sonini qaytaradi."""
    async with db.transaction() as session:
        user_ids = list(await session.scalars(select(ReviewSettings.user_id).where(ReviewSettings.auto_reply.is_(True))))
    for user_id in user_ids:
        try:
            message = await _run_user(db, settings, user_id)
        except AppError as error:
            message = error.message
        except Exception as error:
            logger.error("review_auto_run_failed type=%s", type(error).__name__)
            message = "Tekshiruvda kutilmagan xato, keyingi tekshiruvda qayta uriniladi."
        async with db.transaction() as session:
            current = await session.get(ReviewSettings, user_id, with_for_update=True)
            if current:
                current.last_run_at, current.last_message = utcnow(), message[:300]
    return len(user_ids)


async def reply_loop(db: Database, settings: Settings, wake: asyncio.Event) -> None:
    """Web jarayonida ishlaydi: sozlama saqlanganda yoki "Hozir tekshirish" bosilganda `wake` uyg'otadi."""
    loop = asyncio.get_running_loop()
    while True:
        wake.clear()
        started = loop.time()
        if not settings.maintenance_mode:
            try:
                await run_due(db, settings)
            except Exception as error:
                logger.error("review_reply_loop_failed type=%s", type(error).__name__)
        try:
            await asyncio.wait_for(wake.wait(), timeout=settings.review_reply_interval_seconds)
        except TimeoutError:
            pass
        await asyncio.sleep(max(0.0, 30 - (loop.time() - started)))
