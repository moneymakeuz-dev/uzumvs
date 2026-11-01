"""A'zoning Uzum ulanishi: API tokeni (do'konlar ro'yxati) va kabinet logini (yuk xatlari, XLSM, timeslot)."""
import asyncio
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.config import ROOT, Settings
from app.db import Database
from app.errors import AppError
from app.models import TimeslotWatch, UzumAccount
from app.services import uzum_cabinet

SHOPS_MAX_AGE = timedelta(hours=6)
STATUS_LABELS = {"active": "Ulangan", "token_failed": "Token eskirgan", "login_failed": "Kirib bo'lmadi"}
LOGIN_FAILED = "Uzum kabinetiga kirib bo'lmadi: login yoki parol o'zgargan. Uzum ulanishini yangilang."
TOKEN_FAILED = "Uzum API tokeni muddati tugagan yoki o'chirilgan. Uzum ulanishida yangi token kiriting."


@dataclass(frozen=True)
class Credentials:
    user_id: UUID
    username: str
    password: str


def profile_dir(user_id: UUID) -> Path:
    """A'zoning Playwright profili (XLSM yuborish va shablon uchun); har kimniki alohida."""
    return ROOT / "var" / "uzum-profiles" / user_id.hex


def remove_profile(user_id: UUID) -> None:
    shutil.rmtree(profile_dir(user_id), ignore_errors=True)


def cipher(settings: Settings) -> Fernet:
    key = settings.uzum_credentials_key.get_secret_value()
    if not key:
        raise AppError("uzum_not_configured", "Uzum ulanishi serverda sozlanmagan. Administrator bilan bog'laning.", 503)
    return Fernet(key.encode())


def reveal(settings: Settings, value: str) -> str:
    try:
        return cipher(settings).decrypt(value.encode()).decode()
    except InvalidToken:
        raise AppError("uzum_reconnect_required", "Uzum ulanish ma'lumotlarini o'qib bo'lmadi. Uzum ulanishini yangilang.", 409) from None


def hide(settings: Settings, value: str) -> str:
    return cipher(settings).encrypt(value.encode()).decode()


def account_view(account: UzumAccount | None) -> dict[str, Any]:
    if account is None:
        return {"connected": False, "status": "missing", "label": "Ulanmagan", "message": "", "login_hint": "", "shops": []}
    return {"connected": account.status == "active", "status": account.status, "label": STATUS_LABELS[account.status],
            "message": account.status_message, "login_hint": account.login_hint, "shops": account.shops_json}


async def find(session: AsyncSession, user_id: UUID) -> UzumAccount | None:
    return await session.scalar(select(UzumAccount).where(UzumAccount.user_id == user_id))


async def is_connected(session: AsyncSession, user_id: UUID) -> bool:
    return await session.scalar(select(UzumAccount.status).where(UzumAccount.user_id == user_id)) == "active"


def require_active(account: UzumAccount | None) -> UzumAccount:
    if account is None:
        raise AppError("uzum_not_connected", "Avval Uzum akkauntingizni ulang: Profil → Uzum ulanishi.", 409)
    if account.status != "active":
        raise AppError(f"uzum_{account.status}", account.status_message or LOGIN_FAILED, 409)
    return account


async def mark(db: Database, user_id: UUID, status: str, message: str) -> None:
    async with db.transaction() as session:
        await session.execute(update(UzumAccount).where(UzumAccount.user_id == user_id)
                              .values(status=status, status_message=message, updated_at=utcnow()))


async def connect(db: Database, settings: Settings, user_id: UUID, api_token: str, login: str, password: str) -> dict[str, Any]:
    """Token va loginni Uzum'da tekshirib, shifrlab saqlaydi. Ikkalasi bitta akkauntniki bo'lishi shart."""
    cipher(settings)
    shops = await uzum_cabinet.fetch_shops(api_token)
    candidates = uzum_cabinet.login_candidates(login)
    async with uzum_cabinet.client() as http:
        for index, username in enumerate(candidates):
            cabinet = uzum_cabinet.Cabinet(http, username, password)
            try:
                await cabinet.login()
                break
            except AppError as error:
                if error.code != "uzum_login_invalid" or index == len(candidates) - 1:
                    raise
        if not await uzum_cabinet.can_open_shop(cabinet, shops[0]["id"]):
            raise AppError("uzum_account_mismatch", "Token va login boshqa-boshqa akkauntlarga tegishli: "
                           "tokendagi do'kon bu login bilan kabinetda ochilmadi.", 422, fields={"login": "Loginni tekshiring."})
    async with db.transaction() as session:
        account = await session.scalar(select(UzumAccount).where(UzumAccount.user_id == user_id).with_for_update())
        if account is None:
            account = UzumAccount(user_id=user_id)
            session.add(account)
        account.login_hint = uzum_cabinet.login_hint(login)
        account.login_secret = hide(settings, cabinet.username)
        account.password_secret = hide(settings, password)
        account.api_token_secret = hide(settings, api_token)
        account.access_token_secret = hide(settings, cabinet.token)
        account.shops_json, account.shops_checked_at = shops, utcnow()
        account.status, account.status_message, account.updated_at = "active", "", utcnow()
        await session.flush()
        view = account_view(account)
    # Brauzer profili oldingi akkaunt bilan kirgan bo'lishi mumkin: keyingi yuborishda yangi login bilan kiradi.
    await asyncio.to_thread(remove_profile, user_id)
    return view


