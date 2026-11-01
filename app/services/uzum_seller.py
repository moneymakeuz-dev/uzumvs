import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import UUID

from app.config import ROOT, Settings
from app.db import Database
from app.errors import AppError
from app.services import uzum_accounts, uzum_cabinet
from app.services.uzum_accounts import Credentials

UPLOAD_TIMEOUT_SECONDS = 240
TEMPLATE_TIMEOUT_SECONDS = 240
TEMPLATE_CACHE = ROOT / "var" / "uzum-template" / "template.xlsm"
TEMPLATE_MAX_AGE_SECONDS = 24 * 3600
REQUIRED_CACHE = ROOT / "var" / "uzum-template" / "required-characteristics.json"
_browser_lock = asyncio.Lock()
logger = logging.getLogger(__name__)


async def get_template(db: Database, settings: Settings, user_id: UUID, shop_id: str) -> bytes:
    """Uzum kabinetidagi rasmiy shablonni (“Скачать файл”) a'zoning sessiyasida yuklab, bir kunga keshlaydi."""
    if TEMPLATE_CACHE.is_file() and time.time() - TEMPLATE_CACHE.stat().st_mtime < TEMPLATE_MAX_AGE_SECONDS:
        return TEMPLATE_CACHE.read_bytes()
    credentials = await uzum_accounts.credentials(db, settings, user_id)
    TEMPLATE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    target = TEMPLATE_CACHE.with_suffix(".part")
    target.unlink(missing_ok=True)
    returncode, result = await run_script(settings, "download", target, shop_id, TEMPLATE_TIMEOUT_SECONDS, credentials)
    if returncode != 0 or result.get("status") != "downloaded" or not target.is_file():
        target.unlink(missing_ok=True)
        if result.get("code") == "auth_timeout":
            raise AppError("uzum_auth_timeout", "Uzum kabinetiga kirib bo'lmadi (CAPTCHA yoki tasdiqlash kutilmoqda).", 408)
        if TEMPLATE_CACHE.is_file():
            return TEMPLATE_CACHE.read_bytes()
        raise AppError("uzum_template_unavailable", "Uzum shablonini kabinetdan olib bo'lmadi. Faylni qo'lda tanlang.", 502)
    target.replace(TEMPLATE_CACHE)
    return TEMPLATE_CACHE.read_bytes()


async def required_characteristics(db: Database, settings: Settings, user_id: UUID, shop_id: str,
                                   category_id: int) -> list[dict]:
    """Kategoriya uchun Uzum majburiy xarakteristikalari (Rang, O'lcham...); shablonda yo'q, kabinet API'sidan olinadi."""
    cache = json.loads(REQUIRED_CACHE.read_text(encoding="utf-8")) if REQUIRED_CACHE.is_file() else {}
    entry = cache.get(str(category_id))
    if entry and time.time() - entry["at"] < TEMPLATE_MAX_AGE_SECONDS:
        return entry["items"]
    async with uzum_accounts.cabinet(db, settings, user_id) as cabinet:
        characteristics = await uzum_cabinet.required_characteristics(cabinet, shop_id, category_id)
    items = []
    for item in characteristics:
        title = (item.get("characteristicTitle") or {}).get("ru")
        if item.get("requiredType") == "REQUIRED" and isinstance(title, str):
            field = "color" if title == "Цвет" else "size" if title.startswith("Размер") else None
            items.append({"title": title, "field": field})
    cache[str(category_id)] = {"at": time.time(), "items": items}
    REQUIRED_CACHE.parent.mkdir(parents=True, exist_ok=True)
    REQUIRED_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return items