async def disconnect(db: Database, user_id: UUID) -> None:
    async with db.transaction() as session:
        await session.execute(delete(UzumAccount).where(UzumAccount.user_id == user_id))
        await session.execute(update(TimeslotWatch).where(
            TimeslotWatch.user_id == user_id, TimeslotWatch.status == "active",
        ).values(status="cancelled", message="Uzum ulanishi o'chirildi, kuzatuv to'xtatildi.", updated_at=utcnow()))
    await asyncio.to_thread(remove_profile, user_id)


async def shops(db: Database, settings: Settings, user_id: UUID) -> list[dict[str, str]]:
    """A'zoning do'konlari: 6 soatda bir OpenAPI'dan yangilanadi, Uzum ishlamasa oxirgi ro'yxat qaytadi."""
    async with db.transaction() as session:
        account = require_active(await find(session, user_id))
        cached, checked_at, secret = account.shops_json, account.shops_checked_at, account.api_token_secret
    if utcnow() - checked_at < SHOPS_MAX_AGE:
        return cached
    try:
        fresh = await uzum_cabinet.fetch_shops(reveal(settings, secret))
    except AppError as error:
        if error.code in {"uzum_token_invalid", "uzum_shop_missing"}:
            await mark(db, user_id, "token_failed", TOKEN_FAILED)
            raise AppError("uzum_token_failed", TOKEN_FAILED, 409) from None
        return cached
    async with db.transaction() as session:
        await session.execute(update(UzumAccount).where(UzumAccount.user_id == user_id)
                              .values(shops_json=fresh, shops_checked_at=utcnow()))
    return fresh


async def ensure_shop(db: Database, settings: Settings, user_id: UUID, shop_id: str) -> None:
    if shop_id not in {shop["id"] for shop in await shops(db, settings, user_id)}:
        raise AppError("uzum_shop_invalid", "Tanlangan do'kon sizning Uzum do'konlaringiz orasida yo'q.", 422)


async def credentials(db: Database, settings: Settings, user_id: UUID) -> Credentials:
    async with db.transaction() as session:
        account = require_active(await find(session, user_id))
        return Credentials(user_id, reveal(settings, account.login_secret), reveal(settings, account.password_secret))


@asynccontextmanager
async def cabinet(db: Database, settings: Settings, user_id: UUID, *, background: bool = False) -> AsyncIterator[uzum_cabinet.Cabinet]:
    """A'zoning kabinet sessiyasi. Fon tekshiruvi (background) token eskirgan bo'lsa ham ishlaydi — u faqat loginga tayanadi.

    Login rad etilsa ulanish 'login_failed' bo'ladi va a'zo yangilamaguncha qayta urinilmaydi
    (aks holda Uzum akkauntni ko'p urinish uchun bloklashi mumkin).
    """
    async with db.transaction() as session:
        account = await find(session, user_id)
        if not (background and account is not None and account.status == "token_failed"):
            require_active(account)
        username, password = reveal(settings, account.login_secret), reveal(settings, account.password_secret)
        stored = account.access_token_secret
    token = reveal(settings, stored) if stored else None
    async with uzum_cabinet.client() as http:
        current = uzum_cabinet.Cabinet(http, username, password, token)
        try:
            yield current
        except AppError as error:
            if error.code == "uzum_login_invalid":
                await mark(db, user_id, "login_failed", LOGIN_FAILED)
                raise AppError("uzum_login_failed", LOGIN_FAILED, 409) from None
            raise
        finally:
            if current.token and current.token != token:
                async with db.transaction() as session:
                    await session.execute(update(UzumAccount).where(UzumAccount.user_id == user_id)
                                          .values(access_token_secret=hide(settings, current.token)))