async def run_script(settings: Settings, mode: str, path: Path, shop_id: str, limit_seconds: int,
                     credentials: Credentials) -> tuple[int, dict]:
    if not settings.uzum_browser:
        raise AppError("uzum_browser_disabled", "Uzum brauzer yuklashi bu serverda o'chirilgan.", 503)
    node = shutil.which("node")
    script = ROOT / "scripts" / "uzum_upload.mjs"
    if not node or not script.is_file():
        raise AppError("uzum_browser_unavailable", "Mahalliy Playwright yuklagichi mavjud emas.", 503)
    environment = os.environ.copy()
    # Har bir a'zo o'z logini va alohida brauzer profili bilan: sessiyalar va cookie'lar aralashmaydi.
    environment.update({"UZUM_SELLER_EMAIL": credentials.username, "UZUM_SELLER_PASSWORD": credentials.password,
                        "UZUM_MODE": mode, "UZUM_BROWSER_PROFILE": str(uzum_accounts.profile_dir(credentials.user_id)),
                        "UZUM_DEBUG_DIR": str(ROOT / "var" / "uzum-debug" / credentials.user_id.hex)})
    command = [node, str(script), str(path), shop_id]
    if os.name != "nt" and not environment.get("DISPLAY"):
        # Serverda ekran yo'q: ko'rinadigan brauzer virtual displey (Xvfb) ichida ishlaydi.
        xvfb = shutil.which("xvfb-run")
        if not xvfb:
            raise AppError("uzum_browser_unavailable", "Serverda virtual displey (xvfb) o'rnatilmagan.", 503)
        command = [xvfb, "-a", *command]
        home = ROOT / "var" / "browser-home"
        home.mkdir(parents=True, exist_ok=True)
        environment["HOME"] = str(home)
    async with _browser_lock:
        try:
            # Veb-server Windows'da selector event loop ishlatadi, u asyncio subprocess'ni qo'llamaydi.
            completed = await asyncio.to_thread(
                subprocess.run, command,
                cwd=ROOT, env=environment, capture_output=True, timeout=limit_seconds, check=False,
            )
        except subprocess.TimeoutExpired:
            raise AppError("uzum_upload_timeout", "Uzum javobi olinmadi. Kabinetni tekshiring va darhol qayta yubormang.", 504) from None
        except OSError:
            raise AppError("uzum_browser_unavailable", "Playwright brauzerini ishga tushirib bo'lmadi.", 503) from None
    try:
        result = json.loads(completed.stdout.decode("utf-8").splitlines()[-1])
    except (UnicodeDecodeError, IndexError, json.JSONDecodeError):
        result = {}
    if completed.returncode != 0:
        logger.warning("uzum_script_failed mode=%s code=%s stderr=%s", mode, result.get("code"),
                       completed.stderr.decode("utf-8", "replace").strip()[:500])
    return completed.returncode, result


async def upload_template(db: Database, settings: Settings, user_id: UUID, data: bytes, shop_id: str) -> dict[str, str]:
    if not settings.uzum_browser:
        raise AppError("uzum_browser_disabled", "Uzum brauzer yuklashi bu serverda o'chirilgan.", 503)
    await uzum_accounts.ensure_shop(db, settings, user_id, shop_id)
    credentials = await uzum_accounts.credentials(db, settings, user_id)
    runtime_dir = ROOT / "var"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="uzum-upload-", dir=runtime_dir) as temporary:
        template = Path(temporary) / "product-template.xlsm"
        template.write_bytes(data)
        returncode, result = await run_script(settings, "upload", template, shop_id, UPLOAD_TIMEOUT_SECONDS, credentials)
        if returncode != 0 or result.get("status") != "accepted":
            code = result.get("code")
            if code == "auth_timeout":
                raise AppError("uzum_auth_timeout", "Uzum kabinetiga kirib bo'lmadi (CAPTCHA yoki tasdiqlash kutilmoqda).", 408)
            if code == "import_errors":
                raise AppError("uzum_import_errors", f"Uzum faylni rad etdi: {str(result.get('message') or '')[:600]}", 422)
            if code == "upload_rejected":
                raise AppError("uzum_upload_rejected", "Uzum faylni qabul qilmadi; import oynasidagi xatoni tekshiring.", 422)
            if code == "unknown_outcome":
                return {"status": "sent", "shop_id": shop_id,
                        "message": "Fayl Uzum'ga yuborildi, lekin natija aniqlanmadi. Kabinetdagi “Мои товары” ni tekshiring."}
            raise AppError("uzum_upload_failed", "Uzum faylini yuborib bo'lmadi. Kabinetdagi holatni tekshiring.", 502)
        return {"status": "accepted", "shop_id": shop_id, "message": "Uzum faylni qabul qildi; mahsulot importi bir necha daqiqa olishi mumkin."}
